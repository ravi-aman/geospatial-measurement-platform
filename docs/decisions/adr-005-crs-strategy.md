# ADR-005: Per-feature local equal-area projection with a geodesic cross-check

**Status:** Accepted · Details and numbers: [docs/geospatial/crs.md](../geospatial/crs.md)

## Context
Areas and lengths must not be computed in degrees; the assignment asks to project to "an appropriate projected
coordinate system" and leaves the strategy open. Inputs can be anywhere on Earth, span UTM zones, be near the poles,
or arrive in a projected CRS that is unsuitable for measurement (Web Mercator).

## Decision
1. Resolve the source CRS: client override > declared > unknown. **Never guess** a missing CRS.
2. Transform every feature to WGS 84, then project **each feature** to a **Lambert Azimuthal Equal-Area** CRS centred
   on the 1° grid cell containing it (own centre if wider than 2°), and measure there.
3. Compute an **ellipsoidal geodesic reference** (Karney, pyproj.Geod) for every measured feature, report the relative
   difference, and flag differences above 0.1 %.
4. Keep a per-feature **UTM** strategy available (`MEASUREMENT_STRATEGY=utm`) for users who need UTM EPSG codes.
5. Never measure in the source CRS, even when it is projected.

## Alternatives considered
| Option | Problem |
|---|---|
| Measure in EPSG:4326 | Square degrees are not area. |
| EPSG:3857 (Web Mercator) | Area +29 % at Delhi, +13,000 % at 85°N. |
| `estimate_utm_crs()` once per dataset | One zone for all features: outer features of multi-zone datasets get large distortion; fails for polar data; UTM is conformal, not equal-area (−0.08 % … +0.2 % area in-zone). |
| UTM per feature | Better, still −0.08 % … +0.2 % area and undefined at the poles. Kept as the alternative strategy. |
| Geodesic only (no projection) | Most accurate, but the assignment explicitly asks to project; kept as the cross-check. |
| Measure in the source CRS if projected | Breaks for Web Mercator and for national grids far from their origin. |

## Why
Measured (script in `backend/scripts/compare_projections.py`): local LAEA matches the geodesic reference to
< 0.001 % for features up to 50 km and works at the poles; UTM is off by up to 0.13 % for the same shapes.
Snapping centres to a 1° grid lets features share one cached `Transformer`, so the cost is the same as UTM.

## Trade-offs
* The measurement CRS is a PROJ string rather than an EPSG code — less familiar, but reproducible (paste into QGIS).
* LAEA linear distortion is non-zero for very large features (reported by the cross-check).

## Consequences
* Every measured feature carries `projected_crs`, `method`, `geodesic_*` and `relative_difference` — accuracy is
  evidence, not a claim.
* A property-based test pins the UTM zone selection to GeoPandas' `estimate_utm_crs()`.
