"""Font discovery, text measurement, wrapping and fitting (in points).

Text is measured with the real TrueType font when it can be found (macOS system + Office DFonts folders,
Windows font folders, Linux font dirs); otherwise with a substitute whose glyphs are at least as wide
(Arial/Helvetica for Calibri), optionally multiplied by a safety factor. The same wrap function is used by
the layout (to choose font sizes and split slides) and by the preview renderer (to draw the lines), so the
preview shows exactly what was measured. PowerPoint wraps on its own; because measurement errs on the wide
side, PowerPoint never needs more lines than we reserved.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import ImageFont

_HOME = os.path.expanduser("~")
_FONT_DIRS = [
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    f"{_HOME}/Library/Fonts",
    "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts",
    "/Applications/Microsoft Word.app/Contents/Resources/DFonts",
    "/Applications/Microsoft Excel.app/Contents/Resources/DFonts",
    "/Applications/Microsoft Outlook.app/Contents/Resources/DFonts",
    os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts") if os.environ.get(
        "LOCALAPPDATA") else "",
    f"{_HOME}/.fonts",
    f"{_HOME}/.local/share/fonts",
]
_RECURSIVE_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts"]

# Substitutes when a family is missing: (family, extra width factor). Listed substitutes are at least as wide
# as the requested face (or metric-compatible), so measurement stays on the safe side.
_SANS = [("Arial", 1.0), ("Helvetica", 1.0), ("Liberation Sans", 1.0), ("DejaVu Sans", 1.0)]
_SERIF = [("Georgia", 1.0), ("DejaVu Serif", 1.0), ("Times New Roman", 1.12), ("Liberation Serif", 1.12)]
_SUBSTITUTES: dict[str, list[tuple[str, float]]] = {
    "calibri": [("Carlito", 1.0)] + _SANS,
    "calibri light": [("Carlito", 1.0), ("Helvetica Neue Light", 1.0), ("Helvetica Neue", 1.0)] + _SANS,
    "aptos": [(f, 1.05) for f, _ in _SANS],
    "aptos display": [(f, 1.03) for f, _ in _SANS],
    "segoe ui": [("Arial", 1.03), ("Helvetica", 1.03), ("Liberation Sans", 1.03), ("DejaVu Sans", 1.0)],
    "segoe ui semibold": [("Arial", 1.04), ("Helvetica", 1.04), ("DejaVu Sans", 1.0)],
    "helvetica neue": [("Helvetica", 1.0), ("Arial", 1.0)] + _SANS[2:],
    "helvetica": [("Arial", 1.0)] + _SANS[2:],
    "arial": [("Helvetica", 1.0)] + _SANS[2:],
    "cambria": [("Caladea", 1.0), ("Georgia", 1.0)] + _SERIF[1:],
    "georgia": [("DejaVu Serif", 1.0), ("Times New Roman", 1.15), ("Liberation Serif", 1.15)],
    "times new roman": [("Liberation Serif", 1.0), ("Times", 1.0), ("Georgia", 1.0)],
    "verdana": [("DejaVu Sans", 1.0), ("Arial", 1.15)],
    "tahoma": [("DejaVu Sans", 1.0), ("Arial", 1.05)],
    "trebuchet ms": [("Arial", 1.02), ("DejaVu Sans", 1.0)],
}
_SERIF_HINTS = ("serif", "georgia", "cambria", "times", "garamond", "palatino", "book", "minion", "charter")
_SAFETY = 1.02  # global margin for kerning/hinting differences with PowerPoint


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@lru_cache(maxsize=1)
def _font_files() -> list[str]:
    files: list[str] = []
    for d in _FONT_DIRS:
        if d and os.path.isdir(d):
            try:
                files += [os.path.join(d, f) for f in os.listdir(d)]
            except OSError:
                pass
    for d in _RECURSIVE_DIRS:
        if os.path.isdir(d):
            files += glob.glob(os.path.join(d, "**", "*.[tToO][tTtT][fFcC]"), recursive=True)
    return [f for f in files if f.lower().endswith((".ttf", ".ttc", ".otf"))]


def _style_flags(style: str) -> tuple[bool, bool, bool]:
    s = style.lower()
    bold = any(w in s for w in ("bold", "black", "heavy", "semibold", "demibold"))
    italic = "italic" in s or "oblique" in s
    light = any(w in s for w in ("light", "thin", "ultralight", "condensed", "narrow", "medium"))
    return bold, italic, light


@lru_cache(maxsize=None)
def _find_face(family: str, bold: bool, italic: bool) -> tuple[str, int, bool] | None:
    """(path, index, synthetic_bold) for an installed face of exactly this family, or None."""
    want = _norm(family)
    prefix = want[:5]
    best: tuple[int, str, int, bool] | None = None
    for path in _font_files():
        stem = _norm(os.path.splitext(os.path.basename(path))[0])
        if not stem.startswith(prefix):
            continue
        for index in range(0, 24 if path.lower().endswith(".ttc") else 1):
            try:
                f = ImageFont.truetype(path, 10, index=index)
            except (OSError, ValueError):
                break
            fam, style = f.getname()
            fam = fam or ""
            style = style or ""
            # Some faces report "Calibri Light" + "Regular", others "Calibri" + "Light".
            full = _norm(fam + ("" if style.lower() in ("regular", "normal", "roman", "book") else style))
            fb, fi, fl = _style_flags(style)
            if _norm(fam) == want and not fl:
                score = (fb == bold) * 2 + (fi == italic)
            elif full == want or (_norm(fam) == want and fl and "light" in want):
                score = (fb == bold) * 2 + (fi == italic)
            else:
                continue
            if fi and not italic:
                score -= 3
            if best is None or score > best[0]:
                best = (score, path, index, bold and not fb)
    if best is None:
        return None
    return best[1], best[2], best[3]


@dataclass(frozen=True)
class Face:
    path: str | None
    index: int
    factor: float  # width multiplier applied to measurements
    synthetic_bold: bool


@lru_cache(maxsize=None)
def resolve(family: str, bold: bool = False, italic: bool = False) -> Face:
    fam = family.strip() or "Calibri"
    hit = _find_face(fam, bold, italic)
    if hit:
        return Face(hit[0], hit[1], 1.0, hit[2])
    key = fam.lower()
    subs = _SUBSTITUTES.get(key)
    if subs is None:
        subs = ([(f, 1.05) for f, _ in _SERIF[:2]] + _SERIF[2:]) if any(h in key for h in _SERIF_HINTS) else [
            (f, 1.05) for f, _ in _SANS]
    for sub, factor in subs:
        hit = _find_face(sub, bold, italic)
        if hit:
            return Face(hit[0], hit[1], factor * (1.03 if hit[2] else 1.0), hit[2])
    for sub, _ in _SANS:
        hit = _find_face(sub, bold, italic)
        if hit:
            return Face(hit[0], hit[1], 1.05, hit[2])
    return Face(None, 0, 1.0, bold)


@lru_cache(maxsize=256)
def pil_font(family: str, size_px: float, bold: bool = False, italic: bool = False):
    face = resolve(family, bold, italic)
    px = max(1, round(size_px))
    if face.path:
        return ImageFont.truetype(face.path, px, index=face.index)
    try:
        return ImageFont.load_default(px)
    except TypeError:  # pragma: no cover - very old Pillow
        return ImageFont.load_default()


@lru_cache(maxsize=20000)
def _width100(text: str, family: str, bold: bool, italic: bool) -> float:
    """Width at 100 pt, in points (text width scales linearly with size)."""
    if not text:
        return 0.0
    face = resolve(family, bold, italic)
    if face.path is None:
        return len(text) * 100 * (0.58 if bold else 0.54)
    f = pil_font(family, 100, bold, italic)
    w = float(f.getlength(text))
    if face.synthetic_bold:
        w *= 1.03
    return w * face.factor


def text_width(text: str, family: str, size: float, bold: bool = False, italic: bool = False) -> float:
    return _width100(text, family, bold, italic) * size / 100 * _SAFETY


def wrap(text: str, family: str, size: float, width: float, bold: bool = False, italic: bool = False) -> list[str]:
    """Greedy word wrap (explicit newlines respected); over-long words are broken like PowerPoint does."""
    out: list[str] = []
    for para in text.split("\n"):
        words = para.split()
        if not words:
            out.append("")
            continue
        cur = ""
        for word in words:
            trial = f"{cur} {word}" if cur else word
            if text_width(trial, family, size, bold, italic) <= width:
                cur = trial
                continue
            if cur:
                out.append(cur)
            while text_width(word, family, size, bold, italic) > width and len(word) > 1:
                cut = len(word) - 1
                while cut > 1 and text_width(word[:cut], family, size, bold, italic) > width:
                    cut -= 1
                out.append(word[:cut])
                word = word[cut:]
            cur = word
        out.append(cur)
    return out


def longest_word(text: str, family: str, size: float, bold: bool = False) -> float:
    return max((text_width(w, family, size, bold) for w in text.split()), default=0.0)


# --------------------------------------------------------------------------- fitting


@dataclass
class P:
    """A paragraph to fit. Sizes are relative to the block's base size."""

    text: str
    family: str
    rel: float = 1.0
    bold: bool = False
    italic: bool = False
    indent: float = 0.0  # left text indent, in em of the block's base size (bullets)
    bullet_at: float = 0.0  # bullet position, in em of the base size
    space_before: float = 0.0  # in em, ignored for the first paragraph
    line: float = 1.2  # line pitch in em
    color: str = "#000000"
    bullet: str = ""  # bullet character ("" = none)
    bullet_color: str = ""
    align: str = "l"
    spacing: float = 0.0  # letter spacing in pt
    caps: bool = False
    level: int = 0
    lead: int = 0  # number of leading characters set in bold ("Kosten: ...")


@dataclass
class Fit:
    size: float
    ok: bool
    height: float
    lines: list[list[str]] = field(default_factory=list)

    def para_size(self, p: P) -> float:
        return round(self.size * p.rel * 2) / 2


def para_size(base: float, p: P) -> float:
    return round(base * p.rel * 2) / 2


def measure(paras: list[P], base: float, width: float) -> tuple[float, list[list[str]]]:
    total = 0.0
    lines_all: list[list[str]] = []
    for i, p in enumerate(paras):
        s = para_size(base, p)
        txt = p.text.upper() if p.caps else p.text
        extra = p.spacing * max(0, len(txt) - 1)
        w = width - p.indent * base - extra
        lines = wrap(txt, p.family, s, max(w, s), p.bold or p.lead > 0, p.italic) if txt else [""]
        lines_all.append(lines)
        if i:
            total += p.space_before * s
        total += len(lines) * p.line * s
    return total, lines_all


def fit(paras: list[P], width: float, height: float, max_size: float, min_size: float, *,
        max_lines: int | None = None, step: float = 1.0) -> Fit:
    """Largest base size in [min_size, max_size] at which all paragraphs fit in width x height."""
    size = max_size
    while True:
        h, lines = measure(paras, size, width)
        n = sum(len(x) for x in lines)
        if h <= height + 0.01 and (max_lines is None or n <= max_lines):
            return Fit(size, True, h, lines)
        if size - step < min_size - 1e-6:
            return Fit(size, False, h, lines)
        size = round(size - step, 2)


def truncate_to_fit(p: P, width: float, height: float, size: float) -> str:
    """Shorten a single paragraph (with an ellipsis) so it fits at `size`."""
    words = p.text.split()
    while len(words) > 1:
        words.pop()
        cand = " ".join(words).rstrip(",;:-–") + "…"
        q = P(**{**p.__dict__, "text": cand})
        h, _ = measure([q], size / p.rel if p.rel else size, width)
        if h <= height + 0.01:
            return cand
    return (words[0] if words else "") + "…"
