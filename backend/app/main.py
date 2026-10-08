"""FastAPI application factory.

Run with ``uvicorn app.main:create_app --factory``. A factory (instead of a module-level ``app``) means
importing the package has no side effects, and tests build isolated apps with their own settings.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.errors import install_error_handlers
from app.api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware
from app.api.routes import files, jobs, system
from app.config import Settings, get_settings
from app.container import Container
from app.observability.logging import configure_logging

# Multipart framing (boundaries, part headers, the optional 'crs' field) on top of the file itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024

DESCRIPTION = """
Upload a **KML** file or a **zipped Shapefile**; every feature is extracted with its geometry type, geometry,
CRS and attributes, and measured: **area (m²)** for polygons, **length (m)** for lines. Points need no measurement.

Measurements are never computed in degrees: each feature is projected to a local metric CRS
(Lambert Azimuthal Equal-Area by default) and cross-checked against an ellipsoidal geodesic reference.

Processing is asynchronous: `POST /api/files/` returns `202` and a `Location`; poll `GET /api/files/{id}/`
(or send `Prefer: wait=10`). All errors share one envelope: `{"error": {"code", "message", "details", "request_id"}}`.
"""


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = container or Container.build(settings)
        if settings.embedded_worker and state.worker is None:
            state.start_embedded_worker()
        app.state.container = state
        try:
            yield
        finally:
            if container is None:  # only tear down what we built
                state.shutdown()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs" if settings.expose_docs else None,
        redoc_url="/redoc" if settings.expose_docs else None,
        openapi_url="/openapi.json" if settings.expose_docs else None,
    )
    install_error_handlers(app)
    app.include_router(files.router)
    app.include_router(jobs.router)
    app.include_router(system.router)

    # Middleware order: the last added runs first (outermost).
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "Prefer", "X-Request-ID"],
        expose_headers=["Location", "X-Request-ID", "Retry-After", "Idempotent-Replayed", "Preference-Applied"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)
    return app
