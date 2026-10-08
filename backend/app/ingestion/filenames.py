"""Filename handling for untrusted uploads.

The client-supplied filename is *display metadata only*. It is never used to build a filesystem path or an
object-storage key (those are derived from the content hash), so sanitisation here is about safe display
and logging - not about preventing traversal, which is impossible by construction.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath, PureWindowsPath

MAX_FILENAME_LENGTH = 255
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WHITESPACE = re.compile(r"\s+")
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_display_filename(raw: str | None, *, fallback: str = "upload") -> str:
    """Return a printable, path-free filename suitable for display and logs.

    * strips any directory component (both ``/`` and ``\\`` separators, e.g. ``C:\\x\\..\\evil.kml``)
    * Unicode NFC normalisation, control characters removed, whitespace collapsed
    * truncated to 255 characters while preserving the extension
    """
    if not raw:
        return fallback
    name = PureWindowsPath(PurePosixPath(raw).name).name  # handles both separator styles
    name = unicodedata.normalize("NFC", name)
    name = _CONTROL_CHARS.sub("", name)
    name = _WHITESPACE.sub(" ", name).strip().strip(".")
    if not name:
        return fallback
    if len(name) > MAX_FILENAME_LENGTH:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            name = stem[: MAX_FILENAME_LENGTH - len(ext) - 1] + "." + ext
        else:
            name = name[:MAX_FILENAME_LENGTH]
    return name


def file_extension(name: str) -> str:
    """Lower-cased final suffix including the dot (``'.kml'``), or ``''``."""
    suffix = PurePosixPath(name).suffix
    return suffix.lower()


def safe_stem(name: str, *, fallback: str = "dataset", max_length: int = 64) -> str:
    """Conservative ASCII stem usable as a *local* file name inside a private temp directory."""
    stem = PurePosixPath(PureWindowsPath(name).name).stem
    stem = _SAFE_STEM.sub("_", stem).strip("._-")[:max_length]
    return stem or fallback
