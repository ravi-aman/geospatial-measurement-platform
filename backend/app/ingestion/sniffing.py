"""Content-based format detection.

The extension and the client's ``Content-Type`` header are both attacker-controlled, so the format is
decided by the extension allow-list *and* confirmed by the file's leading bytes ("magic numbers").
A ``.kml`` that is really a ZIP (or an executable), or a ``.zip`` that is really XML, is rejected.
"""

from __future__ import annotations

import re

from app.domain.enums import SourceFormat
from app.domain.errors import UnsupportedMediaTypeError, UploadRejectedError

SNIFF_BYTES = 64 * 1024

ALLOWED_EXTENSIONS: dict[str, SourceFormat] = {".kml": SourceFormat.KML, ".zip": SourceFormat.SHAPEFILE}

_ZIP_LOCAL_HEADER = b"PK\x03\x04"
_ZIP_EMPTY_ARCHIVE = b"PK\x05\x06"
_UTF8_BOM = b"\xef\xbb\xbf"

# Skip an optional XML declaration, comments and processing instructions, then require the root element
# to be <kml> (optionally namespace-prefixed). DTDs are rejected separately and more strictly by
# app.ingestion.kml_safety; here we only reject them early to fail fast.
_KML_ROOT = re.compile(
    r"""^\s*
        (?:<\?xml[^>]*\?>\s*)?
        (?:(?:<!--.*?-->|<\?(?!xml)[^>]*\?>)\s*)*
        <(?:[A-Za-z_][\w.\-]*:)?kml(?=[\s>/])""",
    re.VERBOSE | re.DOTALL,
)


def require_supported_extension(extension: str) -> SourceFormat:
    """Format implied by the extension allow-list; checked first, before any bytes are copied or hashed."""
    fmt = ALLOWED_EXTENSIONS.get(extension)
    if fmt is None:
        raise UnsupportedMediaTypeError(
            "Only .kml files and .zip archives containing a Shapefile are accepted.",
            details={"extension": extension or None, "allowed": sorted(ALLOWED_EXTENSIONS)},
        )
    return fmt


def detect_format(extension: str, head: bytes) -> SourceFormat:
    """Return the dataset format or raise a structured upload error.

    :param extension: lower-cased extension of the client filename (``'.kml'``)
    :param head: the first bytes of the uploaded content (up to :data:`SNIFF_BYTES`)
    """
    fmt = require_supported_extension(extension)
    if not head:
        raise UploadRejectedError("The uploaded file is empty.", code="EMPTY_FILE")

    if fmt is SourceFormat.SHAPEFILE:
        if head.startswith(_ZIP_EMPTY_ARCHIVE):
            raise UploadRejectedError("The ZIP archive is empty.", code="SHAPEFILE_MISSING")
        if not head.startswith(_ZIP_LOCAL_HEADER):
            raise UploadRejectedError("File has a .zip extension but is not a ZIP archive.", code="CONTENT_MISMATCH")
        return fmt

    if head.startswith(_ZIP_LOCAL_HEADER):
        raise UploadRejectedError(
            "File has a .kml extension but is a ZIP archive (KMZ is not supported; unzip it first).",
            code="CONTENT_MISMATCH",
        )
    if not looks_like_kml(head):
        raise UploadRejectedError(
            "File is not a KML document: expected an XML document whose root element is <kml>.",
            code="INVALID_KML",
        )
    return fmt


def looks_like_kml(head: bytes) -> bool:
    if head.startswith(_UTF8_BOM):
        head = head[len(_UTF8_BOM) :]
    text = head.decode("utf-8", errors="replace")
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        return False
    return _KML_ROOT.match(text) is not None
