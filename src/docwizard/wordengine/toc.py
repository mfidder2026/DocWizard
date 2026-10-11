"""Rebuild the table of contents so it is correct the moment the file opens.

Works for both TOC flavours Word produces: a TOC field inside a content
control (docPartGallery "Table of Contents") and a bare field spanning body
paragraphs. Entries link to `_Toc` bookmarks on the headings and carry
PAGEREF fields; page numbers are refreshed by Word on open (updateFields).
"""
from __future__ import annotations

import re

from lxml import etree

from .fields import set_update_fields_on_open
from .ns import NS, qn, wval
from .order import put, put_new
from .reader import DocModel
from .housestyle import text_width_twips


def _instr_of(p: etree._Element) -> str:
    return "".join(t.text or "" for t in p.iter(qn("w:instrText")))


def find_toc(model: DocModel) -> tuple[etree._Element, int, int, str] | None:
    """Return (container, first_idx, last_idx, instr) of the TOC field paragraphs."""
    body = model.pkg.body
    containers = [body] + [s.find(qn("w:sdtContent")) for s in body.findall(qn("w:sdt"))]
    for c in containers:
        if c is None:
            continue
        kids = list(c)
        for i, p in enumerate(kids):
            if etree.QName(p).localname != "p" or not re.search(r"\bTOC\b", _instr_of(p)):
                continue
            # Walk fldChars from this paragraph until the TOC's own end.
            depth, started = 0, False
            instr = ""
            for j in range(i, len(kids)):
                for e in kids[j].iter(qn("w:fldChar"), qn("w:instrText")):
                    if etree.QName(e).localname == "instrText":
                        if depth == 1:
                            instr += e.text or ""
                        continue
                    kind = e.get(qn("w:fldCharType"))
                    if kind == "begin":
                        depth += 1
                        started = True
                    elif kind == "end":
                        depth -= 1
                        if started and depth == 0:
                            return c, i, j, instr
            return c, i, len(kids) - 1, instr
    return None


def _max_level(instr: str) -> int:
    m = re.search(r'\\o\s+"?(\d)\s*-\s*(\d)"?', instr)
    return int(m.group(2)) if m else 3


def ensure_heading_bookmarks(model: DocModel) -> list[tuple[int, str, str, str]]:
    """Make sure every heading has a _Toc bookmark. Returns [(level, number, title, bookmark)]."""
    doc = model.pkg.document
    next_id = max([int(v) for v in doc.xpath("//w:bookmarkStart/@w:id", namespaces=NS) if v.isdigit()],
                  default=0) + 1
    out = []
    for node in model.outline():
        p = node.block.element
        existing = [b.get(qn("w:name")) for b in p.findall(qn("w:bookmarkStart"))
                    if (b.get(qn("w:name")) or "").startswith("_Toc")]
        if existing:
            name = existing[0]
        else:
            name = f"_TocDW{next_id:06d}"
            bs = etree.Element(qn("w:bookmarkStart"))
            bs.set(qn("w:id"), str(next_id))
            bs.set(qn("w:name"), name)
            be = etree.Element(qn("w:bookmarkEnd"))
            be.set(qn("w:id"), str(next_id))
            ppr = p.find(qn("w:pPr"))
            p.insert(p.index(ppr) + 1 if ppr is not None else 0, bs)
            p.append(be)
            next_id += 1
        out.append((node.level, node.number, node.title, name))
    return out


def _heading_numbered(model: DocModel, p: etree._Element) -> bool:
    ppr = p.find(qn("w:pPr"))
    if ppr is not None and ppr.find(qn("w:numPr")) is not None:
        nid = wval(ppr.find(f"{qn('w:numPr')}/{qn('w:numId')}"))
        return nid not in (None, "0")
    sid = wval(ppr.find(qn("w:pStyle"))) if ppr is not None else None
    return model.styles.is_numbered(sid)


def rebuild_toc(model: DocModel) -> int:
    """Rebuild cached TOC entries. Returns number of entries (0 if the doc has no TOC)."""
    found = find_toc(model)
    if not found:
        return 0
    container, i, j, instr = found
    max_level = _max_level(instr)
    entries = [e for e in ensure_heading_bookmarks(model) if e[0] <= max_level]
    numbered = {n.block.element: _heading_numbered(model, n.block.element) for n in model.outline()}
    numbered_by_bm = {}
    for node in model.outline():
        for b in node.block.element.findall(qn("w:bookmarkStart")):
            numbered_by_bm[b.get(qn("w:name"))] = numbered[node.block.element]

    width = text_width_twips(model)
    kids = list(container)
    anchor = kids[i]
    new: list[etree._Element] = []
    if not entries:
        p = _para(model, 1, width, False)
        _field_start(p, instr)
        _run_text(p, "Geen koppen gevonden.")
        _fld(p, "end")
        new.append(p)
    for n, (level, number, title, bm) in enumerate(entries):
        has_num = numbered_by_bm.get(bm, False)
        p = _para(model, level, width, has_num)
        if n == 0:
            _field_start(p, instr)
        h = etree.SubElement(p, qn("w:hyperlink"))
        h.set(qn("w:anchor"), bm)
        h.set(qn("w:history"), "1")
        if has_num and number:
            _run_text(h, number)
            _tab(h)
        _run_text(h, title)
        _tab(h)
        _fld(h, "begin")
        _instr(h, f" PAGEREF {bm} \\h ")
        _fld(h, "separate")
        _run_text(h, "")
        _fld(h, "end")
        if n == len(entries) - 1:
            _fld(p, "end")
        new.append(p)
    for k in range(i, j + 1):
        container.remove(kids[k])
    pos = i
    for p in new:
        container.insert(pos, p)
        pos += 1
    set_update_fields_on_open(model.pkg)
    return len(entries)


def _para(model: DocModel, level: int, width: int, numbered: bool) -> etree._Element:
    sid = model.styles.find_toc(level)
    if not sid:
        ppr = etree.Element(qn("w:pPr"))
        put_new(ppr, "w:spacing", {"w:after": "100"})
        put_new(ppr, "w:ind", {"w:left": str(220 * (level - 1))})
        sid = model.styles.ensure_style(f"TOC{level}", f"toc {level}", model.styles.default_para, ppr=ppr)
    p = etree.Element(qn("w:p"))
    ppr = etree.SubElement(p, qn("w:pPr"))
    put_new(ppr, "w:pStyle", {"w:val": sid})
    tabs = etree.Element(qn("w:tabs"))
    if numbered:
        t = etree.SubElement(tabs, qn("w:tab"))
        t.set(qn("w:val"), "left")
        t.set(qn("w:pos"), str(220 * (level - 1) + 440 + 220 * max(0, level - 2)))
    t = etree.SubElement(tabs, qn("w:tab"))
    t.set(qn("w:val"), "right")
    t.set(qn("w:leader"), "dot")
    t.set(qn("w:pos"), str(width))
    put(ppr, tabs)
    return p


def _rpr() -> etree._Element:
    rpr = etree.Element(qn("w:rPr"))
    etree.SubElement(rpr, qn("w:noProof"))
    return rpr


def _run_text(parent, text: str) -> None:
    r = etree.SubElement(parent, qn("w:r"))
    r.append(_rpr())
    t = etree.SubElement(r, qn("w:t"))
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _tab(parent) -> None:
    r = etree.SubElement(parent, qn("w:r"))
    r.append(_rpr())
    etree.SubElement(r, qn("w:tab"))


def _fld(parent, kind: str) -> None:
    r = etree.SubElement(parent, qn("w:r"))
    r.append(_rpr())
    f = etree.SubElement(r, qn("w:fldChar"))
    f.set(qn("w:fldCharType"), kind)


def _instr(parent, text: str) -> None:
    r = etree.SubElement(parent, qn("w:r"))
    r.append(_rpr())
    t = etree.SubElement(r, qn("w:instrText"))
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _field_start(p, instr: str) -> None:
    _fld(p, "begin")
    _instr(p, instr if instr.strip() else ' TOC \\o "1-3" \\h \\z \\u ')
    _fld(p, "separate")
