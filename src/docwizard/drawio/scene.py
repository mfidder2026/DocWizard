"""Converts a Layout into backend-neutral drawing primitives (shared by the SVG and PNG renderers)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .layout import Layout, NodeBox
from .route import Box, Point
from .styles import STRIP, EdgeVisual
from .text import Line, Run, layout_runs

LINE_HEIGHT = 1.25


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float
    fill: str | None
    stroke: str | None
    width: float = 1.0
    rx: float = 0.0
    dash: str | None = None


@dataclass
class Poly:
    points: list[Point]
    fill: str | None
    stroke: str | None
    width: float = 1.0
    closed: bool = True
    dash: str | None = None


@dataclass
class Ellipse:
    cx: float
    cy: float
    rx: float
    ry: float
    fill: str | None
    stroke: str | None
    width: float = 1.0


@dataclass
class Text:
    lines: list[Line]
    box: Box
    align: str = "center"  # left | center
    valign: str = "middle"  # top | middle
    rotate: bool = False  # -90 degrees (vertical band titles)


Prim = Rect | Poly | Ellipse | Text


@dataclass
class Scene:
    width: float
    height: float
    prims: list[Prim] = field(default_factory=list)


# --------------------------------------------------------------------------- shape helpers


def _arc(cx: float, cy: float, rx: float, ry: float, a0: float, a1: float, n: int = 12) -> list[Point]:
    return [(cx + rx * math.cos(a0 + (a1 - a0) * i / n), cy + ry * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(n + 1)]


def _round_rect_points(b: Box, r: float) -> list[Point]:
    r = min(r, b.w / 2, b.h / 2)
    pts: list[Point] = []
    pts += _arc(b.r - r, b.y + r, r, r, -math.pi / 2, 0, 6)
    pts += _arc(b.r - r, b.b - r, r, r, 0, math.pi / 2, 6)
    pts += _arc(b.x + r, b.b - r, r, r, math.pi / 2, math.pi, 6)
    pts += _arc(b.x + r, b.y + r, r, r, math.pi, 1.5 * math.pi, 6)
    return pts


def _cloud_points(b: Box) -> list[Point]:
    # Union-of-bumps outline approximating draw.io's cloud shape.
    bumps = [(0.25, 0.62, 0.22, 0.30), (0.42, 0.36, 0.22, 0.30), (0.66, 0.34, 0.24, 0.30),
             (0.82, 0.58, 0.18, 0.28), (0.55, 0.72, 0.30, 0.25)]
    pts: list[Point] = []
    cx, cy = b.cx, b.cy
    for i in range(120):
        a = 2 * math.pi * i / 120
        best = 0.0
        dx, dy = math.cos(a), math.sin(a)
        for bx, by, rx, ry in bumps:
            ex, ey = b.x + bx * b.w, b.y + by * b.h
            erx, ery = rx * b.w, ry * b.h
            # ray from centre hitting ellipse: solve |(c + t d - e)/r| = 1
            ox, oy = (cx - ex) / erx, (cy - ey) / ery
            vx, vy = dx / erx, dy / ery
            qa = vx * vx + vy * vy
            qb = 2 * (ox * vx + oy * vy)
            qc = ox * ox + oy * oy - 1
            disc = qb * qb - 4 * qa * qc
            if disc >= 0:
                t = (-qb + math.sqrt(disc)) / (2 * qa)
                best = max(best, t)
        pts.append((cx + dx * best, cy + dy * best))
    return pts


def node_prims(nb: NodeBox) -> list[Prim]:
    v = nb.visual
    b = nb.anchor
    fill, stroke, sw = v.fill if v.fill != "none" else None, v.stroke, v.stroke_width
    dash = "6 3" if v.dashed else None
    out: list[Prim] = []
    shape = v.shape
    if shape == "rect" or shape == "server":
        out.append(Rect(b.x, b.y, b.w, b.h, fill, stroke, sw, 0, dash))
    elif shape in ("round", "round_more"):
        if v.drawio.find("archimate3") >= 0:
            r = 10.0
        elif "arcSize=6" in v.drawio:
            r = 0.06 * min(b.w, b.h)
        elif shape == "round_more":
            r = 0.22 * min(b.w, b.h)
        else:
            r = (0.10 if "arcSize=10" in v.drawio else 0.12) * min(b.w, b.h)
        out.append(Rect(b.x, b.y, b.w, b.h, fill, stroke, sw, r, dash))
    elif shape == "oct":
        c = 10
        out.append(Poly([(b.x, b.y + c), (b.x + c, b.y), (b.r - c, b.y), (b.r, b.y + c), (b.r, b.b - c),
                         (b.r - c, b.b), (b.x + c, b.b), (b.x, b.b - c)], fill, stroke, sw))
    elif shape == "cylinder":
        ry = 12.0 if "size=12" in v.drawio else 10.0
        body = [(b.x, b.y + ry), (b.x, b.b - ry)]
        body += [(b.cx + b.w / 2 * math.cos(a), b.b - ry + ry * math.sin(a))
                 for a in [math.pi - math.pi * i / 24 for i in range(25)]]
        body += [(b.r, b.y + ry)]
        body += [(b.cx + b.w / 2 * math.cos(a), b.y + ry + ry * math.sin(a))
                 for a in [-math.pi * i / 24 for i in range(25)]]
        out.append(Poly(body, fill, stroke, sw))
        lid = [(b.cx + b.w / 2 * math.cos(a), b.y + ry + ry * math.sin(a)) for a in
               [math.pi - math.pi * i / 24 for i in range(25)]]
        out.append(Poly(lid, None, stroke, sw, closed=False))
    elif shape == "hcylinder":
        rx = 12.0 if "size=12" in v.drawio else 10.0
        body = [(b.r - rx + rx * math.cos(a), b.cy + b.h / 2 * math.sin(a)) for a in
                [-math.pi / 2 + math.pi * i / 24 for i in range(25)]]
        body += [(b.x + rx + rx * math.cos(a), b.cy + b.h / 2 * math.sin(a)) for a in
                 [math.pi / 2 + math.pi * i / 24 for i in range(25)]]
        out.append(Poly(body, fill, stroke, sw))
        lid = [(b.r - rx + rx * math.cos(a), b.cy + b.h / 2 * math.sin(a)) for a in
               [math.pi / 2 + math.pi * i / 24 for i in range(25)]]
        out.append(Poly(lid, None, stroke, sw, closed=False))
    elif shape == "actor":
        hw = b.w / 4
        out.append(Ellipse(b.cx, b.y + b.h / 8, hw, b.h / 8, fill or "#FFFFFF", stroke, sw))
        out.append(Poly([(b.cx, b.y + b.h / 4), (b.cx, b.y + 2 * b.h / 3)], None, stroke, sw, closed=False))
        out.append(Poly([(b.x, b.y + b.h / 3), (b.r, b.y + b.h / 3)], None, stroke, sw, closed=False))
        out.append(Poly([(b.x, b.b), (b.cx, b.y + 2 * b.h / 3), (b.r, b.b)], None, stroke, sw, closed=False))
    elif shape == "person":
        hs = min(b.w * 0.45, b.h * 0.45)
        r = hs / 2
        body = Box(b.x, b.y + hs * 0.8, b.w, b.h - hs * 0.8)
        out.append(Rect(body.x, body.y, body.w, body.h, fill, stroke, sw, r))
        out.append(Ellipse(b.cx, b.y + r, r, r, fill, stroke, sw))
    elif shape == "cloud":
        out.append(Poly(_cloud_points(b), fill, stroke, sw))
    elif shape == "hexagon":
        s = b.w * 0.12
        out.append(Poly([(b.x + s, b.y), (b.r - s, b.y), (b.r, b.cy), (b.r - s, b.b), (b.x + s, b.b), (b.x, b.cy)],
                        fill, stroke, sw))
    elif shape == "process":
        s = b.w * 0.08
        out.append(Rect(b.x, b.y, b.w, b.h, fill, stroke, sw))
        out.append(Poly([(b.x + s, b.y), (b.x + s, b.b)], None, stroke, sw, closed=False))
        out.append(Poly([(b.r - s, b.y), (b.r - s, b.b)], None, stroke, sw, closed=False))
    elif shape == "folder":
        tw, th = 46, 12
        out.append(Poly([(b.x, b.y), (b.x + tw, b.y), (b.x + tw, b.y + th), (b.r, b.y + th), (b.r, b.b), (b.x, b.b)],
                        fill, stroke, sw))
    elif shape == "browser":
        out.append(Rect(b.x, b.y, b.w, b.h, fill, stroke, sw, 6))
        out.append(Poly([(b.x, b.y + 16), (b.r, b.y + 16)], None, stroke, sw, closed=False))
        for i in range(3):
            out.append(Ellipse(b.x + 10 + i * 9, b.y + 8, 2.5, 2.5, stroke, None))
        out.append(Rect(b.x + 40, b.y + 4, b.w - 50, 8, "#FFFFFF" if v.font_color != "#FFFFFF" else stroke,
                        None, 0, 3))
    elif shape == "mobile":
        out.append(Rect(b.x, b.y, b.w, b.h, fill, stroke, sw, 10))
        out.append(Poly([(b.x + 14, b.y), (b.x + 14, b.b)], None, stroke, sw, closed=False))
        out.append(Poly([(b.r - 14, b.y), (b.r - 14, b.b)], None, stroke, sw, closed=False))
    else:
        out.append(Rect(b.x, b.y, b.w, b.h, fill, stroke, sw, 0, dash))
    if v.icon:
        out += archimate_icon(v.icon, b.r - 20, b.y + 5, "#333333", fill or "#FFFFFF")
    align = "left" if v.align == "left" else "center"
    out.append(Text(nb.lines, nb.text_box, align, "middle"))
    return out


# --------------------------------------------------------------------------- ArchiMate icons (15 x 15)


def archimate_icon(kind: str, x: float, y: float, stroke: str, fill: str) -> list[Prim]:
    def P(pts: list[Point], closed: bool = True, f: str | None = None) -> Poly:
        return Poly([(x + px, y + py) for px, py in pts], f, stroke, 1.0, closed)

    def E(cx: float, cy: float, rx: float, ry: float | None = None, f: str | None = None) -> Ellipse:
        return Ellipse(x + cx, y + cy, rx, ry if ry is not None else rx, f, stroke, 1.0)

    def R(px: float, py: float, w: float, h: float, rx: float = 0, f: str | None = None) -> Rect:
        return Rect(x + px, y + py, w, h, f, stroke, 1.0, rx)

    if kind == "comp":
        return [R(3, 0, 12, 15, f=fill), R(0, 3, 6, 3, f=fill), R(0, 9, 6, 3, f=fill)]
    if kind == "serv":
        return [R(0, 3, 15, 9, 4.5)]
    if kind == "proc":
        return [P([(0, 5), (9, 5), (9, 1.5), (15, 7.5), (9, 13.5), (9, 10), (0, 10)])]
    if kind == "func":
        return [P([(0, 4), (7.5, 0), (15, 4), (15, 15), (7.5, 11), (0, 15)])]
    if kind == "interaction":
        return [Poly(_arc(x + 6.5, y + 7.5, 6, 6.5, math.pi / 2, 1.5 * math.pi), None, stroke, 1.0, True),
                Poly(_arc(x + 8.5, y + 7.5, 6, 6.5, -math.pi / 2, math.pi / 2), None, stroke, 1.0, True)]
    if kind in ("event", "event2"):
        pts = [(0, 2), (11, 2)] + [(11 + 4 * math.cos(a), 7.5 + 5.5 * math.sin(a)) for a in
                                    [-math.pi / 2 + math.pi * i / 8 for i in range(9)]] + [(0, 13), (4, 7.5)]
        return [P(pts)]
    if kind == "interface":
        return [E(10, 7.5, 4.5), P([(0, 7.5), (5.5, 7.5)], False)]
    if kind == "actor":
        return [E(7.5, 2.5, 2.5), P([(7.5, 5), (7.5, 10)], False), P([(3, 7), (12, 7)], False),
                P([(3.5, 15), (7.5, 10), (11.5, 15)], False)]
    if kind == "role":
        return [P([(4, 2), (12, 2)] + [(12 + 3 * math.cos(a), 7.5 + 5.5 * math.sin(a)) for a in
                                         [-math.pi / 2 + math.pi * i / 8 for i in range(9)]] + [(4, 13)], True),
                E(4, 7.5, 3, 5.5)]
    if kind == "collab":
        return [E(5, 7.5, 5), E(10, 7.5, 5)]
    if kind in ("passive", "contract", "representation", "product"):
        prims: list[Prim] = [R(0, 2, 15, 11), P([(0, 5.5), (15, 5.5)], False)]
        if kind == "contract":
            prims.append(P([(0, 9.5), (15, 9.5)], False))
        if kind == "product":
            prims = [R(0, 2, 15, 11), R(0, 2, 7, 3.5)]
        return prims
    if kind == "node":
        return [P([(0, 4), (4, 0), (15, 0), (15, 11), (11, 15), (0, 15)]), P([(0, 4), (11, 4), (11, 15)], False),
                P([(11, 4), (15, 0)], False)]
    if kind == "device":
        return [R(1, 0, 13, 10, 1.5), P([(3, 10), (0, 14), (15, 14), (12, 10)])]
    if kind == "sysSw":
        return [E(9, 6, 5.5), E(6, 9, 5.5, f=fill)]
    if kind == "artifact":
        return [P([(2, 0), (10, 0), (13, 3), (13, 15), (2, 15)]), P([(10, 0), (10, 3), (13, 3)], False)]
    if kind == "netw":
        return [P([(4, 3), (14, 3), (11, 12), (1, 12)]), E(4, 3, 1.6, f=stroke), E(14, 3, 1.6, f=stroke),
                E(11, 12, 1.6, f=stroke), E(1, 12, 1.6, f=stroke)]
    if kind == "path":
        return [P([(2, 7.5), (13, 7.5)], False), P([(4, 4.5), (1, 7.5), (4, 10.5)], False),
                P([(11, 4.5), (14, 7.5), (11, 10.5)], False)]
    if kind == "equipment":
        return [E(5, 9, 4), E(11, 4, 3)]
    if kind == "facility":
        return [P([(0, 15), (0, 0), (3, 0), (3, 8), (7, 5), (7, 8), (11, 5), (11, 8), (15, 5), (15, 15)])]
    if kind == "workPackage":
        return [Poly(_arc(x + 7.5, y + 7.5, 6, 6, 0.3, 2 * math.pi - 0.2), None, stroke, 1.0, False),
                P([(13.5, 3.5), (13.5, 7.5), (9.5, 7.5)], False)]
    if kind == "deliverable":
        wave = [(15 - 15 * i / 12, 12 + 2 * math.sin(math.pi * 2 * i / 12)) for i in range(13)]
        return [P([(0, 1), (15, 1)] + wave)]
    if kind == "plateau":
        return [P([(4, 3), (15, 3)], False), P([(2, 7.5), (13, 7.5)], False), P([(0, 12), (11, 12)], False)]
    if kind == "gap":
        return [E(7.5, 7.5, 5), P([(0, 6), (15, 6)], False), P([(0, 9), (15, 9)], False)]
    if kind == "driver":
        prims = [E(7.5, 7.5, 6), E(7.5, 7.5, 1.5, f=stroke)]
        for i in range(8):
            a = math.pi * i / 4
            prims.append(P([(7.5 + 1.5 * math.cos(a), 7.5 + 1.5 * math.sin(a)),
                            (7.5 + 7.5 * math.cos(a), 7.5 + 7.5 * math.sin(a))], False))
        return prims
    if kind in ("goal", "outcome"):
        prims = [E(7.5, 7.5, 7), E(7.5, 7.5, 4.5), E(7.5, 7.5, 2, f=stroke)]
        if kind == "outcome":
            prims.append(P([(7.5, 7.5), (14, 1)], False))
        return prims
    if kind == "principle":
        return [R(0, 0, 15, 15), P([(7.5, 3), (7.5, 9.5)], False), E(7.5, 12, 0.8, f=stroke)]
    if kind in ("requirement", "constraint"):
        prims = [P([(3, 2), (15, 2), (12, 13), (0, 13)])]
        if kind == "constraint":
            prims.append(P([(6, 2), (3, 13)], False))
        return prims
    if kind == "assess":
        return [E(9, 6, 5), P([(5.5, 9.5), (0, 15)], False)]
    if kind == "meaning":
        return [Ellipse(x + 7.5, y + 7.5, 7, 5, None, stroke, 1.0)]
    if kind == "amValue":
        return [Ellipse(x + 7.5, y + 7.5, 7.5, 4.5, None, stroke, 1.0)]
    if kind == "capability":
        return [R(10, 0, 5, 5), R(5, 5, 5, 5), R(10, 5, 5, 5), R(0, 10, 5, 5), R(5, 10, 5, 5), R(10, 10, 5, 5)]
    if kind == "resource":
        return [R(0, 3, 13, 9, 2), R(13, 5.5, 2, 4), P([(4, 5), (4, 10)], False), P([(7, 5), (7, 10)], False)]
    if kind == "valueStream":
        return [P([(0, 3), (11, 3), (15, 7.5), (11, 12), (0, 12), (4, 7.5)])]
    if kind == "course":
        return [E(10, 5, 4.5), E(10, 5, 1.5, f=stroke), P([(0, 15), (6, 9)], False)]
    if kind == "location":
        return [Poly([(x + 7.5, y + 15)] + _arc(x + 7.5, y + 5.5, 5, 5.5, math.pi * 0.8, math.pi * 2.2, 12),
                     None, stroke, 1.0, True)]
    if kind == "grouping":
        return [Poly([(x, y + 4), (x, y), (x + 8, y), (x + 8, y + 4), (x + 15, y + 4), (x + 15, y + 15),
                      (x, y + 15), (x, y + 4), (x + 8, y + 4)], None, stroke, 1.0, False, "2 2")]
    return []


# --------------------------------------------------------------------------- edges


def _marker(kind: str, tip: Point, frm: Point, color: str, width: float) -> tuple[list[Prim], float]:
    """Arrowhead primitives at `tip` for a segment coming from `frm`; returns (prims, setback)."""
    dx, dy = tip[0] - frm[0], tip[1] - frm[1]
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    px, py = -uy, ux

    def at(back: float, side: float) -> Point:
        return (tip[0] - ux * back + px * side, tip[1] - uy * back + py * side)

    if kind in ("block", "block_open"):
        f = color if kind == "block" else "#FFFFFF"
        return [Poly([tip, at(10, 4.5), at(10, -4.5)], f, color, width)], 10.0
    if kind == "block_thin":
        return [Poly([tip, at(10, 3.5), at(10, -3.5)], color, color, width)], 9.0
    if kind == "open":
        return [Poly([at(9, 5), tip, at(9, -5)], None, color, width, closed=False)], 0.0
    if kind in ("diamond", "diamond_open"):
        f = color if kind == "diamond" else "#FFFFFF"
        return [Poly([tip, at(7, 4.5), at(14, 0), at(7, -4.5)], f, color, width)], 14.0
    if kind == "dot":
        c = at(3.5, 0)
        return [Ellipse(c[0], c[1], 3.5, 3.5, color, color, width)], 7.0
    return [], 0.0


def edge_prims(points: list[Point], vis: EdgeVisual) -> list[Prim]:
    pts = list(points)
    if len(pts) < 2:
        return []
    end_prims, end_back = _marker(vis.end, pts[-1], pts[-2], vis.stroke, vis.width)
    start_prims, start_back = _marker(vis.start, pts[0], pts[1], vis.stroke, vis.width)

    def setback(a: Point, b: Point, dist: float) -> Point:
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        if d <= dist or dist <= 0:
            return b if dist <= 0 else a
        return (b[0] - (b[0] - a[0]) * dist / d, b[1] - (b[1] - a[1]) * dist / d)

    pts[-1] = setback(pts[-2], pts[-1], end_back)
    pts[0] = setback(pts[1], pts[0], start_back)
    return [Poly(_rounded_corners(pts), None, vis.stroke, vis.width, closed=False, dash=vis.dash)] + start_prims + \
        end_prims


def _rounded_corners(pts: list[Point], r: float = 6.0) -> list[Point]:
    """Small rounded bends, like draw.io's rounded=1."""
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        d1 = math.hypot(b[0] - a[0], b[1] - a[1])
        d2 = math.hypot(c[0] - b[0], c[1] - b[1])
        rr = min(r, d1 / 2, d2 / 2)
        if rr < 1:
            out.append(b)
            continue
        p1 = (b[0] - (b[0] - a[0]) * rr / d1, b[1] - (b[1] - a[1]) * rr / d1)
        p2 = (b[0] + (c[0] - b[0]) * rr / d2, b[1] + (c[1] - b[1]) * rr / d2)
        for i in range(5):
            t = i / 4
            # quadratic Bezier with control point at the corner
            out.append(((1 - t) ** 2 * p1[0] + 2 * (1 - t) * t * b[0] + t * t * p2[0],
                        (1 - t) ** 2 * p1[1] + 2 * (1 - t) * t * b[1] + t * t * p2[1]))
    out.append(pts[-1])
    return out


# --------------------------------------------------------------------------- full scene


def build_scene(layout: Layout) -> Scene:
    scene = Scene(layout.width, layout.height)
    P = scene.prims
    P.append(Rect(0, 0, layout.width, layout.height, "#FFFFFF", None, 0))

    for gb in layout.groups.values():
        v, b = gb.visual, gb.box
        if v.strip:
            P.append(Rect(b.x, b.y, b.w, b.h, v.fill, v.stroke, v.stroke_width))
            P.append(Rect(b.x, b.y, STRIP, b.h, v.header_fill, v.stroke, v.stroke_width))
            lines = layout_runs([Run(gb.title, 12, True, v.font_color)], max(b.h - 16, 40), max_lines=2)
            P.append(Text(lines, Box(b.x, b.y, STRIP, b.h), "center", "middle", rotate=True))
        else:
            fill = None if v.fill == "none" else v.fill
            P.append(Rect(b.x, b.y, b.w, b.h, fill, v.stroke, v.stroke_width, 6 if v.rounded else 0,
                          v.dash if v.dashed else None))
            runs = [Run(gb.title, 12, True, v.font_color)]
            lines = layout_runs(runs, b.w - 20, max_lines=1)
            if gb.caption:
                lines += layout_runs([Run(f"[{gb.caption}]", 10, False, "#666666")], b.w - 20, max_lines=1)
            P.append(Text(lines, Box(b.x + 10, b.y + 6, b.w - 20, 30), "left", "top"))

    for er in layout.edges:
        P.extend(edge_prims(er.points, er.visual))

    for nb in layout.nodes.values():
        P.extend(node_prims(nb))

    for er in layout.edges:
        if er.label_box and er.lines:
            lb = er.label_box
            P.append(Rect(lb.x, lb.y, lb.w, lb.h, "#FFFFFF", None, 0, 2))
            P.append(Text(er.lines, lb, "center", "middle"))

    if layout.title_box and layout.title_lines:
        P.append(Text(layout.title_lines, layout.title_box, "left", "top"))

    if layout.legend:
        lg = layout.legend
        b = lg.box
        P.append(Rect(b.x, b.y, b.w, b.h, "#FFFFFF", "#B3B3B3", 1, 4))
        P.append(Text(layout_runs([Run(lg.title, 11, True, "#333333")], b.w), Box(b.x + 12, b.y + 8, b.w - 24, 16),
                      "left", "top"))
        for i, item in enumerate(lg.items):
            cy = b.y + 34 + i * lg.row_h
            P.extend(edge_prims([(b.x + 12, cy), (b.x + 56, cy)], item.visual))
            P.append(Text(layout_runs([Run(item.name, 10, False, "#333333")], b.w - 70), Box(b.x + 64, cy - 8,
                                                                                               b.w - 70, 16),
                          "left", "middle"))
    return scene


