"""OOXML namespaces and helpers."""
from __future__ import annotations

from lxml import etree

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "v": "urn:schemas-microsoft-com:vml",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
}

W = NS["w"]

REL_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
REL_HYPERLINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
REL_COMMENTS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
REL_NUMBERING = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"
REL_OFFICE_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
REL_STYLES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
REL_SETTINGS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"

CT_COMMENTS = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
CT_NUMBERING = "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"


def qn(tag: str) -> str:
    """'w:p' -> '{ns}p'."""
    prefix, local = tag.split(":")
    return f"{{{NS[prefix]}}}{local}"


def el(tag: str, attrib: dict[str, str] | None = None, *children) -> etree._Element:
    prefix = tag.split(":")[0]
    e = etree.Element(qn(tag), nsmap={prefix: NS[prefix]})
    for k, v in (attrib or {}).items():
        e.set(qn(k) if ":" in k else k, str(v))
    for c in children:
        if c is not None:
            e.append(c)
    return e


def sub(parent: etree._Element, tag: str, attrib: dict[str, str] | None = None) -> etree._Element:
    e = etree.SubElement(parent, qn(tag))
    for k, v in (attrib or {}).items():
        e.set(qn(k) if ":" in k else k, str(v))
    return e


def wval(e: etree._Element | None, default: str | None = None) -> str | None:
    if e is None:
        return default
    return e.get(qn("w:val"), default)


def xpath(e: etree._Element, path: str) -> list:
    return e.xpath(path, namespaces=NS)


def local(e: etree._Element) -> str:
    return etree.QName(e).localname
