"""Upload service + job executor end to end (real storage, real queue, real PostGIS)."""

from __future__ import annotations

import io
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point, Polygon, box
from sqlalchemy import func, select, update

from app.config import Settings
from app.container import Container
from app.db.models import Feature, FileUpload, ProcessingJob
from app.db.repositories import JobRepository
from app.domain.enums import JobStatus
from app.domain.errors import InvalidRequestError, UploadRejectedError
from app.services.processing import JobExecutor
from app.services.uploads import IncomingUpload, UploadService
from app.storage.local import LocalStorage
from tests.builders import BOWTIE, kml_document, kml_placemark, kml_point, kml_polygon, shapefile_zip, square

KML = kml_document(
    kml_placemark("plot", kml_polygon(square(77, 28, 0.01)), {"owner": "Asha"}),
    kml_placemark("bowtie", kml_polygon(BOWTIE)),
    kml_placemark("well", kml_point(77.2, 28.6)),
    kml_placemark("no geometry"),
)


def upload(container: Container, data: bytes, name: str = "survey.kml", **kwargs: object) -> object:
    service = UploadService(container.session_factory, container.storage, container.settings)
    return service.accept(IncomingUpload(filename=name, content_type="x", stream=io.BytesIO(data), **kwargs))  # type: ignore[arg-type]


def job_of(container: Container, job_id: uuid.UUID) -> ProcessingJob:
    with container.session_factory() as session:
        job = session.get(ProcessingJob, job_id)
        assert job is not None
        return job


def feature_count(container: Container, job_id: uuid.UUID) -> int:
    with container.session_factory() as session:
        return int(session.execute(select(func.count()).where(Feature.job_id == job_id)).scalar_one())


class TestUploadService:
    def test_kml_upload_creates_file_job_and_blob(self, container: Container) -> None:
        result = upload(container, KML)
        job = job_of(container, result.job_id)  # type: ignore[attr-defined]
        assert job.status == JobStatus.PENDING and job.source_format == "KML"
        assert job.storage_key.startswith(f"uploads/{job.content_sha256[:2]}/") and job.storage_key.endswith(".kml")
        assert container.storage.exists(job.storage_key)
        assert not result.replayed and not result.job_reused  # type: ignore[attr-defined]

    def test_duplicate_content_reuses_the_job(self, container: Container) -> None:
        first = upload(container, KML, "a.kml")
        second = upload(container, KML, "b.kml")
        assert first.job_id == second.job_id and first.file_id != second.file_id  # type: ignore[attr-defined]
        assert second.job_reused  # type: ignore[attr-defined]

    def test_different_crs_override_is_a_different_job(self, container: Container, workdir: Path) -> None:
        archive = shapefile_zip([box(500000, 3100000, 500100, 3100100)], crs=None, workdir=workdir)
        a = upload(container, archive, "a.zip")
        b = upload(container, archive, "a.zip", crs="EPSG:32643")
        assert a.job_id != b.job_id  # type: ignore[attr-defined]
        assert job_of(container, b.job_id).crs_override == "EPSG:32643"  # type: ignore[attr-defined]

    def test_idempotency_key_replays_the_original(self, container: Container) -> None:
        first = upload(container, KML, idempotency_key="client-retry-0001")
        again = upload(container, KML, idempotency_key="client-retry-0001")
        assert again.replayed and again.file_id == first.file_id  # type: ignore[attr-defined]
        with container.session_factory() as session:
            assert session.execute(select(func.count()).select_from(FileUpload)).scalar_one() == 1

    def test_idempotency_key_with_different_content_is_rejected(self, container: Container) -> None:
        upload(container, KML, idempotency_key="client-retry-0002")
        with pytest.raises(UploadRejectedError) as exc:
            upload(container, kml_document(kml_placemark("x", kml_point(1, 1))), idempotency_key="client-retry-0002")
        assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"

    def test_malformed_idempotency_key(self, container: Container) -> None:
        with pytest.raises(InvalidRequestError):
            upload(container, KML, idempotency_key="bad key!")

    def test_retryable_failed_job_is_requeued_on_reupload(self, container: Container) -> None:
        result = upload(container, KML)
        with container.session_factory.begin() as session:
            session.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == result.job_id)  # type: ignore[attr-defined]
                .values(status="FAILED", error_code="INFRASTRUCTURE_ERROR", error_retryable=True, attempts=3)
            )
        upload(container, KML)
        assert job_of(container, result.job_id).status == JobStatus.PENDING  # type: ignore[attr-defined]

    def test_permanently_failed_job_is_not_requeued(self, container: Container) -> None:
        result = upload(container, KML)
        with container.session_factory.begin() as session:
            session.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == result.job_id)  # type: ignore[attr-defined]
                .values(status="FAILED", error_code="INVALID_KML", error_retryable=False)
            )
        upload(container, KML)
        assert job_of(container, result.job_id).status == JobStatus.FAILED  # type: ignore[attr-defined]

    def test_rejected_upload_persists_nothing(self, container: Container) -> None:
        with pytest.raises(UploadRejectedError):
            upload(container, b"<html></html>")
        with container.session_factory() as session:
            assert session.execute(select(func.count()).select_from(ProcessingJob)).scalar_one() == 0
        assert not any(Path(container.settings.storage_local_root).rglob("*.kml"))


class TestJobExecution:
    def test_kml_job_completes_with_failure_isolation(
        self, container: Container, run_worker: Callable[[], int]
    ) -> None:
        result = upload(container, KML)
        assert run_worker() == 1
        job = job_of(container, result.job_id)  # type: ignore[attr-defined]
        assert job.status == JobStatus.COMPLETED_WITH_ERRORS  # the geometry-less placemark failed; others fine
        assert (job.total_features, job.measured_features, job.not_applicable_features, job.failed_features) == (
            4,
            2,
            1,
            1,
        )
        assert job.crs == "EPSG:4326" and job.crs_source == "FILE" and job.duration_ms is not None
        assert job.summary is not None and job.summary["repaired_features"] == 1
        assert job.locked_by is None and job.finished_at is not None
        assert feature_count(container, job.id) == 4

    def test_shapefile_job(self, container: Container, run_worker: Callable[[], int], workdir: Path) -> None:
        # A Shapefile holds a single geometry type; a null shape record is legal and must not sink the file.
        geoms = [box(77, 28, 77.01, 28.01), box(77.02, 28, 77.03, 28.01), Polygon(BOWTIE), None]
        result = upload(container, shapefile_zip(geoms, workdir=workdir, folder="export/"), "parcels.zip")
        run_worker()
        job = job_of(container, result.job_id)  # type: ignore[attr-defined]
        assert job.status == JobStatus.COMPLETED_WITH_ERRORS and job.total_features == 4
        assert (job.measured_features, job.failed_features) == (3, 1)
        assert job.summary is not None and job.summary["layers"][0]["name"] == "parcels"
        assert job.summary["layers"][0]["driver"] == "ESRI Shapefile"

    def test_line_and_point_shapefiles(
        self, container: Container, run_worker: Callable[[], int], workdir: Path
    ) -> None:
        lines = upload(
            container, shapefile_zip([LineString([(77, 28), (77.1, 28)])] * 3, workdir=workdir, stem="roads"), "r.zip"
        )
        points = upload(container, shapefile_zip([Point(77, 28)] * 2, workdir=workdir, stem="wells"), "w.zip")
        run_worker()
        assert job_of(container, lines.job_id).measured_features == 3  # type: ignore[attr-defined]
        assert job_of(container, points.job_id).not_applicable_features == 2  # type: ignore[attr-defined]

    def test_shapefile_without_prj_completes_with_explicit_crs_errors(
        self, container: Container, run_worker: Callable[[], int], workdir: Path
    ) -> None:
        archive = shapefile_zip([box(10, 10, 20, 20)], drop={".prj"}, workdir=workdir)
        result = upload(container, archive, "noprj.zip")
        run_worker()
        job = job_of(container, result.job_id)  # type: ignore[attr-defined]
        assert job.status == JobStatus.COMPLETED_WITH_ERRORS and job.crs is None
        assert [w["code"] for w in job.warnings][:1] == ["CRS_MISSING"]

    def test_unreadable_dataset_fails_permanently(
        self, container: Container, run_worker: Callable[[], int], workdir: Path
    ) -> None:
        from tests.builders import zip_bytes

        result = upload(container, zip_bytes({"x.shp": b"garbage", "x.shx": b"garbage", "x.dbf": b"garbage"}), "x.zip")
        run_worker()
        job = job_of(container, result.job_id)  # type: ignore[attr-defined]
        assert job.status == JobStatus.FAILED and job.error_code == "UNREADABLE_DATASET"
        assert not job.error_retryable and job.attempts == 1

    def test_missing_blob_is_retried_then_fails(
        self, settings_factory: Callable[..., Settings], container: Container
    ) -> None:
        result = upload(container, KML)
        job = job_of(container, result.job_id)  # type: ignore[attr-defined]
        container.storage.delete(job.storage_key)
        with container.session_factory.begin() as session:
            session.execute(update(ProcessingJob).where(ProcessingJob.id == job.id).values(max_attempts=2))
        processed = 0
        while processed < 5:
            with container.session_factory.begin() as session:
                claimed = JobRepository(session).claim_next("t", 60)
            if claimed is None:
                break
            JobExecutor(container.session_factory, container.storage, container.settings).execute(claimed)
            processed += 1
        final = job_of(container, job.id)
        assert processed == 2  # retried once (backoff 0 s in tests), then gave up
        assert final.status == JobStatus.FAILED and final.error_code == "INFRASTRUCTURE_ERROR" and final.error_retryable

    def test_rerun_after_crash_does_not_duplicate_features(
        self, container: Container, run_worker: Callable[[], int]
    ) -> None:
        result = upload(container, KML)
        run_worker()
        with container.session_factory.begin() as session:  # simulate a crash: job back to PENDING, rows remain
            session.execute(
                update(ProcessingJob).where(ProcessingJob.id == result.job_id).values(status="PENDING", attempts=0)  # type: ignore[attr-defined]
            )
        run_worker()
        assert feature_count(container, result.job_id) == 4  # type: ignore[attr-defined]

    def test_worker_that_lost_its_lease_writes_nothing(self, container: Container) -> None:
        result = upload(container, KML)
        with container.session_factory.begin() as session:
            claimed = JobRepository(session).claim_next("slow", 60)
        assert claimed is not None
        with container.session_factory.begin() as session:  # another worker took over
            session.execute(update(ProcessingJob).where(ProcessingJob.id == claimed.id).values(locked_by="other:1"))
        status = JobExecutor(container.session_factory, container.storage, container.settings).execute(claimed)
        assert status is None
        assert feature_count(container, result.job_id) == 0  # type: ignore[attr-defined]
        assert job_of(container, claimed.id).locked_by == "other:1"

    def test_graceful_shutdown_releases_the_job(self, container: Container) -> None:
        upload(container, KML)
        with container.session_factory.begin() as session:
            claimed = JobRepository(session).claim_next("stopping", 60)
        assert claimed is not None
        stop = threading.Event()
        stop.set()
        status = JobExecutor(container.session_factory, container.storage, container.settings).execute(claimed, stop)
        assert status is JobStatus.PENDING
        job = job_of(container, claimed.id)
        assert (job.status, job.attempts) == (JobStatus.PENDING, 0)

    def test_exhausted_job_is_failed_without_processing(self, container: Container) -> None:
        upload(container, KML)
        with container.session_factory.begin() as session:
            session.execute(update(ProcessingJob).values(attempts=3, max_attempts=3))
            claimed = JobRepository(session).claim_next("w", 60)
        assert claimed is not None and claimed.exhausted
        status = JobExecutor(container.session_factory, container.storage, container.settings).execute(claimed)
        assert status is JobStatus.FAILED
        assert job_of(container, claimed.id).error_code == "MAX_ATTEMPTS_EXCEEDED"

    def test_utm_strategy_end_to_end(
        self, settings_factory: Callable[..., Settings], db_engine: object, clean_db: None, tmp_path: Path
    ) -> None:
        from app.db.session import create_session_factory
        from app.worker.runner import Worker

        settings = settings_factory(measurement_strategy="utm")
        utm_container = Container(
            settings,
            db_engine,
            create_session_factory(db_engine),
            LocalStorage(tmp_path / "s"),  # type: ignore[arg-type]
        )
        result = upload(utm_container, kml_document(kml_placemark("p", kml_polygon(square(77.2, 28.6, 0.01)))))
        Worker(settings, utm_container.session_factory, utm_container.storage).run_until_idle()
        with utm_container.session_factory() as session:
            feature = session.execute(select(Feature).where(Feature.job_id == result.job_id)).scalar_one()  # type: ignore[attr-defined]
        assert feature.projected_crs == "EPSG:32643" and feature.projection_method == "UTM"


def test_polygon_validity_checked_in_postgis_too(container: Container, run_worker: Callable[[], int]) -> None:
    """The stored original is the invalid bow-tie; the stored repaired geometry is valid (PostGIS agrees)."""
    result = upload(container, kml_document(kml_placemark("bowtie", kml_polygon(BOWTIE))))
    run_worker()
    with container.session_factory() as session:
        row = session.execute(
            select(func.ST_IsValid(Feature.geom), func.ST_IsValid(Feature.geom_repaired), Feature.area_m2).where(
                Feature.job_id == result.job_id  # type: ignore[attr-defined]
            )
        ).one()
    assert row[0] is False and row[1] is True and row[2] > 0
    assert Polygon(BOWTIE).is_valid is False
