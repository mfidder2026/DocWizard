"""Style sheet analysis: which style means what, in any Word UI language.

Built-in Word styles keep an English w:name ("heading 1") but get a localized
w:styleId ("Kop1" in Dutch Word). Corporate templates add custom styles that are
based on built-ins or carry an outline level. We resolve all of that into
semantic roles the builder can use.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from .ns import qn, wval
from .package import DocxPackage
from .ns import REL_STYLES

_HEADING_RX = re.compile(
    r"^(heading|kop|titre|überschrift|uberschrift|encabezado|titolo|rubrik|overskrift|nagłówek)\s*(\d)$", re.I
)
_TOC_RX = re.compile(r"^(toc|inhopg|inhoudsopgave|tdm|verzeichnis|tm)\s*(\d)$", re.I)

# English built-in names (w:name is English in every UI language).
ROLE_NAMES = {
    "title": ["title"],
    "subtitle": ["subtitle"],
    "body": ["body text", "normal"],
    "list_bullet": ["list bullet"],
    "list_number": ["list number"],
    "list_paragraph": ["list paragraph"],
    "caption": ["caption"],
    "quote": ["quote", "intense quote"],
    "code": ["html preformatted", "plain text", "macro text"],
    "toc_heading": ["toc heading"],
    "table_grid": ["table grid"],
    "hyperlink": ["hyperlink"],
    "footnote_text": ["footnote text"],
}


@dataclass
class Style:
    id: str
    name: str
    type: str
    based_on: str | None
    outline_level: int | None  # 0-based, own value only
    has_numbering: bool
    element: etree._Element = field(repr=False)


class StyleSheet:
    def __init__(self, pkg: DocxPackage):
        self.pkg = pkg
        self.part = pkg.related_part(REL_STYLES) or "word/styles.xml"
        self.styles: dict[str, Style] = {}
        self.default_para: str | None = None
        self.default_table: str | None = None
        if pkg.has(self.part):
            self._load(pkg.xml(self.part))

    def _load(self, root: etree._Element) -> None:
        for s in root.findall(qn("w:style")):
            sid = s.get(qn("w:styleId"))
            stype = s.get(qn("w:type"), "paragraph")
            name = wval(s.find(qn("w:name")), sid) or sid
            ppr = s.find(qn("w:pPr"))
            ol = None
            numbered = False
            if ppr is not None:
                o = ppr.find(qn("w:outlineLvl"))
                if o is not None and wval(o, "9").isdigit():
                    ol = int(wval(o))
                numbered = ppr.find(qn("w:numPr")) is not None
            st = Style(sid, name, stype, wval(s.find(qn("w:basedOn"))), ol, numbered, s)
            self.styles[sid] = st
            if s.get(qn("w:default")) in ("1", "true"):
                if stype == "paragraph":
                    self.default_para = sid
                elif stype == "table":
                    self.default_table = sid

    # ---- lookups ----------------------------------------------------------
    def get(self, sid: str | None) -> Style | None:
        return self.styles.get(sid) if sid else None

    def by_name(self, name: str) -> Style | None:
        n = name.lower()
        for s in self.styles.values():
            if s.name.lower() == n:
                return s
        return None

    def chain(self, sid: str | None):
        seen = set()
        while sid and sid not in seen and sid in self.styles:
            seen.add(sid)
            yield self.styles[sid]
            sid = self.styles[sid].based_on

    def heading_level(self, sid: str | None) -> int | None:
        """1-based heading level of a paragraph style, or None."""
        for s in self.chain(sid):
            m = _HEADING_RX.match(s.name.strip()) or _HEADING_RX.match(s.id.strip())
            if m:
                return int(m.group(2))
            if s.outline_level is not None and s.outline_level < 9:
                return s.outline_level + 1
            if s.name.lower() in ("title", "subtitle"):
                return None
        return None

    def toc_level(self, sid: str | None) -> int | None:
        s = self.get(sid)
        if not s:
            return None
        m = _TOC_RX.match(s.name.strip()) or _TOC_RX.match(s.id.strip())
        return int(m.group(2)) if m else None

    def has_role_name(self, sid: str | None, role: str) -> bool:
        names = ROLE_NAMES.get(role, [])
        return any(s.name.lower() in names for s in self.chain(sid))

    def is_numbered(self, sid: str | None) -> bool:
        return any(s.has_numbering for s in self.chain(sid))

    def find_role(self, role: str) -> str | None:
        for name in ROLE_NAMES.get(role, []):
            s = self.by_name(name)
            if s:
                return s.id
        return None

    def find_heading(self, level: int) -> str | None:
        best = None
        for s in self.styles.values():
            if s.type != "paragraph":
                continue
            m = _HEADING_RX.match(s.name.strip())
            if m and int(m.group(2)) == level:
                return s.id
            if best is None and self.heading_level(s.id) == level:
                best = s.id
        return best

    def find_toc(self, level: int) -> str | None:
        for s in self.styles.values():
            m = _TOC_RX.match(s.name.strip())
            if m and int(m.group(2)) == level:
                return s.id
        return None

    def ensure_style(self, sid: str, name: str, based_on: str | None = None, stype: str = "paragraph",
                     ppr: etree._Element | None = None, rpr: etree._Element | None = None) -> str:
        """Create a minimal style when the template lacks one (e.g. Caption)."""
        if sid in self.styles:
            return sid
        root = self.pkg.xml(self.part)
        s = etree.SubElement(root, qn("w:style"))
        s.set(qn("w:type"), stype)
        s.set(qn("w:styleId"), sid)
        n = etree.SubElement(s, qn("w:name"))
        n.set(qn("w:val"), name)
        if based_on:
            b = etree.SubElement(s, qn("w:basedOn"))
            b.set(qn("w:val"), based_on)
        etree.SubElement(s, qn("w:qFormat"))
        if ppr is not None:
            s.append(ppr)
        if rpr is not None:
            s.append(rpr)
        self.styles[sid] = Style(sid, name, stype, based_on, None, False, s)
        return sid
