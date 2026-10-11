"""LLM-facing documentation of the deck spec: a compact prompt guide and a permissive JSON schema."""

from __future__ import annotations

from typing import Any

from .spec import CHART_KINDS, SLIDE_TYPES

DECK_PROMPT_GUIDE = """\
Write the presentation as ONE JSON object (no prose, no markdown, no comments). Do NOT describe colours,
fonts, positions or sizes: the design is applied automatically.

Storyline rules
- Pyramid principle: start with the answer/recommendation, then the supporting arguments, then details.
- Every slide title is an ACTION TITLE: one full sentence with the takeaway (max ~15 words),
  e.g. "Een centrale kluis halveert het risico op misbruik van beheeraccounts", not "Risico's".
- One message per slide. Max 5 bullets per slide, max ~12 words per bullet; sub-bullets are rare.
- Pick the slide type that fits the content: kpi for 2-4 key numbers, timeline for phases/planning,
  comparison for options or before/after (with a verdict), chart for number series, table for structured
  facts, quote for a strong statement, section to open each main part. Use bullets only when nothing fits.
- Use only facts from the source document; copy numbers exactly. Put the source in "source" when useful.
- Write all text in the deck language ("nl" = Dutch). Speaker notes: 2-4 sentences of what to SAY.
- 8-14 slides is typical. A title slide comes first and a closing slide (next steps or "Vragen?") last.

Fields
- deck: "title", "subtitle", "author", "date", "language" ("nl"), "audience", "slides": [...]
- every slide: "type", "title", optional "subtitle", "notes", "source"
- type-specific fields:
  title: (title/subtitle only) | agenda: "bullets" (agenda items) | section: "title", "subtitle"
  bullets: "bullets": ["text", {"text": "...", "sub": ["..."]}]
  two_column: "columns": [{"heading", "bullets": [...]}, {"heading", "bullets": [...]}]
  kpi: "kpis": [{"value": "€ 1,2 mln", "label": "Besparing per jaar", "detail": "short context"}] (2-4)
  quote: "quote": {"text", "source"}
  timeline: "steps": [{"label", "date", "text"}] (3-6)
  table: "table": {"header": [...], "rows": [[...], ...]} (max 6 columns, max ~8 rows)
  chart: "chart": {"kind": "bar|column|line|pie|stacked", "categories": [...],
         "series": [{"name", "values": [numbers]}], "unit": "€ mln"}, optional "bullets" (key points)
  diagram / image: "image": {"ref": "diagram:<id>" or media name, "caption"}, optional "bullets"
  comparison: "comparison": {"left": {"heading", "bullets"}, "right": {"heading", "bullets"}, "verdict"}
  closing: optional "bullets" (next steps / decisions asked)

Example (shortened):
{"title":"Centrale wachtwoordkluis voor beheeraccounts","subtitle":"Advies aan de stuurgroep",
"author":"Enterprise architectuur","date":"oktober 2026","language":"nl","audience":"Stuurgroep IV",
"slides":[
{"type":"title","title":"Centrale wachtwoordkluis voor beheeraccounts","subtitle":"Advies aan de stuurgroep"},
{"type":"bullets","title":"Wij adviseren een centrale kluis in te voeren vóór Q3 2027",
 "bullets":["Beheeraccounts zijn nu verspreid en slecht te controleren","Een kluis maakt elk gebruik herleidbaar",
 "Investering verdient zich binnen twee jaar terug"],"notes":"Ik begin met het advies. Daarna licht ik toe waarom."},
{"type":"section","title":"Waarom nu"},
{"type":"kpi","title":"Het huidige beheer kost tijd en vergroot het risico",
 "kpis":[{"value":"1.240","label":"Beheeraccounts","detail":"verspreid over 38 systemen"},
 {"value":"62%","label":"Gedeelde wachtwoorden"}],"source":"Inventarisatie IV, 2026"},
{"type":"comparison","title":"Een centrale kluis scoort beter dan het huidige beheer",
 "comparison":{"left":{"heading":"Huidige situatie","bullets":["Wachtwoorden in Excel"]},
 "right":{"heading":"Centrale kluis","bullets":["Automatische rotatie"]},"verdict":"Kies de centrale kluis"}},
{"type":"closing","title":"Wij vragen de stuurgroep om drie besluiten",
 "bullets":["Akkoord op het advies","Budget van € 450.000","Start pilot in januari"]}]}
"""

_TEXT_LIST: dict[str, Any] = {
    "type": "array",
    "items": {"anyOf": [
        {"type": "string"},
        {"type": "object", "properties": {"text": {"type": "string"}, "sub": {"type": "array",
                                                                             "items": {"type": "string"}}}},
    ]},
}
_COLUMN: dict[str, Any] = {"type": "object", "properties": {"heading": {"type": "string"}, "bullets": _TEXT_LIST}}

DECK_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "author": {"type": "string"},
        "date": {"type": "string"},
        "language": {"type": "string"},
        "audience": {"type": "string"},
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(SLIDE_TYPES)},
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "bullets": _TEXT_LIST,
                    "columns": {"type": "array", "items": _COLUMN},
                    "kpis": {"type": "array", "items": {"type": "object", "properties": {
                        "value": {"type": ["string", "number"]}, "label": {"type": "string"},
                        "detail": {"type": "string"}}}},
                    "quote": {"type": "object", "properties": {"text": {"type": "string"},
                                                               "source": {"type": "string"}}},
                    "steps": {"type": "array", "items": {"type": "object", "properties": {
                        "label": {"type": "string"}, "date": {"type": "string"}, "text": {"type": "string"}}}},
                    "table": {"type": "object", "properties": {
                        "header": {"type": "array", "items": {"type": "string"}},
                        "rows": {"type": "array", "items": {"type": "array",
                                                            "items": {"type": ["string", "number"]}}}}},
                    "chart": {"type": "object", "properties": {
                        "kind": {"type": "string", "enum": list(CHART_KINDS)},
                        "categories": {"type": "array", "items": {"type": "string"}},
                        "series": {"type": "array", "items": {"type": "object", "properties": {
                            "name": {"type": "string"},
                            "values": {"type": "array", "items": {"type": ["number", "string", "null"]}}}}},
                        "unit": {"type": "string"}}},
                    "image": {"type": "object", "properties": {"ref": {"type": "string"},
                                                               "caption": {"type": "string"}}},
                    "comparison": {"type": "object", "properties": {"left": _COLUMN, "right": _COLUMN,
                                                                    "verdict": {"type": "string"}}},
                    "notes": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["type", "title"],
            },
        },
    },
    "required": ["title", "slides"],
}
