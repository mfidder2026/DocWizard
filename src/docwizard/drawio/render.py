"""SVG and PNG (Pillow) backends for a Scene."""

from __future__ import annotations

import io
import math
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw

from .scene import Ellipse, Poly, Rect, Scene, Text
from .text import FONT_FAMILY, LINE_HEIGHT, Line, get_font

Point = tuple[float, float]


def _baselines(text: Text) -> list[tuple[Line, float]]:
    """Baseline y for each line (cap-height centred within its line box)."""
    total = sum(ln.size * LINE_HEIGHT for ln in text.lines)
    if text.rotate:
        extent = text.box.h
        start = 0.0
    else:
        extent = text.box.h
        start = text.box.y
    top = start + ((extent - total) / 2 if text.valign == "middle" else 0)
    out = []
    for ln in text.lines:
        lh = ln.size * LINE_HEIGHT
        out.append((ln, top + lh / 2 + ln.size * 0.36))
        top += lh
    return out


def _fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------------- SVG


def scene_to_svg(scene: Scene, title: str = "") -> str:
    w, h = scene.width, scene.height
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt(w)}" height="{_fmt(h)}" '
        f'viewBox="0 0 {_fmt(w)} {_fmt(h)}" font-family="{FONT_FAMILY}">'
    ]
    if title:
        out.append(f"<title>{escape(title)}</title>")
    for p in scene.prims:
        if isinstance(p, Rect):
            attrs = _paint(p.fill, p.stroke, p.width, p.dash)
            rx = f' rx="{_fmt(p.rx)}"' if p.rx else ""
            out.append(f'<rect x="{_fmt(p.x)}" y="{_fmt(p.y)}" width="{_fmt(p.w)}" height="{_fmt(p.h)}"{rx}{attrs}/>')
        elif isinstance(p, Poly):
            pts = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in p.points)
            tag = "polygon" if p.closed else "polyline"
            fill = p.fill if p.closed else None
            out.append(f'<{tag} points="{pts}"{_paint(fill, p.stroke, p.width, p.dash)} stroke-linejoin="round"/>')
        elif isinstance(p, Ellipse):
            out.append(f'<ellipse cx="{_fmt(p.cx)}" cy="{_fmt(p.cy)}" rx="{_fmt(p.rx)}" ry="{_fmt(p.ry)}"'
                       f'{_paint(p.fill, p.stroke, p.width, None)}/>')
        elif isinstance(p, Text):
            out.append(_svg_text(p))
    out.append("</svg>")
    return "\n".join(out)


def _paint(fill: str | None, stroke: str | None, width: float, dash: str | None) -> str:
    s = f' fill="{fill or "none"}"'
    if stroke and width > 0:
        s += f' stroke="{stroke}" stroke-width="{_fmt(width)}"'
        if dash:
            s += f' stroke-dasharray="{dash}"'
    else:
        s += ' stroke="none"'
    return s


def _svg_text(t: Text) -> str:
    parts = []
    if t.rotate:
        # Rotate -90 degrees around the box centre; lay out along the box height.
        cx, cy = t.box.cx, t.box.cy
        group = f'<g transform="translate({_fmt(cx)},{_fmt(cy)}) rotate(-90)">'
        total = sum(ln.size * LINE_HEIGHT for ln in t.lines)
        top = -total / 2
        for ln in t.lines:
            lh = ln.size * LINE_HEIGHT
            base = top + lh / 2 + ln.size * 0.36
            parts.append(_tspan_text(ln, 0, base, "middle"))
            top += lh
        return group + "".join(parts) + "</g>"
    for ln, base in _baselines(t):
        if t.align == "left":
            parts.append(_tspan_text(ln, t.box.x, base, "start"))
        else:
            parts.append(_tspan_text(ln, t.box.cx, base, "middle"))
    return "".join(parts)


def _tspan_text(ln: Line, x: float, y: float, anchor: str) -> str:
    weight = ' font-weight="bold"' if ln.bold else ""
    return (f'<text x="{_fmt(x)}" y="{_fmt(y)}" font-size="{_fmt(ln.size)}"{weight} fill="{ln.color}" '
            f'text-anchor="{anchor}">{escape(ln.text)}</text>')


# --------------------------------------------------------------------------- PNG


def _dash_segments(points: list[Point], pattern: list[float], closed: bool) -> list[list[Point]]:
    pts = points + ([points[0]] if closed else [])
    segs: list[list[Point]] = []
    idx, remaining, on = 0, pattern[0], True
    cur: list[Point] = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        seg_len = math.hypot(b[0] - a[0], b[1] - a[1])
        pos = 0.0
        while seg_len - pos > 1e-9:
            step = min(remaining, seg_len - pos)
            pos += step
            f = pos / seg_len
            p = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            if on:
                cur.append(p)
            remaining -= step
            if remaining <= 1e-9:
                if on and len(cur) > 1:
                    segs.append(cur)
                on = not on
                idx = (idx + 1) % len(pattern)
                remaining = pattern[idx]
                cur = [p]
    if on and len(cur) > 1:
        segs.append(cur)
    return segs


class _PilCanvas:
    def __init__(self, scene: Scene, scale: float) -> None:
        self.k = scale
        self.img = Image.new("RGB", (max(1, math.ceil(scene.width * scale)), max(1, math.ceil(scene.height * scale))),
                             "#FFFFFF")
        self.draw = ImageDraw.Draw(self.img)

    def _p(self, pts: list[Point]) -> list[Point]:
        return [(x * self.k, y * self.k) for x, y in pts]

    def _lw(self, w: float) -> int:
        return max(1, round(w * self.k))

    def _stroke_path(self, pts: list[Point], color: str, width: float, dash: str | None, closed: bool) -> None:
        lw = self._lw(width)
        if dash:
            pattern = [float(v) * self.k * max(width, 1.0) for v in dash.replace(",", " ").split()]
            if any(v <= 0 for v in pattern):
                pattern = [3 * self.k, 3 * self.k]
            for seg in _dash_segments(self._p(pts), pattern, closed):
                self.draw.line(seg, fill=color, width=lw, joint="curve")
            return
        sp = self._p(pts + ([pts[0]] if closed else []))
        self.draw.line(sp, fill=color, width=lw, joint="curve")
        if lw > 2:  # round caps
            r = lw / 2
            for x, y in (sp[0], sp[-1]):
                self.draw.ellipse([x - r + 0.5, y - r + 0.5, x + r - 0.5, y + r - 0.5], fill=color)

    def rect(self, p: Rect) -> None:
        k = self.k
        box = [p.x * k, p.y * k, (p.x + p.w) * k, (p.y + p.h) * k]
        if p.rx:
            if p.fill:
                self.draw.rounded_rectangle(box, radius=p.rx * k, fill=p.fill)
            if p.stroke and p.width > 0:
                if p.dash:
                    from .scene import _round_rect_points
                    from .route import Box
                    pts = _round_rect_points(Box(p.x, p.y, p.w, p.h), p.rx)
                    self._stroke_path(pts, p.stroke, p.width, p.dash, True)
                else:
                    self.draw.rounded_rectangle(box, radius=p.rx * k, outline=p.stroke, width=self._lw(p.width))
            return
        if p.fill:
            self.draw.rectangle(box, fill=p.fill)
        if p.stroke and p.width > 0:
            pts = [(p.x, p.y), (p.x + p.w, p.y), (p.x + p.w, p.y + p.h), (p.x, p.y + p.h)]
            if p.dash:
                self._stroke_path(pts, p.stroke, p.width, p.dash, True)
            else:
                self.draw.rectangle(box, outline=p.stroke, width=self._lw(p.width))

    def poly(self, p: Poly) -> None:
        if p.closed and p.fill:
            self.draw.polygon(self._p(p.points), fill=p.fill)
        if p.stroke and p.width > 0:
            self._stroke_path(p.points, p.stroke, p.width, p.dash, p.closed)

    def ellipse(self, p: Ellipse) -> None:
        k = self.k
        box = [(p.cx - p.rx) * k, (p.cy - p.ry) * k, (p.cx + p.rx) * k, (p.cy + p.ry) * k]
        self.draw.ellipse(box, fill=p.fill, outline=p.stroke if p.stroke else None,
                          width=self._lw(p.width) if p.stroke else 0)

    def text(self, t: Text) -> None:
        k = self.k
        if t.rotate:
            total = sum(ln.size * LINE_HEIGHT for ln in t.lines)
            tw = max(1, math.ceil(t.box.h * k))
            th = max(1, math.ceil(total * k) + 4)
            layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            top = 0.0
            for ln in t.lines:
                lh = ln.size * LINE_HEIGHT
                base = top + lh / 2 + ln.size * 0.36
                d.text((tw / 2, base * k), ln.text, font=get_font(ln.size * k, ln.bold), fill=ln.color, anchor="ms")
                top += lh
            layer = layer.rotate(90, expand=True)
            x = round(t.box.cx * k - layer.width / 2)
            y = round(t.box.cy * k - layer.height / 2)
            self.img.paste(layer, (x, y), layer)
            return
        for ln, base in _baselines(t):
            font = get_font(ln.size * k, ln.bold)
            if t.align == "left":
                self.draw.text((t.box.x * k, base * k), ln.text, font=font, fill=ln.color, anchor="ls")
            else:
                self.draw.text((t.box.cx * k, base * k), ln.text, font=font, fill=ln.color, anchor="ms")


def scene_to_png(scene: Scene, scale: float = 2.0) -> bytes:
    """Rasterise with 2x supersampling for anti-aliased edges, then downsample."""
    supersample = 2 if scene.width * scene.height * scale * scale * 4 < 60_000_000 else 1
    canvas = _PilCanvas(scene, scale * supersample)
    for p in scene.prims:
        if isinstance(p, Rect):
            canvas.rect(p)
        elif isinstance(p, Poly):
            canvas.poly(p)
        elif isinstance(p, Ellipse):
            canvas.ellipse(p)
        elif isinstance(p, Text):
            canvas.text(p)
    img = canvas.img
    if supersample > 1:
        img = img.resize((max(1, round(scene.width * scale)), max(1, round(scene.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    dpi = round(96 * scale)
    img.save(buf, format="PNG", optimize=True, dpi=(dpi, dpi))
    return buf.getvalue()
