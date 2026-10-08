"""Selection of the projected CRS in which each feature is measured.

Measuring in lon/lat degrees is meaningless (a degree of longitude is 111 km at the equator and 0 km at the
poles), so every feature is projected to a *local* metric CRS first. Two interchangeable strategies:

``LocalEqualAreaStrategy`` (default)
    Lambert Azimuthal Equal-Area (LAEA) centred near the feature. LAEA is *equal-area*: areas are exact
    (to floating-point precision) regardless of where the centre is. Linear scale error grows only with
    distance ``d`` from the centre, approximately ``(d / R)^2 / 8`` - about 0.0008 % at 50 km and 0.012 %
    at 200 km - which is smaller than UTM's in-zone error. Works everywhere, including the poles.
    Centres are snapped to a 1-degree grid so features in the same area share one CRS/transformer
    (fast, deterministic); features wider than 2 degrees get their own centre.

``UtmStrategy``
    The UTM zone containing the feature's bbox centre (what ``GeoDataFrame.estimate_utm_crs()`` does,
    but per feature and vectorised). Familiar EPSG codes, but UTM is conformal, not equal-area: in-zone
    scale factor 0.9996-1.0010 means areas are off by -0.08 % ... +0.2 %, more outside the zone, and UTM is
    undefined beyond 84N / 80S (those features fall back to LAEA).

Both are validated per feature against an ellipsoidal geodesic reference - see ``measurement.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from pyproj import CRS

from app.domain.enums import ProjectionMethod

ChoiceKey = tuple[str, float, float] | tuple[str, int]


@dataclass(frozen=True, slots=True)
class ProjectionChoice:
    key: ChoiceKey
    label: str  # "EPSG:32643" or a reproducible PROJ string
    method: ProjectionMethod

    def crs(self) -> CRS:
        if self.method is ProjectionMethod.UTM:
            return CRS.from_user_input(self.label)
        return CRS.from_proj4(self.label)


class ProjectionStrategy(Protocol):
    name: str

    def assign(self, bounds: NDArray[np.float64]) -> list[ProjectionChoice]:
        """Return one choice per row of ``bounds`` (``N x 4``: minx, miny, maxx, maxy in WGS 84)."""
        ...


def _laea_choice(lat0: float, lon0: float) -> ProjectionChoice:
    lat0 = round(max(-90.0, min(90.0, lat0)), 6)
    lon0 = round(((lon0 + 180.0) % 360.0) - 180.0, 6)
    label = f"+proj=laea +lat_0={lat0:g} +lon_0={lon0:g} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    return ProjectionChoice(("laea", lat0, lon0), label, ProjectionMethod.LOCAL_EQUAL_AREA)


class LocalEqualAreaStrategy:
    name = "local_equal_area"

    def __init__(self, cell_size_deg: float = 1.0, large_extent_deg: float = 2.0) -> None:
        self.cell = cell_size_deg
        self.large_extent = large_extent_deg

    def centre(self, minx: float, miny: float, maxx: float, maxy: float) -> tuple[float, float]:
        cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
        if max(maxx - minx, maxy - miny) > self.large_extent:
            return round(cy, 2), round(cx, 2)
        snap_y = math.floor(cy / self.cell) * self.cell + self.cell / 2.0
        snap_x = math.floor(cx / self.cell) * self.cell + self.cell / 2.0
        return snap_y, snap_x

    def assign(self, bounds: NDArray[np.float64]) -> list[ProjectionChoice]:
        return [_laea_choice(*self.centre(*row)) for row in bounds.tolist()]


def utm_epsg(lon: float, lat: float) -> int | None:
    """EPSG code of the WGS 84 UTM zone containing (lon, lat); ``None`` outside UTM's 80S-84N domain."""
    if lat > 84.0 or lat < -80.0:
        return None
    zone = math.floor((lon + 180.0) / 6.0) % 60 + 1
    return (32600 if lat >= 0 else 32700) + zone


class UtmStrategy:
    name = "utm"

    def __init__(self) -> None:
        self._polar_fallback = LocalEqualAreaStrategy()

    def assign(self, bounds: NDArray[np.float64]) -> list[ProjectionChoice]:
        choices: list[ProjectionChoice] = []
        for minx, miny, maxx, maxy in bounds.tolist():
            epsg = utm_epsg((minx + maxx) / 2.0, (miny + maxy) / 2.0)
            if epsg is None:
                choices.append(_laea_choice(*self._polar_fallback.centre(minx, miny, maxx, maxy)))
            else:
                choices.append(ProjectionChoice(("utm", epsg), f"EPSG:{epsg}", ProjectionMethod.UTM))
        return choices


def strategy_for(name: str) -> ProjectionStrategy:
    if name == "utm":
        return UtmStrategy()
    if name == "local_equal_area":
        return LocalEqualAreaStrategy()
    raise ValueError(f"unknown measurement strategy: {name}")
