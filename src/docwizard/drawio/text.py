"""Font discovery, text measurement and word wrapping (shared by layout, SVG and PNG)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from PIL import ImageFont

FONT_FAMILY = "Helvetica, Arial, sans-serif"

# (path, index) candidates, regular and bold.
_REGULAR = [
    ("/System/Library/Fonts/Helvetica.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
    ("/Library/Fonts/Arial.ttf", 0),
    ("C:\\Windows\\Fonts\\arial.ttf", 0),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 0),
    ("/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", 0),
    ("DejaVuSans.ttf", 0),
]
_BOLD = [
    ("/System/Library/Fonts/Helvetica.ttc", 1),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
    ("/Library/Fonts/Arial Bold.ttf", 0),
    ("C:\\Windows\\Fonts\\arialbd.ttf", 0),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 0),
    ("/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", 0),
    ("DejaVuSans-Bold.ttf", 0),
]

LINE_HEIGHT = 1.25


@lru_cache(maxsize=None)
def _font_source(bold: bool) -> tuple[str, int] | None:
    for path, index in _BOLD if bold else _REGULAR:
        if os.path.isabs(path) and not os.path.exists(path):
            continue
        try:
            ImageFont.truetype(path, 12, index=index)
            return path, index
        except OSError:
            continue
    return None


@lru_cache(maxsize=256)
def get_font(size: float, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """TrueType font at pixel size `size`; falls back to the regular face, then Pillow's default."""
    px = max(1, round(size))
    src = _font_source(bold) or _font_source(False)
    if src:
        return ImageFont.truetype(src[0], px, index=src[1])
    try:
        return ImageFont.load_default(px)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


@lru_cache(maxsize=4096)
def text_width(text: str, size: float, bold: bool = False) -> float:
    """Width in px of `text` at font size `size` (measured at 4x for sub-pixel accuracy)."""
    if not text:
        return 0.0
    font = get_font(size * 4, bold)
    try:
        return float(font.getlength(text)) / 4
    except AttributeError:
        return len(text) * size * 0.55


@dataclass(frozen=True)
class Run:
    """One paragraph of a label with uniform formatting."""

    text: str
    size: float = 12
    bold: bool = False
    color: str = "#000000"
    italic: bool = False


@dataclass(frozen=True)
class Line:
    text: str
    size: float
    bold: bool
    color: str
    width: float


def wrap(text: str, width: float, size: float, bold: bool = False) -> list[str]:
    """Greedy word wrap; words longer than the width are hard-split."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}" if cur else word
        if text_width(trial, size, bold) <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        while text_width(word, size, bold) > width and len(word) > 1:
            cut = len(word)
            while cut > 1 and text_width(word[:cut] + "-", size, bold) > width:
                cut -= 1
            lines.append(word[:cut] + "-")
            word = word[cut:]
        cur = word
    if cur:
        lines.append(cur)
    return lines


def layout_runs(runs: list[Run], width: float, max_lines: int | None = None) -> list[Line]:
    out: list[Line] = []
    for run in runs:
        for text in wrap(run.text, width, run.size, run.bold):
            out.append(Line(text, run.size, run.bold, run.color, text_width(text, run.size, run.bold)))
    if max_lines is not None and len(out) > max_lines:
        out = out[:max_lines]
        last = out[-1]
        txt = last.text
        while txt and text_width(txt + "…", last.size, last.bold) > width:
            txt = txt[:-1]
        out[-1] = Line(txt.rstrip() + "…", last.size, last.bold, last.color, text_width(txt + "…", last.size))
    return out


def lines_height(lines: list[Line]) -> float:
    return sum(line.size * LINE_HEIGHT for line in lines)


def longest_word_width(runs: list[Run]) -> float:
    return max((text_width(w, r.size, r.bold) for r in runs for w in r.text.split()), default=0.0)
