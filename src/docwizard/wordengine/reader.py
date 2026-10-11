"""Read a .docx into a block model + a Markdown view for the LLM.

The block model keeps a pointer to every original XML element, so later steps
(edit in place, insert chapter, fill template) operate on the real document
instead of a lossy copy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from lxml import etree

from .ns import NS, qn, wval, local
from .package import DocxPackage
from .styles import StyleSheet

BlockKind = Literal["heading", "paragraph", "list", "table", "toc", "image", "pagebreak", "section", "other"]


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: str | None = None


@dataclass
class Block:
    index: int                      # position among body children
    kind: BlockKind
    element: etree._Element = field(repr=False)
    style_id: str | None = None
    level: int = 0                  # heading level (1..9) or list level (0..8)
    ordered: bool = False
    spans: list[Span] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)   # tables
    images: list[str] = field(default_factory=list)       # media part names
    has_section_break: bool = False
    page_break_before: bool = False

    @property
    def text(self) -> str:
        if self.kind == "table":
            return "\n".join(" | ".join(r) for r in self.rows)
        return "".join(s.text for s in self.spans)


@dataclass
class OutlineNode:
    block: Block
    title: str
    level: int
    number: str
    start: int         # block list index of the heading
    end: int           # exclusive block list index where the section ends (incl. subsections)


class DocModel:
    def __init__(self, pkg: DocxPackage):
        self.pkg = pkg
        self.styles = StyleSheet(pkg)
        self.numbering = _Numbering(pkg)
        self.blocks: list[Block] = []
        self._read()

    @classmethod
    def open(cls, path) -> "DocModel":
        return cls(DocxPackage.open(path))

    # ---- parsing --------------------------------------------------------
    def _read(self) -> None:
        for i, child in enumerate(self.pkg.body):
            tag = local(child)
            if tag == "p":
                self.blocks.append(self._paragraph(i, child))
            elif tag == "tbl":
                self.blocks.append(self._table(i, child))
            elif tag == "sdt":
                self.blocks.append(self._sdt(i, child))
            elif tag == "sectPr":
                self.blocks.append(Block(i, "section", child))
            else:
                self.blocks.append(Block(i, "other", child))

    def _paragraph(self, i: int, p: etree._Element) -> Block:
        ppr = p.find(qn("w:pPr"))
        sid = wval(ppr.find(qn("w:pStyle"))) if ppr is not None else None
        sid = sid or self.styles.default_para
        spans, images = self._spans(p)
        b = Block(i, "paragraph", p, sid, spans=spans, images=images)
        if ppr is not None:
            b.has_section_break = ppr.find(qn("w:sectPr")) is not None
            b.page_break_before = ppr.find(qn("w:pageBreakBefore")) is not None
        if any(re.search(r"\bTOC\b", t or "") for t in p.xpath(".//w:instrText/text()", namespaces=NS)):
            b.kind = "toc"
            return b
        if self.styles.toc_level(sid):
            b.kind = "toc"
            return b
        lvl = self.styles.heading_level(sid)
        if lvl is None and ppr is not None:
            o = ppr.find(qn("w:outlineLvl"))
            if o is not None and (wval(o) or "9").isdigit() and int(wval(o)) < 9:
                lvl = int(wval(o)) + 1
        if lvl and b.text.strip():
            b.kind, b.level = "heading", lvl
            return b
        num = self.numbering.paragraph_numbering(p, self.styles)
        if num is not None and b.text.strip():
            b.kind, b.level, b.ordered = "list", num[0], num[1]
            return b
        if not b.text.strip():
            if images:
                b.kind = "image"
            elif p.xpath(".//w:br[@w:type='page']", namespaces=NS):
                b.kind = "pagebreak"
        return b

    def _spans(self, p: etree._Element) -> tuple[list[Span], list[str]]:
        spans: list[Span] = []
        images: list[str] = []
        rels = None

        def walk(node, link=None):
            nonlocal rels
            for c in node:
                tag = local(c)
                if tag == "r":
                    rpr = c.find(qn("w:rPr"))
                    bold = _on(rpr, "w:b")
                    italic = _on(rpr, "w:i")
                    rs = wval(rpr.find(qn("w:rStyle"))) if rpr is not None else None
                    code = bool(rs and "code" in rs.lower())
                    for t in c:
                        tl = local(t)
                        if tl == "t":
                            _add(spans, Span(t.text or "", bold, italic, code, link))
                        elif tl == "tab":
                            _add(spans, Span("\t", bold, italic, code, link))
                        elif tl in ("br", "cr") and t.get(qn("w:type")) != "page":
                            _add(spans, Span("\n", bold, italic, code, link))
                        elif tl == "noBreakHyphen":
                            _add(spans, Span("-", bold, italic, code, link))
                        elif tl in ("drawing", "pict"):
                            for blip in t.xpath(".//a:blip/@r:embed | .//v:imagedata/@r:id", namespaces=NS):
                                part = self.pkg.part_for_rel(self.pkg.main_part, blip)
                                if part:
                                    images.append(part)
                elif tag == "hyperlink":
                    rid = c.get(qn("r:id"))
                    target = None
                    if rid:
                        if rels is None:
                            rels = self.pkg.rel_map(self.pkg.main_part)
                        target = rels.get(rid, {}).get("target")
                    elif c.get(qn("w:anchor")):
                        target = "#" + c.get(qn("w:anchor"))
                    walk(c, target)
                elif tag in ("ins", "smartTag", "customXml", "fldSimple", "sdt", "sdtContent"):
                    walk(c, link)
                # w:del, bookmarks, proofErr, permStart... carry no visible text
        walk(p)
        return spans, images

    def _table(self, i: int, tbl: etree._Element) -> Block:
        rows = []
        for tr in tbl.findall(qn("w:tr")):
            cells = []
            for tc in tr.findall(qn("w:tc")):
                paras = tc.xpath(".//w:p", namespaces=NS)
                txt = "\n".join("".join(s.text for s in self._spans(pp)[0]) for pp in paras)
                cells.append(txt.strip())
            rows.append(cells)
        tstyle = tbl.find(f"{qn('w:tblPr')}/{qn('w:tblStyle')}")
        return Block(i, "table", tbl, wval(tstyle), rows=rows)

    def _sdt(self, i: int, sdt: etree._Element) -> Block:
        gallery = sdt.xpath("./w:sdtPr/w:docPartObj/w:docPartGallery/@w:val", namespaces=NS)
        if gallery and "Table of Contents" in gallery[0]:
            return Block(i, "toc", sdt)
        if sdt.xpath(".//w:instrText[contains(., 'TOC')]", namespaces=NS):
            return Block(i, "toc", sdt)
        # Block-level content control (often used on cover pages): read its paragraphs as text.
        content = sdt.find(qn("w:sdtContent"))
        spans: list[Span] = []
        if content is not None:
            for pp in content.findall(qn("w:p")):
                if spans:
                    spans.append(Span("\n"))
                spans.extend(self._spans(pp)[0])
        return Block(i, "other", sdt, spans=spans)

    # ---- views ------------------------------------------------------------
    def outline(self) -> list[OutlineNode]:
        nodes: list[OutlineNode] = []
        counters = [0] * 10
        heads = [(i, b) for i, b in enumerate(self.blocks) if b.kind == "heading"]
        for n, (i, b) in enumerate(heads):
            counters[b.level - 1] += 1
            for k in range(b.level, 10):
                counters[k] = 0
            number = ".".join(str(c) for c in counters[: b.level] if c) or str(counters[0])
            end = len(self.blocks)
            for j, b2 in heads[n + 1:]:
                if b2.level <= b.level:
                    end = j
                    break
            nodes.append(OutlineNode(b, b.text.strip(), b.level, number, i, end))
        return nodes

    def to_markdown(self, start: int = 0, end: int | None = None, block_ids: bool = False) -> str:
        out: list[str] = []
        for i, b in enumerate(self.blocks[start:end], start):
            md = block_markdown(b)
            if md is None or (md == "[INHOUDSOPGAVE]" and out and out[-1].endswith("[INHOUDSOPGAVE]")):
                continue
            if block_ids:
                md = f"<!-- b{i} -->\n{md}"
            out.append(md)
        return "\n\n".join(out).strip() + "\n"

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for b in self.blocks:
            kinds[b.kind] = kinds.get(b.kind, 0) + 1
        words = sum(len(b.text.split()) for b in self.blocks)
        return {"blocks": len(self.blocks), "kinds": kinds, "words": words}


def block_markdown(b: Block) -> str | None:
    if b.kind == "heading":
        return "#" * min(b.level, 6) + " " + spans_markdown(b.spans).strip()
    if b.kind == "paragraph":
        t = spans_markdown(b.spans).strip()
        return t or None
    if b.kind == "list":
        return "  " * b.level + ("1. " if b.ordered else "- ") + spans_markdown(b.spans).strip()
    if b.kind == "table":
        return table_markdown(b.rows)
    if b.kind == "image":
        return "\n".join(f"![afbeelding]({p})" for p in b.images)
    if b.kind == "toc":
        return "[INHOUDSOPGAVE]"
    if b.kind == "pagebreak":
        return None
    if b.kind == "other" and b.text.strip():
        return b.text.strip()
    return None


def spans_markdown(spans: list[Span]) -> str:
    out = []
    for s in spans:
        t = s.text
        if not t:
            continue
        core = t.strip()
        if not core:
            out.append(t)
            continue
        lead = t[: len(t) - len(t.lstrip())]
        trail = t[len(t.rstrip()):]
        if s.code:
            core = f"`{core}`"
        else:
            if s.bold and s.italic:
                core = f"***{core}***"
            elif s.bold:
                core = f"**{core}**"
            elif s.italic:
                core = f"*{core}*"
        if s.link and not s.link.startswith("#"):
            core = f"[{core}]({s.link})"
        out.append(lead + core + trail)
    return "".join(out).replace("\t", " ")


def table_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [[c.replace("\n", "<br>").replace("|", "\\|") for c in r] + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(norm[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join(lines)


def _on(rpr, tag) -> bool:
    if rpr is None:
        return False
    e = rpr.find(qn(tag))
    return e is not None and wval(e, "1") not in ("0", "false", "off")


def _add(spans: list[Span], s: Span) -> None:
    if spans:
        last = spans[-1]
        if (last.bold, last.italic, last.code, last.link) == (s.bold, s.italic, s.code, s.link):
            last.text += s.text
            return
    spans.append(s)


class _Numbering:
    """Minimal numbering.xml reader: is (numId, ilvl) a bullet or ordered list?"""

    def __init__(self, pkg: DocxPackage):
        from .ns import REL_NUMBERING
        self.part = pkg.related_part(REL_NUMBERING)
        self.formats: dict[tuple[str, int], str] = {}
        if not self.part or not pkg.has(self.part):
            return
        root = pkg.xml(self.part)
        abstract = {}
        for a in root.findall(qn("w:abstractNum")):
            lv = {}
            for lvl in a.findall(qn("w:lvl")):
                lv[int(lvl.get(qn("w:ilvl"), "0"))] = wval(lvl.find(qn("w:numFmt")), "decimal")
            abstract[a.get(qn("w:abstractNumId"))] = lv
        for n in root.findall(qn("w:num")):
            aid = wval(n.find(qn("w:abstractNumId")))
            for ilvl, fmt in abstract.get(aid, {}).items():
                self.formats[(n.get(qn("w:numId")), ilvl)] = fmt

    def paragraph_numbering(self, p, styles: StyleSheet) -> tuple[int, bool] | None:
        ppr = p.find(qn("w:pPr"))
        numpr = ppr.find(qn("w:numPr")) if ppr is not None else None
        sid = wval(ppr.find(qn("w:pStyle"))) if ppr is not None else None
        if numpr is None:
            for s in styles.chain(sid):
                if s.has_numbering:
                    numpr = s.element.find(f"{qn('w:pPr')}/{qn('w:numPr')}")
                    break
        if numpr is None:
            return None
        num_id = wval(numpr.find(qn("w:numId")))
        if num_id in (None, "0"):
            return None
        ilvl = int(wval(numpr.find(qn("w:ilvl")), "0") or 0)
        fmt = self.formats.get((num_id, ilvl), "bullet")
        return ilvl, fmt not in ("bullet", "none")
