# ADR-004: PostgreSQL + PostGIS (Supabase), geometry stored in PostGIS

**Status:** Accepted

## Context
We need durable job state, per-feature results queryable by file with filters, sorting and pagination, and a way to
put potentially millions of features on a web map. The project owner chose Supabase (managed PostgreSQL) as the
database.

## Decision
* PostgreSQL with the **PostGIS** extension; schema managed by Alembic.
* Feature geometry stored as PostGIS `geometry(Geometry, 4326)` (the original, in WGS 84) with a GiST index; the
  repaired geometry (only when repaired) in `geom_repaired`; coordinates whose CRS is unknown in an SRID-less
  `geom_raw` column, never mislabelled as 4326. A CHECK constraint guarantees at most one of `geom` / `geom_raw`.
* Attributes in JSONB; measurements as columns of `features` (1:1, no join needed).
* Tables in a **dedicated schema** (`geomeasure`, configurable) mapped with SQLAlchemy's `schema_translate_map`.

## Why PostGIS (and not plain PostgreSQL with GeoJSON in JSONB)
* **Vector tiles in SQL**: `ST_AsMVT` / `ST_AsMVTGeom` / `ST_TileEnvelope` + the GiST index serve map tiles for any
  dataset size without sending GeoJSON to the browser. This alone justifies the extension.
* **Spatial correctness checks** from an independent implementation (tests assert `ST_IsValid` on stored geometries).
* **Future spatial queries** — bbox filters, intersections with lease boundaries, dissolved footprints
  (`ST_Union`) — come for free.
* Aereo's public FOSS4G-Asia talk describes PostGIS as part of their platform; the choice is conventional in this
  domain.

## Why a dedicated schema
Supabase automatically exposes the `public` schema through its REST (PostgREST) API. Tables in their own schema are
not exposed, so the service's data is reachable only through this API. The same mechanism gives every test run an
isolated, throwaway schema built by the real migrations.

## Supabase-specific consequences (found while building)
* Direct DB host is IPv6-only → the app connects through the **Session pooler** (IPv4).
* Server-side prepared statements are disabled (`prepare_threshold=None`) so the app also works through the
  transaction-mode pooler; the app holds no session state (no `SET`, no `LISTEN`).
* Supabase renders `double precision` with `extra_float_digits = 0` (15 significant digits). A pagination test failed
  against Supabase because a float cursor no longer *equalled* the stored value; cursors now carry only the last
  `feature_index` and the database resolves the sort value itself.
* PostGIS lives in Supabase's `extensions` schema; the migration enables it there when that schema exists and in the
  default schema otherwise (CI uses the `postgis/postgis` image).

## Alternatives considered
* **SQLite/SpatiaLite** — no concurrent workers, no `SKIP LOCKED`.
* **Geometries in object storage (GeoParquet) + DB for metadata only** — the right step at 100M+ features
  (see [scalability](../architecture/scalability.md)); unnecessary complexity now.

## Trade-offs
* Geometry is stored twice overall (original file in object storage + WGS 84 copy in PostGIS). Accepted: the copy is
  what makes tiles, filtering and spatial checks cheap.
