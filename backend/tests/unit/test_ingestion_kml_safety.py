"""KML XML-safety scan: XXE, entity expansion, malformed documents."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.errors import DatasetError
from app.ingestion.kml_safety import scan_kml
from tests.builders import kml_document, kml_placemark, kml_point


def scan_bytes(tmp_path: Path, data: bytes) -> object:
    path = tmp_path / "doc.kml"
    path.write_bytes(data)
    return scan_kml(path)


def error_code(tmp_path: Path, data: bytes) -> str:
    with pytest.raises(DatasetError) as exc:
        scan_bytes(tmp_path, data)
    return exc.value.code


def test_counts_placemarks_and_network_links(tmp_path: Path) -> None:
    link = "<NetworkLink><Link><href>http://example.com/x.kml</href></Link></NetworkLink>"
    doc = kml_document(kml_placemark("a", kml_point(1, 2)), kml_placemark("b", kml_point(3, 4)), link)
    result = scan_bytes(tmp_path, doc)
    assert result.placemarks == 2  # type: ignore[attr-defined]
    assert result.network_links == 1  # type: ignore[attr-defined]


def test_xxe_external_entity_is_rejected(tmp_path: Path) -> None:
    doc = kml_document(
        kml_placemark("&xxe;", kml_point(1, 2)),
        prolog='<!DOCTYPE kml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n',
    )
    assert error_code(tmp_path, doc) == "KML_UNSAFE"


def test_billion_laughs_is_rejected(tmp_path: Path) -> None:
    prolog = (
        '<!DOCTYPE kml [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>\n'
    )
    assert error_code(tmp_path, kml_document(kml_placemark("&lol3;"), prolog=prolog)) == "KML_UNSAFE"


def test_wrong_root_element(tmp_path: Path) -> None:
    assert error_code(tmp_path, b'<?xml version="1.0"?><gpx></gpx>') == "INVALID_KML"


def test_malformed_xml(tmp_path: Path) -> None:
    assert error_code(tmp_path, b"<kml><Document><Placemark></Document>") == "INVALID_KML"


def test_empty_document(tmp_path: Path) -> None:
    assert error_code(tmp_path, b"   ") == "INVALID_KML"
