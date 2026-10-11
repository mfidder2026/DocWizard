"""Slide layouts: turn each spec slide into one or more SlideScenes on a 12-column grid.

All measurements here are in points (a 16:9 slide is 960 x 540 pt). Every text block is fitted with
`textfit` (largest size that fits, never below the role's minimum); bullet slides, tables and timelines that
do not fit even at the minimum size are split into continuation slides ("(vervolg)").
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from PIL import Image as PILImage

from .scene import Para, Picture, SlideScene, TableP, ChartP, TextBox, emu, line, rect, textbox
from .spec import Bullet, Column, DeckSpec, Slide, parse_number, shorten
from .textfit import P, fit, longest_word, measure, text_width, truncate_to_fit, wrap
from .themes import Theme, mix

if TYPE_CHECKING:  # pragma: no cover
    from .template import TemplateInfo

ImageResolver = Callable[[str], "tuple[bytes, str] | None"]

SQUARE = "■"  # ■
DASH = "–"  # –


# --------------------------------------------------------------------------- grid


@dataclass
class Frame:
    W: float = 960.0
    H: float = 540.0
    ml: float = 60.0
    mr: float = 60.0
    gutter: float = 24.0
    kicker_y: float = 38.0
    title_y: float = 56.0
    title_h: float = 76.0
    content_top: float = 168.0
    content_bottom: float = 468.0
    source_y: float = 478.0
    footer_y: float = 506.0
    title_w: float = 0.0  # 0 = 10 columns

    @property
    def cw(self) -> float:
        return self.W - self.ml - self.mr

    @property
    def col(self) -> float:
        return (self.cw - 11 * self.gutter) / 12

    def span(self, n: int) -> float:
        return n * self.col + (n - 1) * self.gutter

    def x(self, i: int) -> float:
        """Left edge of column i (0-based)."""
        return self.ml + i * (self.col + self.gutter)

    @classmethod
    def for_size(cls, w: float, h: float) -> "Frame":
        if abs(w - 960) < 1 and abs(h - 540) < 1:
            return cls()
        sx, sy = w / 960, h / 540
        f = cls(W=w, H=h, ml=60 * sx, mr=60 * sx, gutter=24 * sx)
        for k in ("kicker_y", "title_y", "title_h", "content_top", "content_bottom", "source_y", "footer_y"):
            setattr(f, k, getattr(cls(), k) * sy)
        return f


@dataclass
class Area:
    x: float
    y: float
    w: float
    h: float

    @property
    def b(self) -> float:
        return self.y + self.h

    @property
    def r(self) -> float:
        return self.x + self.w


@dataclass
class Ctx:
    deck: DeckSpec
    theme: Theme
    frame: Frame
    images: ImageResolver | None = None
    tpl: "TemplateInfo | None" = None
    warnings: list[str] = field(default_factory=list)
    split: int = 0


# --------------------------------------------------------------------------- text helpers


def _p(text: str, family: str, color: str, **kw) -> P:
    return P(text=text, family=family, color=color, **kw)


def _lead(text: str) -> int:
    """Bold lead-in for 'Label: explanation' bullets."""
    i = text.find(": ")
    return i + 1 if 2 <= i <= 38 and i < len(text) - 6 else 0


def bullet_paras(bullets: list[Bullet], th: Theme, *, sub_rel: float = 0.82, gap: float = 0.6,
                 color: str | None = None, marker: str = SQUARE, inherit: bool = False) -> list[P]:
    out: list[P] = []
    for b in bullets:
        out.append(_p(b.text, th.body_font, color or th.text, indent=1.0, bullet_at=0.0, space_before=gap,
                      line=1.18, bullet=marker, bullet_color=th.accent, lead=_lead(b.text), level=0))
        for s in b.sub:
            out.append(_p(s, th.body_font, th.muted, rel=sub_rel, indent=2.0, bullet_at=1.0, space_before=0.25,
                          line=1.18, bullet=DASH, bullet_color=th.muted, level=1))
    return out


def _fit_or_truncate(p: P, w: float, h: float, mx: float, mn: float, ctx: Ctx, what: str,
                     max_lines: int | None = None) -> tuple[P, float, float]:
    f = fit([p], w, h, mx, mn, max_lines=max_lines)
    if not f.ok:
        p = P(**{**p.__dict__, "text": truncate_to_fit(p, w, h if max_lines is None else min(
            h, max_lines * p.line * mn * p.rel), mn * p.rel)})
        ctx.warnings.append(f"{what}: tekst ingekort om te passen")
        f = fit([p], w, h, mn, mn)
    return p, f.size, f.height


# --------------------------------------------------------------------------- common chrome


def _scene(ctx: Ctx, s: Slide, role: str, bg: str | None = None) -> SlideScene:
    sc = SlideScene(kind=s.type, background=bg or ctx.theme.background, notes=s.notes, title=s.title)
    if ctx.tpl is not None:
        role = ctx.tpl.resolve_role(role)
        sc.background = ctx.tpl.background_for(role)
        sc.decor = ctx.tpl.decor(role)
    sc.layout = role
    return sc


def header(sc: SlideScene, s: Slide, ctx: Ctx, title: str | None = None) -> Area:
    """Kicker + action title + optional subtitle; returns the content area below it."""
    th, fr = ctx.theme, ctx.frame
    title = s.title if title is None else title
    if ctx.tpl is not None:
        return _tpl_header(sc, s, ctx, title)
    tw = fr.title_w or fr.span(10)
    if s.kicker:
        sc.add(textbox(fr.ml, fr.kicker_y, fr.span(8), 14, [
            _p(shorten(s.kicker, 60), th.body_font, th.accent, bold=True, caps=True, spacing=1.2, line=1.2)],
            th.sizes.kicker, name="kicker"))
    tp = _p(title, th.heading_font, th.heading_color, bold=th.heading_bold, line=1.1)
    tp, size, h = _fit_or_truncate(tp, tw, fr.title_h, *th.sizes.title, ctx, f"titel '{shorten(title, 30)}'",
                                   max_lines=3)
    sc.add(textbox(fr.ml, fr.title_y, tw, fr.title_h, [tp], size, name="title", placeholder="title"))
    top = fr.content_top
    if s.subtitle:
        y = fr.title_y + h + 8
        sp = _p(s.subtitle, th.body_font, th.muted, line=1.2)
        sp, ss, sh = _fit_or_truncate(sp, tw, 44, *th.sizes.subtitle, ctx, "ondertitel", max_lines=2)
        sc.add(textbox(fr.ml, y, tw, sh + 2, [sp], ss, name="subtitle"))
        top = max(top, y + sh + 20)
    return Area(fr.ml, top, fr.cw, fr.content_bottom - top)


def _tpl_header(sc: SlideScene, s: Slide, ctx: Ctx, title: str) -> Area:
    tpl, th = ctx.tpl, ctx.theme
    assert tpl is not None
    tr = tpl.ph_rect(sc.layout, "title")
    content = tpl.content_rect(sc.layout)
    if tr is None:  # blank layout: draw our own title in the content area's top
        tr = Area(content.x, max(20.0, content.y - 70), content.w, 60)
    tp = _p(title, tpl.title_font or th.heading_font, tpl.title_color or th.heading_color, line=1.08,
            align=tpl.title_align)
    tp, size, h = _fit_or_truncate(tp, tr.w, tr.h, min(tpl.title_size, 32), min(20, tpl.title_size), ctx,
                                   f"titel '{shorten(title, 30)}'", max_lines=3)
    sc.add(textbox(tr.x, tr.y, tr.w, tr.h, [tp], size, anchor=tpl.title_anchor,
                   placeholder="title" if tpl.ph_rect(sc.layout, "title") else None, name="title"))
    top = max(content.y, tr.y + tr.h + 8)
    if s.subtitle:
        sp = _p(s.subtitle, th.body_font, th.muted, line=1.2)
        sp, ss, sh = _fit_or_truncate(sp, content.w, 40, *th.sizes.subtitle, ctx, "ondertitel", max_lines=2)
        sc.add(textbox(content.x, top, content.w, sh + 2, [sp], ss, name="subtitle"))
        top += sh + 14
    return Area(content.x, top, content.w, content.b - top)


def footer(sc: SlideScene, s: Slide, ctx: Ctx) -> None:
    th, fr = ctx.theme, ctx.frame
    if s.source:
        src = s.source if s.source.lower().startswith(("bron", "source")) else (
            f"Bron: {s.source}" if ctx.deck.language.startswith("nl") else f"Source: {s.source}")
        if ctx.tpl is not None:
            c = ctx.tpl.content_rect(sc.layout)
            x, y, w = c.x, c.b + 4, c.w
        else:
            x, y, w = fr.ml, fr.source_y, fr.cw
        sp = _p(src, th.body_font, th.muted, line=1.2)
        sp, size, _ = _fit_or_truncate(sp, w, 13, 9, 8, ctx, "bronvermelding", max_lines=1)
        sc.add(textbox(x, y, w, 13, [sp], size, name="source"))
    if ctx.tpl is not None:
        sc.meta["slidenum"] = True
        return
    sc.add(textbox(fr.ml, fr.footer_y, fr.span(8), 12, [
        _p(shorten(ctx.deck.title, 90), th.body_font, th.muted, line=1.2)], th.sizes.footer, name="footer"))
    sc.add(textbox(fr.W - fr.mr - 60, fr.footer_y, 60, 12, [
        _p("0", th.body_font, th.muted, line=1.2, align="r", bold=True)], th.sizes.footer, name="slidenum"))


def _box_para(sc: SlideScene, x: float, y: float, w: float, h: float, ps: list[P], mx: float, mn: float,
              ctx: Ctx, what: str, anchor: str = "t", max_lines: int | None = None,
              placeholder: str | None = None) -> float:
    """Fit a group of paragraphs; on overflow drop trailing paragraphs (warning). Returns used height."""
    f = fit(ps, w, h, mx, mn, max_lines=max_lines)
    while not f.ok and len(ps) > 1:
        ps = ps[:-1]
        ctx.warnings.append(f"{what}: inhoud ingekort om te passen")
        f = fit(ps, w, h, mx, mn, max_lines=max_lines)
    if not f.ok:
        p, size, hh = _fit_or_truncate(ps[0], w, h, mx, mn, ctx, what, max_lines)
        ps, f.size, f.height = [p], size, hh
    sc.add(textbox(x, y, w, h, ps, f.size, anchor=anchor, placeholder=placeholder))
    return f.height


# --------------------------------------------------------------------------- slide types


def lay_title(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th, fr, deck = ctx.theme, ctx.frame, ctx.deck
    sc = _scene(ctx, s, "title")
    meta = " · ".join(x for x in (deck.author, deck.date) if x)
    if ctx.tpl is not None and sc.layout == "title":
        tpl = ctx.tpl
        tr = tpl.ph_rect("title", "title")
        tp = _p(s.title, tpl.title_font or th.heading_font, tpl.title_color or th.heading_color, line=1.05,
                align=tpl.ctr_title_align)
        tp, size, _ = _fit_or_truncate(tp, tr.w, tr.h, min(tpl.ctr_title_size, 44), 24, ctx, "titeldia",
                                       max_lines=3)
        sc.add(textbox(tr.x, tr.y, tr.w, tr.h, [tp], size, anchor=tpl.ctr_title_anchor, placeholder="title"))
        sr = tpl.ph_rect("title", "subtitle")
        if sr is not None:
            ps = [_p(x, th.body_font, th.muted, line=1.2, align=tpl.subtitle_align)
                  for x in (s.subtitle, meta) if x]
            if ps:
                ps[-1].rel = 0.8 if len(ps) > 1 else 1.0
                ps[-1].space_before = 0.6
                _box_para(sc, sr.x, sr.y, sr.w, sr.h, ps, min(tpl.subtitle_size, 24), 12, ctx, "titeldia",
                          anchor=tpl.subtitle_anchor, placeholder="subtitle")
        return [sc]
    if ctx.tpl is not None:  # template without a title layout: big title in the content area
        c = ctx.tpl.content_rect(sc.layout)
        tp = _p(s.title, th.heading_font, th.heading_color, line=1.05)
        tp, size, h = _fit_or_truncate(tp, c.w, c.h * 0.6, 40, 24, ctx, "titeldia", max_lines=3)
        sc.add(textbox(c.x, c.y + c.h * 0.2, c.w, h + 4, [tp], size,
                       placeholder="title" if ctx.tpl.ph_rect(sc.layout, "title") else None))
        if s.subtitle or meta:
            ps = [_p(x, th.body_font, th.muted, line=1.2) for x in (s.subtitle, meta) if x]
            _box_para(sc, c.x, c.y + c.h * 0.2 + h + 16, c.w, 70, ps, 20, 12, ctx, "titeldia")
        return [sc]

    # Built-in design: calm left column, colour panel on the right.
    panel_x = fr.x(8) + fr.col / 2
    sc.add(rect(panel_x, 0, fr.W - panel_x, fr.H, th.title_panel, name="panel"))
    sc.add(rect(panel_x, fr.H - 120, 8, 120, th.accent2 if not th.dark else th.accent, name="panel accent"))
    tw = panel_x - fr.ml - 48
    tp = _p(s.title, th.heading_font, th.heading_color, bold=th.heading_bold, line=1.05)
    tp, size, h = _fit_or_truncate(tp, tw, 200, *th.sizes.deck_title, ctx, "titeldia", max_lines=4)
    sub_h = 0.0
    sp = None
    if s.subtitle:
        sp = _p(s.subtitle, th.body_font, th.muted, line=1.25)
        sp, ssize, sub_h = _fit_or_truncate(sp, tw, 80, 20, 15, ctx, "ondertitel", max_lines=3)
    kick_h = 30 if s.kicker else 0
    block = kick_h + h + (20 + sub_h if sp else 0)
    top = max(110.0, (fr.H - block) / 2 - 20)
    sc.add(rect(fr.ml, top - 26, 44, 4, th.accent, name="accent bar"))
    if s.kicker:
        sc.add(textbox(fr.ml, top, tw, 16, [_p(shorten(s.kicker, 60), th.body_font, th.accent, bold=True, caps=True,
                                                spacing=1.4, line=1.2)], 12, name="kicker"))
    sc.add(textbox(fr.ml, top + kick_h, tw, h + 4, [tp], size, name="title", placeholder="title"))
    if sp is not None:
        sc.add(textbox(fr.ml, top + kick_h + h + 20, tw, sub_h + 4, [sp], ssize, name="subtitle"))
    if meta:
        sc.add(line(fr.ml, fr.H - 78, fr.ml + tw, fr.H - 78, th.line, 0.75))
        sc.add(textbox(fr.ml, fr.H - 66, tw, 16, [_p(meta, th.body_font, th.muted, line=1.2)], 12, name="meta"))
    return [sc]


def lay_section(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th, fr = ctx.theme, ctx.frame
    num = f"{s.number:02d}" if s.number else ""
    if ctx.tpl is not None:
        sc = _scene(ctx, s, "section")
        if sc.layout == "section":
            tpl = ctx.tpl
            tr = tpl.ph_rect("section", "title")
            tp = _p(s.title, tpl.title_font or th.heading_font, tpl.title_color or th.heading_color, line=1.05,
                    align=tpl.section_align)
            tp, size, _ = _fit_or_truncate(tp, tr.w, tr.h, min(tpl.title_size, 40), 22, ctx, "sectiedia", 3)
            sc.add(textbox(tr.x, tr.y, tr.w, tr.h, [tp], size, anchor=tpl.section_anchor, placeholder="title"))
            br = tpl.ph_rect("section", "body")
            sub = s.subtitle or (f"Deel {s.number}" if ctx.deck.language.startswith("nl") else f"Part {s.number}")
            if br is not None and sub:
                _box_para(sc, br.x, br.y, br.w, br.h, [_p(sub, th.body_font, th.muted, line=1.2,
                                                          align=tpl.section_align)],
                          min(tpl.body_size, 20), 12, ctx, "sectiedia", anchor="t", placeholder="body")
            return [sc]
        # no section layout: draw our own divider inside the template's title-only layout
        c = tpl_area(ctx, sc)
        y = c.y + c.h * 0.15
        if num:
            sc.add(textbox(c.x, y, c.w, 80, [_p(num, th.heading_font, th.accent, line=1.0)], 72, name="number"))
            y += 96
        sc.add(rect(c.x, y, 48, 4, th.accent))
        tp = _p(s.title, th.heading_font, th.heading_color, line=1.08)
        tp, size, h = _fit_or_truncate(tp, c.w, c.b - y - 20, 36, 22, ctx, "sectiedia", 3)
        sc.add(textbox(c.x, y + 18, c.w, h + 4, [tp], size,
                       placeholder="title" if ctx.tpl.ph_rect(sc.layout, "title") else None))
        return [sc]

    sc = _scene(ctx, s, "section", bg=th.section_bg)
    tw = fr.span(9)
    if num:
        sc.add(textbox(fr.ml, 112, fr.span(4), 110, [_p(num, th.heading_font, th.section_number, line=1.0)], 110,
                       name="number"))
    sc.add(rect(fr.ml, 262, 48, 4, th.section_number))
    tp = _p(s.title, th.heading_font, th.on_section, bold=th.heading_bold, line=1.06)
    tp, size, h = _fit_or_truncate(tp, tw, 130, *th.sizes.section, ctx, "sectiedia", max_lines=3)
    sc.add(textbox(fr.ml, 284, tw, h + 4, [tp], size, name="title", placeholder="title"))
    if s.subtitle:
        sp = _p(s.subtitle, th.body_font, mix(th.on_section, th.section_bg, 0.25), line=1.25)
        sp, ss, sh = _fit_or_truncate(sp, tw, 60, 20, 14, ctx, "sectiedia", 2)
        sc.add(textbox(fr.ml, 284 + h + 14, tw, sh + 4, [sp], ss, name="subtitle"))
    return [sc]


def tpl_area(ctx: Ctx, sc: SlideScene) -> Area:
    assert ctx.tpl is not None
    return ctx.tpl.content_rect(sc.layout)


def lay_agenda(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th = ctx.theme
    sc = _scene(ctx, s, "title_only")
    a = header(sc, s, ctx)
    items = [b.text for b in s.bullets] or ["—"]
    two = len(items) > 7
    cols = [items[: (len(items) + 1) // 2], items[(len(items) + 1) // 2:]] if two else [items]
    gap = 40.0
    cw = (a.w - gap) / 2 if two else min(a.w, ctx.frame.span(9) if ctx.tpl is None else a.w)
    rows = max(len(c) for c in cols)
    row_h = min(64.0 if rows <= 4 else 54.0, a.h / max(rows, 4))
    num_w = 46.0
    # One common text size for all rows.
    size = 22.0
    for c in cols:
        for it in c:
            f = fit([_p(it, th.body_font, th.text, line=1.12)], cw - num_w, row_h - 10, size, 13, max_lines=2)
            size = min(size, f.size)
    n = 0
    for ci, c in enumerate(cols):
        x = a.x + ci * (cw + gap)
        for ri, it in enumerate(c):
            n += 1
            y = a.y + ri * row_h
            sc.add(line(x, y, x + cw, y, th.line, 0.75))
            sc.add(textbox(x, y, num_w, row_h, [_p(f"{n:02d}", th.body_font, th.accent, bold=True, line=1.1)],
                           size * 0.9, anchor="m"))
            p = _p(it, th.body_font, th.text, line=1.12)
            p.text = truncate_to_fit(p, cw - num_w, row_h - 6, size) if not fit(
                [p], cw - num_w, row_h - 6, size, size).ok else p.text
            sc.add(textbox(x + num_w, y, cw - num_w, row_h, [p], size, anchor="m"))
        y = a.y + len(c) * row_h
        sc.add(line(x, y, x + cw, y, th.line, 0.75))
    footer(sc, s, ctx)
    return [sc]


def _cont(title: str, i: int, lang: str) -> str:
    return title if i == 0 else f"{title} ({'vervolg' if lang.startswith('nl') else 'continued'})"


def lay_bullets(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th = ctx.theme
    if not s.bullets:
        return lay_statement(s, ctx)
    tpl_body = ctx.tpl is not None and ctx.tpl.resolve_role("content") == "content"
    role = "content" if tpl_body else "title_only"
    marker = "inherit" if tpl_body else SQUARE
    mx, mn = th.sizes.body
    if tpl_body:
        mx = min(ctx.tpl.body_size, mx)
    lang = ctx.deck.language

    def make(title: str, first: bool) -> tuple[SlideScene, Area]:
        sc = _scene(ctx, s, role)
        sc.title = title
        a = header(sc, Slide(s.type, title, s.subtitle if first else "", s.kicker), ctx, title)
        if ctx.tpl is None:
            a = Area(a.x, a.y, min(a.w, ctx.frame.span(10)), a.h)
        return sc, a

    _, a0 = make(s.title, True)
    _, a1 = make(_cont(s.title, 1, lang), False)
    chunks = [s.bullets]
    if not fit(bullet_paras(s.bullets, th, marker=marker), a0.w, a0.h, mx, mn).ok:
        # Split: greedily fill slides at a comfortable size (a little above the minimum).
        comfy = min(mx, mn + 3)
        chunks, cur = [], []
        for b in s.bullets:
            a = a0 if not chunks else a1
            if cur and not fit(bullet_paras(cur + [b], th, marker=marker), a.w, a.h, comfy, comfy).ok:
                chunks.append(cur)
                cur = []
            cur.append(b)
        if cur:
            chunks.append(cur)
        if len(chunks) > 1:
            ctx.split += len(chunks) - 1
            ctx.warnings.append(f"dia '{shorten(s.title, 40)}' gesplitst over {len(chunks)} dia's")
    out = []
    for i, chunk in enumerate(chunks):
        title = _cont(s.title, i, lang)
        sc, a = make(title, i == 0)
        if i:
            sc.notes = ("(Vervolg van de vorige dia.)" if lang.startswith("nl") else "(Continued.)")
        _box_para(sc, a.x, a.y, a.w, a.h, bullet_paras(chunk, th, marker=marker), mx, mn, ctx,
                  f"dia '{shorten(title, 30)}'", placeholder="body" if tpl_body else None)
        footer(sc, s, ctx)
        out.append(sc)
    return out


def lay_statement(s: Slide, ctx: Ctx) -> list[SlideScene]:
    """A bullets slide without bullets: one big statement."""
    th, fr = ctx.theme, ctx.frame
    sc = _scene(ctx, s, "title_only")
    if ctx.tpl is not None:
        a = header(sc, Slide(s.type, s.title, s.subtitle), ctx)
        footer(sc, s, ctx)
        return [sc]
    if s.kicker:
        sc.add(textbox(fr.ml, fr.kicker_y, fr.span(8), 14, [
            _p(s.kicker, th.body_font, th.accent, bold=True, caps=True, spacing=1.2, line=1.2)], th.sizes.kicker))
    tw = fr.span(10)
    tp = _p(s.title, th.heading_font, th.heading_color, bold=th.heading_bold, line=1.1)
    tp, size, h = _fit_or_truncate(tp, tw, 200, 40, 26, ctx, "statement", max_lines=4)
    sh, sp, ss = 0.0, None, 0.0
    if s.subtitle:
        sp = _p(s.subtitle, th.body_font, th.muted, line=1.25)
        sp, ss, sh = _fit_or_truncate(sp, tw, 90, 20, 15, ctx, "statement", 3)
    block = h + (sh + 22 if sp else 0)
    top = (fr.H - block) / 2 - 10
    sc.add(rect(fr.ml, top - 28, 44, 4, th.accent))
    sc.add(textbox(fr.ml, top, tw, h + 4, [tp], size, placeholder="title", name="title"))
    if sp:
        sc.add(textbox(fr.ml, top + h + 22, tw, sh + 4, [sp], ss))
    footer(sc, s, ctx)
    return [sc]


def _column_block(sc: SlideScene, col: Column, x: float, y: float, w: float, h: float, size: float, ctx: Ctx,
                  heading_color: str | None = None) -> None:
    th = ctx.theme
    top = y
    if col.heading:
        hp = _p(col.heading, th.body_font, heading_color or th.accent, bold=True, line=1.15)
        hp, hs, hh = _fit_or_truncate(hp, w, 48, size * 1.05, 13, ctx, "kolomkop", 2)
        sc.add(textbox(x, y, w, hh + 2, [hp], hs))
        y += hh + 8
        sc.add(line(x, y, x + w, y, th.line, 0.75))
        sc.add(rect(x, y - 1.25, 36, 2.5, heading_color or th.accent))
        y += 14
    if col.bullets:
        ps = bullet_paras(col.bullets, th, gap=0.55)
        _box_para(sc, x, y, w, top + h - y, ps, size, size, ctx, "kolom")


def _columns_size(cols: list[Column], w: float, h: float, ctx: Ctx, mx: float, mn: float) -> float:
    th = ctx.theme
    size = mx
    for c in cols:
        head = 0.0
        if c.heading:
            head = measure([_p(c.heading, th.body_font, th.accent, bold=True, line=1.15)], mx * 1.05, w)[0] + 22
        f = fit(bullet_paras(c.bullets, th, gap=0.55), w, h - head, mx, mn)
        size = min(size, f.size)
    return size


def _column_height(c: Column, w: float, size: float, ctx: Ctx) -> float:
    th = ctx.theme
    h = 0.0
    if c.heading:
        h += measure([_p(c.heading, th.body_font, th.accent, bold=True, line=1.15)], size * 1.05, w)[0] + 22
    if c.bullets:
        h += measure(bullet_paras(c.bullets, th, gap=0.55), size, w)[0]
    return h


def lay_two_column(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th = ctx.theme
    if ctx.tpl is not None and ctx.tpl.resolve_role("two") == "two":
        return _tpl_two(s, ctx, s.columns[0], s.columns[1], "")
    sc = _scene(ctx, s, "title_only")
    a = header(sc, s, ctx)
    gap = ctx.frame.gutter * 2
    w = (a.w - gap) / 2
    mx, mn = th.sizes.column
    size = _columns_size(s.columns, w, a.h, ctx, mx, mn)
    for i, c in enumerate(s.columns[:2]):
        _column_block(sc, c, a.x + i * (w + gap), a.y, w, a.h, size, ctx)
    footer(sc, s, ctx)
    return [sc]


def _verdict(sc: SlideScene, text: str, x: float, y: float, w: float, h: float, ctx: Ctx) -> None:
    th = ctx.theme
    sc.add(rect(x, y, w, h, th.accent, kind="round", radius=4))
    vp = _p(f"→  {text}", th.body_font, th.on_accent, bold=True, line=1.15)
    vp, vs, vh = _fit_or_truncate(vp, w - 40, h - 12, 17, 12, ctx, "conclusie", 2)
    sc.add(textbox(x + 20, y, w - 40, h, [vp], vs, anchor="m"))


def lay_comparison(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th = ctx.theme
    comp = s.comparison
    assert comp is not None
    if ctx.tpl is not None and ctx.tpl.resolve_role("two") == "two":
        return _tpl_two(s, ctx, comp.left, comp.right, comp.verdict)
    sc = _scene(ctx, s, "title_only")
    a = header(sc, s, ctx)
    vh = 52.0 if comp.verdict else 0.0
    gap = ctx.frame.gutter
    card_h = a.h - (vh + 16 if vh else 0)
    w = (a.w - gap) / 2
    pad = 22.0
    mx, mn = th.sizes.column
    size = _columns_size([comp.left, comp.right], w - 2 * pad, card_h - 2 * pad - 6, ctx, mx, mn)
    need = max(_column_height(c, w - 2 * pad, size, ctx) for c in (comp.left, comp.right)) + 2 * pad + 10
    card_h = min(card_h, max(need, 150.0))
    for i, (c, col) in enumerate(((comp.left, th.muted), (comp.right, th.accent))):
        x = a.x + i * (w + gap)
        sc.add(rect(x, a.y, w, card_h, th.surface, kind="round", radius=4))
        sc.add(rect(x, a.y, w, 4, col))
        _column_block(sc, c, x + pad, a.y + pad + 4, w - 2 * pad, card_h - 2 * pad - 4, size, ctx,
                      heading_color=th.text if i == 0 else th.accent)
    if comp.verdict:
        _verdict(sc, comp.verdict, a.x, a.y + card_h + 16, a.w, vh, ctx)
    footer(sc, s, ctx)
    return [sc]


def _tpl_two(s: Slide, ctx: Ctx, left: Column, right: Column, verdict: str) -> list[SlideScene]:
    th, tpl = ctx.theme, ctx.tpl
    assert tpl is not None
    sc = _scene(ctx, s, "two")
    _tpl_header(sc, Slide(s.type, s.title), ctx, s.title)
    r1, r2 = tpl.ph_rect("two", "body"), tpl.ph_rect("two", "body2")
    assert r1 is not None and r2 is not None
    top = r1.y
    if s.subtitle:
        sp = _p(s.subtitle, th.body_font, th.muted, line=1.2)
        sp, ss, sh = _fit_or_truncate(sp, r2.r - r1.x, 40, *th.sizes.subtitle, ctx, "ondertitel", 2)
        sc.add(textbox(r1.x, top, r2.r - r1.x, sh + 2, [sp], ss))
        top += sh + 12
    bottom = max(r1.b, r2.b)
    vh = 48.0 if verdict else 0.0
    if verdict:
        bottom -= vh + 14
    mx = min(tpl.body_size, th.sizes.column[0] + 2)
    mn = th.sizes.column[1]

    def paras(c: Column) -> list[P]:
        ps = bullet_paras(c.bullets, th, marker="inherit", gap=0.5)
        if c.heading:
            ps.insert(0, _p(c.heading, th.body_font, th.accent, bold=True, line=1.15, bullet="none", rel=1.05))
        return ps

    size = mx
    for r, c in ((r1, left), (r2, right)):
        size = min(size, fit(paras(c), r.w, bottom - top, mx, mn).size)
    if verdict:  # verdict bar directly under the columns, not at the bottom of the slide
        need = max(measure(paras(c), size, r.w)[0] for r, c in ((r1, left), (r2, right)))
        bottom = min(bottom, top + need + 12)
        _verdict(sc, verdict, r1.x, bottom + 14, r2.r - r1.x, vh, ctx)
    for key, r, c in (("body", r1, left), ("body2", r2, right)):
        _box_para(sc, r.x, top, r.w, bottom - top, paras(c), size, size, ctx, "kolom", placeholder=key)
    footer(sc, s, ctx)
    return [sc]


def lay_kpi(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th = ctx.theme
    sc = _scene(ctx, s, "title_only")
    a = header(sc, s, ctx)
    n = len(s.kpis)
    gap = ctx.frame.gutter
    w = (a.w - gap * (n - 1)) / n
    pad = 22.0
    inner = w - 2 * pad
    # Common value size: the largest at which every value fits on one line.
    vmax, vmin = th.sizes.kpi
    vmax = min(vmax, 72 if n <= 2 else vmax)
    vsize = vmax
    for k in s.kpis:
        vsize = min(vsize, fit([_p(k.value, th.figure_font, th.accent, line=1.0)], inner, vmax * 1.05, vmax, vmin,
                               max_lines=1).size)
    lsize = 17.0
    dsize = 13.0
    for k in s.kpis:
        if k.label:
            lsize = min(lsize, fit([_p(k.label, th.body_font, th.text, bold=True, line=1.15)], inner, 60, 17, 12).size)
    dsize = min(dsize, lsize - 2)
    need = 0.0
    for k in s.kpis:
        hh = pad + 8 + vsize * 1.05 + 14
        if k.label:
            hh += measure([_p(k.label, th.body_font, th.text, bold=True, line=1.15)], lsize, inner)[0] + 8
        if k.detail:
            hh += measure([_p(k.detail, th.body_font, th.muted, line=1.25)], dsize, inner)[0]
        need = max(need, hh + pad)
    card_h = min(a.h, max(need, 190.0))
    y0 = a.y + max(0.0, (a.h - card_h) / 2 - 24)
    for i, k in enumerate(s.kpis):
        x = a.x + i * (w + gap)
        sc.add(rect(x, y0, w, card_h, th.surface, kind="round", radius=4))
        sc.add(rect(x, y0, w, 4, th.accent))
        y = y0 + pad + 8
        vp = _p(k.value, th.figure_font, th.accent, line=1.0)
        own = vsize
        f1 = fit([vp], inner, vsize * 1.05, vsize, 16, max_lines=1)
        if not f1.ok:  # a very long figure: shrink this card only (never cut a number)
            vp.text = truncate_to_fit(vp, inner, 16 * 1.05, 16)
            ctx.warnings.append(f"kpi '{k.value}' ingekort")
            own = 16
        else:
            own = f1.size
        sc.add(textbox(x + pad, y, inner, vsize * 1.05, [vp], own, anchor="b"))
        y += vsize * 1.05 + 14
        if k.label:
            lp = _p(k.label, th.body_font, th.text, bold=True, line=1.15)
            lp, ls, lh = _fit_or_truncate(lp, inner, 60, lsize, 12, ctx, "kpi-label", 3)
            sc.add(textbox(x + pad, y, inner, lh + 2, [lp], ls))
            y += lh + 8
        if k.detail:
            dp = _p(k.detail, th.body_font, th.muted, line=1.25)
            avail = y0 + card_h - pad - y
            dp, ds, dh = _fit_or_truncate(dp, inner, avail, dsize, min(10, dsize), ctx, "kpi-toelichting")
            sc.add(textbox(x + pad, y, inner, avail, [dp], ds))
    footer(sc, s, ctx)
    return [sc]


def lay_quote(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th, fr = ctx.theme, ctx.frame
    q = s.quote
    assert q is not None
    sc = _scene(ctx, s, "title_only")
    if s.title or ctx.tpl is not None:
        a = header(sc, s, ctx) if s.title else tpl_area(ctx, sc)
    else:
        a = Area(fr.ml, 90, fr.cw, fr.H - 170)
    mark_w = 74.0
    tw = min(a.w - mark_w, fr.span(9) if ctx.tpl is None else a.w - mark_w)
    qp = _p(q.text, th.heading_font, th.text, line=1.22)
    src_h = 26.0 if q.source else 0.0
    qmax, qmin = th.sizes.quote
    qp, qs, qh = _fit_or_truncate(qp, tw, a.h - src_h - 20, qmax if not s.title else qmax - 4, qmin, ctx, "citaat")
    block = qh + (src_h + 14 if q.source else 0)
    y = a.y + max(0.0, (a.h - block) / 2 - 6)
    sc.add(textbox(a.x - 4, y - 18, mark_w, 122, [_p("“", "Georgia", th.accent, line=1.0)], 120,
                   name="quote mark"))
    sc.add(textbox(a.x + mark_w, y, tw, qh + 4, [qp], qs, name="quote"))
    if q.source:
        sc.add(rect(a.x + mark_w, y + qh + 18, 28, 2, th.accent))
        sp = _p(q.source, th.body_font, th.muted, bold=True, line=1.2)
        sp, ss, sh = _fit_or_truncate(sp, tw - 40, 22, 15, 11, ctx, "citaatbron", 1)
        sc.add(textbox(a.x + mark_w + 40, y + qh + 9, tw - 40, 20, [sp], ss))
    footer(sc, s, ctx)
    return [sc]


def lay_timeline(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th = ctx.theme
    per = 6
    chunks = [s.steps[i: i + per] for i in range(0, len(s.steps), per)]
    if len(chunks) > 1 and len(chunks[-1]) == 1:  # avoid a lonely last step
        chunks[-2:] = [chunks[-2][:-1], [chunks[-2][-1]] + chunks[-1]]
    if len(chunks) > 1:
        ctx.split += len(chunks) - 1
        ctx.warnings.append(f"tijdlijn '{shorten(s.title, 40)}' gesplitst over {len(chunks)} dia's")
    out = []
    for ci, steps in enumerate(chunks):
        title = _cont(s.title, ci, ctx.deck.language)
        sc = _scene(ctx, s, "title_only")
        sc.title = title
        a = header(sc, Slide(s.type, title, s.subtitle if ci == 0 else "", s.kicker), ctx, title)
        n = len(steps)
        colw = a.w / n
        tw = colw - 18
        has_dates = any(st.date for st in steps)
        lab = 20.0
        txt = 15.0
        for st in steps:
            lab = min(lab, fit([_p(st.label, th.body_font, th.text, bold=True, line=1.15)], tw, 50, 20, 12).size)
        for st in steps:
            if st.text:
                txt = min(txt, fit([_p(st.text, th.body_font, th.muted, line=1.25)], tw, a.h * 0.45, 15, 10).size)
        below = max(measure([_p(st.label, th.body_font, th.text, bold=True, line=1.15)], lab, tw)[0] + 10 + (
            measure([_p(st.text, th.body_font, th.muted, line=1.25)], txt, tw)[0] if st.text else 0)
            for st in steps)
        block = (48 if has_dates else 18) + 24 + below
        line_y = a.y + max(0.0, (a.h - block) / 2 - 10) + (48 if has_dates else 18)
        dot = 14.0
        sc.add(line(a.x, line_y, a.r, line_y, th.accent3 if not th.dark else th.line, 2.0))
        for i, st in enumerate(steps):
            x = a.x + i * colw
            first = i == 0 and ci == 0
            sc.add(rect(x, line_y - dot / 2, dot, dot, th.accent if first or not th.dark else th.accent,
                        line=None, kind="ellipse"))
            if not first:
                sc.add(rect(x + 3.5, line_y - dot / 2 + 3.5, dot - 7, dot - 7, th.background, kind="ellipse"))
            if st.date:
                dp = _p(st.date, th.body_font, th.accent, bold=True, line=1.15)
                dp, ds, _ = _fit_or_truncate(dp, tw, 22, 16, 10, ctx, "tijdlijn", 1)
                sc.add(textbox(x, line_y - 40, tw, 22, [dp], ds, anchor="b"))
            y = line_y + 22
            lp = _p(st.label, th.body_font, th.text, bold=True, line=1.15)
            lp, ls, lh = _fit_or_truncate(lp, tw, 50, lab, 12, ctx, "tijdlijn", 2)
            sc.add(textbox(x, y, tw, lh + 2, [lp], ls))
            y += lh + 8
            if st.text:
                tp = _p(st.text, th.body_font, th.muted, line=1.25)
                tp, ts, th_ = _fit_or_truncate(tp, tw, a.b - y, txt, 10, ctx, "tijdlijn")
                sc.add(textbox(x, y, tw, a.b - y, [tp], ts))
        footer(sc, s, ctx)
        if ci:
            sc.notes = "(Vervolg)" if ctx.deck.language.startswith("nl") else "(Continued)"
        out.append(sc)
    return out


# ----- tables


def _is_num(v: str) -> bool:
    n, unit = parse_number(v)
    return n is not None and len(unit) <= 6 and len(v) <= 18


def table_geometry(header: list[str], rows: list[list[str]], w: float, h: float, th: Theme, size: float,
                   pad: float = 6.0) -> tuple[list[float], list[float]] | None:
    """Column widths and row heights at `size`, or None when it does not fit in w x h."""
    fam, hfam = th.body_font, th.body_font
    n = len(header)
    pitch = size * 1.2
    cols = [[header[c]] + [r[c] for r in rows] for c in range(n)]
    nat = [max(text_width(v, fam, size, bold=(j == 0)) for j, v in enumerate(col)) + 2 * pad + 2 for col in cols]
    minw = [max(longest_word(v, fam, size, bold=True) for v in col) + 2 * pad + 2 for col in cols]
    minw = [max(m, 42.0) for m in minw]
    if sum(minw) > w:
        return None
    if sum(nat) <= w:
        widths = [x * w / sum(nat) for x in nat]
        # favour the text-heavy columns a little less: keep proportional to natural width
    else:
        rest = w - sum(minw)
        want = [max(0.0, a - b) for a, b in zip(nat, minw)]
        tot = sum(want) or 1.0
        widths = [m + rest * x / tot for m, x in zip(minw, want)]
    heights = []
    for j, row in enumerate([header] + rows):
        lines = max(len(wrap(v, hfam if j == 0 else fam, size, widths[c] - 2 * pad - 2, bold=(j == 0)))
                    for c, v in enumerate(row))
        heights.append(lines * pitch + 2 * pad)
    if sum(heights) > h:
        return None
    return widths, heights


def lay_table(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th = ctx.theme
    t = s.table
    assert t is not None
    probe = SlideScene("x", "")
    if ctx.tpl is not None:
        probe.layout = ctx.tpl.resolve_role("title_only")
    a = header(probe, s, ctx)
    w = a.w
    smax, smin = th.sizes.table
    # find the largest size at which everything fits; otherwise split rows
    chunks: list[list[list[str]]] = [t.rows]
    geo = None
    size = smax
    while size >= smin:
        geo = table_geometry(t.header, t.rows, w, a.h, th, size)
        if geo:
            break
        size -= 1
    if not geo:
        size = max(smin, 12.0)
        chunks, cur = [], []
        for r in t.rows:
            if cur and not table_geometry(t.header, cur + [r], w, a.h, th, size):
                chunks.append(cur)
                cur = []
            cur.append(r)
        if cur:
            chunks.append(cur)
        ctx.split += len(chunks) - 1
        ctx.warnings.append(f"tabel '{shorten(s.title, 40)}' gesplitst over {len(chunks)} dia's")
    out = []
    numeric = [sum(_is_num(r[c]) for r in t.rows if r[c]) > max(1, len([r for r in t.rows if r[c]])) * 0.6
               for c in range(len(t.header))]
    for ci, rows in enumerate(chunks):
        title = _cont(s.title, ci, ctx.deck.language)
        sc = _scene(ctx, s, "title_only")
        sc.title = title
        a = header(sc, Slide(s.type, title, s.subtitle if ci == 0 else "", s.kicker), ctx, title)
        sz = size
        g = table_geometry(t.header, rows, w, a.h, th, sz)
        while g is None and sz > 8:
            sz -= 1
            g = table_geometry(t.header, rows, w, a.h, th, sz)
        if g is None:
            ctx.warnings.append(f"tabel '{shorten(s.title, 30)}' past niet volledig")
            g = ([w / len(t.header)] * len(t.header), [a.h / (len(rows) + 1)] * (len(rows) + 1))
        widths, heights = g
        spare = (a.h * 0.9 - sum(heights)) / len(heights)
        if spare > 0:
            heights = [x + min(spare, 12.0) for x in heights]
        sc.add(TableP(emu(a.x), emu(a.y), emu(sum(widths)), emu(sum(heights)), t.header, rows,
                      [emu(x) for x in widths], [emu(x) for x in heights], th.body_font, sz, th.body_font,
                      th.text, th.accent, th.on_accent, th.surface, th.background, th.line, numeric))
        footer(sc, s, ctx)
        out.append(sc)
    return out


# ----- chart / image with optional key points


def _side_bullets(sc: SlideScene, s: Slide, a: Area, ctx: Ctx) -> Area:
    """Key points in a side column (4 of 12 columns); returns the remaining area for the visual."""
    th = ctx.theme
    if not s.bullets:
        return a
    sw = (a.w - ctx.frame.gutter * 2) * 0.34
    x = a.r - sw
    sc.add(line(x, a.y, x + sw, a.y, th.accent, 2.0))
    _box_para(sc, x, a.y + 16, sw, a.h - 16, bullet_paras(s.bullets, th, gap=0.6), 17, 12, ctx, "kernpunten")
    return Area(a.x, a.y, a.w - sw - ctx.frame.gutter * 2, a.h)


def lay_chart(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th = ctx.theme
    c = s.chart
    assert c is not None
    sc = _scene(ctx, s, "title_only")
    a = header(sc, s, ctx)
    a = _side_bullets(sc, s, a, ctx)
    if c.unit and c.kind != "pie":
        unit = c.unit
        nl = ctx.deck.language.startswith("nl")
        if unit.strip() == "%":
            txt = "In procenten" if nl else "In percent"
        elif unit[:1].isalpha():
            txt = unit[:1].upper() + unit[1:]
        else:
            txt = f"In {unit}"
        sc.add(textbox(a.x, a.y, a.w, 16, [_p(txt, th.body_font, th.muted, line=1.2)], 11))
        a = Area(a.x, a.y + 20, a.w, a.h - 20)
    sc.add(ChartP(emu(a.x), emu(a.y), emu(a.w), emu(a.h), c, th.chart, th.body_font, th.sizes.chart, th.text,
                  th.muted, th.line, th.background))
    footer(sc, s, ctx)
    return [sc]


def _image_blob(ref: str, ctx: Ctx) -> tuple[bytes, str, int, int] | None:
    if ctx.images is None:
        return None
    try:
        res = ctx.images(ref)
    except Exception as exc:  # resolver errors must not break the deck
        ctx.warnings.append(f"afbeelding '{ref}': {exc}")
        return None
    if not res:
        return None
    blob, ext = res
    try:
        with PILImage.open(io.BytesIO(blob)) as im:
            w, h = im.size
            fmt = (im.format or ext or "png").lower()
    except Exception:
        ctx.warnings.append(f"afbeelding '{ref}' heeft een onbekend formaat")
        return None
    ext = {"jpeg": "jpg", "mpo": "jpg"}.get(fmt, fmt)
    return blob, ext, w, h


def lay_image(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th = ctx.theme
    img = s.image
    assert img is not None
    sc = _scene(ctx, s, "title_only")
    a = header(sc, s, ctx)
    a = _side_bullets(sc, s, a, ctx)
    cap_h = 0.0
    if img.caption:
        cap_h = 22.0
    box = Area(a.x, a.y, a.w, a.h - cap_h)
    got = _image_blob(img.ref, ctx)
    if got is None:
        ctx.warnings.append(f"afbeelding '{img.ref}' niet gevonden; tijdelijke plaatshouder gebruikt")
        sc.add(rect(box.x, box.y, box.w, box.h, th.surface, line=th.line, kind="round", radius=4))
        label = ("Afbeelding: " if ctx.deck.language.startswith("nl") else "Image: ") + img.ref
        sc.add(textbox(box.x, box.y, box.w, box.h, [_p(label, th.body_font, th.muted, line=1.2, align="c")], 14,
                       anchor="m"))
        ix, iw, iy, ih = box.x, box.w, box.y, box.h
    else:
        blob, ext, pw, ph = got
        scale = min(box.w / pw, box.h / ph, 2.5)
        iw, ih = pw * scale, ph * scale
        ix = box.x + (box.w - iw) / 2 if not s.bullets else box.x
        iy = box.y + (box.h - ih) / 2 if s.type == "image" else box.y
        sc.add(Picture(emu(ix), emu(iy), emu(iw), emu(ih), blob, ext, name=img.ref))
    if img.caption:
        cp = _p(img.caption, th.body_font, th.muted, line=1.2, italic=False)
        cp, cs, _ = _fit_or_truncate(cp, max(iw, 200), 18, 12, 9, ctx, "bijschrift", 1)
        sc.add(textbox(ix, iy + ih + 6, max(iw, 200), 18, [cp], cs))
    footer(sc, s, ctx)
    return [sc]


def lay_closing(s: Slide, ctx: Ctx) -> list[SlideScene]:
    th, fr, deck = ctx.theme, ctx.frame, ctx.deck
    if ctx.tpl is not None:
        if s.bullets:
            sc_list = lay_bullets(Slide("bullets", s.title, s.subtitle, bullets=s.bullets, notes=s.notes), ctx)
            for sc in sc_list:
                sc.kind = "closing"
            return sc_list
        t = Slide("title", s.title, s.subtitle or deck.author, notes=s.notes)
        sc = _scene(ctx, s, "section" if ctx.tpl.resolve_role("section") == "section" else "title")
        if sc.layout == "section":
            out = lay_section(Slide("section", s.title, s.subtitle or deck.author, notes=s.notes), ctx)
        else:
            out = lay_title(t, ctx)
        for o in out:
            o.kind = "closing"
        return out
    sc = _scene(ctx, s, "title_only", bg=th.section_bg)
    on, sub = th.on_section, mix(th.on_section, th.section_bg, 0.3)
    if s.bullets:
        lw = fr.span(5)
        tp = _p(s.title, th.heading_font, on, bold=th.heading_bold, line=1.06)
        tp, size, h = _fit_or_truncate(tp, lw, 200, 40, 26, ctx, "slotdia", 4)
        sh, sp, ss = 0.0, None, 0.0
        if s.subtitle:
            sp = _p(s.subtitle, th.body_font, sub, line=1.25)
            sp, ss, sh = _fit_or_truncate(sp, lw, 100, 18, 13, ctx, "slotdia", 4)
        ty = (fr.H - h - (sh + 18 if sp else 0)) / 2
        sc.add(rect(fr.ml, ty - 24, 48, 4, th.section_number))
        sc.add(textbox(fr.ml, ty, lw, h + 4, [tp], size, placeholder="title"))
        if sp is not None:
            sc.add(textbox(fr.ml, ty + h + 18, lw, sh + 4, [sp], ss))
        x = fr.x(6)
        w = fr.span(6)
        items = s.bullets[:6]
        row_h = min(76.0, (fr.H - 220) / len(items))
        size = 20.0
        for b in items:
            size = min(size, fit([_p(b.text, th.body_font, on, line=1.18)], w - 54, row_h - 16, 20, 13).size)
        y0 = (fr.H - row_h * len(items)) / 2
        for i, b in enumerate(items):
            y = y0 + i * row_h
            sc.add(rect(x, y + 6, 32, 32, None, line=th.section_number, line_w=1.5, kind="ellipse"))
            sc.add(textbox(x, y + 6, 32, 32, [_p(str(i + 1), th.body_font, on, bold=True, align="c", line=1.0)], 14,
                           anchor="m"))
            bp = _p(b.text, th.body_font, on, line=1.18)
            if not fit([bp], w - 54, row_h - 12, size, size).ok:
                bp.text = truncate_to_fit(bp, w - 54, row_h - 12, size)
            sc.add(textbox(x + 54, y + 4, w - 54, row_h - 8, [bp], size, anchor="t"))
            if i < len(items) - 1:
                sc.add(line(x + 54, y + row_h - 2, x + w, y + row_h - 2, mix(th.on_section, th.section_bg, 0.75), 0.75))
        return [sc]
    tw = fr.span(9)
    tp = _p(s.title, th.heading_font, on, bold=th.heading_bold, line=1.05)
    tp, size, h = _fit_or_truncate(tp, tw, 160, 54, 32, ctx, "slotdia", 3)
    top = (fr.H - h) / 2 - 30
    sc.add(rect(fr.ml, top - 28, 48, 4, th.section_number))
    sc.add(textbox(fr.ml, top, tw, h + 4, [tp], size, placeholder="title"))
    y = top + h + 18
    if s.subtitle:
        sp = _p(s.subtitle, th.body_font, sub, line=1.25)
        sp, ss, sh = _fit_or_truncate(sp, tw, 70, 20, 14, ctx, "slotdia", 2)
        sc.add(textbox(fr.ml, y, tw, sh + 4, [sp], ss))
    meta = " · ".join(x for x in (deck.author, deck.date) if x)
    if meta:
        sc.add(textbox(fr.ml, fr.H - 66, tw, 16, [_p(meta, th.body_font, sub, line=1.2)], 12))
    return [sc]


LAYOUTS = {
    "title": lay_title, "agenda": lay_agenda, "section": lay_section, "bullets": lay_bullets,
    "two_column": lay_two_column, "kpi": lay_kpi, "quote": lay_quote, "timeline": lay_timeline,
    "table": lay_table, "chart": lay_chart, "diagram": lay_image, "image": lay_image,
    "comparison": lay_comparison, "closing": lay_closing,
}


def plan_deck(deck: DeckSpec, theme: Theme, *, images: ImageResolver | None = None,
              tpl: "TemplateInfo | None" = None) -> tuple[list[SlideScene], Ctx]:
    """Lay out every slide; returns the scenes plus the context (warnings, split count)."""
    frame = Frame.for_size(tpl.W, tpl.H) if tpl is not None else Frame()
    ctx = Ctx(deck, theme, frame, images, tpl, warnings=list(deck.warnings))
    scenes: list[SlideScene] = []
    for s in deck.slides:
        fn = LAYOUTS.get(s.type, lay_bullets)
        try:
            scenes.extend(fn(s, ctx))
        except Exception as exc:  # never lose the whole deck over one slide
            ctx.warnings.append(f"dia '{shorten(s.title, 40)}' ({s.type}) kon niet worden opgemaakt: {exc}")
            fb = Slide("bullets", s.title or "—", s.subtitle, s.kicker, bullets=s.bullets, notes=s.notes)
            scenes.extend(lay_bullets(fb, ctx))
    for i, sc in enumerate(scenes, 1):
        sc.meta["number"] = i
        for p in sc.prims:
            if isinstance(p, TextBox) and p.name == "slidenum":
                p.paras[0].text = str(i)
    return scenes, ctx


__all__ = ["Frame", "Area", "Ctx", "plan_deck", "LAYOUTS", "bullet_paras", "Para"]
