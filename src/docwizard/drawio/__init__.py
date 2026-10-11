"""Deterministic architecture diagrams from a small LLM-produced JSON spec.

    spec = normalize_spec(raw)          # lenient clean-up of LLM output
    xml = build_drawio(spec)            # native, uncompressed draw.io file content
    svg = render_svg(spec)              # standalone SVG preview (same layout)
    png = render_png(spec, scale=2.0)   # Pillow PNG for Word embedding (same layout)
"""

from __future__ import annotations

from typing import Any

from .drawio_xml import build_xml
from .layout import Layout, compute_layout
from .prompt import SPEC_JSON_SCHEMA, SPEC_PROMPT_GUIDE
from .render import scene_to_png, scene_to_svg
from .scene import build_scene
from .spec import DiagramSpec, Edge, Group, Node, normalize_spec
from .styles import edge_text, node_runs

__all__ = [
    "DiagramSpec",
    "Node",
    "Group",
    "Edge",
    "Layout",
    "normalize_spec",
    "compute_layout",
    "build_drawio",
    "render_svg",
    "render_png",
    "SPEC_PROMPT_GUIDE",
    "SPEC_JSON_SCHEMA",
]


def _spec(spec: DiagramSpec | dict[str, Any] | list[Any] | str) -> DiagramSpec:
    return spec if isinstance(spec, DiagramSpec) else normalize_spec(spec)


def build_drawio(spec: DiagramSpec | dict[str, Any] | list[Any] | str) -> str:
    """Full uncompressed <mxfile> document; opens in draw.io desktop and app.diagrams.net."""
    return build_xml(compute_layout(_spec(spec)), node_runs, edge_text)


def render_svg(spec: DiagramSpec | dict[str, Any] | list[Any] | str) -> str:
    s = _spec(spec)
    return scene_to_svg(build_scene(compute_layout(s)), s.title)


def render_png(spec: DiagramSpec | dict[str, Any] | list[Any] | str, scale: float = 2.0) -> bytes:
    return scene_to_png(build_scene(compute_layout(_spec(spec))), scale)
