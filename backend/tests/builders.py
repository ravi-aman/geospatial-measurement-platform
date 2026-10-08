"""Programmatic test-data builders (KML documents, zipped Shapefiles, hostile archives).

Generating inputs in code keeps every fixture's intent visible next to the assertion that uses it, and lets
tests create variants (missing .prj, wrong CRS, corrupt bytes) without a pile of opaque binary files.
"""

from __future__ import annotations

import io
import stat
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

KML_NS = "http://www.opengis.net/kml/2.2"


# ---------------------------------------------------------------------------- KML
def _coords(points: Iterable[tuple[float, ...]]) -> str:
    return " ".join(",".join(f"{v:.10g}" for v in p) for p in points)


def kml_point(lon: float, lat: float) -> str:
    return f"<Point><coordinates>{_coords([(lon, lat)])}</coordinates></Point>"


def kml_line(points: list[tuple[float, float]]) -> str:
    return f"<LineString><coordinates>{_coords(points)}</coordinates></LineString>"


def kml_polygon(shell: list[tuple[float, float]], holes: list[list[tuple[float, float]]] | None = None) -> str:
    inner = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{_coords(h)}</coordinates></LinearRing></innerBoundaryIs>"
        for h in holes or []
    )
    return (
        f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{_coords(shell)}</coordinates></LinearRing>"
        f"</outerBoundaryIs>{inner}</Polygon>"
    )


def kml_placemark(name: str, geometry_xml: str = "", data: Mapping[str, Any] | None = None) -> str:
    extended = ""
    if data:
        items = "".join(f'<Data name="{k}"><value>{v}</value></Data>' for k, v in data.items())
        extended = f"<ExtendedData>{items}</ExtendedData>"
    return f"<Placemark><name>{name}</name>{extended}{geometry_xml}</Placemark>"


def kml_document(*placemarks: str, folders: Mapping[str, list[str]] | None = None, prolog: str = "") -> bytes:
    folder_xml = "".join(
        f"<Folder><name>{name}</name>{''.join(items)}</Folder>" for name, items in (folders or {}).items()
    )
    body = f'<kml xmlns="{KML_NS}"><Document><name>test</name>{"".join(placemarks)}{folder_xml}</Document></kml>'
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n{prolog}{body}').encode()


def square(lon: float, lat: float, size_deg: float) -> list[tuple[float, float]]:
    return [(lon, lat), (lon + size_deg, lat), (lon + size_deg, lat + size_deg), (lon, lat + size_deg), (lon, lat)]


BOWTIE = [(77.0, 28.0), (77.01, 28.01), (77.01, 28.0), (77.0, 28.01), (77.0, 28.0)]


# ---------------------------------------------------------------------------- Shapefiles
def shapefile_zip(
    geometries: list[BaseGeometry | None],
    *,
    crs: str | None = "EPSG:4326",
    attributes: Mapping[str, list[Any]] | None = None,
    stem: str = "parcels",
    folder: str = "",
    drop: Iterable[str] = (),
    extra_entries: Mapping[str, bytes] | None = None,
    workdir: Path,
) -> bytes:
    """Write a real Shapefile with GDAL and return it zipped.

    ``drop`` removes components (e.g. ``{".prj"}`` for a missing-CRS dataset); ``folder`` nests the files in a
    sub-directory inside the archive; ``extra_entries`` adds arbitrary members.
    """
    data = dict(attributes or {"name": [f"f{i}" for i in range(len(geometries))]})
    gdf = gpd.GeoDataFrame(data, geometry=list(geometries), crs=crs)
    out_dir = workdir / f"shp-{stem}-{abs(hash((stem, folder, crs, len(geometries)))):x}"
    out_dir.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_dir / f"{stem}.shp", driver="ESRI Shapefile", engine="pyogrio")
    dropped = {d.lower() for d in drop}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out_dir.iterdir()):
            if path.suffix.lower() in dropped:
                continue
            zf.write(path, f"{folder}{path.name}")
        for name, content in (extra_entries or {}).items():
            zf.writestr(name, content)
    return buffer.getvalue()


def zip_bytes(entries: Mapping[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def zip_with_symlink(target: str = "/etc/passwd") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        info = zipfile.ZipInfo("parcels.shp")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, target)
        zf.writestr("parcels.shx", b"x")
        zf.writestr("parcels.dbf", b"x")
    return buffer.getvalue()


def zip_with_encrypted_flag() -> bytes:
    """ZIP whose .shp entry claims to be encrypted (general-purpose flag bit 0)."""
    data = bytearray(zip_bytes({"a.shp": b"x", "a.shx": b"x", "a.dbf": b"x"}, compression=zipfile.ZIP_STORED))
    # Set bit 0 of the general-purpose flag in every local header (offset 6) and central header (offset 8).
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = 0
        while (pos := data.find(signature, start)) != -1:
            data[pos + offset] |= 0x01
            start = pos + 4
    return bytes(data)
