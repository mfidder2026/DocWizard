"""LLM-facing documentation of the spec: a compact prompt guide and a JSON schema."""

from __future__ import annotations

from typing import Any

from .catalog import ARCHIMATE_RELATIONS, ARCHIMATE_TYPES, C4_TYPES, GENERIC_TYPES, SIMPLE_RELATIONS
from .spec import GROUP_KINDS

_CORE_ARCHIMATE = [
    "business-actor", "business-role", "business-process", "business-function", "business-service",
    "business-object", "business-event", "application-component", "application-service",
    "application-interface", "application-function", "application-process", "data-object", "node", "device",
    "system-software", "technology-service", "artifact", "communication-network", "path", "stakeholder", "driver",
    "goal", "principle", "requirement", "constraint", "work-package", "deliverable", "plateau", "gap",
    "grouping", "location",
]

SPEC_PROMPT_GUIDE = f"""\
Describe the diagram as ONE JSON object (no prose, no comments). Do NOT give coordinates or sizes:
layout, colours and shapes are computed automatically.

Fields:
- "title": short diagram title.
- "notation": "archimate" (enterprise architecture views), "c4" (software context/container/component
  diagrams) or "generic" (simple infrastructure / cloud / flow pictures).
- "direction": "TB" (top to bottom, default) or "LR" (left to right).
- "groups": optional containers: {{"id", "label", "kind", "parent"}}. kind is one of
  {", ".join(GROUP_KINDS)}. Use kind "layer" for full-width horizontal bands (e.g. Business / Application /
  Technology layers, listed top to bottom). "parent" is another group id or null (nesting is allowed).
- "nodes": {{"id", "label", "type", "group", "description", "tech"}}. id: short unique slug
  (e.g. "zaaksysteem"). label: 1-4 words. group: a group id or null. tech: optional technology
  (e.g. "PostgreSQL"). description: optional, max ~12 words (shown in C4 only).
- "edges": {{"from", "to", "label", "type"}}: from/to are node ids. label: optional, 1-3 words.

Node types per notation:
- archimate: {", ".join(_CORE_ARCHIMATE)}.
- c4: {", ".join(C4_TYPES)}.
- generic: {", ".join(GENERIC_TYPES)}.
Edge types: archimate uses {", ".join(ARCHIMATE_RELATIONS)} (serving points from the service provider to
the user; access points from the behaviour/component to the data and may add "access": "read"|"write"|
"readwrite"); c4 and generic use {", ".join(SIMPLE_RELATIONS)} (default = solid arrow, async = dashed arrow).

Rules: 4-20 nodes; every node in at most one group; every edge must reference existing node ids;
keep labels in the document's language; avoid edges that just repeat containment.

Example:
{{"title":"Doelarchitectuur zaakgericht werken","notation":"archimate","direction":"TB",
"groups":[{{"id":"bus","label":"Bedrijfslaag","kind":"layer"}},{{"id":"app","label":"Applicatielaag","kind":"layer"}},
{{"id":"tech","label":"Technologielaag","kind":"layer"}}],
"nodes":[{{"id":"behandelaar","label":"Behandelaar","type":"business-role","group":"bus"}},
{{"id":"afhandelen","label":"Zaak afhandelen","type":"business-process","group":"bus"}},
{{"id":"zaakdienst","label":"Zaakregistratie","type":"application-service","group":"app"}},
{{"id":"zaaksysteem","label":"Zaaksysteem","type":"application-component","group":"app"}},
{{"id":"db","label":"Zaakdatabase","type":"system-software","group":"tech","tech":"PostgreSQL"}}],
"edges":[{{"from":"behandelaar","to":"afhandelen","type":"assignment"}},
{{"from":"zaakdienst","to":"afhandelen","type":"serving"}},
{{"from":"zaaksysteem","to":"zaakdienst","type":"realization"}},
{{"from":"db","to":"zaaksysteem","type":"serving"}}]}}
"""

_ALL_NODE_TYPES = sorted(set(ARCHIMATE_TYPES) | set(C4_TYPES) | set(GENERIC_TYPES))
_ALL_EDGE_TYPES = sorted(set(ARCHIMATE_RELATIONS) | set(SIMPLE_RELATIONS))

SPEC_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "DiagramSpec",
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "notation", "direction", "groups", "nodes", "edges"],
    "properties": {
        "title": {"type": "string"},
        "notation": {"type": "string", "enum": ["archimate", "c4", "generic"]},
        "direction": {"type": "string", "enum": ["TB", "LR"]},
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "label", "kind", "parent"],
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "kind": {"type": "string", "enum": list(GROUP_KINDS)},
                    "parent": {"type": ["string", "null"]},
                },
            },
        },
        "nodes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "label", "type", "group"],
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "type": {"type": "string", "enum": _ALL_NODE_TYPES},
                    "group": {"type": ["string", "null"]},
                    "description": {"type": "string"},
                    "tech": {"type": "string"},
                },
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["from", "to", "type"],
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "label": {"type": "string"},
                    "type": {"type": "string", "enum": _ALL_EDGE_TYPES},
                    "tech": {"type": "string"},
                    "access": {"type": "string", "enum": ["read", "write", "readwrite"]},
                },
            },
        },
    },
}
