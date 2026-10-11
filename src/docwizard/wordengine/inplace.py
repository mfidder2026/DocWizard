"""Edit text inside an existing document without touching its layout.

Used for translate / correct / review. A paragraph is split into *spans*
(runs with identical formatting). The LLM sees the text with numbered tags
for formatting changes (`Dit is <1>vet</1>.`) and atomic tokens for things it
must keep (`<x2/>` = field, image, tab, footnote reference). We write the
answer back into the original runs, optionally as tracked changes.
"""
from __future__ import annotations

import copy
import datetime as _dt
import difflib
import re
from dataclasses import dataclass, field

from lxml import etree

from .fields import text_parts
from .ns import NS, REL_COMMENTS, CT_COMMENTS, qn
from .package import DocxPackage

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_TEXT_TAGS = {"t"}
_ATOMIC_TAGS = {"tab", "br", "cr", "drawing", "pict", "object", "footnoteReference", "endnoteReference",
                "fldChar", "instrText", "sym", "noBreakHyphen", "softHyphen", "ptab", "commentReference",
                "separator", "continuationSeparator", "footnoteRef", "endnoteRef", "lastRenderedPageBreak"}


@dataclass
class Piece:
    kind: str                       # "text" | "atom"
    run: etree._Element             # the w:r
    element: etree._Element         # w:t or the atomic child
    text: str = ""


@dataclass
class Segment:
    """One editable paragraph."""
    id: str
    part: str
    paragraph: etree._Element = field(repr=False)
    spans: list[list[Piece]] = field(default_factory=list, repr=False)   # consecutive text pieces with equal rPr
    atoms: list[Piece] = field(default_factory=list, repr=False)
    slots: list[int] = field(default_factory=list, repr=False)          # slot (between atoms) of each span
    tagged_spans: bool = False
    tagged: str = ""                # what the LLM sees
    plain: str = ""
    context: str = ""               # e.g. "kop 2", "tabelcel", "koptekst"

    @property
    def is_simple(self) -> bool:
        return len(self.spans) <= 1 and not self.atoms


def _rpr_key(run: etree._Element) -> bytes:
    rpr = run.find(qn("w:rPr"))
    if rpr is None:
        return b""
    rpr = copy.deepcopy(rpr)
    for t in ("w:lang", "w:noProof", "w:rsid"):
        for e in rpr.findall(qn(t)):
            rpr.remove(e)
    return etree.tostring(rpr)


def _in_field_instr_or_deleted(e: etree._Element) -> bool:
    for a in e.iterancestors():
        ln = etree.QName(a).localname
        if ln in ("del", "moveFrom"):
            return True
        if ln in ("p", "body", "hdr", "ftr"):
            break
    return False


def extract_segments(pkg: DocxPackage, *, include_headers: bool = True, include_tables: bool = True,
                     include_notes: bool = True) -> list[Segment]:
    segs: list[Segment] = []
    parts = text_parts(pkg) if include_headers or include_notes else [pkg.main_part]
    for part in parts:
        if not include_headers and re.search(r"(header|footer)", part):
            continue
        if not include_notes and re.search(r"(footnotes|endnotes)", part):
            continue
        root = pkg.xml(part)
        for pi, p in enumerate(root.iter(qn("w:p"))):
            if not include_tables and any(etree.QName(a).localname == "tc" for a in p.iterancestors()):
                continue
            if _is_toc_paragraph(p):
                continue
            seg = _segment(part, pi, p)
            if seg and seg.plain.strip():
                segs.append(seg)
    return segs


_TOC_STYLE = re.compile(r"^(toc|inhopg|inhoudsopgave|tdm|verzeichnis)\s*\d", re.I)


def _is_toc_paragraph(p: etree._Element) -> bool:
    ps = p.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
    if ps is not None and _TOC_STYLE.match(ps.get(qn("w:val"), "")):
        return True
    instr = "".join(t.text or "" for t in p.iter(qn("w:instrText")))
    return "PAGEREF" in instr and "_Toc" in instr


def _segment(part: str, pi: int, p: etree._Element) -> Segment | None:
    # Paragraphs nested in text boxes appear both inside the outer paragraph and on their own; only
    # take runs whose nearest paragraph is p.
    pieces: list[Piece] = []
    in_field_result = 0
    for r in p.iter(qn("w:r")):
        if next(r.iterancestors(qn("w:p"))) is not p or _in_field_instr_or_deleted(r):
            continue
        for c in r:
            ln = etree.QName(c).localname
            if ln == "rPr":
                continue
            if ln == "fldChar":
                pieces.append(Piece("atom", r, c))
                continue
            if ln == "t":
                pieces.append(Piece("text", r, c, c.text or ""))
            elif ln in _ATOMIC_TAGS or ln == "AlternateContent":
                pieces.append(Piece("atom", r, c))
    if not any(pc.kind == "text" and pc.text.strip() for pc in pieces):
        return None
    # Text inside complex field instructions/results (TOC entries, page numbers) is left alone.
    depth = 0
    keep: list[Piece] = []
    for pc in pieces:
        if pc.kind == "atom" and etree.QName(pc.element).localname == "fldChar":
            kind = pc.element.get(qn("w:fldCharType"))
            depth += 1 if kind == "begin" else (-1 if kind == "end" else 0)
            if kind == "begin" and depth == 1:
                keep.append(pc)
            continue
        if depth > 0:
            continue
        keep.append(pc)
    pieces = keep
    seg = Segment(id=f"{part}#{pi}", part=part, paragraph=p)
    tagged, plain = [], []
    span_no = 0
    current_key = None
    open_tag = False
    text_runs = [pc for pc in pieces if pc.kind == "text"]
    keys = {_rpr_key(pc.run) for pc in text_runs}
    multi = len(keys) > 1
    for pc in pieces:
        if pc.kind == "atom":
            if open_tag:
                tagged.append(f"</{span_no}>")
                open_tag = False
            current_key = None
            seg.atoms.append(pc)
            tagged.append(f"<x{len(seg.atoms)}/>")
            continue
        key = _rpr_key(pc.run)
        if key != current_key or not seg.spans:
            if open_tag:
                tagged.append(f"</{span_no}>")
                open_tag = False
            seg.spans.append([pc])
            seg.slots.append(len(seg.atoms))
            current_key = key
            if multi:
                span_no = len(seg.spans)
                tagged.append(f"<{span_no}>")
                open_tag = True
        else:
            seg.spans[-1].append(pc)
        tagged.append(_esc(pc.text))
        plain.append(pc.text)
    if open_tag:
        tagged.append(f"</{span_no}>")
    seg.tagged = "".join(tagged)
    seg.plain = "".join(plain)
    seg.tagged_spans = multi
    if not multi and not seg.atoms:
        seg.tagged = seg.plain
    seg.context = _context(part, p)
    return seg


def _esc(s: str) -> str:
    return s.replace("<", "‹").replace(">", "›")


def _unesc(s: str) -> str:
    return s.replace("‹", "<").replace("›", ">")


def _context(part: str, p: etree._Element) -> str:
    if "header" in part:
        return "koptekst"
    if "footer" in part:
        return "voettekst"
    if "footnotes" in part or "endnotes" in part:
        return "voetnoot"
    if any(etree.QName(a).localname == "tc" for a in p.iterancestors()):
        return "tabelcel"
    ps = p.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
    return f"stijl {ps.get(qn('w:val'))}" if ps is not None else "alinea"


# ---- applying edits ---------------------------------------------------------------

_TAG_RX = re.compile(r"<(/?)(\d+)>|<x(\d+)\s*/>")


class TagMismatch(ValueError):
    pass


def parse_tagged(seg: Segment, new_text: str) -> dict[int, str]:
    """Map an LLM answer back to {span_index: text}. Raises TagMismatch when the structure is broken."""
    n_atoms = len(re.findall(r"<x\d+\s*/>", new_text))
    if n_atoms != len(seg.atoms):
        raise TagMismatch(f"{n_atoms} van {len(seg.atoms)} vaste elementen terug")
    texts: dict[int, str] = {k: "" for k in range(len(seg.spans))}
    if not seg.spans:
        return texts
    if seg.tagged_spans:
        pos, current, slot = 0, None, 0
        for m in _TAG_RX.finditer(new_text):
            chunk = new_text[pos:m.start()]
            pos = m.end()
            if chunk:
                texts[current if current is not None else _span_in_slot(seg, slot)] += chunk
            if m.group(3):
                slot += 1
                current = None
            elif m.group(1):
                current = None
            else:
                current = int(m.group(2)) - 1
                if current >= len(seg.spans):
                    raise TagMismatch(f"onbekende tag <{m.group(2)}>")
        tail = new_text[pos:]
        if tail:
            texts[current if current is not None else _span_in_slot(seg, slot)] += tail
    else:
        if _TAG_RX.search(re.sub(r"<x\d+\s*/>", "", new_text)):
            new_text = re.sub(r"</?\d+>", "", new_text)
        for slot, chunk in enumerate(re.split(r"<x\d+\s*/>", new_text)):
            if chunk:
                texts[_span_in_slot(seg, slot)] += chunk
    return {k: _unesc(v) for k, v in texts.items()}


def _span_in_slot(seg: Segment, slot: int) -> int:
    """First span in this slot, or the nearest span before it (text the model put between two atoms)."""
    best = 0
    for i, sl in enumerate(seg.slots):
        if sl == slot:
            return i
        if sl < slot:
            best = i
    return best


def apply_text(seg: Segment, new_text: str, *, track: bool = False, author: str = "DocWizard",
               date: str | None = None, rev_ids: "RevisionIds | None" = None) -> bool:
    """Write new text into the paragraph. Returns False if it had to fall back (formatting tags lost)."""
    ok = True
    try:
        texts = parse_tagged(seg, new_text)
    except TagMismatch:
        ok = False
        plain = _unesc(re.sub(r"</?\d+>|<x\d+\s*/>", "", new_text))
        dom = max(range(len(seg.spans)), key=lambda k: sum(len(pc.text) for pc in seg.spans[k])) if seg.spans else 0
        texts = {k: "" for k in range(len(seg.spans))}
        if seg.spans:
            texts[dom] = plain
    for k, span in enumerate(seg.spans):
        _set_span(span, texts.get(k, ""), track, author, date, rev_ids)
    return ok


class RevisionIds:
    def __init__(self, pkg: DocxPackage):
        vals = []
        for part in text_parts(pkg):
            vals += [int(v) for v in pkg.xml(part).xpath("//w:ins/@w:id|//w:del/@w:id|//w:comment/@w:id",
                                                          namespaces=NS) if v.lstrip("-").isdigit()]
        self.n = max(vals, default=1000) + 1

    def next(self) -> str:
        self.n += 1
        return str(self.n)


def _set_span(span: list[Piece], new: str, track: bool, author: str, date: str | None, rev_ids) -> None:
    old = "".join(pc.text for pc in span)
    if old == new:
        return
    if not track:
        span[0].element.text = new
        span[0].element.set(XML_SPACE, "preserve")
        for pc in span[1:]:
            pc.element.text = ""
        return
    # Tracked: rebuild the span's runs as a word diff of equal / w:del / w:ins runs.
    first_run = span[0].run
    template_run = copy.deepcopy(first_run)
    for c in list(template_run):
        if etree.QName(c).localname != "rPr":
            template_run.remove(c)
    anchor = first_run
    # Remove the span's text from its runs (keep runs that also carry atoms).
    runs = []
    for pc in span:
        if pc.run not in runs:
            runs.append(pc.run)
        pc.element.getparent().remove(pc.element)
    date = date or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_nodes = []
    a, b = _words(old), _words(new)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            new_nodes.append(_mk_run(template_run, "".join(a[i1:i2])))
            continue
        if i2 > i1:
            d = etree.Element(qn("w:del"))
            d.set(qn("w:id"), rev_ids.next() if rev_ids else "1")
            d.set(qn("w:author"), author)
            d.set(qn("w:date"), date)
            d.append(_mk_run(template_run, "".join(a[i1:i2]), deleted=True))
            new_nodes.append(d)
        if j2 > j1:
            ins = etree.Element(qn("w:ins"))
            ins.set(qn("w:id"), rev_ids.next() if rev_ids else "2")
            ins.set(qn("w:author"), author)
            ins.set(qn("w:date"), date)
            ins.append(_mk_run(template_run, "".join(b[j1:j2])))
            new_nodes.append(ins)
    # Insert where the first run was; drop now-empty runs.
    for node in reversed(new_nodes):
        anchor.addnext(node)
    for r in runs:
        if all(etree.QName(c).localname == "rPr" for c in r):
            r.getparent().remove(r)


def _words(s: str) -> list[str]:
    return re.findall(r"\s+|[\w\-]+|[^\w\s]", s, re.UNICODE)


def _mk_run(template_run: etree._Element, text: str, deleted: bool = False) -> etree._Element:
    r = copy.deepcopy(template_run)
    t = etree.SubElement(r, qn("w:delText" if deleted else "w:t"))
    t.text = text
    t.set(XML_SPACE, "preserve")
    return r


def set_language(pkg: DocxPackage, lang: str) -> None:
    """Set proofing language (w:lang) on runs and the document defaults, e.g. 'en-GB'."""
    for part in text_parts(pkg):
        for lang_el in pkg.xml(part).iter(qn("w:lang")):
            lang_el.set(qn("w:val"), lang)
    from .styles import StyleSheet
    st = StyleSheet(pkg)
    if pkg.has(st.part):
        for lang_el in pkg.xml(st.part).iter(qn("w:lang")):
            lang_el.set(qn("w:val"), lang)


# ---- comments ---------------------------------------------------------------------

class Comments:
    """Add Word comments anchored to paragraphs."""

    def __init__(self, pkg: DocxPackage, author: str = "DocWizard", initials: str = "DW"):
        self.pkg = pkg
        self.author, self.initials = author, initials
        self.part = pkg.related_part(REL_COMMENTS)
        if not self.part:
            self.part = "word/comments.xml"
            root = etree.Element(qn("w:comments"), nsmap={"w": NS["w"]})
            pkg.set_xml(self.part, root)
            pkg.add_rel(pkg.main_part, REL_COMMENTS, "comments.xml")
            pkg.ensure_override_ct(self.part, CT_COMMENTS)
        self.root = pkg.xml(self.part)
        ids = [int(c.get(qn("w:id"))) for c in self.root.findall(qn("w:comment"))]
        self.next_id = max(ids, default=-1) + 1

    def add(self, paragraph: etree._Element, text: str, end_paragraph: etree._Element | None = None) -> str:
        cid = str(self.next_id)
        self.next_id += 1
        date = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        c = etree.SubElement(self.root, qn("w:comment"))
        c.set(qn("w:id"), cid)
        c.set(qn("w:author"), self.author)
        c.set(qn("w:date"), date)
        c.set(qn("w:initials"), self.initials)
        for i, line in enumerate([l for l in text.split("\n") if l.strip()] or [text]):
            p = etree.SubElement(c, qn("w:p"))
            if i == 0:
                r0 = etree.SubElement(p, qn("w:r"))
                etree.SubElement(r0, qn("w:annotationRef"))
            r = etree.SubElement(p, qn("w:r"))
            t = etree.SubElement(r, qn("w:t"))
            t.text = line
            t.set(XML_SPACE, "preserve")
        start = etree.Element(qn("w:commentRangeStart"))
        start.set(qn("w:id"), cid)
        ppr = paragraph.find(qn("w:pPr"))
        paragraph.insert(paragraph.index(ppr) + 1 if ppr is not None else 0, start)
        endp = end_paragraph if end_paragraph is not None else paragraph
        end = etree.SubElement(endp, qn("w:commentRangeEnd"))
        end.set(qn("w:id"), cid)
        r = etree.SubElement(endp, qn("w:r"))
        etree.SubElement(r, qn("w:commentReference")).set(qn("w:id"), cid)
        return cid


__all__ = ["Segment", "extract_segments", "apply_text", "parse_tagged", "TagMismatch", "RevisionIds",
           "Comments", "set_language"]
