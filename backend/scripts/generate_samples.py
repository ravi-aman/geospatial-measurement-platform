"""Generate the synthetic sample datasets in ``samples/`` (reproducible; no third-party or private data).

    python scripts/generate_samples.py            # from backend/, writes ../samples

Geometries are synthetic shapes placed around a fictional open-cast mine site near Raipur (Chhattisgarh) and
a fictional parcel block near New Delhi. Names and attributes are invented sample values.
Requires the dev dependencies (GeoPandas).
"""

from __future__ import annotations

import io
import shutil
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import geopandas as gpd
from shapely.affinity import translate
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
SITE_LON, SITE_LAT = 81.62, 21.31  # fictional mine site near Raipur


# ---------------------------------------------------------------------------- KML helpers
def coords(points: list[tuple[float, ...]]) -> str:
    return " ".join(",".join(f"{v:.7f}".rstrip("0").rstrip(".") for v in p) for p in points)


def polygon_xml(shell: list[tuple[float, ...]], holes: list[list[tuple[float, ...]]] | None = None) -> str:
    inner = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{coords(h)}</coordinates></LinearRing></innerBoundaryIs>"
        for h in holes or []
    )
    return (
        "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
        f"{coords(shell)}</coordinates></LinearRing></outerBoundaryIs>{inner}</Polygon>"
    )


def placemark(name: str, geometry: str = "", data: dict[str, object] | None = None, description: str = "") -> str:
    extended = ""
    if data:
        items = "".join(f'<Data name="{escape(k)}"><value>{escape(str(v))}</value></Data>' for k, v in data.items())
        extended = f"<ExtendedData>{items}</ExtendedData>"
    desc = f"<description>{escape(description)}</description>" if description else ""
    return f"<Placemark><name>{escape(name)}</name>{desc}{extended}{geometry}</Placemark>"


def kml(name: str, *parts: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
        f"<Document><name>{escape(name)}</name>\n" + "\n".join(parts) + "\n</Document>\n</kml>\n"
    )


def folder(name: str, *placemarks: str) -> str:
    return f"<Folder><name>{escape(name)}</name>\n" + "\n".join(placemarks) + "\n</Folder>"


def ring(lon: float, lat: float, dx: float, dy: float, z: float | None = None) -> list[tuple[float, ...]]:
    pts = [(lon, lat), (lon + dx, lat), (lon + dx, lat + dy), (lon, lat + dy), (lon, lat)]
    return [(*p, z) for p in pts] if z is not None else pts


def mine_site_kml() -> str:
    lon, lat = SITE_LON, SITE_LAT
    lease = ring(lon, lat, 0.04, 0.03)
    pit_a = [(lon + 0.006, lat + 0.006), (lon + 0.016, lat + 0.005), (lon + 0.019, lat + 0.013),
             (lon + 0.012, lat + 0.018), (lon + 0.005, lat + 0.014), (lon + 0.006, lat + 0.006)]
    pit_b = ring(lon + 0.024, lat + 0.008, 0.009, 0.007)
    pit_b_sump = ring(lon + 0.027, lat + 0.010, 0.002, 0.002)  # hole: water sump inside pit B
    # Digitised with crossing edges (a "bow-tie"): invalid as drawn, repaired before measuring.
    waste_dump = [(lon + 0.030, lat + 0.020), (lon + 0.037, lat + 0.027), (lon + 0.037, lat + 0.020),
                  (lon + 0.030, lat + 0.027), (lon + 0.030, lat + 0.020)]
    stockpiles = "<MultiGeometry>" + "".join(
        polygon_xml(ring(lon + 0.002 + i * 0.003, lat + 0.022, 0.0018, 0.0015)) for i in range(3)
    ) + "</MultiGeometry>"
    haul_north = "<LineString><coordinates>" + coords([
        (lon + 0.012, lat + 0.018), (lon + 0.015, lat + 0.022), (lon + 0.022, lat + 0.024), (lon + 0.031, lat + 0.023)
    ]) + "</coordinates></LineString>"
    haul_east = "<LineString><coordinates>" + coords([
        (lon + 0.019, lat + 0.011), (lon + 0.024, lat + 0.011), (lon + 0.039, lat + 0.004)
    ]) + "</coordinates></LineString>"
    conveyor = "<MultiGeometry>" + "".join(
        "<LineString><coordinates>" + coords(seg) + "</coordinates></LineString>"
        for seg in ([(lon + 0.008, lat + 0.023), (lon + 0.008, lat + 0.028)],
                    [(lon + 0.008, lat + 0.028), (lon + 0.018, lat + 0.029)])
    ) + "</MultiGeometry>"
    weighbridge = "<MultiGeometry><Point><coordinates>" + coords([(lon + 0.038, lat + 0.003)]) + \
        "</coordinates></Point><LineString><coordinates>" + \
        coords([(lon + 0.037, lat + 0.003), (lon + 0.039, lat + 0.003)]) + "</coordinates></LineString></MultiGeometry>"

    return kml(
        "Synthetic mine site survey",
        folder(
            "Lease and pits",
            placemark("Mining lease boundary", polygon_xml(lease), {"lease_id": "ML-0001", "declared_area_ha": 1255.6}),
            placemark("Pit A", polygon_xml(pit_a), {"bench_count": 6, "status": "active"}),
            placemark("Pit B (with sump)", polygon_xml(pit_b, [pit_b_sump]), {"status": "active"}),
            placemark("Waste dump (self-intersecting as digitised)", polygon_xml(waste_dump), {"status": "active"}),
        ),
        folder(
            "Stockpiles",
            placemark("Coal stockpiles 1-3", stockpiles, {"material": "coal"}),
        ),
        folder(
            "Haul roads",
            placemark("Haul road north", haul_north, {"surface": "gravel", "width_m": 18}),
            placemark("Haul road east", haul_east, {"surface": "gravel", "width_m": 15}),
            placemark("Conveyor (two segments)", conveyor),
        ),
        folder(
            "Survey control",
            *(
                placemark(f"Control point {i + 1}", f"<Point><coordinates>{coords([(lon + dx, lat + dy, z)])}</coordinates></Point>",
                          {"elevation_m": z})
                for i, (dx, dy, z) in enumerate([(0.001, 0.001, 288.4), (0.039, 0.001, 291.2), (0.020, 0.029, 287.9)])
            ),
            placemark("Weighbridge (point and line)", weighbridge),
            placemark("Planned office (location not yet surveyed)"),
        ),
    )


def delhi_parcels_kml() -> str:
    lon, lat = 77.2090, 28.6139
    parts = []
    for row in range(3):
        for col in range(4):
            shell = ring(lon + col * 0.0012, lat + row * 0.0010, 0.001, 0.0008)
            parts.append(placemark(f"Parcel {row * 4 + col + 1:02d}", polygon_xml(shell),
                                   {"block": "B", "plot_no": row * 4 + col + 1}))
    return kml("Synthetic parcel block", *parts)


# ---------------------------------------------------------------------------- Shapefile helpers
def write_zip(gdf: gpd.GeoDataFrame, target: Path, stem: str, *, drop: set[str] | None = None,
              folder: str = "") -> None:
    with tempfile.TemporaryDirectory() as tmp:
        shp = Path(tmp) / f"{stem}.shp"
        gdf.to_file(shp, driver="ESRI Shapefile", engine="pyogrio")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(Path(tmp).iterdir()):
                if path.suffix.lower() not in (drop or set()):
                    zf.write(path, f"{folder}{path.name}")
        target.write_bytes(buffer.getvalue())


def plots(lon: float, lat: float, n: int = 6) -> list[Polygon]:
    return [box(lon + i * 0.0015, lat, lon + i * 0.0015 + 0.0012, lat + 0.001) for i in range(n)]


def main() -> None:
    if SAMPLES.exists():
        for child in SAMPLES.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
    (SAMPLES / "kml").mkdir(parents=True, exist_ok=True)
    (SAMPLES / "shapefile").mkdir(parents=True, exist_ok=True)

    (SAMPLES / "kml" / "mine_site_survey.kml").write_text(mine_site_kml(), encoding="utf-8")
    (SAMPLES / "kml" / "parcel_block_wgs84.kml").write_text(delhi_parcels_kml(), encoding="utf-8")

    attrs = {"plot_no": list(range(1, 7)), "owner": [f"Owner {c}" for c in "ABCDEF"], "landuse": ["agri"] * 6}
    wgs = gpd.GeoDataFrame(attrs, geometry=plots(77.2090, 28.6139), crs=4326)

    write_zip(wgs.to_crs(32643), SAMPLES / "shapefile" / "plots_utm43n.zip", "plots_utm43n")
    write_zip(wgs.to_crs(3857), SAMPLES / "shapefile" / "plots_web_mercator.zip", "plots_web_mercator")
    write_zip(wgs.to_crs(32643), SAMPLES / "shapefile" / "plots_no_prj.zip", "plots_no_prj", drop={".prj"})

    lon, lat = SITE_LON, SITE_LAT
    roads = gpd.GeoDataFrame(
        {"name": ["Access road", "Ramp 1", "Ramp 2"], "surface": ["asphalt", "gravel", "gravel"]},
        geometry=[
            LineString([(lon - 0.02, lat - 0.01), (lon, lat), (lon + 0.012, lat + 0.018)]),
            LineString([(lon + 0.008, lat + 0.007), (lon + 0.012, lat + 0.012)]),
            LineString([(lon + 0.026, lat + 0.009), (lon + 0.030, lat + 0.013)]),
        ],
        crs=4326,
    )
    write_zip(roads, SAMPLES / "shapefile" / "roads_wgs84.zip", "roads", folder="export/")

    wells = gpd.GeoDataFrame(
        {"well_id": ["BW-1", "BW-2", "BW-3", "BW-4"], "depth_m": [62.0, 58.5, 71.2, 66.0]},
        geometry=[Point(lon + dx, lat + dy) for dx, dy in [(0.002, 0.004), (0.035, 0.006), (0.01, 0.027), (0.03, 0.028)]],
        crs=4326,
    )
    write_zip(wells, SAMPLES / "shapefile" / "borewells_points.zip", "borewells")

    bowtie = Polygon([(lon + 0.05, lat), (lon + 0.06, lat + 0.01), (lon + 0.06, lat), (lon + 0.05, lat + 0.01),
                      (lon + 0.05, lat)])
    multi = MultiPolygon([box(lon + 0.07, lat, lon + 0.075, lat + 0.004),
                          translate(box(lon + 0.07, lat, lon + 0.075, lat + 0.004), 0.008)])
    parcels = gpd.GeoDataFrame(
        {"parcel": ["Multipart parcel", "Self-intersecting parcel", "Empty record"], "survey_no": [101, 102, 103]},
        geometry=[multi, bowtie, None],
        crs=4326,
    )
    write_zip(parcels, SAMPLES / "shapefile" / "parcels_edge_cases.zip", "parcels_edge_cases")

    for path in sorted(SAMPLES.rglob("*.*")):
        if path.suffix in (".kml", ".zip"):
            print(f"{path.relative_to(SAMPLES)}  ({path.stat().st_size} bytes)")  # noqa: T201


if __name__ == "__main__":
    main()
