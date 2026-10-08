"""ZIP safety: traversal, bombs, symlinks, encryption, structure and extraction guarantees."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from shapely.geometry import Point

from app.domain.errors import DatasetError, UploadRejectedError
from app.ingestion.archive import ZipLimits, extract_shapefile, inspect_shapefile_zip
from tests.builders import shapefile_zip, zip_bytes, zip_with_encrypted_flag, zip_with_symlink

LIMITS = ZipLimits(max_entries=50, max_uncompressed_bytes=10 * 1024 * 1024, max_compression_ratio=1000)
SHAPEFILE_PARTS = {"p.shp": b"shp", "p.shx": b"shx", "p.dbf": b"dbf"}


def write(tmp_path: Path, data: bytes, name: str = "upload.zip") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def rejected_code(path: Path, limits: ZipLimits = LIMITS) -> str:
    with pytest.raises(UploadRejectedError) as exc:
        inspect_shapefile_zip(path, limits)
    return exc.value.code


class TestInspection:
    def test_accepts_complete_shapefile(self, tmp_path: Path) -> None:
        archive = inspect_shapefile_zip(write(tmp_path, zip_bytes({**SHAPEFILE_PARTS, "p.prj": b"x"})), LIMITS)
        assert set(archive.components) == {".shp", ".shx", ".dbf", ".prj"}
        assert archive.has_prj
        assert archive.stem == "p"

    def test_components_matched_case_insensitively_in_subfolder(self, tmp_path: Path) -> None:
        data = zip_bytes({"Data/Roads.SHP": b"1", "data/roads.shx": b"2", "DATA/ROADS.dbf": b"3"})
        archive = inspect_shapefile_zip(write(tmp_path, data), LIMITS)
        assert set(archive.components) == {".shp", ".shx", ".dbf"}
        assert not archive.has_prj

    def test_macos_resource_forks_are_ignored(self, tmp_path: Path) -> None:
        data = zip_bytes({**SHAPEFILE_PARTS, "__MACOSX/._p.shp": b"junk", "._p.shp": b"junk", ".DS_Store": b""})
        archive = inspect_shapefile_zip(write(tmp_path, data), LIMITS)
        assert archive.components[".shp"].filename == "p.shp"
        assert "__MACOSX/._p.shp" in archive.ignored_entries

    def test_unrelated_entries_are_reported_as_ignored(self, tmp_path: Path) -> None:
        archive = inspect_shapefile_zip(write(tmp_path, zip_bytes({**SHAPEFILE_PARTS, "readme.txt": b"hi"})), LIMITS)
        assert archive.ignored_entries == ["readme.txt"]

    def test_not_a_zip(self, tmp_path: Path) -> None:
        assert rejected_code(write(tmp_path, b"PK\x03\x04 truncated garbage")) == "INVALID_ZIP"

    def test_no_shapefile(self, tmp_path: Path) -> None:
        assert rejected_code(write(tmp_path, zip_bytes({"doc.kml": b"<kml/>"}))) == "SHAPEFILE_MISSING"

    @pytest.mark.parametrize("missing", ["p.shx", "p.dbf"])
    def test_missing_required_component(self, tmp_path: Path, missing: str) -> None:
        parts = {k: v for k, v in SHAPEFILE_PARTS.items() if k != missing}
        with pytest.raises(UploadRejectedError) as exc:
            inspect_shapefile_zip(write(tmp_path, zip_bytes(parts)), LIMITS)
        assert exc.value.code == "SHAPEFILE_INCOMPLETE"
        assert exc.value.details["missing"] == ["." + missing.split(".")[1]]

    def test_component_with_different_stem_does_not_count(self, tmp_path: Path) -> None:
        data = zip_bytes({"a.shp": b"1", "b.shx": b"2", "a.dbf": b"3"})
        assert rejected_code(write(tmp_path, data)) == "SHAPEFILE_INCOMPLETE"

    def test_multiple_shapefiles_rejected(self, tmp_path: Path) -> None:
        data = zip_bytes({**SHAPEFILE_PARTS, "q.shp": b"1", "q.shx": b"2", "q.dbf": b"3"})
        assert rejected_code(write(tmp_path, data)) == "MULTIPLE_SHAPEFILES"


class TestHostileArchives:
    @pytest.mark.parametrize(
        "name",
        ["../../evil.shp", "a/../../evil.shp", "/etc/evil.shp", "\\windows\\evil.shp", "C:evil.shp", "..\\evil.shp"],
    )
    def test_path_traversal_rejected(self, tmp_path: Path, name: str) -> None:
        assert (
            rejected_code(write(tmp_path, zip_bytes({name: b"x", "p.shx": b"x", "p.dbf": b"x"}))) == "ZIP_UNSAFE_PATH"
        )

    def test_symlink_rejected(self, tmp_path: Path) -> None:
        assert rejected_code(write(tmp_path, zip_with_symlink())) == "ZIP_UNSAFE_ENTRY"

    def test_encrypted_entries_rejected(self, tmp_path: Path) -> None:
        assert rejected_code(write(tmp_path, zip_with_encrypted_flag())) == "ZIP_ENCRYPTED"

    def test_too_many_entries(self, tmp_path: Path) -> None:
        entries = {f"junk{i}.txt": b"" for i in range(60)}
        assert rejected_code(write(tmp_path, zip_bytes({**SHAPEFILE_PARTS, **entries}))) == "ZIP_TOO_MANY_ENTRIES"

    def test_zip_bomb_by_compression_ratio(self, tmp_path: Path) -> None:
        bomb = zip_bytes({**SHAPEFILE_PARTS, "p.dbf": b"\0" * (5 * 1024 * 1024)})
        limits = ZipLimits(50, 100 * 1024 * 1024, max_compression_ratio=100)
        assert rejected_code(write(tmp_path, bomb), limits) == "ZIP_BOMB_SUSPECTED"

    def test_zip_bomb_by_total_size(self, tmp_path: Path) -> None:
        big = zip_bytes({**SHAPEFILE_PARTS, "p.dbf": b"\0" * (2 * 1024 * 1024)})
        limits = ZipLimits(50, max_uncompressed_bytes=1024 * 1024, max_compression_ratio=1e9)
        assert rejected_code(write(tmp_path, big), limits) == "ZIP_TOO_LARGE"


class TestExtraction:
    def test_extracts_only_components_to_flat_names(self, tmp_path: Path, workdir: Path) -> None:
        data = shapefile_zip([Point(77, 28)], folder="nested/dir/", extra_entries={"notes.txt": b"x"}, workdir=workdir)
        archive_path = write(tmp_path, data)
        archive = inspect_shapefile_zip(archive_path, LIMITS)
        dest = tmp_path / "out"
        dest.mkdir()
        shp = extract_shapefile(archive_path, archive, dest, LIMITS)
        extracted = {p.name for p in dest.iterdir()}
        assert shp == dest / "parcels.shp"
        assert {"parcels.shp", "parcels.shx", "parcels.dbf", "parcels.prj"} <= extracted
        assert extracted <= {"parcels.shp", "parcels.shx", "parcels.dbf", "parcels.prj", "parcels.cpg"}
        assert "notes.txt" not in extracted  # non-component entries are never written
        assert not any(p.is_dir() for p in dest.iterdir())  # archive folders are flattened

    def test_lying_header_cannot_expand_beyond_declared_size(self, tmp_path: Path) -> None:
        """A forged, too-small declared size is rejected and never produces more than the declared bytes.

        (zipfile stops at the declared size and the CRC check then fails; our own counter is a second layer.)
        """
        archive_path = write(tmp_path, zip_bytes(SHAPEFILE_PARTS, compression=zipfile.ZIP_STORED))
        archive = inspect_shapefile_zip(archive_path, LIMITS)
        archive.components[".dbf"].file_size = 1
        dest = tmp_path / "out"
        dest.mkdir()
        with pytest.raises(DatasetError) as exc:
            extract_shapefile(archive_path, archive, dest, LIMITS)
        assert exc.value.code in {"INVALID_ZIP", "ZIP_BOMB_SUSPECTED"}
        assert (dest / "p.dbf").stat().st_size <= 1

    def test_streaming_budget_caps_total_extracted_bytes(self, tmp_path: Path) -> None:
        archive_path = write(tmp_path, zip_bytes({**SHAPEFILE_PARTS, "p.dbf": b"d" * 4096}))
        archive = inspect_shapefile_zip(archive_path, LIMITS)
        tight = ZipLimits(max_entries=50, max_uncompressed_bytes=1024, max_compression_ratio=1000)
        dest = tmp_path / "out"
        dest.mkdir()
        with pytest.raises(DatasetError) as exc:
            extract_shapefile(archive_path, archive, dest, tight)
        assert exc.value.code == "ZIP_BOMB_SUSPECTED"

    def test_corrupt_member_detected_by_crc(self, tmp_path: Path) -> None:
        data = bytearray(zip_bytes({"p.shp": b"A" * 64, "p.shx": b"x", "p.dbf": b"x"}, compression=zipfile.ZIP_STORED))
        payload_at = data.find(b"A" * 64)
        data[payload_at] = ord("B")  # flip one byte of stored data -> CRC mismatch
        archive_path = write(tmp_path, bytes(data))
        archive = inspect_shapefile_zip(archive_path, LIMITS)
        dest = tmp_path / "out"
        dest.mkdir()
        with pytest.raises(DatasetError) as exc:
            extract_shapefile(archive_path, archive, dest, LIMITS)
        assert exc.value.code == "INVALID_ZIP"
