from __future__ import annotations

import pytest

from app.domain.enums import SourceFormat
from app.domain.errors import UnsupportedMediaTypeError, UploadRejectedError
from app.ingestion.filenames import file_extension, safe_stem, sanitize_display_filename
from app.ingestion.sniffing import detect_format, looks_like_kml
from tests.builders import kml_document, kml_placemark, kml_point, zip_bytes


class TestSanitizeDisplayFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("survey.kml", "survey.kml"),
            ("../../etc/passwd.kml", "passwd.kml"),
            ("C:\\Users\\x\\..\\evil.zip", "evil.zip"),
            ("/abs/path/data.zip", "data.zip"),
            ("bad\x00name\x1f.kml", "badname.kml"),
            ("  spaced   out  name.kml ", "spaced out name.kml"),
            ("", "upload"),
            (None, "upload"),
            ("...", "upload"),
        ],
    )
    def test_strips_paths_and_control_characters(self, raw: str | None, expected: str) -> None:
        assert sanitize_display_filename(raw) == expected

    def test_truncates_long_names_but_keeps_extension(self) -> None:
        name = sanitize_display_filename("a" * 400 + ".kml")
        assert len(name) == 255
        assert name.endswith(".kml")

    def test_unicode_is_normalised(self) -> None:
        decomposed = "cafe\u0301.kml"  # 'e' + combining acute accent
        assert sanitize_display_filename(decomposed) == "caf\u00e9.kml"


def test_file_extension_is_lowercased() -> None:
    assert file_extension("PARCELS.ZIP") == ".zip"
    assert file_extension("noext") == ""


def test_safe_stem_is_ascii_and_flat() -> None:
    assert safe_stem("../../Plot Boundaries (v2).shp") == "Plot_Boundaries_v2"
    assert safe_stem("...") == "dataset"


class TestDetectFormat:
    KML = kml_document(kml_placemark("p", kml_point(77, 28)))

    def test_valid_kml(self) -> None:
        assert detect_format(".kml", self.KML) is SourceFormat.KML

    def test_kml_with_bom_comment_and_prefix(self) -> None:
        head = b'\xef\xbb\xbf<?xml version="1.0"?>\n<!-- exported --><kml:kml xmlns:kml="x"></kml:kml>'
        assert detect_format(".kml", head) is SourceFormat.KML

    def test_valid_zip(self) -> None:
        assert detect_format(".zip", zip_bytes({"a.txt": b"x"})) is SourceFormat.SHAPEFILE

    @pytest.mark.parametrize("extension", [".exe", ".geojson", ".kmz", ".shp", ""])
    def test_rejects_unsupported_extensions(self, extension: str) -> None:
        with pytest.raises(UnsupportedMediaTypeError) as exc:
            detect_format(extension, b"anything")
        assert exc.value.status_code == 415

    def test_rejects_empty(self) -> None:
        with pytest.raises(UploadRejectedError, match="empty") as exc:
            detect_format(".kml", b"")
        assert exc.value.code == "EMPTY_FILE"

    def test_rejects_zip_disguised_as_kml(self) -> None:
        with pytest.raises(UploadRejectedError) as exc:
            detect_format(".kml", zip_bytes({"doc.kml": self.KML}))
        assert exc.value.code == "CONTENT_MISMATCH"

    def test_rejects_xml_disguised_as_zip(self) -> None:
        with pytest.raises(UploadRejectedError) as exc:
            detect_format(".zip", self.KML)
        assert exc.value.code == "CONTENT_MISMATCH"

    def test_rejects_executable_renamed_to_kml(self) -> None:
        with pytest.raises(UploadRejectedError) as exc:
            detect_format(".kml", b"MZ\x90\x00\x03\x00\x00\x00")
        assert exc.value.code == "INVALID_KML"

    def test_rejects_empty_zip_archive(self) -> None:
        with pytest.raises(UploadRejectedError) as exc:
            detect_format(".zip", zip_bytes({}))
        assert exc.value.code == "SHAPEFILE_MISSING"

    @pytest.mark.parametrize(
        "document",
        [
            b"<html><body>not kml</body></html>",
            b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY x "y">]><kml></kml>',
            b"plain text",
        ],
    )
    def test_looks_like_kml_rejects_non_kml(self, document: bytes) -> None:
        assert not looks_like_kml(document)
