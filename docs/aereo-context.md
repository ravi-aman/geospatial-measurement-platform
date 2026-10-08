# AEREO context

Why this assignment fits AEREO's domain, based only on **public** information (press releases, investor and partner
pages, a FOSS4G conference abstract and a public API description). Nothing here claims knowledge of AEREO's internal
systems, and this project does **not** reproduce or imitate their architecture.

## Public company context (with sources)

* **Who:** AEREO (formerly Aarav Unmanned Systems, rebranded in September 2022), founded in 2013 at IIT Kanpur,
  headquartered in Bengaluru. Builds drones (e.g. the DGCA type-certified AEREO ZFR fixed-wing VTOL) and an
  AI-powered data analytics platform for managing large capital assets.
  [Series B release, Jul 2024](https://www.aap.com.au/aapreleases/cision20240718ae64748) ·
  [CXOToday, Sep 2022](https://cxotoday.com/media-coverage/aarav-rebrands-as-aereo-set-to-embark-on-made-in-india-for-the-world-journey/) ·
  [Indian Defence News, Jun 2023](https://www.indiandefensenews.in/2023/06/aereo-zfr-drone-receives-type.html)
* **Platform:** *Aereo Cloud*, described as a geospatial AI platform turning aerial data into business intelligence
  (3D digital twins, construction progress monitoring, dashboards).
  [Seed Group, Jul 2025](https://seedgroup.com/2025/07/the-uae-embraces-drone-intelligence-in-smart-infrastructure-with-seed-group-partner-aereo/) ·
  [JSW Ventures](https://www.jswvc.com/aereo)
* **Industries:** metals and mining, large infrastructure, urban and rural mapping, land records. Government work
  includes SVAMITVA village mapping and DILRMP land records; Coal India selected AEREO to produce orthomosaics,
  DEMs and analytics for mines, driven by a mandate for drone-based volumetric assessment.
  [Digital Terminal, Mar 2023](https://digitalterminal.in/startup/coal-india-selected-aereo-to-digitize-7-major-coal-mines)
* **Engineering signals (public):** a FOSS4G-Asia 2023 talk by AEREO's platform lead, "Utilising Cloud-Optimised
  GeoTIFF and PostGIS for mapping India's rural areas", describes a WebGIS built with PostGIS, COG, GeoDjango and GDAL
  ([talk page](https://talks.osgeo.org/foss4g-asia-2023/talk/MRUPV9/)); a public tile-server API description
  ([tiles.aereo.io/openapi.json](https://tiles.aereo.io/openapi.json)) lists vector tiles, COG raster tiles, MBTiles,
  terrain and 3D tiles.

## How this assignment relates to that domain

Drone survey and GIS workflows constantly exchange **vector boundaries** — lease boundaries, pits, stockpile
footprints, haul roads, village parcels — as KML and Shapefile, and many decisions hinge on their **areas and
lengths**: land-record parcel areas, mining lease compliance, progress against planned boundaries. Getting those
numbers wrong by a projection choice (Web Mercator overstates area by ~29 % in central India) or by silently dropping
KML folders is a real-world failure mode, which is why this project spends most of its depth on CRS correctness,
validation and failure isolation.

The synthetic sample data reflects that context (a fictional open-cast mine site near Raipur with a lease boundary,
pits, a self-intersecting waste dump, stockpiles, haul roads and survey control points; a parcel block), but it is
invented data, not AEREO's.

Ways this service could fit into a larger geospatial pipeline (ideas, not claims):
* a validation-and-measurement step when customers upload reference boundaries to compare against drone outputs;
* a building block for "area inside the lease" / progress checks by intersecting uploaded boundaries with processed
  layers in PostGIS;
* its vector-tile endpoint follows the same delivery pattern (MVT from PostGIS) that is standard in WebGIS stacks.

## Engineering skills this project demonstrates

* Correct geodesy in practice: CRS resolution, axis order, projection choice with measured error, ellipsoidal
  cross-checks, honest handling of unknown CRSs.
* Defensive ingestion of untrusted GIS files (archives, XML, GDAL).
* A production-shaped backend: transactional queue with leases and fencing, idempotency, deduplication, structured
  errors and logs, migrations, tests against real PostGIS.
* WebGIS delivery: PostGIS vector tiles, MapLibre, virtualised tables over keyset pagination.
* Documentation of decisions and trade-offs.

## What must NOT be claimed

* That this project uses, mirrors or was informed by AEREO's internal architecture or code.
* Product names or features not found in public sources (e.g. specific KML/Shapefile support in Aereo Cloud,
  partnerships, headcount or revenue).
* That the sample data is real survey data or relates to any real AEREO site or customer.
* That the stack choices (FastAPI vs GeoDjango, MapLibre, Supabase) were made to match AEREO — they are justified
  on their own merits in the [ADRs](decisions/README.md).
