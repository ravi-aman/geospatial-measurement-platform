"""Geospatial File Measurement API."""

__version__ = "1.0.0"

# Bumped whenever the processing algorithm changes in a way that changes results. It is part of the
# job fingerprint, so re-uploading a file after an algorithm change re-processes it instead of reusing
# stale cached results.
PROCESSOR_VERSION = 1
