"""Streaming safety scan of KML documents before they reach GDAL.

KML is XML, so it inherits XML's attack surface:

* **Billion laughs / quadratic blowup** - recursive entity expansion that turns kilobytes into gigabytes.
* **XXE** - external entities that read local files (``file:///etc/passwd``) or make network requests
  (SSRF) while parsing.

``defusedxml`` forbids DTDs, entity declarations and external references outright. Legitimate KML never
needs them. The scan is streaming (``iterparse`` + ``clear()``), so memory stays flat regardless of size.

The scan also records ``<NetworkLink>`` elements: GDAL never fetches them, but users should know their
remote content is not part of the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import iterparse

from app.domain.errors import DatasetError


@dataclass(frozen=True, slots=True)
class KmlScan:
    placemarks: int
    network_links: int


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def scan_kml(path: Path) -> KmlScan:
    """Validate that ``path`` is well-formed, DTD-free KML. Raises :class:`DatasetError` otherwise."""
    placemarks = 0
    network_links = 0
    root_checked = False
    try:
        for event, element in iterparse(
            str(path), events=("start", "end"), forbid_dtd=True, forbid_entities=True, forbid_external=True
        ):
            if event == "start":
                if not root_checked:
                    if _local(element.tag) != "kml":
                        raise DatasetError("INVALID_KML", "The XML root element is not <kml>.")
                    root_checked = True
                continue
            name = _local(element.tag)
            if name == "Placemark":
                placemarks += 1
                element.clear()  # keep memory flat for large documents
            elif name == "NetworkLink":
                network_links += 1
    except DefusedXmlException as exc:
        raise DatasetError(
            "KML_UNSAFE", "The KML contains a DTD or entity declarations, which are not allowed."
        ) from exc
    except ParseError as exc:
        raise DatasetError("INVALID_KML", f"The KML is not well-formed XML: {exc}") from exc
    if not root_checked:
        raise DatasetError("INVALID_KML", "The KML document is empty.")
    return KmlScan(placemarks=placemarks, network_links=network_links)
