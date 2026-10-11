"""Visual themes: colours, fonts and type scale.

Font choice (documented decision): the built-in themes use **Calibri / Calibri Light** for sans text and
**Georgia** for the serif headings of "rijk". Calibri ships with every Microsoft Office installation on
Windows *and* macOS (Office for Mac bundles it), so a deck always opens with the intended typeface wherever
PowerPoint runs; Georgia is a system font on both platforms. Aptos (the newest Office default) is a cloud
font that is not guaranteed on older or offline installs, and Segoe UI / Helvetica Neue exist on only one
platform. Text measurement (textfit) uses the real font files when present (Office's DFonts folder on macOS,
C:\\Windows\\Fonts) and otherwise falls back to Arial/Helvetica metrics, which are *wider* than Calibri, so
fitted text never overflows in PowerPoint (it just has a bit of spare room).
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field, replace

from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# --------------------------------------------------------------------------- colour helpers


def rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def hexc(r: float, g: float, b: float) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, round(v))) for v in (r, g, b)))


def mix(c1: str, c2: str, t: float) -> str:
    """Blend: t=0 -> c1, t=1 -> c2."""
    a, b = rgb(c1), rgb(c2)
    return hexc(*(a[i] + (b[i] - a[i]) * t for i in range(3)))


def luminance(c: str) -> float:
    def ch(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = rgb(c)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(c1: str, c2: str) -> float:
    l1, l2 = sorted((luminance(c1), luminance(c2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def readable_on(bg: str, light: str = "#FFFFFF", dark: str = "#1A1A1A") -> str:
    return light if contrast(bg, light) >= contrast(bg, dark) else dark


# --------------------------------------------------------------------------- theme


@dataclass(frozen=True)
class Sizes:
    """Type scale in points: (max, min) per role; the fitter picks the largest size that fits."""

    deck_title: tuple[float, float] = (44, 30)
    title: tuple[float, float] = (28, 20)
    subtitle: tuple[float, float] = (16, 13)
    body: tuple[float, float] = (22, 15)
    column: tuple[float, float] = (18, 13)
    small: tuple[float, float] = (13, 10)
    kicker: float = 11
    footer: float = 9
    kpi: tuple[float, float] = (60, 30)
    quote: tuple[float, float] = (32, 20)
    section: tuple[float, float] = (40, 28)
    table: tuple[float, float] = (16, 10)
    chart: float = 12


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    background: str
    surface: str  # cards, zebra rows, panels
    text: str
    muted: str
    accent: str
    accent2: str
    accent3: str
    line: str  # hairlines, gridlines
    section_bg: str
    on_section: str
    section_number: str
    chart: tuple[str, ...]
    heading_font: str = "Calibri Light"
    body_font: str = "Calibri"
    heading_bold: bool = False  # headings in the light/semibold cut look calmer than bold
    sizes: Sizes = field(default_factory=Sizes)
    dark: bool = False
    title_panel: str = ""  # colour of the decorative block on the title slide
    title_color: str = ""  # slide titles (default: text colour)

    @property
    def heading_color(self) -> str:
        return self.title_color or self.text

    @property
    def on_accent(self) -> str:
        return readable_on(self.accent)

    @property
    def figure_font(self) -> str:
        """Big numbers: serif heading faces (Georgia) have old-style figures, so use the body face."""
        h = self.heading_font.lower()
        return self.body_font if any(k in h for k in ("georgia", "cambria", "serif", "times", "garamond")) else self.heading_font

    @property
    def strong_font(self) -> str:
        """Font for bold labels: the body family (Calibri Light bold looks thin)."""
        return self.body_font


THEMES: dict[str, Theme] = {
    "helder": Theme(
        name="helder", label="Helder",
        background="#FFFFFF", surface="#F3F6FA", text="#16202E", muted="#5E6B7C",
        accent="#1F4E9A", accent2="#5B8FD6", accent3="#B8CCE8", line="#D9E0EA",
        section_bg="#1F4E9A", on_section="#FFFFFF", section_number="#8FB3E8",
        chart=("#1F4E9A", "#5B8FD6", "#9DB9E2", "#3F6E5A", "#C98A2E", "#7A8696"),
        title_panel="#1F4E9A",
    ),
    "donker": Theme(
        name="donker", label="Donker",
        background="#0E1726", surface="#17233A", text="#EEF3FA", muted="#93A3BA",
        accent="#2EC4E6", accent2="#7DDCF0", accent3="#2B4A6B", line="#26354E",
        section_bg="#091120", on_section="#FFFFFF", section_number="#2EC4E6",
        chart=("#2EC4E6", "#8FA8C8", "#F2B84B", "#5A7BA6", "#9AE6B4", "#C3CEDD"),
        dark=True, title_panel="#17233A",
    ),
    "rijk": Theme(
        name="rijk", label="Rijk (institutioneel)",
        background="#FAF8F4", surface="#EEF2F7", text="#1B2533", muted="#5F6875",
        accent="#1D3A5F", accent2="#7FA7CF", accent3="#C9D9EA", line="#DCD9D2",
        section_bg="#1D3A5F", on_section="#FFFFFF", section_number="#9FC0E0",
        chart=("#1D3A5F", "#7FA7CF", "#B9CFE6", "#A88B5A", "#5E7D6E", "#8A8F98"),
        heading_font="Georgia", body_font="Calibri", heading_bold=False,
        title_panel="#1D3A5F", title_color="#1D3A5F",
    ),
    "natuur": Theme(
        name="natuur", label="Natuur",
        background="#FBFCF9", surface="#EEF3EC", text="#1D2A22", muted="#5D6B61",
        accent="#2F6B4F", accent2="#8DB596", accent3="#CFE0D1", line="#DCE4DA",
        section_bg="#2F6B4F", on_section="#FFFFFF", section_number="#A9D1B3",
        chart=("#2F6B4F", "#8DB596", "#C7A35A", "#5C7FA3", "#A3B8A8", "#7C6F5B"),
        title_panel="#2F6B4F",
    ),
}


def get_theme(theme: "Theme | str | None") -> Theme:
    if isinstance(theme, Theme):
        return theme
    if theme is None or theme == "":
        return THEMES["helder"]
    key = str(theme).lower().strip()
    if key in THEMES:
        return THEMES[key]
    if key.endswith((".docx", ".dotx")):
        return theme_from_docx(str(theme))
    raise KeyError(f"unknown theme {theme!r}; choose from {', '.join(THEMES)}")


# --------------------------------------------------------------------------- derive from Office files


def _scheme_colours(theme_xml: bytes) -> dict[str, str]:
    root = etree.fromstring(theme_xml)
    out: dict[str, str] = {}
    scheme = root.find(f".//{{{A}}}clrScheme")
    if scheme is None:
        return out
    for el in scheme:
        name = etree.QName(el).localname
        c = el.find(f"{{{A}}}srgbClr")
        if c is not None:
            out[name] = "#" + c.get("val", "000000").upper()
            continue
        c = el.find(f"{{{A}}}sysClr")
        if c is not None:
            out[name] = "#" + (c.get("lastClr") or ("FFFFFF" if c.get("val") == "window" else "000000")).upper()
    return out


def _scheme_fonts(theme_xml: bytes) -> tuple[str, str]:
    root = etree.fromstring(theme_xml)
    major = root.find(f".//{{{A}}}majorFont/{{{A}}}latin")
    minor = root.find(f".//{{{A}}}minorFont/{{{A}}}latin")
    return (major.get("typeface", "") if major is not None else "",
            minor.get("typeface", "") if minor is not None else "")


def _distinct(colours: list[str], min_dist: float = 40) -> list[str]:
    out: list[str] = []
    for c in colours:
        if not c:
            continue
        if all(sum((a - b) ** 2 for a, b in zip(rgb(c), rgb(o))) ** 0.5 >= min_dist for o in out):
            out.append(c)
    return out


def theme_from_colours(name: str, label: str, *, accent: str, colours: dict[str, str], heading_font: str,
                       body_font: str, background: str | None = None, text: str | None = None) -> Theme:
    """Build a complete, harmonious theme from a house accent colour plus an Office colour scheme."""
    bg = background or colours.get("lt1", "#FFFFFF")
    dark = luminance(bg) < 0.25
    txt = text or colours.get("dk1", "#1A1A1A")
    if contrast(bg, txt) < 7:
        txt = "#F2F4F8" if dark else "#1A2230"
    if txt.upper() == "#000000":
        txt = "#1A1F28"  # pure black looks harsh on screen
    if contrast(bg, accent) < 3:
        accent = mix(accent, "#000000" if not dark else "#FFFFFF", 0.35)
    theme_accents = [colours.get(f"accent{i}", "") for i in range(1, 7)]
    accent2 = next((c for c in _distinct([accent] + theme_accents)[1:] if contrast(bg, c) >= 1.8),
                   mix(accent, bg, 0.45))
    chart = _distinct([accent, accent2, mix(accent, bg, 0.55)] + theme_accents + [colours.get("dk2", "")])[:6]
    while len(chart) < 6:
        chart.append(mix(accent, bg, 0.2 + 0.12 * len(chart)))
    return Theme(
        name=name, label=label, background=bg, surface=mix(bg, accent, 0.06 if not dark else 0.12), text=txt,
        muted=mix(txt, bg, 0.42), accent=accent, accent2=accent2, accent3=mix(accent, bg, 0.75),
        line=mix(txt, bg, 0.85), section_bg=accent, on_section=readable_on(accent),
        section_number=mix(accent, readable_on(accent), 0.55), chart=tuple(chart),
        heading_font=heading_font or "Calibri Light", body_font=body_font or "Calibri",
        heading_bold=False, dark=dark, title_panel=accent, title_color=accent if contrast(bg, accent) >= 7 else "",
    )


def theme_from_docx(path: str) -> Theme:
    """Theme in the house style of a Word template: theme colours/fonts + Heading 1 colour and font."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        theme_xml = z.read("word/theme/theme1.xml") if "word/theme/theme1.xml" in names else b""
        styles_xml = z.read("word/styles.xml") if "word/styles.xml" in names else b""
    colours = _scheme_colours(theme_xml) if theme_xml else {}
    major, minor = _scheme_fonts(theme_xml) if theme_xml else ("", "")
    heading_colour = heading_font = body_font = ""
    title_colour = ""
    if styles_xml:
        root = etree.fromstring(styles_xml)

        def style_by_name(*names: str) -> etree._Element | None:
            for st in root.iter(f"{{{W}}}style"):
                nm = st.find(f"{{{W}}}name")
                if nm is not None and nm.get(f"{{{W}}}val", "").lower() in names:
                    return st
            return None

        def font_of(st: etree._Element | None) -> str:
            if st is None:
                return ""
            rf = st.find(f"{{{W}}}rPr/{{{W}}}rFonts")
            if rf is None:
                return ""
            if rf.get(f"{{{W}}}ascii"):
                return rf.get(f"{{{W}}}ascii")
            th = rf.get(f"{{{W}}}asciiTheme", "")
            return major if th.startswith("major") else minor if th.startswith("minor") else ""

        def colour_of(st: etree._Element | None) -> str:
            if st is None:
                return ""
            c = st.find(f"{{{W}}}rPr/{{{W}}}color")
            if c is None:
                return ""
            val = c.get(f"{{{W}}}val", "")
            if re.fullmatch(r"[0-9A-Fa-f]{6}", val):
                return "#" + val.upper()
            tc = c.get(f"{{{W}}}themeColor", "")
            return colours.get({"text2": "dk2", "text1": "dk1"}.get(tc, tc), "")

        h1 = style_by_name("heading 1", "kop 1")
        normal = style_by_name("normal", "standaard")
        title = style_by_name("title", "titel")
        heading_colour = colour_of(h1)
        title_colour = colour_of(title)
        heading_font = font_of(h1)
        body_font = font_of(normal)
        if not body_font:
            rf = root.find(f"{{{W}}}docDefaults//{{{W}}}rFonts")
            if rf is not None:
                body_font = rf.get(f"{{{W}}}ascii") or (
                    major if rf.get(f"{{{W}}}asciiTheme", "").startswith("major") else minor)
    accent = heading_colour or title_colour or colours.get("dk2") or colours.get("accent1") or "#1F4E9A"
    if accent.upper() in ("#000000", "#FFFFFF"):
        accent = colours.get("accent1", "#1F4E9A")
    return theme_from_colours("huisstijl", "Huisstijl (Word-sjabloon)", accent=accent, colours=colours,
                              heading_font=heading_font or major, body_font=body_font or minor,
                              background="#FFFFFF")


def with_fonts(theme: Theme, heading: str | None = None, body: str | None = None) -> Theme:
    return replace(theme, heading_font=heading or theme.heading_font, body_font=body or theme.body_font)
