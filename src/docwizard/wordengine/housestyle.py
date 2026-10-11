"""Learn the house style of a document by example.

Corporate templates are rarely clean: some use proper styles, others use direct
formatting copied from older documents. So for every semantic role (heading N,
body, bullet, numbered list, table, ...) we pick an *exemplar* from the
document itself and clone its paragraph/run/table properties. Generated content
therefore looks exactly like content the template author wrote by hand.
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass

from lxml import etree

from .ns import NS, qn, wval
from .order import put
from .reader import Block, DocModel

_GUIDANCE_RX = re.compile(r"^\s*(\[.*\]|<<.*>>|\{\{.*\}\}|<.*>)\s*$", re.S)
_PLACEHOLDER_COLORS = {"808080", "7F7F7F", "A6A6A6", "BFBFBF", "FF0000", "C00000", "0070C0", "999999", "595959"}


def is_guidance(b: Block) -> bool:
    """Template instruction text like '[Beschrijf hier ...]' that must not survive into output."""
    t = b.text.strip()
    if not t:
        return False
    if _GUIDANCE_RX.match(t):
        return True
    runs = b.element.findall(f".//{qn('w:r')}")
    texty = [r for r in runs if "".join(r.itertext()).strip()]
    if texty and all(_run_is_placeholder(r) for r in texty):
        return True
    return False


def _run_is_placeholder(r) -> bool:
    rpr = r.find(qn("w:rPr"))
    if rpr is None:
        return False
    rs = wval(rpr.find(qn("w:rStyle"))) or ""
    if "placeholder" in rs.lower() or "tijdelijke" in rs.lower():
        return True
    if rpr.find(qn("w:highlight")) is not None:
        return True
    color = (wval(rpr.find(qn("w:color"))) or "").upper()
    italic = rpr.find(qn("w:i")) is not None
    return italic and color in _PLACEHOLDER_COLORS


@dataclass
class ParaFormat:
    style_id: str | None
    ppr: etree._Element | None = None     # cloned per use
    rpr: etree._Element | None = None     # dominant direct run formatting, cloned per use

    def new_ppr(self) -> etree._Element:
        ppr = copy.deepcopy(self.ppr) if self.ppr is not None else etree.Element(qn("w:pPr"))
        for t in ("w:sectPr", "w:pPrChange"):
            for e in ppr.findall(qn(t)):
                ppr.remove(e)
        if self.style_id:
            s = etree.Element(qn("w:pStyle"))
            s.set(qn("w:val"), self.style_id)
            put(ppr, s)
        return ppr

    def new_rpr(self) -> etree._Element | None:
        return copy.deepcopy(self.rpr) if self.rpr is not None else None


class HouseStyle:
    def __init__(self, model: DocModel):
        self.model = model
        st = model.styles
        blocks = model.blocks
        first_heading = next((i for i, b in enumerate(blocks) if b.kind == "heading"), 0)
        body_region = blocks[first_heading:]

        self.headings: dict[int, ParaFormat] = {}
        for lvl in range(1, 10):
            cands = [b for b in body_region if b.kind == "heading" and b.level == lvl]
            if cands:
                self.headings[lvl] = _format_from(cands, model)
            else:
                sid = st.find_heading(lvl)
                if sid:
                    self.headings[lvl] = ParaFormat(sid)

        paras = [b for b in body_region if b.kind == "paragraph" and b.text.strip() and not is_guidance(b)]
        if not paras:
            paras = [b for b in blocks if b.kind == "paragraph" and b.text.strip() and not is_guidance(b)
                     and st.heading_level(b.style_id) is None and not st.has_role_name(b.style_id, "title")
                     and not st.has_role_name(b.style_id, "subtitle")]
        self.body = _format_from(paras, model) if paras else ParaFormat(st.default_para)
        # The body exemplar must not carry list numbering or keep-with-next tricks.
        if self.body.ppr is not None:
            for t in ("w:numPr", "w:keepNext", "w:pageBreakBefore", "w:outlineLvl"):
                for e in self.body.ppr.findall(qn(t)):
                    self.body.ppr.remove(e)

        bullets = [b for b in blocks if b.kind == "list" and not b.ordered and not is_guidance(b)]
        numbers = [b for b in blocks if b.kind == "list" and b.ordered and not is_guidance(b)]
        self.bullet = _format_from(bullets, model) if bullets else self._role("list_bullet")
        self.number = _format_from(numbers, model) if numbers else self._role("list_number")

        self.quote = self._role("quote")
        self.caption = self._role("caption")
        self.code = self._role("code")

        tables = [b for b in body_region if b.kind == "table"] or [b for b in blocks if b.kind == "table"]
        self.table_exemplar: etree._Element | None = tables[0].element if tables else None
        self.headings_numbered = any(self._is_numbered(f) for f in self.headings.values())

    def _role(self, role: str) -> ParaFormat | None:
        sid = self.model.styles.find_role(role)
        return ParaFormat(sid) if sid else None

    def _is_numbered(self, f: ParaFormat) -> bool:
        if f.ppr is not None and f.ppr.find(qn("w:numPr")) is not None:
            return True
        return self.model.styles.is_numbered(f.style_id)

    def heading(self, level: int) -> ParaFormat:
        if level in self.headings:
            return self.headings[level]
        # Deeper than the template defines: reuse the deepest known level's look.
        known = [lv for lv in self.headings if lv < level]
        if known:
            return self.headings[max(known)]
        return ParaFormat(self.model.styles.find_heading(level))

    def summary(self) -> dict:
        def sid(f):
            return f.style_id if f else None
        return {
            "headings": {lvl: f.style_id for lvl, f in self.headings.items()},
            "body": sid(self.body), "bullet": sid(self.bullet), "number": sid(self.number),
            "quote": sid(self.quote), "caption": sid(self.caption), "code": sid(self.code),
            "table_exemplar": self.table_exemplar is not None, "headings_numbered": self.headings_numbered,
        }


def _format_from(blocks: list[Block], model: DocModel) -> ParaFormat:
    styles = Counter(b.style_id for b in blocks)
    sid = styles.most_common(1)[0][0]
    same = [b for b in blocks if b.style_id == sid]
    ex = same[0].element
    ppr = ex.find(qn("w:pPr"))
    ppr = copy.deepcopy(ppr) if ppr is not None else None
    return ParaFormat(sid, ppr, _dominant_rpr(same))


_RPR_STRIP = ("w:b", "w:bCs", "w:i", "w:iCs", "w:highlight", "w:vanish", "w:strike", "w:rStyle", "w:lang",
              "w:noProof", "w:webHidden", "w:u")


def _dominant_rpr(blocks: list[Block]) -> etree._Element | None:
    """Direct run formatting shared by most of the text (e.g. fonts applied by hand)."""
    weights: Counter[bytes] = Counter()
    sample: dict[bytes, etree._Element] = {}
    total = 0
    for b in blocks:
        for r in b.element.iter(qn("w:r")):
            n = len("".join(t.text or "" for t in r.findall(qn("w:t"))))
            if not n:
                continue
            total += n
            rpr = r.find(qn("w:rPr"))
            if rpr is None:
                key = b""
            else:
                rpr = copy.deepcopy(rpr)
                for t in _RPR_STRIP:
                    for e in rpr.findall(qn(t)):
                        rpr.remove(e)
                key = etree.tostring(rpr) if len(rpr) else b""
            weights[key] += n
            sample.setdefault(key, rpr)
    if not total:
        return None
    key, w = weights.most_common(1)[0]
    if not key or w / total < 0.6:
        return None
    return sample[key]


def text_width_twips(model: DocModel) -> int:
    sect = model.pkg.body.find(qn("w:sectPr"))
    if sect is not None:
        sz, mar = sect.find(qn("w:pgSz")), sect.find(qn("w:pgMar"))
        try:
            w = int(sz.get(qn("w:w")))
            return w - int(mar.get(qn("w:left"))) - int(mar.get(qn("w:right")))
        except (TypeError, ValueError, AttributeError):
            pass
    return 9026  # A4 with 2.54 cm margins


__all__ = ["HouseStyle", "ParaFormat", "is_guidance", "text_width_twips", "NS"]
