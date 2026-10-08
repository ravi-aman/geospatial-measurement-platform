# Architecture Decision Records

Each record states the context, the decision, the alternatives that were seriously considered, why this one won,
the trade-offs accepted and the consequences for the codebase. Status values: *Accepted* (implemented) or
*Proposed* (designed, not built).

| ADR | Decision | Status |
|---|---|---|
| [001](adr-001-framework.md) | FastAPI (not Django/DRF) | Accepted |
| [002](adr-002-processing-model.md) | Asynchronous processing on a PostgreSQL-backed job queue with separate workers | Accepted |
| [003](adr-003-storage.md) | Object-storage abstraction, content-addressed keys; local disk in dev, S3 in production | Accepted |
| [004](adr-004-database.md) | PostgreSQL + PostGIS (Supabase), geometry in PostGIS, dedicated schema | Accepted |
| [005](adr-005-crs-strategy.md) | Per-feature local equal-area projection + geodesic cross-check; never guess a CRS | Accepted |
| [006](adr-006-geometry-repair.md) | Repair invalid polygons with `MakeValid(structure)`, keep original and repaired | Accepted |
| [007](adr-007-frontend-and-map.md) | Vite SPA + MapLibre on PostGIS vector tiles | Accepted |
| [008](adr-008-idempotency-and-caching.md) | Fingerprint dedup + Idempotency-Key; no Redis | Accepted |
| [009](adr-009-streaming-instead-of-geodataframes.md) | Stream with pyogrio + Shapely 2 + pyproj; GeoPandas only in tests | Accepted |
| [010](adr-010-sync-data-layer.md) | Synchronous SQLAlchemy 2 + psycopg 3 for API and worker | Accepted |
