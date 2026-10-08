# ADR-006: Repair invalid polygons with MakeValid(structure); keep original and repaired

**Status:** Accepted · Details: [docs/geospatial/geometry-handling.md](../geospatial/geometry-handling.md)

## Context
Hand-digitised survey data regularly contains invalid polygons (self-intersections, bow-ties, overlapping
multi-parts). Area is undefined or wrong for invalid geometries: the shoelace sum of a symmetric bow-tie's two
halves cancels, so `Polygon(bowtie).area` returns exactly `0.0`.

## Decision
* Validate every measurable geometry (`shapely.is_valid`), record the GEOS reason.
* Repair polygons with `shapely.make_valid(method="structure", keep_collapsed=False)`; keep only the areal result.
  Lines use the default method; degenerate lines are reported as unrepairable.
* Store the original (`geom`) **and** the repaired geometry (`geom_repaired`, only when repaired); measure the
  repaired one; flag `GEOMETRY_REPAIRED` with the reason. Unrepairable → `FAILED`.

## Alternatives considered
* **Reject invalid features** — safe but discards common, mostly-minor issues and frustrates users.
* **`buffer(0)`** — the historical trick; can silently delete parts of bow-ties and is not a documented repair.
* **`make_valid(method="linework")`** — keeps every edge; produces collections with lines for polygon input that
  then need extra filtering, and treats holes by even-odd rule rather than by structure.
* **Silent repair** — rejected: the user must be able to see that the measured shape differs from the drawn one.

## Trade-offs
Repair is an interpretation of an ambiguous drawing. It is made visible (flag, reason, both geometries) rather than
hidden. A "strict" policy (reject instead of repair) is a small future setting.
