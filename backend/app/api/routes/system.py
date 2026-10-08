"""Service endpoints: liveness, readiness and capabilities."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.api.deps import ContainerDep
from app.api.schemas import CapabilitiesOut, HealthOut, ReadinessOut
from app.ingestion.sniffing import ALLOWED_EXTENSIONS
from app.storage import StorageError

router = APIRouter(tags=["system"])
logger = logging.getLogger("app.api")


@router.get("/health", response_model=HealthOut, summary="Liveness: the process is up")
def health() -> HealthOut:
    # Deliberately dependency-free: a database outage must not make the orchestrator restart healthy pods.
    return HealthOut(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessOut,
    summary="Readiness: dependencies are reachable",
    responses={503: {"model": ReadinessOut}},
)
def ready(container: ContainerDep, response: Response) -> ReadinessOut:
    checks: dict[str, str] = {}
    try:
        with container.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # any failure means "not ready"; the reason is logged, not exposed
        logger.warning("readiness: database unavailable", extra={"fields": {"error": type(exc).__name__}})
        checks["database"] = "unavailable"
    try:
        container.storage.check()
        checks["storage"] = "ok"
    except StorageError:
        checks["storage"] = "unavailable"
    is_ready = all(v == "ok" for v in checks.values())
    response.status_code = 200 if is_ready else 503
    return ReadinessOut(status="ready" if is_ready else "not_ready", checks=checks)


@router.get("/api/capabilities", response_model=CapabilitiesOut, summary="Accepted formats and limits")
def capabilities(container: ContainerDep) -> CapabilitiesOut:
    s = container.settings
    return CapabilitiesOut(
        formats=sorted({f.value for f in ALLOWED_EXTENSIONS.values()}),
        extensions=sorted(ALLOWED_EXTENSIONS),
        max_upload_bytes=s.max_upload_bytes,
        max_features_per_file=s.max_features_per_file,
        measurement_strategy=s.measurement_strategy,
        measurements={
            "Polygon": "area_m2",
            "MultiPolygon": "area_m2",
            "LineString": "length_m",
            "MultiLineString": "length_m",
            "Point": "none",
            "MultiPoint": "none",
        },
    )
