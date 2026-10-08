"""Domain vocabulary shared by every layer (API, services, processing, persistence)."""

from __future__ import annotations

from enum import StrEnum


class SourceFormat(StrEnum):
    KML = "KML"
    SHAPEFILE = "SHAPEFILE"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_JOB_STATUSES

    @property
    def has_results(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS)


TERMINAL_JOB_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS, JobStatus.FAILED})


class MeasurementStatus(StrEnum):
    MEASURED = "MEASURED"  # area (polygonal) or length (linear) computed
    NOT_APPLICABLE = "NOT_APPLICABLE"  # points: no measurement is defined
    UNSUPPORTED = "UNSUPPORTED"  # geometry type we deliberately do not measure (e.g. mixed collections)
    FAILED = "FAILED"  # measurement should exist but could not be computed (bad geometry, no CRS, ...)


class GeometryFamily(StrEnum):
    AREAL = "AREAL"
    LINEAR = "LINEAR"
    PUNTAL = "PUNTAL"
    MIXED = "MIXED"


class CrsSource(StrEnum):
    FILE = "FILE"  # declared by the dataset (.prj, or KML which is WGS 84 by specification)
    USER_OVERRIDE = "USER_OVERRIDE"  # supplied by the client on upload


class ProjectionMethod(StrEnum):
    LOCAL_EQUAL_AREA = "LOCAL_EQUAL_AREA"  # Lambert Azimuthal Equal-Area centred near the feature
    UTM = "UTM"  # Universal Transverse Mercator zone of the feature


class FeatureCode(StrEnum):
    """Machine-readable codes attached to individual features (errors and informational issues)."""

    # errors -> measurement_status FAILED
    GEOMETRY_MISSING = "GEOMETRY_MISSING"
    GEOMETRY_MALFORMED = "GEOMETRY_MALFORMED"
    GEOMETRY_EMPTY = "GEOMETRY_EMPTY"
    GEOMETRY_INVALID_UNREPAIRABLE = "GEOMETRY_INVALID_UNREPAIRABLE"
    GEOMETRY_TOO_COMPLEX = "GEOMETRY_TOO_COMPLEX"
    CRS_MISSING = "CRS_MISSING"
    COORDINATES_OUT_OF_RANGE = "COORDINATES_OUT_OF_RANGE"
    TRANSFORM_FAILED = "TRANSFORM_FAILED"
    MEASUREMENT_FAILED = "MEASUREMENT_FAILED"
    # reasons -> measurement_status UNSUPPORTED
    MIXED_GEOMETRY_COLLECTION = "MIXED_GEOMETRY_COLLECTION"
    UNSUPPORTED_GEOMETRY_TYPE = "UNSUPPORTED_GEOMETRY_TYPE"
    # informational issues (measurement may still be MEASURED)
    GEOMETRY_REPAIRED = "GEOMETRY_REPAIRED"
    COLLECTION_NORMALIZED = "COLLECTION_NORMALIZED"
    HIGH_PROJECTION_DISTORTION = "HIGH_PROJECTION_DISTORTION"
    ANTIMERIDIAN_CROSSING_SUSPECTED = "ANTIMERIDIAN_CROSSING_SUSPECTED"
    PROPERTIES_SANITIZED = "PROPERTIES_SANITIZED"


class DatasetWarningCode(StrEnum):
    """File-level warnings surfaced on the file resource."""

    CRS_MISSING = "CRS_MISSING"
    CRS_INVALID = "CRS_INVALID"
    CRS_UNSUPPORTED = "CRS_UNSUPPORTED"
    CRS_OVERRIDDEN = "CRS_OVERRIDDEN"
    NO_FEATURES = "NO_FEATURES"
    Z_COORDINATES_IGNORED = "Z_COORDINATES_IGNORED"
    KML_NETWORK_LINKS_IGNORED = "KML_NETWORK_LINKS_IGNORED"
    ZIP_ENTRIES_IGNORED = "ZIP_ENTRIES_IGNORED"
    UNSUPPORTED_FEATURES = "UNSUPPORTED_FEATURES"
