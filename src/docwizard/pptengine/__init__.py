"""Deterministic PowerPoint decks from a small LLM-produced JSON deck spec.

    deck = normalize_deck(raw)                       # lenient clean-up of LLM output
    report = build_pptx(deck, "out.pptx", theme="helder")   # native, editable .pptx
    pngs = render_previews(deck, theme="helder")     # Pillow previews (same layout)

Architecture (mirrors docwizard.drawio): spec -> normalize -> layout (grid + text fitting) -> scene
(primitives in EMU) -> writer (python-pptx) / render (Pillow).
"""

from __future__ import annotations

import os
from typing import Any, Callable

from .layout import plan_deck
from .prompt import DECK_JSON_SCHEMA, DECK_PROMPT_GUIDE
from .render import render_scene
from .scene import SlideScene, emu
from .spec import SLIDE_TYPES, DeckSpec, Slide, normalize_deck
from .template import TemplateInfo, analyze_pptx_template, load_template
from .themes import THEMES, Theme, get_theme, theme_from_docx
from .writer import write_deck

ImageResolver = Callable[[str], "tuple[bytes, str] | None"]

__all__ = [
    "DeckSpec", "Slide", "normalize_deck", "build_pptx", "render_previews", "analyze_pptx_template",
    "Theme", "THEMES", "theme_from_docx", "DECK_PROMPT_GUIDE", "DECK_JSON_SCHEMA", "SLIDE_TYPES", "plan",
]


def _deck(deck: DeckSpec | dict[str, Any] | list[Any] | str) -> DeckSpec:
    return deck if isinstance(deck, DeckSpec) else normalize_deck(deck)


def plan(deck: DeckSpec | dict[str, Any] | list[Any] | str, *, theme: Theme | str = "helder",
         template: str | None = None, images: ImageResolver | None = None
         ) -> tuple[DeckSpec, list[SlideScene], dict[str, Any], TemplateInfo | None, Theme]:
    """Lay out a deck: (deck, scenes, report, template info, effective theme)."""
    d = _deck(deck)
    tpl = load_template(template) if template else None
    th = tpl.theme if tpl is not None else get_theme(theme)
    scenes, ctx = plan_deck(d, th, images=images, tpl=tpl)
    seen: set[str] = set()
    warnings = [w for w in ctx.warnings if not (w in seen or seen.add(w))]
    return d, scenes, {"slides": len(scenes), "warnings": warnings, "split": ctx.split}, tpl, th


def build_pptx(deck: DeckSpec | dict[str, Any] | list[Any] | str, out_path: str | os.PathLike | Any, *,
               theme: Theme | str = "helder", template: str | None = None,
               images: ImageResolver | None = None) -> dict[str, Any]:
    """Write a native .pptx; returns {"slides": n, "warnings": [...], "split": n}.

    With `template` (a .pptx/.potx path) the deck is built on the template's masters/layouts/theme; otherwise
    on a clean 16:9 file in the chosen built-in theme. `images(ref)` resolves image/diagram refs to
    (bytes, ext) or None.
    """
    d, scenes, report, tpl, th = plan(deck, theme=theme, template=template, images=images)
    write_deck(scenes, d, th, out_path, tpl=tpl)
    return report


def render_previews(deck: DeckSpec | dict[str, Any] | list[Any] | str, *, theme: Theme | str = "helder",
                    template: str | None = None, images: ImageResolver | None = None,
                    width: int = 1280) -> list[bytes]:
    """PNG previews (one per output slide, including continuation slides)."""
    _, scenes, _, tpl, _ = plan(deck, theme=theme, template=template, images=images)
    w, h = (emu(tpl.W), emu(tpl.H)) if tpl is not None else (12192000, 6858000)
    return [render_scene(sc, w, h, width) for sc in scenes]
