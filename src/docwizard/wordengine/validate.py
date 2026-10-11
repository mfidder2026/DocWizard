"""Structural checks before a document is handed to the user."""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from collections import Counter

from lxml import etree

from .ns import NS, qn
from .order import _INDEX
from .package import DocxPackage


def validate(pkg_or_bytes: DocxPackage | bytes, *, placeholders: bool = False) -> list[str]:
    """Return a list of problems (empty = OK). `placeholders=True` also reports unfilled {{fields}}."""
    data = pkg_or_bytes.to_bytes() if isinstance(pkg_or_bytes, DocxPackage) else pkg_or_bytes
    problems: list[str] = []
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = set(zf.namelist())
    xml: dict[str, etree._Element] = {}
    for n in names:
        if n.endswith(".xml") or n.endswith(".rels"):
            try:
                xml[n] = etree.fromstring(zf.read(n))
            except etree.XMLSyntaxError as e:
                problems.append(f"{n}: XML ongeldig ({e})")
    ct = xml.get("[Content_Types].xml")
    if ct is None:
        return problems + ["[Content_Types].xml ontbreekt"]
    defaults = {d.get("Extension", "").lower() for d in ct.findall(qn("ct:Default"))}
    overrides = {o.get("PartName") for o in ct.findall(qn("ct:Override"))}
    for n in names:
        if n.endswith("/") or n == "[Content_Types].xml":
            continue
        ext = n.rsplit(".", 1)[-1].lower()
        if "/" + n not in overrides and ext not in defaults:
            problems.append(f"Geen content type voor {n}")
    for n, root in xml.items():
        if not n.endswith(".rels"):
            continue
        src_dir = posixpath.dirname(posixpath.dirname(n))
        for r in root:
            if r.get("TargetMode") == "External":
                continue
            target = r.get("Target", "")
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(src_dir, target))
            if path not in names:
                problems.append(f"{n}: relatie {r.get('Id')} wijst naar ontbrekend {path}")
    doc = xml.get("word/document.xml")
    if doc is not None:
        problems += _check_doc(doc, placeholders)
    return problems


def _check_doc(doc: etree._Element, placeholders: bool) -> list[str]:
    out = []
    ids = Counter(doc.xpath("//w:bookmarkStart/@w:id", namespaces=NS))
    dup = [k for k, v in ids.items() if v > 1]
    if dup:
        out.append(f"Dubbele bladwijzer-ID's: {dup[:5]}")
    names = Counter(doc.xpath("//w:bookmarkStart/@w:name", namespaces=NS))
    dupn = [k for k, v in names.items() if v > 1]
    if dupn:
        out.append(f"Dubbele bladwijzernamen: {dupn[:5]}")
    dids = Counter(doc.xpath("//wp:docPr/@id", namespaces=NS))
    dupd = [k for k, v in dids.items() if v > 1]
    if dupd:
        out.append(f"Dubbele tekening-ID's: {dupd[:5]}")
    # Schema order of property containers we write to.
    for kind in ("pPr", "rPr", "tcPr", "tblPr"):
        idx = _INDEX[kind]
        for e in doc.iter(qn(f"w:{kind}")):
            parent = etree.QName(e.getparent()).localname
            if kind == "rPr" and parent not in ("r", "pPr"):
                continue
            seq = [idx.get(etree.QName(c).localname, -1) for c in e if isinstance(c.tag, str)]
            seq = [s for s in seq if s >= 0]
            if seq != sorted(seq):
                out.append(f"Verkeerde volgorde in w:{kind}: " +
                           ", ".join(etree.QName(c).localname for c in e if isinstance(c.tag, str)))
                break
    # Field balance.
    begins = len(doc.xpath("//w:fldChar[@w:fldCharType='begin']", namespaces=NS))
    ends = len(doc.xpath("//w:fldChar[@w:fldCharType='end']", namespaces=NS))
    if begins != ends:
        out.append(f"Velden niet in balans: {begins} begin / {ends} end")
    body = doc.find(qn("w:body"))
    if body is not None and len(body) and etree.QName(body[-1]).localname != "sectPr":
        out.append("Laatste body-element is geen sectPr")
    leftovers = [] if not placeholders else [t for t in doc.xpath("//w:t/text()", namespaces=NS) if re.search(r"\{\{[^}]+\}\}", t)]
    if leftovers:
        out.append(f"Niet-ingevulde placeholders: {leftovers[:5]}")
    return out
