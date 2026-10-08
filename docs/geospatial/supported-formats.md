# Supported formats

> Code: [`app/ingestion/`](../../backend/app/ingestion/), [`app/geoprocessing/reader.py`](../../backend/app/geoprocessing/reader.py),
> [`app/geoprocessing/properties.py`](../../backend/app/geoprocessing/properties.py)

All reading goes through GDAL (via pyogrio). GDAL is the reference implementation for both formats — the same
library QGIS and most GIS servers use — so edge cases (encodings, ESRI WKT, multipart records) behave as users expect.

## KML (`.kml`)

| Aspect | Behaviour |
|---|---|
| Driver | GDAL **LIBKML** (bundled in both the PyPI wheels and conda-forge, so dev and production behave the same). The driver name is recorded per layer in the file summary. |
| CRS | Always WGS 84 lon/lat (OGC KML 2.2). |
| Layers | Every `<Folder>`/`<Document>` that contains placemarks is a GDAL layer. **All layers are read**, in order; `feature_id` runs across layers and each feature records its `layer`. (Reading only the first layer — what `geopandas.read_file` does by default — would silently drop most features of a typical KML.) |
| Geometries | Point, LineString, LinearRing, Polygon (with holes), MultiGeometry (homogeneous → Multi*, mixed → GeometryCollection). |
| Attributes | `<name>` and `<description>` (as `Name`, `description`), `<ExtendedData>` `<Data>` and `<SchemaData>`/`<SimpleData>`. LIBKML also adds ~12 presentation fields (`tessellate`, `visibility`, `drawOrder`, …); values equal to the driver's "unset" sentinel are dropped because they are not user data. KML has no fixed schema, so `null` values are dropped too. |
| Altitude | `x,y,z` coordinates are accepted; Z is ignored for measurement (warning). |
| Safety | Before GDAL sees the file: streaming `defusedxml` scan rejects DTDs and entities (XXE, billion laughs) and checks the root element is `<kml>`. |
| `<NetworkLink>` | Counted and reported (`KML_NETWORK_LINKS_IGNORED`); remote content is never fetched. |
| Not supported | KMZ (zipped KML) is rejected with a clear message (`CONTENT_MISMATCH`: unzip first); `gx:Track` and 3D models are not measured. |
| Size note | GDAL's KML drivers parse the whole document in memory, so KML size is bounded by the upload limit. Very large vector data is better delivered as Shapefile (or, in future, GeoPackage/FlatGeobuf/GeoParquet). |

## Zipped Shapefile (`.zip`)

| Aspect | Behaviour |
|---|---|
| Archive | Must contain **exactly one** `.shp`; it may be inside folders (`export/roads.shp`). macOS `__MACOSX/` and `._*` entries, `.DS_Store`, `Thumbs.db` are ignored. |
| Components | `.shp`, `.shx`, `.dbf` required (matched case-insensitively by stem). `.prj` and `.cpg` used when present. Everything else (`.sbn`, `.qix`, `.shp.xml`, readmes) is listed as ignored. |
| CRS | From `.prj` (ESRI WKT mapped to EPSG when possible). Missing `.prj` → `CRS_MISSING`; supply `crs=EPSG:xxxx` on upload. |
| Encoding | `.cpg` if present, else the DBF language-driver byte, else ISO-8859-1 (pyogrio's default). |
| Geometries | One geometry type per Shapefile (format rule): Point/MultiPoint, PolyLine (→ (Multi)LineString), Polygon (→ (Multi)Polygon). Null shape records are allowed and reported as `GEOMETRY_MISSING`. |
| Attributes | All DBF fields kept; `null` values kept (the DBF schema is fixed). |
| Streaming | Read in Arrow batches of `PROCESSING_BATCH_SIZE` (5,000) features — memory is bounded by the batch, not the file. |
| Layer name | The sanitised `.shp` stem (e.g. `plots_utm43n`). |

## Attribute normalisation (both formats)

Attributes are stored as JSONB and returned unchanged except where correctness requires it:

| Value | Stored as | Why |
|---|---|---|
| NaN / ±Infinity | `null` | not valid JSON |
| string with NUL (`\u0000`) | NUL removed | PostgreSQL text/JSONB cannot store NUL |
| date / datetime / time | ISO 8601 string | JSON has no date type |
| `Decimal` | float | JSON number |
| bytes | `{"$base64": "…"}` | binary-safe, explicit |

Any such change adds a `PROPERTIES_SANITIZED` issue to the feature. A display label (`name`) is derived from the
first of `Name`, `name`, `NAME`, `title`, `label`, `id` … that has a value.

## Rejected inputs (HTTP 4xx at upload)

Wrong extension (415), content not matching the extension (`CONTENT_MISMATCH`), not KML (`INVALID_KML`), not a ZIP
or corrupt ZIP (`INVALID_ZIP`), no / several / incomplete Shapefiles, unsafe archive members, archives that expand
too much. The full list with status codes is in [docs/api/upload.md](../api/upload.md) and the threat model in
[security.md](../architecture/security.md).
