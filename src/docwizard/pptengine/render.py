"""Pillow preview renderer for SlideScenes (approximate, but same positions, colours and wrapped lines)."""

from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw

from .scene import EMU_PER_PT, ChartP, Line, Para, Picture, Shape, SlideScene, TableP, TextBox
from .spec import fmt_number
from .textfit import pil_font, text_width, wrap
from .themes import rgb

SS = 2  # supersampling factor


class _Canvas:
    def __init__(self, w_emu: int, h_emu: int, width_px: int, bg: str):
        self.k = width_px * SS / w_emu  # px per EMU
        self.kp = self.k * EMU_PER_PT  # px per pt
        self.size = (width_px * SS, max(1, round(h_emu * self.k)))
        self.img = Image.new("RGB", self.size, rgb(bg))
        self.d = ImageDraw.Draw(self.img)

    def px(self, e: float) -> float:
        return e * self.k

    def font(self, family: str, size_pt: float, bold: bool = False, italic: bool = False):
        return pil_font(family, size_pt * self.kp, bold, italic)


# --------------------------------------------------------------------------- primitives


def _shape(c: _Canvas, s: Shape) -> None:
    box = [c.px(s.x), c.px(s.y), c.px(s.x + s.w), c.px(s.y + s.h)]
    fill = rgb(s.fill) if s.fill else None
    outline = rgb(s.line) if s.line else None
    width = max(1, round(s.line_w * c.kp)) if s.line else 0
    if s.kind == "ellipse":
        c.d.ellipse(box, fill=fill, outline=outline, width=width)
    elif s.kind == "round":
        c.d.rounded_rectangle(box, radius=max(1, s.radius * c.kp), fill=fill, outline=outline, width=width)
    else:
        c.d.rectangle(box, fill=fill, outline=outline, width=width)


def _line(c: _Canvas, ln: Line) -> None:
    c.d.line([c.px(ln.x1), c.px(ln.y1), c.px(ln.x2), c.px(ln.y2)], fill=rgb(ln.color),
             width=max(1, round(ln.width * c.kp)))


def _para_lines(p: Para, width_pt: float) -> list[str]:
    return wrap(p.shown, p.family, p.size, max(width_pt - p.indent - p.spacing * max(0, len(p.shown) - 1), p.size),
                p.bold or p.lead > 0, p.italic) if p.shown else [""]


def _draw_text_run(c: _Canvas, x: float, y: float, text: str, p: Para, bold: bool) -> float:
    font = c.font(p.family, p.size, bold, p.italic)
    if p.spacing:
        for ch in text:
            c.d.text((x, y), ch, font=font, fill=rgb(p.color), anchor="ls")
            x += font.getlength(ch) + p.spacing * c.kp
        return x
    c.d.text((x, y), text, font=font, fill=rgb(p.color), anchor="ls")
    return x + font.getlength(text)


def draw_textbox(c: _Canvas, t: TextBox) -> None:
    x0, y0, w_pt = c.px(t.x), c.px(t.y), t.w / EMU_PER_PT
    laid = [(p, _para_lines(p, w_pt)) for p in t.paras]
    total = sum(len(ls) * p.line + (p.space_before if i else 0) for i, (p, ls) in enumerate(laid))
    h_pt = t.h / EMU_PER_PT
    top = {"t": 0.0, "m": (h_pt - total) / 2, "b": h_pt - total}.get(t.anchor, 0.0)
    y = y0 + top * c.kp
    for i, (p, lines) in enumerate(laid):
        if i:
            y += p.space_before * c.kp
        offset = 0
        for li, ln in enumerate(lines):
            base = y + (p.line * 0.5 + p.size * 0.36) * c.kp
            avail = (w_pt - p.indent) * c.kp
            lw = text_width(ln, p.family, p.size, p.bold or p.lead > 0, p.italic) / 1.02 * c.kp
            xx = x0 + p.indent * c.kp
            if p.align == "c":
                xx += (avail - lw) / 2
            elif p.align == "r":
                xx += avail - lw
            if li == 0 and p.bullet and p.bullet != "none":
                bx = x0 + p.bullet_at * c.kp
                col = rgb(p.bullet_color or p.color)
                if p.bullet in ("■", "inherit") and p.level == 0:
                    sq = p.size * 0.30 * c.kp
                    cy = base - p.size * 0.33 * c.kp
                    if p.bullet == "inherit":
                        c.d.ellipse([bx, cy - sq / 2, bx + sq, cy + sq / 2], fill=col)
                    else:
                        c.d.rectangle([bx, cy - sq / 2, bx + sq, cy + sq / 2], fill=col)
                else:
                    c.d.text((bx, base), "–", font=c.font(p.family, p.size), fill=col, anchor="ls")
            lead_left = max(0, p.lead - offset)
            if lead_left:
                xx = _draw_text_run(c, xx, base, ln[:lead_left], p, True)
                if ln[lead_left:]:
                    _draw_text_run(c, xx, base, ln[lead_left:], p, p.bold)
            else:
                _draw_text_run(c, xx, base, ln, p, p.bold)
            offset += len(ln) + 1
            y += p.line * c.kp


def _picture(c: _Canvas, p: Picture) -> None:
    try:
        im = Image.open(io.BytesIO(p.blob))
        im = im.convert("RGBA")
    except Exception:
        return
    w, h = max(1, round(c.px(p.w))), max(1, round(c.px(p.h)))
    im = im.resize((w, h), Image.LANCZOS)
    c.img.paste(im, (round(c.px(p.x)), round(c.px(p.y))), im)


def _table(c: _Canvas, t: TableP) -> None:
    y = t.y
    for j, h in enumerate(t.row_h):
        vals = t.header if j == 0 else t.rows[j - 1]
        fill = t.header_fill if j == 0 else (t.zebra if j % 2 == 0 else t.background)
        x = t.x
        for ci, w in enumerate(t.col_w):
            c.d.rectangle([c.px(x), c.px(y), c.px(x + w), c.px(y + h)], fill=rgb(fill))
            txt = vals[ci] if ci < len(vals) else ""
            bold = j == 0
            size = t.size
            inner = w / EMU_PER_PT - 2 * t.pad - 2
            lines = wrap(txt, t.family, size, inner, bold)
            pitch = size * 1.2
            th = len(lines) * pitch
            ty = y / EMU_PER_PT + (h / EMU_PER_PT - th) / 2
            font = c.font(t.family, size, bold)
            col = rgb(t.header_text if j == 0 else t.text)
            for ln in lines:
                base = (ty + pitch * 0.5 + size * 0.36) * c.kp
                lx = c.px(x) + t.pad * c.kp
                if t.numeric and t.numeric[ci] and j > 0:
                    lx = c.px(x + w) - t.pad * c.kp - font.getlength(ln)
                c.d.text((lx, base), ln, font=font, fill=col, anchor="ls")
                ty += pitch
            x += w
        if 0 < j < len(t.row_h) - 1:
            c.d.line([c.px(t.x), c.px(y + h), c.px(t.x + sum(t.col_w)), c.px(y + h)], fill=rgb(t.line),
                     width=max(1, round(0.75 * c.kp)))
        y += h
    c.d.line([c.px(t.x), c.px(y), c.px(t.x + sum(t.col_w)), c.px(y)], fill=rgb(t.header_fill),
             width=max(1, round(1.5 * c.kp)))


# --------------------------------------------------------------------------- chart


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * e >= v:
            return m * e
    return 10 * e


def _fmt(v: float, unit: str) -> str:
    s = fmt_number(v, 0 if float(v).is_integer() else 1)
    return s + "%" if unit.strip() == "%" else s


def _chart(c: _Canvas, cp: ChartP) -> None:
    ch = cp.chart
    kp = c.kp
    X, Y, W, H = c.px(cp.x), c.px(cp.y), c.px(cp.w), c.px(cp.h)
    size = cp.size
    font = c.font(cp.family, size)
    bold = c.font(cp.family, size, True)
    small = c.font(cp.family, size - 1)
    text, muted, grid = rgb(cp.text), rgb(cp.muted), rgb(cp.grid)
    pal = [rgb(x) for x in cp.palette]
    multi = len(ch.series) > 1
    if ch.kind == "pie":
        vals = [max(0.0, v or 0.0) for v in ch.series[0].values]
        tot = sum(vals) or 1.0
        leg_w = min(W * 0.4, max(font.getlength(x) for x in ch.categories) + 30 * kp)
        d = min(W - leg_w - 20 * kp, H) * 0.92
        cx, cy = X + (W - leg_w) / 2, Y + H / 2
        bb = [cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2]
        a = -90.0
        for i, v in enumerate(vals):
            sweep = v / tot * 360
            c.d.pieslice(bb, a, a + sweep, fill=pal[i % len(pal)], outline=rgb(cp.background), width=round(1.5 * kp))
            mid = math.radians(a + sweep / 2)
            r = d * 0.5 * 0.79
            if v / tot >= 0.04:
                c.d.text((cx + r * math.cos(mid), cy + r * math.sin(mid)), f"{round(v / tot * 100)}%", font=bold,
                         fill=(255, 255, 255), anchor="mm")
            a += sweep
        hole = d * 0.58 / 2
        c.d.ellipse([cx - hole, cy - hole, cx + hole, cy + hole], fill=rgb(cp.background))
        ly = cy - len(vals) * size * 1.6 * kp / 2
        for i, cat in enumerate(ch.categories):
            lx = X + W - leg_w + 10 * kp
            c.d.rectangle([lx, ly + 3 * kp, lx + 8 * kp, ly + 11 * kp], fill=pal[i % len(pal)])
            c.d.text((lx + 14 * kp, ly + 7 * kp), cat, font=font, fill=text, anchor="lm")
            ly += size * 1.6 * kp
        return
    top = Y + (size * 2.2 * kp if multi else size * 1.2 * kp)
    if multi:
        lx = X
        for i, s in enumerate(ch.series):
            c.d.rectangle([lx, Y + 2 * kp, lx + 8 * kp, Y + 10 * kp], fill=pal[i % len(pal)])
            c.d.text((lx + 13 * kp, Y + 6 * kp), s.name, font=font, fill=text, anchor="lm")
            lx += font.getlength(s.name) + 32 * kp
    n = len(ch.categories)
    vals = [[v or 0.0 for v in s.values] for s in ch.series]
    stacked = ch.kind == "stacked"
    vmax = max([sum(col) for col in zip(*vals)] if stacked else [v for s in vals for v in s] + [0])
    vmin = min([v for s in vals for v in s] + [0])
    hi = _nice_max(vmax)
    lo = -_nice_max(-vmin) if vmin < 0 else 0.0
    labels = (not multi) and n <= 12 and ch.kind != "line"
    if ch.kind == "bar":
        lab_w = min(W * 0.32, max(font.getlength(x) for x in ch.categories) + 10 * kp)
        px0, px1 = X + lab_w, X + W - 40 * kp
        band = (Y + H - top) / n
        span = (hi - lo) or 1
        for i, cat in enumerate(ch.categories):
            cy = top + band * (i + 0.5)
            c.d.text((px0 - 8 * kp, cy), cat, font=font, fill=text, anchor="rm")
            k = len(vals)
            bh = band * 0.62 / k
            for si, sv in enumerate(vals):
                v = sv[i]
                y0 = cy - band * 0.31 + si * bh
                xa = px0 + (0 - lo) / span * (px1 - px0)
                xb = px0 + (v - lo) / span * (px1 - px0)
                c.d.rectangle([min(xa, xb), y0, max(xa, xb), y0 + bh], fill=pal[si % len(pal)])
                if labels:
                    c.d.text((max(xa, xb) + 5 * kp, y0 + bh / 2), _fmt(v, ch.unit), font=bold, fill=text, anchor="lm")
        c.d.line([px0, top, px0, Y + H], fill=grid, width=max(1, round(kp)))
        return
    base_h = size * 2.0 * kp
    py0, py1 = Y + H - base_h, top + (size * 1.4 * kp if labels else 0)
    left = X if labels else X + max(small.getlength(_fmt(hi, ch.unit)), small.getlength(_fmt(lo, ch.unit))) + 8 * kp
    span = (hi - lo) or 1

    def yv(v: float) -> float:
        return py0 - (v - lo) / span * (py0 - py1)

    if not labels:
        for k in range(5):
            v = lo + span * k / 4
            c.d.line([left, yv(v), X + W, yv(v)], fill=grid, width=max(1, round(0.75 * kp)))
            c.d.text((left - 6 * kp, yv(v)), _fmt(v, ch.unit), font=small, fill=muted, anchor="rm")
    band = (X + W - left) / n
    for i, cat in enumerate(ch.categories):
        cx = left + band * (i + 0.5)
        lab = cat
        while font.getlength(lab) > band * 0.98 and len(lab) > 3:
            lab = lab[:-2] + "…"
        c.d.text((cx, py0 + 8 * kp), lab, font=font, fill=text, anchor="mt")
    if ch.kind == "line":
        for si, sv in enumerate(vals):
            pts = [(left + band * (i + 0.5), yv(v)) for i, v in enumerate(sv)]
            c.d.line(pts, fill=pal[si % len(pal)], width=round(2.75 * kp), joint="curve")
            for x, y in pts:
                r = 3.5 * kp
                c.d.ellipse([x - r, y - r, x + r, y + r], fill=pal[si % len(pal)], outline=rgb(cp.background))
    else:
        k = 1 if stacked else len(vals)
        bw = (band - band * (0.8 / 1.8 if not multi else 0.6 / 2.6)) / k
        for i in range(n):
            x0 = left + band * i + (band - bw * k) / 2
            acc = 0.0
            for si, sv in enumerate(vals):
                v = sv[i]
                if stacked:
                    ya, yb = yv(acc), yv(acc + v)
                    acc += v
                    xa = x0
                else:
                    ya, yb = yv(0), yv(v)
                    xa = x0 + si * bw
                c.d.rectangle([xa, min(ya, yb), xa + bw - (1 if k > 1 else 0), max(ya, yb)], fill=pal[si % len(pal)])
                if labels:
                    c.d.text((xa + bw / 2, min(ya, yb) - 4 * kp), _fmt(v, ch.unit), font=bold, fill=text, anchor="mb")
    c.d.line([left, yv(0), X + W, yv(0)], fill=grid, width=max(1, round(kp)))


# --------------------------------------------------------------------------- scene


def render_scene(sc: SlideScene, w_emu: int, h_emu: int, width_px: int = 1280) -> bytes:
    c = _Canvas(w_emu, h_emu, width_px, sc.background)
    for prim in list(sc.decor) + list(sc.prims):
        if isinstance(prim, Shape):
            _shape(c, prim)
        elif isinstance(prim, Line):
            _line(c, prim)
        elif isinstance(prim, TextBox):
            draw_textbox(c, prim)
        elif isinstance(prim, Picture):
            _picture(c, prim)
        elif isinstance(prim, TableP):
            _table(c, prim)
        elif isinstance(prim, ChartP):
            _chart(c, prim)
    out = c.img.resize((width_px, max(1, round(c.size[1] / SS))), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()

