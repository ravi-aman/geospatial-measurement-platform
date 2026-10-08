"""``/api/files`` - upload, file information, measurements, features and vector tiles.

Endpoints are plain ``def``: FastAPI runs them in its threadpool, which suits the synchronous SQLAlchemy
data layer (see app/db/session.py). The multipart body itself is parsed asynchronously before the call.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, Path, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.api import presenters
from app.api.deps import ContainerDep, SessionDep
from app.api.pagination import (
    decode_feature_cursor,
    decode_file_cursor,
    encode_feature_cursor,
    encode_file_cursor,
)
from app.api.schemas import (
    ErrorResponse,
    FeatureCollectionOut,
    FeatureOut,
    FileListOut,
    FileOut,
    MeasurementPageOut,
    PageOut,
)
from app.container import Container
from app.db.models import FileUpload, ProcessingJob
from app.db.repositories import FeatureFilters, FeatureRepository, FileRepository, JobRepository, SortSpec
from app.domain.enums import JobStatus, MeasurementStatus
from app.domain.errors import ConflictError, InvalidRequestError, NotFoundError
from app.observability.context import current_request_id
from app.services.uploads import IncomingUpload

router = APIRouter(prefix="/api/files", tags=["files"])

_ERRORS: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse, "description": "File not found"},
    409: {"model": ErrorResponse, "description": "Results not available (still processing, or failed)"},
}
_PREFER_WAIT = re.compile(r"(?:^|[,;\s])wait\s*=\s*(\d+)", re.IGNORECASE)
_WAIT_POLL_S = 0.25
_MAX_TILE_ZOOM = 22
SortParam = Annotated[
    str,
    Query(
        pattern=r"^-?(feature_id|area_m2|length_m)$",
        description="feature_id | area_m2 | length_m; prefix '-' for descending",
    ),
]


# ---------------------------------------------------------------------------- helpers
def _load_file(session: Session, file_id: uuid.UUID) -> FileUpload:
    file = FileRepository(session).get(file_id)
    if file is None:
        raise NotFoundError("No file with this id.", code="FILE_NOT_FOUND", details={"file_id": str(file_id)})
    return file


def _require_results(file: FileUpload) -> ProcessingJob:
    job = file.job
    status = JobStatus(job.status)
    if status is JobStatus.FAILED:
        raise ConflictError(
            "Processing failed; no results are available for this file.",
            code="PROCESSING_FAILED",
            details={"status": status.value, "error_code": job.error_code, "error_message": job.error_message},
        )
    if not status.has_results:
        raise ConflictError(
            "The file is still being processed; poll GET /api/files/{id}/ for status.",
            code="RESULTS_NOT_READY",
            details={"status": status.value, "retry_after_s": 2},
        )
    return job


def _filters(
    status: list[MeasurementStatus] | None, geometry_type: list[str] | None, layer: str | None
) -> FeatureFilters:
    return FeatureFilters(statuses=[s.value for s in status or []], geometry_types=geometry_type or [], layer=layer)


def _prefer_wait_seconds(prefer: str | None, cap: float) -> float:
    """RFC 7240 ``Prefer: wait=N`` (ignored when combined with ``respond-async``)."""
    if not prefer or "respond-async" in prefer.lower():
        return 0.0
    match = _PREFER_WAIT.search(prefer)
    return min(float(match.group(1)), cap) if match else 0.0


def _wait_for_terminal(container: Container, job_id: uuid.UUID, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with container.session_factory() as session:
            job = JobRepository(session).get(job_id)
            if job is None or JobStatus(job.status).is_terminal:
                return
        time.sleep(_WAIT_POLL_S)


# ---------------------------------------------------------------------------- upload
@router.post(
    "/",
    response_model=FileOut,
    status_code=202,
    summary="Upload a KML file or a zipped Shapefile",
    responses={
        200: {"model": FileOut, "description": "Idempotent replay (same Idempotency-Key and content)"},
        201: {"model": FileOut, "description": "Uploaded and processing already finished"},
        202: {"model": FileOut, "description": "Uploaded; processing is queued or running"},
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
def upload_file(
    response: Response,
    container: ContainerDep,
    file: Annotated[UploadFile, File(description=".kml, or .zip containing one Shapefile (.shp/.shx/.dbf[/.prj])")],
    crs: Annotated[
        str | None, Form(description="Optional CRS override for files without/with a wrong CRS, e.g. EPSG:32643")
    ] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    prefer: Annotated[str | None, Header(description="RFC 7240, e.g. 'wait=10' to wait for processing")] = None,
) -> FileOut:
    """Accepts the file, validates it, and queues it for processing.

    Returns **202** with ``status: PENDING`` and a ``Location`` header; poll ``GET /api/files/{id}/``.
    Send ``Prefer: wait=N`` (N <= 30 s) to wait for completion: the response is then **201** with final results
    if processing finished in time.
    """
    result = container.upload_service().accept(
        IncomingUpload(
            filename=file.filename,
            content_type=file.content_type,
            stream=file.file,
            crs=crs,
            idempotency_key=idempotency_key,
            request_id=current_request_id(),
        )
    )
    wait_s = _prefer_wait_seconds(prefer, container.settings.upload_wait_max_s)
    if wait_s and not result.replayed:
        _wait_for_terminal(container, result.job_id, wait_s)
        response.headers["Preference-Applied"] = f"wait={int(wait_s)}"

    with container.session_factory() as session:
        out = presenters.file_out(_load_file(session, result.file_id))

    response.headers["Location"] = out.links.self_
    if result.replayed:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    elif out.status.is_terminal:
        response.status_code = 201
    else:
        response.status_code = 202
        response.headers["Retry-After"] = "1"
    return out


# ---------------------------------------------------------------------------- file information
@router.get("/", response_model=FileListOut, summary="List recent uploads (newest first)")
def list_files(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> FileListOut:
    before = decode_file_cursor(cursor) if cursor else None
    files = FileRepository(session).list_recent(limit=limit + 1, before=before)
    page, more = files[:limit], len(files) > limit
    next_cursor = encode_file_cursor(page[-1].created_at, page[-1].id) if more and page else None
    return FileListOut(items=[presenters.file_list_item(f) for f in page], next_cursor=next_cursor)


@router.get("/{file_id}/", response_model=FileOut, summary="File information and processing status", responses=_ERRORS)
def get_file(file_id: uuid.UUID, session: SessionDep) -> FileOut:
    return presenters.file_out(_load_file(session, file_id))


# ---------------------------------------------------------------------------- measurements
@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementPageOut,
    responses=_ERRORS,
    summary="Per-feature measurements (area for polygons, length for lines)",
)
def get_measurements(
    file_id: uuid.UUID,
    container: ContainerDep,
    session: SessionDep,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(description="next_cursor from the previous page")] = None,
    status: Annotated[list[MeasurementStatus] | None, Query(description="Filter by measurement status")] = None,
    geometry_type: Annotated[list[str] | None, Query(description="Filter by geometry type, e.g. Polygon")] = None,
    layer: Annotated[str | None, Query()] = None,
    sort: SortParam = "feature_id",
) -> MeasurementPageOut:
    settings = container.settings
    page_size = min(limit or settings.page_size_default, settings.page_size_max)
    file = _load_file(session, file_id)
    job = _require_results(file)
    spec = SortSpec.parse(sort)
    filters = _filters(status, geometry_type, layer)
    repo = FeatureRepository(session)
    rows = repo.page(
        job.id,
        filters=filters,
        sort=spec,
        after=decode_feature_cursor(cursor, spec) if cursor else None,
        limit=page_size + 1,
    )
    page, more = rows[:page_size], len(rows) > page_size
    next_cursor = None
    if more and page:
        last = page[-1]
        next_cursor = encode_feature_cursor(spec, last.feature_index)
    total = repo.count(job.id, filters) if cursor is None else None
    return MeasurementPageOut(
        file_id=file.id,
        status=JobStatus(job.status),
        crs=job.crs,
        items=[presenters.measurement_item(r) for r in page],
        page=PageOut(limit=page_size, next_cursor=next_cursor, total=total),
    )


# ---------------------------------------------------------------------------- features (GeoJSON)
@router.get(
    "/{file_id}/features/",
    response_model=FeatureCollectionOut,
    responses=_ERRORS,
    summary="Features as an RFC 7946 GeoJSON FeatureCollection (paginated)",
)
def get_features(
    file_id: uuid.UUID,
    container: ContainerDep,
    session: SessionDep,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query()] = None,
    status: Annotated[list[MeasurementStatus] | None, Query()] = None,
    geometry_type: Annotated[list[str] | None, Query()] = None,
    layer: Annotated[str | None, Query()] = None,
    sort: SortParam = "feature_id",
) -> FeatureCollectionOut:
    settings = container.settings
    page_size = min(limit or settings.page_size_default, settings.feature_page_size_max)
    file = _load_file(session, file_id)
    job = _require_results(file)
    spec = SortSpec.parse(sort)
    filters = _filters(status, geometry_type, layer)
    repo = FeatureRepository(session)
    rows = repo.page(
        job.id,
        filters=filters,
        sort=spec,
        limit=page_size + 1,
        with_geometry=True,
        after=decode_feature_cursor(cursor, spec) if cursor else None,
    )
    page, more = rows[:page_size], len(rows) > page_size
    next_cursor = None
    if more and page:
        next_cursor = encode_feature_cursor(spec, page[-1].feature_index)
    return FeatureCollectionOut(
        file_id=file.id,
        source_crs=job.crs,
        features=[presenters.feature_out(r) for r in page],
        page=PageOut(
            limit=page_size, next_cursor=next_cursor, total=repo.count(job.id, filters) if cursor is None else None
        ),
    )


@router.get(
    "/{file_id}/features/{feature_id}/",
    response_model=FeatureOut,
    responses=_ERRORS,
    summary="One feature with geometry, attributes, measurement and repaired geometry",
)
def get_feature(file_id: uuid.UUID, feature_id: Annotated[int, Path(ge=0)], session: SessionDep) -> FeatureOut:
    job = _require_results(_load_file(session, file_id))
    row = FeatureRepository(session).get(job.id, feature_id)
    if row is None:
        raise NotFoundError("No feature with this id in the file.", code="FEATURE_NOT_FOUND")
    return presenters.feature_out(row, include_repaired=True)


# ---------------------------------------------------------------------------- vector tiles
@router.get(
    "/{file_id}/tiles/{z}/{x}/{y}.mvt",
    responses={
        **_ERRORS,
        200: {"content": {"application/vnd.mapbox-vector-tile": {}}},
        204: {"description": "Empty tile"},
    },
    summary="Mapbox Vector Tile of the file's features (for MapLibre)",
    response_class=Response,
)
def get_tile(
    file_id: uuid.UUID,
    z: int,
    x: int,
    y: int,
    container: ContainerDep,
    session: SessionDep,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    if not 0 <= z <= _MAX_TILE_ZOOM or not (0 <= x < 2**z and 0 <= y < 2**z):
        raise InvalidRequestError("Tile coordinates out of range.", code="INVALID_TILE")
    job = _require_results(_load_file(session, file_id))
    # Results of a completed job never change, so tiles are safely cacheable by browsers and CDNs.
    etag = f'"{job.id.hex}-{z}-{x}-{y}"'
    headers = {"Cache-Control": "public, max-age=86400", "ETag": etag}
    if if_none_match is not None and etag in (t.strip() for t in if_none_match.split(",")):
        return Response(status_code=304, headers=headers)  # revalidation without re-rendering the tile
    data = FeatureRepository(session).tile(job.id, z, x, y, max_features=container.settings.tile_max_features)
    if not data:
        return Response(status_code=204, headers=headers)
    return Response(content=data, media_type="application/vnd.mapbox-vector-tile", headers=headers)
