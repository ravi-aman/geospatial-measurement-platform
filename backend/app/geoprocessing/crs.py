"""Coordinate Reference System resolution.

Precedence: explicit client override  >  CRS declared by the dataset  >  unknown.

We never *guess* a CRS from coordinate ranges. Values like (512000, 3100000) are obviously projected, but
small local-grid coordinates such as (45.2, 12.8) are indistinguishable from lon/lat - a silent guess turns
a bad input into a wrong answer. Unknown CRS is reported explicitly and measurements are withheld.

KML needs no special case: the OGC KML 2.2 spec fixes the CRS to WGS 84 lon/lat, and GDAL reports
EPSG:4326 for every KML layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

from app.domain.enums import CrsSource, DatasetWarningCode
from app.domain.errors import UploadRejectedError
from app.geoprocessing.models import Issue

WGS84 = CRS.from_epsg(4326)
_OVERRIDE = re.compile(r"^(EPSG|ESRI):(\d{4,6})$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ResolvedCrs:
    crs: CRS
    identifier: str  # "EPSG:32643"; "CUSTOM" when the definition matches no authority code
    name: str
    source: CrsSource

    @property
    def is_geographic(self) -> bool:
        return bool(self.crs.is_geographic)

    @property
    def is_wgs84(self) -> bool:
        return self.identifier == "EPSG:4326"

    def wkt(self) -> str:
        return str(self.crs.to_wkt())

    def transformer_to_wgs84(self) -> Transformer | None:
        """``None`` when coordinates are already WGS 84 lon/lat (identity)."""
        if self.is_wgs84:
            return None
        # always_xy: EPSG:4326 is officially (lat, lon); GIS data is (lon, lat). Without this flag every
        # coordinate would be silently swapped.
        return Transformer.from_crs(self.crs, WGS84, always_xy=True)


def horizontal_component(crs: CRS) -> CRS | None:
    """Return the 2D horizontal CRS usable for planar geometry, or ``None`` if there is none."""
    if crs.is_compound:
        for sub in crs.sub_crs_list:
            if sub.is_geographic or sub.is_projected:
                return sub
        return None
    if crs.is_geographic or crs.is_projected:
        return crs
    return None  # geocentric, engineering (local grid), vertical-only ...


def describe(crs: CRS) -> tuple[str, str]:
    authority = crs.to_authority(min_confidence=70)
    identifier = f"{authority[0]}:{authority[1]}" if authority else "CUSTOM"
    return identifier, str(crs.name)


def parse_crs_override(value: str | None) -> CRS | None:
    """Validate the optional ``crs`` upload parameter (``EPSG:32643`` style codes only).

    Free-form PROJ strings / WKT are deliberately not accepted from clients: authority codes are
    unambiguous, easy to validate, and cannot reference external grid files.
    """
    if value is None or not value.strip():
        return None
    match = _OVERRIDE.fullmatch(value.strip())
    if match is None:
        raise UploadRejectedError(
            "crs must be an authority code such as 'EPSG:32643'.", code="INVALID_CRS", details={"crs": value[:100]}
        )
    try:
        crs = CRS.from_user_input(f"{match[1].upper()}:{match[2]}")
    except CRSError as exc:
        raise UploadRejectedError(f"Unknown CRS '{value}'.", code="INVALID_CRS") from exc
    horizontal = horizontal_component(crs)
    if horizontal is None:
        raise UploadRejectedError(
            "crs must be a geographic or projected CRS.", code="INVALID_CRS", details={"crs": value}
        )
    return horizontal


def resolve_crs(declared: str | None, override: CRS | None) -> tuple[ResolvedCrs | None, list[Issue]]:
    """Resolve the CRS of a layer from the dataset declaration and the optional client override."""
    warnings: list[Issue] = []
    declared_crs: CRS | None = None
    if declared:
        try:
            declared_crs = horizontal_component(CRS.from_user_input(declared))
            if declared_crs is None:
                warnings.append(
                    Issue(
                        DatasetWarningCode.CRS_UNSUPPORTED, "The declared CRS has no geographic or projected component."
                    )
                )
        except CRSError:
            warnings.append(
                Issue(DatasetWarningCode.CRS_INVALID, "The dataset declares a CRS that could not be parsed.")
            )

    if override is not None:
        if declared_crs is not None and not declared_crs.equals(override, ignore_axis_order=True):
            warnings.append(
                Issue(
                    DatasetWarningCode.CRS_OVERRIDDEN,
                    f"Declared CRS '{describe(declared_crs)[0]}' was overridden by the client.",
                )
            )
        identifier, name = describe(override)
        return ResolvedCrs(override, identifier, name, CrsSource.USER_OVERRIDE), warnings

    if declared_crs is None:
        if not any(w.code in (DatasetWarningCode.CRS_INVALID, DatasetWarningCode.CRS_UNSUPPORTED) for w in warnings):
            warnings.append(
                Issue(
                    DatasetWarningCode.CRS_MISSING,
                    "The dataset declares no CRS (e.g. a Shapefile without .prj). Features were extracted but "
                    "area/length cannot be measured safely. Re-upload with the 'crs' parameter to measure.",
                )
            )
        return None, warnings

    identifier, name = describe(declared_crs)
    return ResolvedCrs(declared_crs, identifier, name, CrsSource.FILE), warnings
