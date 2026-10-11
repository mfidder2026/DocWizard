"""Backend-neutral slide primitives (positions in EMU, font sizes in points).

Layouts produce a `SlideScene` per output slide; `writer.py` turns it into native PowerPoint objects and
`render.py` draws the very same primitives with Pillow for previews.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .spec import Chart
from .textfit import P

EMU_PER_PT = 12700
EMU_PER_INCH = 914400


def emu(pt: float) -> int:
    return int(round(pt * EMU_PER_PT))


def pt(e: int) -> float:
    return e / EMU_PER_PT


@dataclass
class Shape:
    """kind: rect | round | ellipse | chevron | pentagon."""

    kind: str
    x: int
    y: int
    w: int
    h: int
    fill: str | None = None
    line: str | None = None
    line_w: float = 0.75  # pt
    radius: float = 0.0  # pt, for kind="round"
    name: str = ""


@dataclass
class Line:
    x1: int
    y1: int
    x2: int
    y2: int
    color: str
    width: float = 0.75  # pt
    dash: bool = False


@dataclass
class Para:
    text: str
    family: str
    size: float
    color: str
    bold: bool = False
    italic: bool = False
    line: float = 0.0  # exact line pitch in pt
    space_before: float = 0.0  # pt
    align: str = "l"  # l | c | r
    bullet: str = ""
    bullet_color: str = ""
    indent: float = 0.0  # pt, left text indent (bullets hang in front of it)
    bullet_at: float = 0.0  # pt, bullet position from the box's left edge
    lead: int = 0  # leading characters in bold
    spacing: float = 0.0  # letter spacing pt
    caps: bool = False
    level: int = 0

    @property
    def shown(self) -> str:
        return self.text.upper() if self.caps else self.text


@dataclass
class TextBox:
    x: int
    y: int
    w: int
    h: int
    paras: list[Para]
    anchor: str = "t"  # t | m | b
    placeholder: str | None = None  # template mode: "title" | "subtitle" | "body" | "body2" ...
    name: str = ""


@dataclass
class Picture:
    x: int
    y: int
    w: int
    h: int
    blob: bytes
    ext: str = "png"
    name: str = ""


@dataclass
class TableP:
    x: int
    y: int
    w: int
    h: int
    header: list[str]
    rows: list[list[str]]
    col_w: list[int]
    row_h: list[int]
    family: str
    size: float
    header_family: str
    text: str
    header_fill: str
    header_text: str
    zebra: str
    background: str
    line: str
    numeric: list[bool] = field(default_factory=list)  # right-align numeric columns
    pad: float = 6.0  # cell padding in pt


@dataclass
class ChartP:
    x: int
    y: int
    w: int
    h: int
    chart: Chart
    palette: tuple[str, ...]
    family: str
    size: float
    text: str
    muted: str
    grid: str
    background: str


Prim = Shape | Line | TextBox | Picture | TableP | ChartP


@dataclass
class SlideScene:
    kind: str  # slide type
    background: str
    prims: list[Prim] = field(default_factory=list)
    notes: str = ""
    title: str = ""
    layout: str = "title_only"  # template layout role: title | section | content | two | title_only | blank
    decor: list[Prim] = field(default_factory=list)  # template mode: master/layout art (preview only)
    meta: dict[str, Any] = field(default_factory=dict)

    def add(self, *prims: Prim) -> None:
        self.prims.extend(prims)


# --------------------------------------------------------------------------- constructors (points in, EMU out)


def rect(x: float, y: float, w: float, h: float, fill: str | None, line: str | None = None, line_w: float = 0.75,
         kind: str = "rect", radius: float = 0.0, name: str = "") -> Shape:
    return Shape(kind, emu(x), emu(y), emu(w), emu(h), fill, line, line_w, radius, name)


def line(x1: float, y1: float, x2: float, y2: float, color: str, width: float = 0.75, dash: bool = False) -> Line:
    return Line(emu(x1), emu(y1), emu(x2), emu(y2), color, width, dash)


def paras_from(ps: list[P], base: float) -> list[Para]:
    out = []
    for i, p in enumerate(ps):
        s = round(base * p.rel * 2) / 2
        out.append(Para(
            text=p.text, family=p.family, size=s, color=p.color, bold=p.bold, italic=p.italic,
            line=round(s * p.line, 2), space_before=round(p.space_before * s, 2) if i else 0.0, align=p.align,
            bullet=p.bullet, bullet_color=p.bullet_color or p.color, indent=round(p.indent * base, 2),
            bullet_at=round(p.bullet_at * base, 2), lead=p.lead,
            spacing=p.spacing, caps=p.caps, level=p.level,
        ))
    return out


def textbox(x: float, y: float, w: float, h: float, ps: list[P], base: float, anchor: str = "t",
            placeholder: str | None = None, name: str = "") -> TextBox:
    return TextBox(emu(x), emu(y), emu(w), emu(h), paras_from(ps, base), anchor, placeholder, name)
