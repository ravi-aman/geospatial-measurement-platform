"""Safe inspection and extraction of zipped Shapefiles.

Threats handled (see docs/architecture/security.md):

* **Path traversal / zip-slip** - entry names are never used as output paths. We extract only the
  shapefile components we need, to fixed flat names (``<stem>.shp`` etc.) inside a private temp
  directory, so ``../../etc/passwd`` or ``C:\\Windows\\...`` cannot escape by construction. Unsafe names
  are still rejected so that a malicious archive is reported as such rather than silently "fixed".
* **Zip bombs** - limits on entry count, total declared uncompressed size, per-entry compression ratio,
  *and* on the bytes actually produced while decompressing (declared sizes in headers can lie).
* **Symlinks, encrypted entries** - rejected.
* **Corruption** - CRC mismatches surface as ``BadZipFile`` during extraction and are reported.
"""

from __future__ import annotations

import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.domain.errors import DatasetError, UploadRejectedError
from app.ingestion.filenames import safe_stem

REQUIRED_COMPONENTS = (".shp", ".shx", ".dbf")
# Components GDAL actually reads. Everything else (spatial indexes, metadata XML, ...) is ignored.
OPTIONAL_COMPONENTS = (".prj", ".cpg")
_IGNORED_BASENAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ZipLimits:
    max_entries: int
    max_uncompressed_bytes: int
    max_compression_ratio: float


@dataclass(slots=True)
class ShapefileArchive:
    """Result of inspecting a ZIP: which entry provides each shapefile component."""

    stem: str  # sanitised stem used for the extracted files and as the layer name
    components: dict[str, zipfile.ZipInfo]
    ignored_entries: list[str] = field(default_factory=list)

    @property
    def has_prj(self) -> bool:
        return ".prj" in self.components


def _reject(message: str, code: str, **details: object) -> UploadRejectedError:
    return UploadRejectedError(message, code=code, details=dict(details))


def _is_unsafe_name(name: str) -> bool:
    if "\x00" in name or name.startswith(("/", "\\")):
        return True
    if len(name) > 1 and name[1] == ":":  # Windows drive letter, e.g. C:evil.shp
        return True
    parts = name.replace("\\", "/").split("/")
    return any(part == ".." for part in parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _is_ignorable(path: PurePosixPath) -> bool:
    return (
        "__MACOSX" in path.parts  # macOS Finder resource forks
        or path.name.startswith("._")
        or path.name.lower() in _IGNORED_BASENAMES
    )


def _component_suffix(path: PurePosixPath) -> str:
    name = path.name.lower()
    if name.endswith(".shp.xml"):
        return ".shp.xml"
    return path.suffix.lower()


def inspect_shapefile_zip(archive_path: Path, limits: ZipLimits) -> ShapefileArchive:
    """Validate the archive's central directory without extracting anything.

    Raises :class:`UploadRejectedError` with a specific ``code`` for every rejection reason.
    """
    try:
        zf = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise _reject("The file is not a valid ZIP archive.", "INVALID_ZIP", reason=str(exc)) from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > limits.max_entries:
            raise _reject(
                "The ZIP archive contains too many entries.",
                "ZIP_TOO_MANY_ENTRIES",
                entries=len(infos),
                max_entries=limits.max_entries,
            )

        total_declared = 0
        shp_entries: list[tuple[PurePosixPath, zipfile.ZipInfo]] = []
        by_name: dict[str, zipfile.ZipInfo] = {}
        ignored: list[str] = []

        for info in infos:
            name = info.filename
            if _is_unsafe_name(name):
                raise _reject("The ZIP archive contains an unsafe path.", "ZIP_UNSAFE_PATH", entry=name)
            if _is_symlink(info):
                raise _reject("The ZIP archive contains a symbolic link.", "ZIP_UNSAFE_ENTRY", entry=name)
            if info.flag_bits & 0x1:
                raise _reject("Encrypted ZIP entries are not supported.", "ZIP_ENCRYPTED", entry=name)
            if info.is_dir():
                continue

            total_declared += info.file_size
            if total_declared > limits.max_uncompressed_bytes:
                raise _reject(
                    "The ZIP archive expands beyond the allowed size.",
                    "ZIP_TOO_LARGE",
                    max_uncompressed_bytes=limits.max_uncompressed_bytes,
                )
            if info.file_size > 0 and info.file_size / max(info.compress_size, 1) > limits.max_compression_ratio:
                raise _reject("Suspicious compression ratio (possible zip bomb).", "ZIP_BOMB_SUSPECTED", entry=name)

            path = PurePosixPath(name.replace("\\", "/"))
            if _is_ignorable(path):
                ignored.append(name)
                continue
            by_name[str(path).lower()] = info
            if _component_suffix(path) == ".shp":
                shp_entries.append((path, info))

        if not shp_entries:
            raise _reject("The ZIP archive does not contain a .shp file.", "SHAPEFILE_MISSING")
        if len(shp_entries) > 1:
            raise _reject(
                "The ZIP archive contains more than one Shapefile; upload one dataset per archive.",
                "MULTIPLE_SHAPEFILES",
                shapefiles=sorted(str(p) for p, _ in shp_entries),
            )

        shp_path, shp_info = shp_entries[0]
        base = str(shp_path.with_suffix("")).lower()
        components: dict[str, zipfile.ZipInfo] = {".shp": shp_info}
        for suffix in REQUIRED_COMPONENTS[1:] + OPTIONAL_COMPONENTS:
            match = by_name.get(base + suffix)
            if match is not None:
                components[suffix] = match

        missing = [s for s in REQUIRED_COMPONENTS if s not in components]
        if missing:
            raise _reject(
                f"The Shapefile is incomplete; missing component(s): {', '.join(missing)}.",
                "SHAPEFILE_INCOMPLETE",
                missing=missing,
                shapefile=str(shp_path),
            )

        used = {id(i) for i in components.values()}
        ignored.extend(i.filename for i in by_name.values() if id(i) not in used)
        return ShapefileArchive(stem=safe_stem(shp_path.name), components=components, ignored_entries=sorted(ignored))


def extract_shapefile(archive_path: Path, archive: ShapefileArchive, dest_dir: Path, limits: ZipLimits) -> Path:
    """Extract the inspected components to ``dest_dir/<stem>.<ext>`` and return the ``.shp`` path.

    Output names are fixed; entry names from the archive are never used as paths. Bytes produced are
    counted while streaming, so a header that under-declares its size cannot exceed the limits.
    """
    budget = limits.max_uncompressed_bytes
    try:
        with zipfile.ZipFile(archive_path) as zf:
            for suffix, info in archive.components.items():
                target = dest_dir / f"{archive.stem}{suffix}"
                written = 0
                with zf.open(info) as src, target.open("xb") as dst:
                    while chunk := src.read(_CHUNK):
                        written += len(chunk)
                        budget -= len(chunk)
                        if written > info.file_size or budget < 0:
                            raise DatasetError("ZIP_BOMB_SUSPECTED", "Archive entry expanded beyond its declared size.")
                        dst.write(chunk)
    except zipfile.BadZipFile as exc:
        raise DatasetError("INVALID_ZIP", f"The ZIP archive is corrupt: {exc}") from exc
    return dest_dir / f"{archive.stem}.shp"
