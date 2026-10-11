"""Deck spec data model and a lenient normaliser for (weak) local-LLM output.

The LLM only writes *what* to say (storyline, slide types, short texts, numbers, notes); everything about
*how* it looks is decided by the layout engine. `normalize_deck` accepts dicts, lists or JSON text with the
typical defects of small models: alternative key names (English and Dutch), strings instead of lists,
numbers as text ("€ 1,2 mln"), missing or unknown slide types, overlong texts and too many bullets.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

SLIDE_TYPES = (
    "title", "agenda", "section", "bullets", "two_column", "kpi", "quote", "timeline",
    "table", "chart", "diagram", "image", "comparison", "closing",
)
CHART_KINDS = ("bar", "column", "line", "pie", "stacked")

# Limits (texts are shortened with an ellipsis, lists are capped; a warning is recorded).
MAX_TITLE = 150
MAX_SUBTITLE = 160
MAX_BULLET = 180
MAX_SUB = 140
MAX_BULLETS = 14
MAX_SUBS = 4
MAX_KPIS = 4
MAX_STEPS = 12
MAX_TABLE_COLS = 7
MAX_TABLE_ROWS = 40
MAX_CATEGORIES = 24
MAX_SERIES = 6


# --------------------------------------------------------------------------- model


@dataclass
class Bullet:
    text: str
    sub: list[str] = field(default_factory=list)


@dataclass
class Column:
    heading: str = ""
    bullets: list[Bullet] = field(default_factory=list)


@dataclass
class Kpi:
    value: str
    label: str = ""
    detail: str = ""


@dataclass
class Quote:
    text: str
    source: str = ""


@dataclass
class Step:
    label: str
    date: str = ""
    text: str = ""


@dataclass
class Table:
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Series:
    name: str
    values: list[float | None] = field(default_factory=list)


@dataclass
class Chart:
    kind: str = "column"
    categories: list[str] = field(default_factory=list)
    series: list[Series] = field(default_factory=list)
    unit: str = ""


@dataclass
class Image:
    ref: str
    caption: str = ""


@dataclass
class Comparison:
    left: Column = field(default_factory=Column)
    right: Column = field(default_factory=Column)
    verdict: str = ""


@dataclass
class Slide:
    type: str = "bullets"
    title: str = ""
    subtitle: str = ""
    kicker: str = ""
    bullets: list[Bullet] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    kpis: list[Kpi] = field(default_factory=list)
    quote: Quote | None = None
    steps: list[Step] = field(default_factory=list)
    table: Table | None = None
    chart: Chart | None = None
    image: Image | None = None
    comparison: Comparison | None = None
    notes: str = ""
    source: str = ""
    number: int = 0  # section number (filled in by the normaliser)
    continued: bool = False  # continuation slide produced by splitting


@dataclass
class DeckSpec:
    title: str = ""
    subtitle: str = ""
    author: str = ""
    date: str = ""
    language: str = "nl"
    audience: str = ""
    slides: list[Slide] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def clean(v: Any) -> Any:
            if isinstance(v, dict):
                return {k: clean(x) for k, x in v.items() if x not in (None, "", [], 0, False) or k == "type"}
            if isinstance(v, list):
                return [clean(x) for x in v]
            return v

        d = asdict(self)
        d.pop("warnings", None)
        return clean(d)


# --------------------------------------------------------------------------- generic helpers

_MD = re.compile(r"(\*\*|__|`|^#+\s*)")
_LEAD = re.compile(r"^\s*(?:[-*•▪▸►●◦·–—]|\d{1,2}[.)]|[a-z][.)])\s+", re.IGNORECASE)


def _lower_keys(d: dict[str, Any]) -> dict[str, Any]:
    return {re.sub(r"[\s\-']+", "_", str(k).strip().lower()): v for k, v in d.items()}


def _get(d: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in d and d[key] not in (None, "", [], {}):
            return d[key]
    return default


def text(value: Any) -> str:
    """Flatten anything to one clean line of text (markdown emphasis and list markers removed)."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float):
        return fmt_number(value)
    if isinstance(value, (list, tuple)):
        return " ".join(t for t in (text(v) for v in value) if t)
    if isinstance(value, dict):
        d = _lower_keys(value)
        return text(_get(d, ("text", "title", "label", "name", "value", "heading", "tekst", "titel")))
    s = str(value).replace(" ", " ")
    s = _MD.sub("", s)
    s = _LEAD.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def shorten(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    cut = s[: limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-–") + "…"


def _split_text_list(s: str) -> list[str]:
    s = s.strip()
    if not s:
        return []
    if "\n" in s:  # keep indentation: "  - sub" lines become sub-bullets
        return [ln.rstrip() for ln in s.splitlines() if text(ln)]
    elif re.search(r"\s[•▪·]\s", s) or s.startswith("•"):
        parts = re.split(r"\s*[•▪·]\s*", s)
    elif s.count(";") >= 1 and len(s) > 60:
        parts = s.split(";")
    else:
        parts = [s]
    return [p for p in (text(p) for p in parts) if p]


def as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return _split_text_list(value)
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return [value]
    return [value]


def fmt_number(v: float, decimals: int | None = None) -> str:
    """Dutch number formatting: 1234.5 -> '1.234,5'."""
    if decimals is None:
        decimals = 0 if float(v).is_integer() else (1 if abs(v) >= 10 else 2)
    s = f"{v:,.{decimals}f}"
    s = s.replace(",", "\0").replace(".", ",").replace("\0", ".")
    if "," in s:
        s = s.rstrip("0").rstrip(",")
    return s


_NUM = re.compile(r"[-+−]?\d[\d.,]*(?:\s\d{3}(?![\d]))*")


def parse_number(value: Any) -> tuple[float | None, str]:
    """('€ 1,2 mln') -> (1.2, '€ mln'); ('45%') -> (45.0, '%'); ('1.234') -> (1234.0, '')."""
    if value is None or isinstance(value, bool):
        return None, ""
    if isinstance(value, (int, float)):
        return float(value), ""
    s = str(value).replace(" ", " ").strip()
    m = _NUM.search(s)
    if not m:
        return None, ""
    raw = m.group().replace(" ", "").replace("−", "-")
    if "," in raw and "." in raw:
        dec = "," if raw.rfind(",") > raw.rfind(".") else "."
    elif "," in raw:
        dec = "," if raw.count(",") == 1 and not re.fullmatch(r"[-+]?\d{1,3}(,\d{3}){2,}", raw) else ""
    elif "." in raw:
        dec = "" if re.fullmatch(r"[-+]?\d{1,3}(\.\d{3})+", raw) else "."
    else:
        dec = ""
    thousands = {",": ".", ".": ",", "": ".,"}[dec]
    for t in thousands:
        raw = raw.replace(t, "")
    if dec == ",":
        raw = raw.replace(",", ".")
    try:
        num = float(raw.rstrip("."))
    except ValueError:
        return None, ""
    unit = (s[: m.start()] + " " + s[m.end():]).strip()
    unit = re.sub(r"\s+", " ", unit).strip(" :")
    return num, unit[:20]


def _parse_raw(raw: Any) -> Any:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str):
        return raw
    t = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if starts:
        t = t[min(starts):]
    for candidate in (t, re.sub(r",\s*([}\]])", r"\1", t)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    # Truncated output (model ran out of tokens): cut at the last complete slide object.
    cut = t
    while cut:
        cut = cut[: cut.rfind("}")] if "}" in cut else ""
        if not cut:
            break
        for tail in ("}]}", "}]", "}"):
            try:
                return json.loads(re.sub(r",\s*([}\]])", r"\1", cut + tail))
            except json.JSONDecodeError:
                continue
    raise ValueError("deck spec is not valid JSON")


# --------------------------------------------------------------------------- slide parsing

_TYPE_SYNONYMS: dict[str, str] = {}
for _canon, _names in {
    "title": "title titel title_slide titleslide cover voorblad opening intro_title titelpagina titeldia front",
    "agenda": "agenda toc inhoud inhoudsopgave contents overview_agenda",
    "section": "section section_header sectionheader divider hoofdstuk sectie chapter deel tussenblad header",
    "bullets": "bullets bullet content text list opsomming tekst punten points body statement key_points "
               "summary samenvatting conclusion conclusie conclusies overview overzicht",
    "two_column": "two_column two_columns twocolumn 2col 2_column columns kolommen twee_kolommen tweekolom",
    "kpi": "kpi kpis metrics metric numbers cijfers kerncijfers stats statistics big_numbers getallen",
    "quote": "quote citaat quotation uitspraak testimonial",
    "timeline": "timeline tijdlijn roadmap planning process proces steps stappen stappenplan phases fases "
                "milestones mijlpalen schedule",
    "table": "table tabel matrix grid",
    "chart": "chart grafiek graph diagram_chart bar_chart barchart column_chart line_chart pie_chart plot",
    "diagram": "diagram architectuur architecture plaat architectuurplaat drawio schema model",
    "image": "image afbeelding picture figure figuur photo foto screenshot illustratie",
    "comparison": "comparison vergelijking versus vs compare pros_cons voor_en_nadelen options opties "
                  "alternatives alternatieven before_after",
    "closing": "closing end einde afsluiting slot questions vragen next_steps vervolgstappen thanks "
               "bedankt dank outro close",
}.items():
    for _n in _names.split():
        _TYPE_SYNONYMS[_n] = _canon

_CHART_SYNONYMS = {
    "bar": "bar", "hbar": "bar", "horizontal": "bar", "horizontal_bar": "bar", "liggend": "bar",
    "column": "column", "columns": "column", "staaf": "column", "staven": "column", "kolom": "column",
    "vertical": "column", "histogram": "column", "clustered": "column",
    "line": "line", "lijn": "line", "lines": "line", "trend": "line", "area": "line",
    "pie": "pie", "taart": "pie", "cirkel": "pie", "donut": "pie", "doughnut": "pie", "ring": "pie",
    "stacked": "stacked", "gestapeld": "stacked", "stacked_bar": "stacked", "stacked_column": "stacked",
}

_TITLE_KEYS = ("title", "titel", "action_title", "headline", "heading", "kop", "header", "message", "boodschap",
               "takeaway", "name")
_SUBTITLE_KEYS = ("subtitle", "ondertitel", "subheading", "subhead", "sub_title", "tagline", "lead", "intro")
_KICKER_KEYS = ("kicker", "eyebrow", "overline", "label", "tag", "category")
_BULLET_KEYS = ("bullets", "bullet_points", "points", "punten", "items", "opsomming", "list", "content", "body",
                "text", "tekst", "key_points", "kernpunten", "lines", "paragraphs", "inhoud", "next_steps",
                "vervolgstappen", "agenda", "sections")
_NOTES_KEYS = ("notes", "speaker_notes", "speakernotes", "notities", "sprekersnotities", "spreker_notities",
               "presenter_notes", "script", "toelichting", "talking_points")
_SOURCE_KEYS = ("source", "bron", "footnote", "voetnoot", "reference", "referentie", "sources", "bronnen")
_KPI_KEYS = ("kpis", "kpi", "metrics", "numbers", "cijfers", "kerncijfers", "stats", "statistics", "figures",
             "big_numbers")
_STEP_KEYS = ("steps", "timeline", "tijdlijn", "milestones", "mijlpalen", "phases", "fases", "stappen", "events",
              "roadmap", "stages")
_COLUMN_KEYS = ("columns", "kolommen", "cols")
_IMAGE_KEYS = ("image", "afbeelding", "picture", "figure", "figuur", "diagram", "img", "ref", "src", "file",
               "media", "diagram_ref", "image_ref")


def _bullets(value: Any, warn: list[str], where: str) -> list[Bullet]:
    out: list[Bullet] = []
    for item in as_list(value):
        if isinstance(item, dict):
            d = _lower_keys(item)
            if len(d) == 1 and not any(k in d for k in ("text", "title", "label", "tekst")):
                # {"Kop": ["sub", "sub"]} or {"Kop": "toelichting"}
                (k, v), = d.items()
                head = text(list(item.keys())[0])
                subs = [text(s) for s in as_list(v)] if isinstance(v, (list, tuple)) else []
                t = head if subs or not isinstance(v, str) else f"{head}: {text(v)}"
                out.append(Bullet(shorten(t, MAX_BULLET), [shorten(s, MAX_SUB) for s in subs if s]))
                continue
            t = text(_get(d, ("text", "tekst", "title", "titel", "label", "point", "punt", "heading", "name")))
            detail = text(_get(d, ("detail", "description", "beschrijving", "toelichting", "explanation")))
            if detail and not t:
                t, detail = detail, ""
            subs_raw = _get(d, ("sub", "subs", "sub_bullets", "subbullets", "children", "items", "bullets",
                                "details", "points", "subpunten"))
            subs = [text(s) for s in as_list(subs_raw)]
            if detail:
                subs.insert(0, detail)
            if t:
                out.append(Bullet(shorten(t, MAX_BULLET), [shorten(s, MAX_SUB) for s in subs if s]))
        elif isinstance(item, (list, tuple)):
            # nested list = sub-bullets of the previous bullet
            subs = [text(s) for s in item if text(s)]
            if out:
                out[-1].sub.extend(shorten(s, MAX_SUB) for s in subs)
            elif subs:
                out.append(Bullet(shorten(subs[0], MAX_BULLET), [shorten(s, MAX_SUB) for s in subs[1:]]))
        else:
            raw = str(item)
            # Indented "  - sub" lines inside a string list.
            is_sub = bool(re.match(r"^\s{2,}[-*•◦]|^\s*[◦–]\s", raw))
            t = text(raw)
            if not t:
                continue
            if is_sub and out:
                out[-1].sub.append(shorten(t, MAX_SUB))
            else:
                out.append(Bullet(shorten(t, MAX_BULLET)))
    for b in out:
        if len(b.sub) > MAX_SUBS:
            warn.append(f"{where}: te veel subpunten, ingekort tot {MAX_SUBS}")
            del b.sub[MAX_SUBS:]
    if len(out) > MAX_BULLETS:
        warn.append(f"{where}: {len(out)} bullets, ingekort tot {MAX_BULLETS}")
        del out[MAX_BULLETS:]
    return out


def _column(value: Any, warn: list[str], where: str) -> Column:
    if isinstance(value, dict):
        d = _lower_keys(value)
        heading = text(_get(d, ("heading", "title", "titel", "kop", "header", "name", "label", "option", "optie")))
        bl = _get(d, ("bullets", "points", "items", "punten", "content", "text", "tekst", "pros", "body", "list"))
        if bl is None:
            rest = {k: v for k, v in value.items() if _lower_keys({k: 0}).popitem()[0] not in
                    ("heading", "title", "titel", "kop", "header", "name", "label", "option", "optie")}
            bl = list(rest.values()) if rest else None
        return Column(shorten(heading, 80), _bullets(bl, warn, where))
    if isinstance(value, (list, tuple)):
        items = list(value)
        if items and isinstance(items[0], str) and len(items) > 1 and len(items[0]) < 40:
            return Column(text(items[0]), _bullets(items[1:], warn, where))
        return Column("", _bullets(items, warn, where))
    return Column("", _bullets(value, warn, where))


def _kpis(value: Any) -> list[Kpi]:
    out: list[Kpi] = []
    if isinstance(value, dict) and not any(k in _lower_keys(value) for k in ("value", "waarde", "number")):
        value = [{"label": k, "value": v} for k, v in value.items()]
    for item in as_list(value):
        if isinstance(item, dict):
            d = _lower_keys(item)
            v = _get(d, ("value", "waarde", "number", "getal", "kpi", "metric", "amount", "bedrag", "figure"))
            label = text(_get(d, ("label", "title", "titel", "name", "naam", "description_short", "kop")))
            detail = text(_get(d, ("detail", "details", "description", "beschrijving", "context", "toelichting",
                                   "note", "subtitle", "trend")))
            unit = text(_get(d, ("unit", "eenheid")))
            if v is None and label:
                v, label = label, ""
            if v is None:
                continue
            vs = fmt_number(float(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else text(v)
            if unit and unit not in vs:
                vs = f"{vs}{unit}" if unit in ("%", "x", "×") else f"{vs} {unit}"
            out.append(Kpi(shorten(vs, 16), shorten(label, 60), shorten(detail, 110)))
        else:
            s = text(item)
            m = re.match(r"^(.*?\d[\d.,]*\s*(?:%|x|×|mln|mld|k|miljoen|miljard|uur|dagen|fte|FTE)?)\s*[-–—:|]\s*(.+)$",
                         s)
            if m:
                out.append(Kpi(shorten(m.group(1).strip(), 16), shorten(m.group(2).strip(), 60)))
            elif s:
                out.append(Kpi(shorten(s, 16)))
    return out


def _steps(value: Any) -> list[Step]:
    out: list[Step] = []
    if isinstance(value, dict) and not any(k in _lower_keys(value) for k in ("label", "title", "date")):
        value = [{"date": k, "label": v} if not isinstance(v, dict) else {"date": k, **v} for k, v in value.items()]
    for item in as_list(value):
        if isinstance(item, dict):
            d = _lower_keys(item)
            label = text(_get(d, ("label", "title", "titel", "name", "naam", "step", "stap", "phase", "fase",
                                  "milestone", "mijlpaal", "event")))
            date = text(_get(d, ("date", "datum", "when", "wanneer", "period", "periode", "time", "quarter",
                                 "kwartaal", "year", "jaar", "moment")))
            desc = text(_get(d, ("text", "tekst", "description", "beschrijving", "detail", "details", "toelichting",
                                 "body")))
            if not label and desc:
                label, desc = desc, ""
            if label or date:
                out.append(Step(shorten(label or date, 50), shorten(date, 24), shorten(desc, 110)))
        else:
            s = text(item)
            m = re.match(r"^((?:Q\d|K\d|\d{4}|week|wk|fase|stap|maand|jan|feb|mrt|apr|mei|jun|jul|aug|sep|okt|nov|dec)"
                         r"[^:–—]{0,18})\s*[:–—]\s*(.+)$", s, re.IGNORECASE)
            if m:
                out.append(Step(shorten(m.group(2), 50), shorten(m.group(1).strip(), 24)))
            elif s:
                out.append(Step(shorten(s, 50)))
    return out


def _table(value: Any, d: dict[str, Any], warn: list[str], where: str) -> Table | None:
    header_raw = None
    rows_raw: Any = None
    if isinstance(value, dict):
        td = _lower_keys(value)
        header_raw = _get(td, ("header", "headers", "columns", "kolommen", "head", "cols"))
        rows_raw = _get(td, ("rows", "rijen", "data", "body", "records", "values"))
    elif isinstance(value, (list, tuple)):
        rows_raw = list(value)
    if rows_raw is None:
        header_raw = header_raw or _get(d, ("header", "headers", "kolommen"))
        rows_raw = _get(d, ("rows", "rijen", "data"))
    rows_raw = as_list(rows_raw) if not isinstance(rows_raw, list) else rows_raw
    header = [shorten(text(h), 40) for h in as_list(header_raw)] if header_raw else []
    rows: list[list[str]] = []
    for r in rows_raw or []:
        if isinstance(r, dict):
            if not header:
                header = [shorten(text(k), 40) for k in r.keys()]
            lk = {text(k): v for k, v in r.items()}
            rows.append([shorten(text(lk.get(h, "")), 90) for h in header])
        elif isinstance(r, (list, tuple)):
            rows.append([shorten(text(c), 90) for c in r])
        elif isinstance(r, str) and "|" in r:
            cells = [c.strip() for c in r.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            rows.append([shorten(text(c), 90) for c in cells])
    if not header and rows:
        header, rows = rows[0], rows[1:]
    if not header and not rows:
        return None
    ncols = min(MAX_TABLE_COLS, max([len(header)] + [len(r) for r in rows]))
    if max([len(header)] + [len(r) for r in rows]) > MAX_TABLE_COLS:
        warn.append(f"{where}: tabel ingekort tot {MAX_TABLE_COLS} kolommen")
    header = (header + [""] * ncols)[:ncols]
    rows = [(r + [""] * ncols)[:ncols] for r in rows if any(c for c in r)]
    if len(rows) > MAX_TABLE_ROWS:
        warn.append(f"{where}: tabel ingekort tot {MAX_TABLE_ROWS} rijen")
        rows = rows[:MAX_TABLE_ROWS]
    return Table(header, rows)


def _chart(value: Any, d: dict[str, Any], warn: list[str], where: str) -> Chart | None:
    c = _lower_keys(value) if isinstance(value, dict) else {}
    if isinstance(value, (list, tuple)):
        c = {"data": list(value)}
    src = {**d, **c}
    kind_raw = text(_get(c, ("kind", "type", "chart_type", "charttype", "soort", "style")) or
                    _get(d, ("chart_type", "chart_kind", "kind")) or "column").lower().replace(" ", "_")
    kind_raw = re.sub(r"_?(chart|grafiek|diagram)$", "", kind_raw) or "column"
    kind = _CHART_SYNONYMS.get(kind_raw, kind_raw if kind_raw in CHART_KINDS else "column")
    unit = text(_get(src, ("unit", "eenheid", "units", "y_unit", "value_unit", "suffix")))
    categories = [shorten(text(x), 30) for x in as_list(_get(src, ("categories", "categorieen", "categorieën",
                                                                   "labels", "x", "x_axis", "xaxis", "periods")))]
    series_raw = _get(src, ("series", "reeksen", "datasets", "values", "waarden", "y"))
    series: list[Series] = []
    units: list[str] = []

    def nums(vals: Any) -> list[float | None]:
        out: list[float | None] = []
        for v in as_list(vals) if not isinstance(vals, (list, tuple)) else vals:
            n, u = parse_number(v)
            if u:
                units.append(u)
            out.append(n)
        return out

    if isinstance(series_raw, dict) and not any(k in _lower_keys(series_raw) for k in ("name", "values", "data")):
        series_raw = [{"name": k, "values": v} for k, v in series_raw.items()]
    if isinstance(series_raw, (list, tuple)) and series_raw and not isinstance(series_raw[0], (dict, list, tuple)):
        series_raw = [{"name": unit or "", "values": series_raw}]
    for s in as_list(series_raw) if not isinstance(series_raw, list) else series_raw:
        if isinstance(s, dict):
            sd = _lower_keys(s)
            name = text(_get(sd, ("name", "naam", "label", "title", "serie")))
            vals = _get(sd, ("values", "waarden", "data", "y", "points"))
            if isinstance(vals, dict):
                if not categories:
                    categories = [shorten(text(k), 30) for k in vals.keys()]
                vals = list(vals.values())
            series.append(Series(shorten(name, 40), nums(vals)))
        elif isinstance(s, (list, tuple)):
            series.append(Series(f"Reeks {len(series) + 1}", nums(s)))
    if not series:
        data = _get(src, ("data", "points", "punten", "items"))
        if isinstance(data, dict):
            data = [{"label": k, "value": v} for k, v in data.items()]
        pairs: list[tuple[str, Any]] = []
        for p in as_list(data):
            if isinstance(p, dict):
                pd = _lower_keys(p)
                pairs.append((text(_get(pd, ("label", "category", "categorie", "name", "naam", "x"))),
                              _get(pd, ("value", "waarde", "y", "amount", "count"))))
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                pairs.append((text(p[0]), p[1]))
        if pairs:
            categories = [shorten(a, 30) for a, _ in pairs]
            series = [Series(unit, nums([b for _, b in pairs]))]
    if not series:
        return None
    if not unit and units:
        unit = max(set(units), key=units.count)
    n = max(len(s.values) for s in series)
    if not categories:
        categories = [str(i + 1) for i in range(n)]
    if len(categories) > MAX_CATEGORIES:
        warn.append(f"{where}: grafiek ingekort tot {MAX_CATEGORIES} categorieën")
        categories = categories[:MAX_CATEGORIES]
    if len(series) > MAX_SERIES:
        warn.append(f"{where}: grafiek ingekort tot {MAX_SERIES} reeksen")
        series = series[:MAX_SERIES]
    for s in series:
        s.values = (s.values + [None] * len(categories))[: len(categories)]
        if not s.name:
            s.name = unit or "Waarde"
    if all(v is None for s in series for v in s.values):
        return None
    if kind == "pie" and len(series) > 1:
        warn.append(f"{where}: taartdiagram toont alleen de eerste reeks")
        series = series[:1]
    if kind == "pie" and any(v is not None and v < 0 for v in series[0].values):
        kind = "column"
    return Chart(kind, categories, series, shorten(unit, 20))


def _image(value: Any, d: dict[str, Any]) -> Image | None:
    caption = text(_get(d, ("caption", "bijschrift", "onderschrift", "alt", "description")))
    if isinstance(value, dict):
        vd = _lower_keys(value)
        ref = text(_get(vd, ("ref", "src", "id", "path", "file", "name", "image", "diagram", "url")))
        caption = text(_get(vd, ("caption", "bijschrift", "alt", "title", "description"))) or caption
    else:
        ref = str(value).strip() if value is not None else ""
    if not ref:
        return None
    return Image(ref, shorten(caption, 160))


def _infer_type(d: dict[str, Any]) -> str | None:
    if _get(d, _KPI_KEYS):
        return "kpi"
    if _get(d, ("quote", "citaat", "quotation")):
        return "quote"
    if _get(d, ("comparison", "vergelijking")) or (_get(d, ("left", "links")) and _get(d, ("right", "rechts"))):
        return "comparison"
    if _get(d, ("chart", "grafiek", "graph", "series")):
        return "chart"
    if _get(d, ("table", "tabel", "rows", "rijen")):
        return "table"
    if _get(d, _STEP_KEYS):
        return "timeline"
    img = _get(d, _IMAGE_KEYS)
    if img:
        ref = text(img) if not isinstance(img, dict) else text(_get(_lower_keys(img), ("ref", "src", "id")))
        return "diagram" if ref.lower().startswith("diagram") or "diagram" in d else "image"
    if _get(d, _COLUMN_KEYS):
        return "two_column"
    if _get(d, _BULLET_KEYS):
        return "bullets"
    return None


def _slide(raw: Any, index: int, warn: list[str]) -> Slide | None:
    where = f"dia {index + 1}"
    if isinstance(raw, str):
        t = text(raw)
        return Slide("bullets", shorten(t, MAX_TITLE)) if t else None
    if isinstance(raw, (list, tuple)):
        items = [text(x) for x in raw if text(x)]
        if not items:
            return None
        return Slide("bullets", shorten(items[0], MAX_TITLE), bullets=_bullets(items[1:], warn, where))
    if not isinstance(raw, dict):
        return None
    d = _lower_keys(raw)
    for wrapper in ("slide", "dia", "content_slide"):
        if isinstance(d.get(wrapper), dict) and len(d) <= 2:
            d = {**d, **_lower_keys(d[wrapper])}
    type_raw = text(_get(d, ("type", "slide_type", "slidetype", "kind", "layout", "soort", "template"))).lower()
    type_raw = re.sub(r"[\s\-]+", "_", type_raw)
    type_raw = re.sub(r"_?(slide|dia)$", "", type_raw)
    stype = _TYPE_SYNONYMS.get(type_raw) or _TYPE_SYNONYMS.get(type_raw.rstrip("s"))
    inferred = _infer_type(d)
    if not stype:
        if type_raw:
            warn.append(f"{where}: onbekend type '{type_raw}', als {inferred or 'bullets'} behandeld")
        stype = inferred or "bullets"
    # A declared type without its content falls back to what the content says.
    s = Slide(stype)
    s.title = shorten(text(_get(d, _TITLE_KEYS)), MAX_TITLE)
    s.subtitle = shorten(text(_get(d, _SUBTITLE_KEYS)), MAX_SUBTITLE)
    s.kicker = shorten(text(_get(d, _KICKER_KEYS)), 40)
    notes = _get(d, _NOTES_KEYS)
    s.notes = "\n".join(text(n) for n in notes) if isinstance(notes, (list, tuple)) else (
        re.sub(r"[ \t]+", " ", str(notes)).strip() if notes else "")
    src = _get(d, _SOURCE_KEYS)
    s.source = shorten(text(src) if not isinstance(src, (list, tuple)) else "; ".join(text(x) for x in src), 200)

    bullet_raw = _get(d, _BULLET_KEYS)
    if stype == "quote":
        q = _get(d, ("quote", "citaat", "quotation", "text", "tekst"))
        if isinstance(q, dict):
            qd = _lower_keys(q)
            quote = Quote(text(_get(qd, ("text", "quote", "tekst", "citaat"))),
                          text(_get(qd, ("source", "author", "bron", "auteur", "by", "who", "spreker"))))
        else:
            quote = Quote(text(q) if q else "", text(_get(d, ("author", "auteur", "by", "who", "attribution",
                                                              "spreker", "speaker"))))
            if not quote.source and src and q:
                quote.source, s.source = text(src), ""
        if not quote.text and s.title:
            quote.text, s.title = s.title, ""
        if quote.text:
            quote.text = shorten(quote.text.strip("\"“”'„ "), 260)
            s.quote = quote
        else:
            s.type = inferred or "bullets"
    if s.type == "kpi":
        s.kpis = _kpis(_get(d, _KPI_KEYS) or bullet_raw)
        if len(s.kpis) > MAX_KPIS:
            warn.append(f"{where}: {len(s.kpis)} kpi's, ingekort tot {MAX_KPIS}")
            s.kpis = s.kpis[:MAX_KPIS]
        if not s.kpis:
            s.type = "bullets"
    if s.type == "timeline":
        s.steps = _steps(_get(d, _STEP_KEYS) or bullet_raw)
        if len(s.steps) > MAX_STEPS:
            warn.append(f"{where}: tijdlijn ingekort tot {MAX_STEPS} stappen")
            s.steps = s.steps[:MAX_STEPS]
        if not s.steps:
            s.type = "bullets"
    if s.type == "table":
        s.table = _table(_get(d, ("table", "tabel", "data")), d, warn, where)
        if not s.table:
            s.type = "bullets"
    if s.type == "chart":
        s.chart = _chart(_get(d, ("chart", "grafiek", "graph", "data")), d, warn, where)
        if not s.chart:
            warn.append(f"{where}: grafiek zonder bruikbare getallen, als bullets behandeld")
            s.type = "bullets"
        elif bullet_raw is not None and _get(d, ("bullets", "points", "key_points", "kernpunten", "punten")):
            s.bullets = _bullets(_get(d, ("bullets", "points", "key_points", "kernpunten", "punten")), warn, where)
    if s.type in ("image", "diagram"):
        s.image = _image(_get(d, _IMAGE_KEYS), d)
        if not s.image:
            warn.append(f"{where}: {s.type} zonder verwijzing, als bullets behandeld")
            s.type = "bullets"
        else:
            if s.image.ref.lower().startswith("diagram:"):
                s.type = "diagram"
            bl = _get(d, ("bullets", "points", "key_points", "kernpunten", "punten"))
            s.bullets = _bullets(bl, warn, where) if bl else []
    if s.type == "comparison":
        comp = _get(d, ("comparison", "vergelijking"))
        cd = _lower_keys(comp) if isinstance(comp, dict) else d
        left = _get(cd, ("left", "links", "option_a", "a", "before", "voor", "huidig", "current", "pros", "voordelen"))
        right = _get(cd, ("right", "rechts", "option_b", "b", "after", "na", "nieuw", "new", "cons", "nadelen"))
        opts = _get(cd, ("options", "opties", "columns", "kolommen", "alternatives", "alternatieven"))
        if (left is None or right is None) and isinstance(opts, (list, tuple)) and len(opts) >= 2:
            left, right = opts[0], opts[1]
        if left is None and isinstance(comp, (list, tuple)) and len(comp) >= 2:
            left, right = comp[0], comp[1]
        if left is not None and right is not None:
            lc, rc = _column(left, warn, where), _column(right, warn, where)
            if not lc.heading and "pros" in cd:
                lc.heading, rc.heading = "Voordelen", "Nadelen"
            verdict = text(_get(cd, ("verdict", "conclusion", "conclusie", "recommendation", "advies", "aanbeveling",
                                     "oordeel", "keuze"))
                           or _get(d, ("verdict", "conclusion", "conclusie", "advies", "aanbeveling")))
            s.comparison = Comparison(lc, rc, shorten(verdict, 160))
        else:
            s.type = "two_column" if _get(d, _COLUMN_KEYS) else "bullets"
    if s.type == "two_column":
        cols = _get(d, _COLUMN_KEYS)
        if cols is None and _get(d, ("left", "links")) is not None:
            cols = [_get(d, ("left", "links")), _get(d, ("right", "rechts"))]
        cl = [_column(c, warn, where) for c in as_list(cols) if c is not None] if cols is not None else []
        if len(cl) < 2 and bullet_raw:
            bl = _bullets(bullet_raw, warn, where)
            half = (len(bl) + 1) // 2
            cl = [Column("", bl[:half]), Column("", bl[half:])]
        if len(cl) > 2:
            warn.append(f"{where}: meer dan twee kolommen, alleen de eerste twee gebruikt")
        if len(cl) >= 2:
            s.columns = cl[:2]
        else:
            s.type = "bullets"
            s.bullets = cl[0].bullets if cl else []
    if s.type in ("bullets", "agenda", "closing", "section", "title") and not s.bullets:
        if s.type == "section" and isinstance(bullet_raw, str) and not s.subtitle:
            s.subtitle = shorten(text(bullet_raw), MAX_SUBTITLE)
        elif s.type == "title" and isinstance(bullet_raw, str) and not s.subtitle:
            s.subtitle = shorten(text(bullet_raw), MAX_SUBTITLE)
        else:
            s.bullets = _bullets(bullet_raw, warn, where)
    if s.type == "title":
        s.bullets = []
    if not s.title and s.type not in ("quote",):
        if s.type == "agenda":
            s.title = "Agenda"
        elif s.type == "closing":
            s.title = ""
        elif s.bullets and s.type == "bullets":
            s.title = s.bullets.pop(0).text
        elif s.type == "section" and s.subtitle:
            s.title, s.subtitle = s.subtitle, ""
    if not s.title and s.type in ("bullets", "section") and not s.bullets and not s.subtitle:
        return None
    return s


# --------------------------------------------------------------------------- deck


def _closing_title(lang: str) -> str:
    return "Vragen?" if lang.startswith("nl") else "Questions?"


def normalize_deck(raw: Any) -> DeckSpec:
    """Turn messy LLM output (dict, list of slides, or JSON text) into a clean DeckSpec.

    Raises ValueError only when nothing usable can be found.
    """
    data = _parse_raw(raw)
    if isinstance(data, list):
        data = {"slides": data}
    if not isinstance(data, dict):
        raise ValueError("deck spec must be an object or a list of slides")
    d = _lower_keys(data)
    for wrapper in ("deck", "presentation", "presentatie", "spec", "result"):
        inner = d.get(wrapper)
        if isinstance(inner, dict):
            d = {**d, **_lower_keys(inner)}
    warn: list[str] = []
    deck = DeckSpec(
        title=shorten(text(_get(d, ("title", "titel", "name", "naam", "deck_title", "onderwerp", "topic"))), MAX_TITLE),
        subtitle=shorten(text(_get(d, ("subtitle", "ondertitel", "sub_title", "tagline"))), MAX_SUBTITLE),
        author=shorten(text(_get(d, ("author", "auteur", "presenter", "presentator", "by", "door", "owner",
                                     "afzender"))), 80),
        date=shorten(text(_get(d, ("date", "datum", "when"))), 40),
        language=(text(_get(d, ("language", "lang", "taal", "locale"))) or "nl").lower()[:5],
        audience=shorten(text(_get(d, ("audience", "doelgroep", "publiek", "for", "voor"))), 60),
    )
    if deck.language in ("nederlands", "dutch", "nl_nl", "nl-nl"):
        deck.language = "nl"
    if deck.language in ("english", "engels", "en_gb", "en_us"):
        deck.language = "en"
    raw_slides = _get(d, ("slides", "dias", "dia_s", "sheets", "pages", "slide", "content", "inhoud"))
    if isinstance(raw_slides, dict):
        raw_slides = list(raw_slides.values()) if all(isinstance(v, dict) for v in raw_slides.values()) else [
            raw_slides]
    slides: list[Slide] = []
    for i, rs in enumerate(as_list(raw_slides) if not isinstance(raw_slides, list) else raw_slides):
        s = _slide(rs, i, warn)
        if s is not None:
            slides.append(s)
    if not slides and not deck.title:
        raise ValueError("deck spec contains no slides")
    lang = deck.language
    nl = lang.startswith("nl")

    # Title slide first; further title slides become section dividers.
    title_idx = [i for i, s in enumerate(slides) if s.type == "title"]
    if title_idx and title_idx[0] != 0:
        ts = slides.pop(title_idx[0])
        slides.insert(0, ts)
    for s in slides[1:]:
        if s.type == "title":
            s.type = "section"
    if not slides or slides[0].type != "title":
        slides.insert(0, Slide("title", deck.title or (slides[0].title if slides else ""), deck.subtitle))
    ts = slides[0]
    if not deck.title:
        deck.title = ts.title or "Presentatie"
    if not ts.title:
        ts.title = deck.title
    if not ts.subtitle and deck.subtitle and deck.subtitle != ts.title:
        ts.subtitle = deck.subtitle
    if not deck.subtitle:
        deck.subtitle = ts.subtitle
    if not ts.kicker and deck.audience:
        ts.kicker = deck.audience

    # Closing slide last (a closing slide earlier in the deck becomes a normal bullets slide).
    for s in slides[1:-1]:
        if s.type == "closing":
            s.type = "bullets"
    if slides[-1].type != "closing" or len(slides) == 1:
        slides.append(Slide("closing", _closing_title(lang)))
    if not slides[-1].title:
        slides[-1].title = ("Vervolgstappen" if nl else "Next steps") if slides[-1].bullets else _closing_title(lang)

    # Section numbering + auto agenda.
    sections = [s for s in slides if s.type == "section"]
    for n, s in enumerate(sections, 1):
        s.number = n
    agendas = [s for s in slides if s.type == "agenda"]
    if not agendas and len(sections) >= 2:
        slides.insert(1, Slide("agenda", "Agenda", bullets=[Bullet(s.title) for s in sections],
                               notes=("We lopen de volgende onderdelen door." if nl else "This is what we will cover.")))
    for a in agendas:
        if not a.bullets:
            a.bullets = [Bullet(s.title) for s in sections] or [
                Bullet(s.title) for s in slides if s.type not in ("title", "agenda", "closing")][:6]
        for b in a.bullets:
            b.sub = []
    # Content slides of a section get the section title as kicker (when the LLM gave none).
    current = ""
    for s in slides:
        if s.type == "section":
            current = s.title
        elif s.type not in ("title", "agenda", "closing") and not s.kicker and current:
            s.kicker = shorten(current, 40)
    deck.slides = slides
    deck.warnings = warn
    return deck
