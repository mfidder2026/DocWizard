"""Diagram spec data model and a lenient normaliser for (weak) LLM output."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .catalog import (
    ARCHIMATE_LAYER_LABELS,
    ARCHIMATE_LAYER_ORDER,
    LAYER_KEYWORDS,
    NOTATIONS,
    access_mode,
    archimate_layer,
    compact,
    detect_notation,
    resolve_node_type,
    resolve_relation,
)

GROUP_KINDS = ("layer", "zone", "system", "cloud", "boundary")


@dataclass
class Group:
    id: str
    label: str
    kind: str = "zone"
    parent: str | None = None


@dataclass
class Node:
    id: str
    label: str
    type: str
    group: str | None = None
    description: str = ""
    tech: str = ""


@dataclass
class Edge:
    source: str
    target: str
    label: str = ""
    type: str = "default"
    tech: str = ""
    access: str | None = None  # ArchiMate access mode: read | write | readwrite


@dataclass
class DiagramSpec:
    title: str = ""
    notation: str = "generic"
    direction: str = "TB"
    groups: list[Group] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    language: str = "nl"
    legend: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "notation": self.notation,
            "direction": self.direction,
            "language": self.language,
            "groups": [g.__dict__.copy() for g in self.groups],
            "nodes": [n.__dict__.copy() for n in self.nodes],
            "edges": [{"from": e.source, "to": e.target, "label": e.label, "type": e.type, "tech": e.tech,
                       **({"access": e.access} if e.access else {})} for e in self.edges],
        }


# --------------------------------------------------------------------------- helpers

_NODE_KEYS = ("nodes", "elements", "components", "items", "entities", "boxes", "vertices", "objects", "systems")
_GROUP_KEYS = ("groups", "containers", "layers", "zones", "boundaries", "clusters", "lanes")
_EDGE_KEYS = ("edges", "relations", "relationships", "links", "connections", "flows", "arrows", "dependencies")
_ID_LIKE = re.compile(r"^[a-z]{1,5}[_\-]?\d+$", re.IGNORECASE)
_EDGE_TEXT = re.compile(r"^\s*(.+?)\s*(?:-+>|=+>|→|->>|--)\s*(.+?)\s*(?::\s*(.*))?$")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(v) for v in value if v is not None)
    if isinstance(value, dict):
        return _text(_first(value, ("label", "name", "title", "text", "id")))
    return re.sub(r"\s+", " ", str(value)).strip()


def _first(d: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    lowered = {str(k).lower(): v for k, v in d.items()}
    for key in keys:
        if key in lowered and lowered[key] not in (None, ""):
            return lowered[key]
    return default


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug[:40] or "item"


class _IdPool:
    def __init__(self) -> None:
        self.used: set[str] = set()

    def take(self, wanted: str, fallback: str) -> str:
        base = _slug(wanted) if wanted else fallback
        candidate, i = base, 2
        while candidate in self.used:
            candidate = f"{base}-{i}"
            i += 1
        self.used.add(candidate)
        return candidate


def _parse_raw(raw: Any) -> Any:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        text = raw.strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start > 0:
            text = text[start:]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Trailing commas are the most common LLM JSON defect.
            return json.loads(re.sub(r",\s*([}\]])", r"\1", text))
    return raw


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        # {"id": {...}} mapping, or a single object
        if all(isinstance(v, (dict, str)) for v in value.values()) and not _first(value, ("label", "name", "id")):
            out = []
            for k, v in value.items():
                if isinstance(v, dict):
                    out.append({"id": k, **v} if "id" not in v else v)
                else:
                    out.append({"id": k, "label": v})
            return out
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


# --------------------------------------------------------------------------- normaliser


def normalize_spec(raw: Any) -> DiagramSpec:
    """Turn messy LLM output (dict, list, or JSON text) into a clean, consistent DiagramSpec.

    Raises ValueError only when no nodes can be found at all.
    """
    data = _parse_raw(raw)
    if isinstance(data, list):
        data = {"nodes": data}
    if not isinstance(data, dict):
        raise ValueError("diagram spec must be an object or a list of nodes")
    for wrapper in ("diagram", "spec", "architecture", "model"):
        inner = data.get(wrapper)
        if isinstance(inner, dict) and any(k in inner for k in _NODE_KEYS):
            data = {**data, **inner}

    raw_nodes: list[Any] = []
    for key in _NODE_KEYS:
        raw_nodes += _as_list(_first(data, (key,)))
    raw_groups: list[Any] = []
    for key in _GROUP_KEYS:
        raw_groups += _as_list(_first(data, (key,)))
    raw_edges: list[Any] = []
    for key in _EDGE_KEYS:
        raw_edges += _as_list(_first(data, (key,)))

    # Nodes nested inside groups ("children"/"nodes" lists).
    group_dicts: list[dict[str, Any]] = []
    for i, g in enumerate(raw_groups):
        gd = {"label": g} if not isinstance(g, dict) else dict(g)
        gd.setdefault("_index", i)
        for key in ("nodes", "children", "elements", "components", "items"):
            kids = gd.pop(key, None)
            for kid in _as_list(kids):
                if isinstance(kid, dict) and any(k in kid for k in ("nodes", "children")) and "type" not in kid:
                    kid = {**kid, "parent": gd.get("id") or gd.get("label") or gd.get("name")}
                    raw_groups.append(kid)
                    continue
                kd = {"label": kid} if not isinstance(kid, dict) else dict(kid)
                kd.setdefault("group", gd.get("id") or gd.get("label") or gd.get("name"))
                raw_nodes.append(kd)
        group_dicts.append(gd)

    if not raw_nodes:
        raise ValueError("diagram spec contains no nodes")

    language = "en" if compact(_first(data, ("language", "lang"), "nl")).startswith("en") else "nl"
    raw_types = [_text(_first(n, ("type", "kind", "element_type", "elementtype", "category", "shape")))
                 for n in raw_nodes if isinstance(n, dict)]
    notation = compact(_first(data, ("notation", "style", "diagram_type", "type"), ""))
    if notation.startswith("archi"):
        notation = "archimate"
    elif notation.startswith("c4"):
        notation = "c4"
    elif notation not in NOTATIONS:
        notation = detect_notation(raw_types)

    direction = compact(_first(data, ("direction", "rankdir", "orientation", "layout"), "TB"))
    direction = "LR" if direction in {"lr", "rl", "leftright", "lefttoright", "horizontal", "rightleft"} else "TB"

    spec = DiagramSpec(
        title=_text(_first(data, ("title", "name", "caption", "label"))),
        notation=notation,
        direction=direction,
        language=language,
        legend=bool(_first(data, ("legend",), True)),
    )

    # ---- groups
    gpool = _IdPool()
    group_lookup: dict[str, str] = {}  # raw id / label (compacted) -> id
    pending_parents: list[tuple[Group, Any]] = []
    for gd in group_dicts:
        label = _text(_first(gd, ("label", "name", "title", "text", "id"))) or "Groep"
        raw_id = _text(_first(gd, ("id", "key")))
        gid = gpool.take(raw_id or label, "g")
        kind_raw = compact(_first(gd, ("kind", "type", "category"), ""))
        kind = next((k for k in GROUP_KINDS if kind_raw.startswith(k)), None)
        if kind is None:
            kind = "layer" if ("laag" in kind_raw or "layer" in kind_raw or "tier" in kind_raw) else "zone"
        group = Group(gid, label, kind)
        spec.groups.append(group)
        for key in (raw_id, label):
            if key:
                group_lookup.setdefault(compact(key), gid)
        group_lookup.setdefault(compact(gid), gid)
        pending_parents.append((group, _first(gd, ("parent", "group", "in", "within"))))
    for group, parent_raw in pending_parents:
        pid = group_lookup.get(compact(_text(parent_raw))) if parent_raw else None
        group.parent = pid if pid != group.id else None

    def resolve_group(raw_group: Any) -> str | None:
        name = _text(raw_group)
        if not name:
            return None
        gid = group_lookup.get(compact(name))
        if gid or _ID_LIKE.match(name):
            return gid
        # Unknown, but descriptive -> create the group the LLM evidently meant.
        kind = "layer" if re.search(r"laag|layer|tier", name, re.IGNORECASE) else "zone"
        new = Group(gpool.take(name, "g"), name, kind)
        spec.groups.append(new)
        group_lookup[compact(name)] = new.id
        return new.id

    # ---- nodes
    npool = _IdPool()
    node_lookup: dict[str, str] = {}
    for i, rn in enumerate(raw_nodes):
        nd = {"label": rn} if not isinstance(rn, dict) else rn
        label = _text(_first(nd, ("label", "name", "title", "text", "id"))) or f"Element {i + 1}"
        raw_id = _text(_first(nd, ("id", "key", "ref")))
        type_raw = _first(nd, ("type", "kind", "element_type", "elementtype", "category", "shape"), "")
        node_type = resolve_node_type(_text(type_raw) or label, notation)
        if raw_id and compact(raw_id) in node_lookup:
            existing = next(n for n in spec.nodes if n.id == node_lookup[compact(raw_id)])
            if compact(existing.label) == compact(label):
                continue  # exact duplicate
        node = Node(
            id=npool.take(raw_id or label, "n"),
            label=label,
            type=node_type,
            group=resolve_group(_first(nd, ("group", "parent", "layer", "container", "zone", "boundary", "in"))),
            description=_text(_first(nd, ("description", "desc", "details", "summary", "toelichting"))),
            tech=_text(_first(nd, ("tech", "technology", "techniek", "technologie", "stack", "platform"))),
        )
        spec.nodes.append(node)
        for key in (raw_id, label, node.id):
            if key:
                node_lookup.setdefault(compact(key), node.id)

    _break_group_cycles(spec)

    # ---- edges
    seen: set[tuple[str, str, str, str]] = set()
    nodes_by_id = {n.id: n for n in spec.nodes}

    def resolve_node(ref: Any) -> str | None:
        name = _text(ref)
        if not name:
            return None
        nid = node_lookup.get(compact(name))
        if nid:
            return nid
        if _ID_LIKE.match(name) or compact(name) in group_lookup:
            return None
        node = Node(npool.take(name, "n"), name, resolve_node_type(name, notation))
        spec.nodes.append(node)
        nodes_by_id[node.id] = node
        node_lookup[compact(name)] = node.id
        return node.id

    for re_ in raw_edges:
        if isinstance(re_, str):
            m = _EDGE_TEXT.match(re_)
            if not m:
                continue
            ed: dict[str, Any] = {"from": m.group(1), "to": m.group(2), "label": m.group(3) or ""}
        elif isinstance(re_, (list, tuple)) and len(re_) >= 2:
            ed = {"from": re_[0], "to": re_[1], "label": re_[2] if len(re_) > 2 else ""}
        elif isinstance(re_, dict):
            ed = re_
        else:
            continue
        src = resolve_node(_first(ed, ("from", "source", "src", "start", "from_id", "source_id", "origin", "van")))
        dst = resolve_node(_first(ed, ("to", "target", "dst", "end", "to_id", "target_id", "destination", "naar")))
        if not src or not dst or src == dst:
            continue
        rel_raw = _text(_first(ed, ("type", "relation", "relationship", "kind", "rel", "style")))
        label = _text(_first(ed, ("label", "name", "description", "text", "title")))
        rel = resolve_relation(rel_raw, notation) if rel_raw else None
        explicit_mode = compact(_first(ed, ("access", "mode", "access_mode", "accesstype"), ""))
        mode = ({"read": "read", "write": "write", "readwrite": "readwrite", "rw": "readwrite", "lezen": "read",
                 "schrijven": "write"}.get(explicit_mode) or access_mode(rel_raw)
                or (access_mode(label.split(" ")[0]) if label else None))
        if notation == "archimate":
            if rel is None and mode:
                rel = "access"
            inferred = rel is None
            if rel is None:
                rel = _infer_archimate_relation(nodes_by_id[src].type, nodes_by_id[dst].type)
            if rel == "access" and not ({nodes_by_id[src].type, nodes_by_id[dst].type} & _PASSIVE):
                # "component reads database (system software)": not valid access -> database serves component.
                rel, inferred = "serving", False
                src, dst = dst, src
            if rel == "serving" and (compact(rel_raw) in _USES_WORDS or inferred):
                ls = _LAYER_RANK.get(archimate_layer(nodes_by_id[src].type))
                ld = _LAYER_RANK.get(archimate_layer(nodes_by_id[dst].type))
                # "A uses B" means B serves A; untyped cross-layer edges point from the upper layer down.
                if compact(rel_raw) in _USES_WORDS or (ls is not None and ld is not None and ls < ld):
                    src, dst = dst, src
            if rel == "access":
                src, dst, mode = _orient_access(nodes_by_id, src, dst, mode)
            else:
                mode = None
        else:
            rel = rel or "default"
            mode = None
        tech = _text(_first(ed, ("tech", "technology", "protocol", "techniek")))
        key = (src, dst, rel, label)
        if key in seen:
            continue
        seen.add(key)
        spec.edges.append(Edge(src, dst, label, rel, tech, mode))

    _finish_groups(spec)
    return spec


_PASSIVE = {"business-object", "data-object", "artifact", "contract", "representation"}
_SERVICES = {"business-service", "application-service", "technology-service"}
_ACTIVE = {"business-actor", "business-role", "application-component", "node", "device", "system-software",
           "business-collaboration", "application-collaboration", "technology-collaboration"}
_BEHAVIOUR = {"business-process", "business-function", "business-interaction", "application-function",
              "application-process", "application-interaction", "technology-function", "technology-process",
              "technology-interaction"}
_EVENTS = {"business-event", "application-event", "technology-event", "implementation-event"}
_MOTIVATION = {"stakeholder", "driver", "assessment", "goal", "outcome", "principle", "requirement", "constraint",
               "meaning", "value"}
_LAYER_RANK = {"business": 0, "application": 1, "technology": 2}
_USES_WORDS = {"uses", "use", "gebruikt", "calls", "call", "roeptaan", "consumes", "dependson", "afhankelijkvan"}


def _infer_archimate_relation(src: str, dst: str) -> str:
    """Best-guess ArchiMate relationship for an untyped edge, based on element types."""
    if dst in _PASSIVE or src in _PASSIVE:
        return "access"
    if src in _MOTIVATION or dst in _MOTIVATION:
        return "influence" if src in _MOTIVATION and dst in _MOTIVATION else "realization"
    if src in _SERVICES:
        return "serving"
    if dst in _SERVICES:
        return "realization"
    if src in {"business-actor", "business-role"} and (dst in _BEHAVIOUR or dst == "business-role"):
        return "assignment"
    if (src in _BEHAVIOUR | _EVENTS) and (dst in _BEHAVIOUR | _EVENTS):
        return "triggering"
    if src in {"node", "device", "system-software"} and dst in {"system-software", "artifact"}:
        return "assignment"
    if src == dst == "application-component":
        return "flow"
    ls, ld = _LAYER_RANK.get(archimate_layer(src)), _LAYER_RANK.get(archimate_layer(dst))
    if ls is not None and ld is not None and ls != ld:
        return "serving"
    return "association"


def _orient_access(nodes: dict[str, Node], src: str, dst: str, mode: str | None) -> tuple[str, str, str | None]:
    """ArchiMate access always runs from the behaviour/active element to the passive one."""
    if nodes[src].type in _PASSIVE and nodes[dst].type not in _PASSIVE:
        flipped = {"read": "write", "write": "read"}.get(mode or "", mode)
        # LLM drew data -> component: that usually means "component reads data".
        return dst, src, flipped if mode else "read"
    return src, dst, mode


def _break_group_cycles(spec: DiagramSpec) -> None:
    by_id = {g.id: g for g in spec.groups}
    for g in spec.groups:
        seen = {g.id}
        cur = g
        while cur.parent:
            if cur.parent in seen or cur.parent not in by_id:
                cur.parent = None
                break
            seen.add(cur.parent)
            cur = by_id[cur.parent]


def _layer_of_group(group: Group, spec: DiagramSpec) -> str | None:
    """Which ArchiMate layer a layer-group represents (by its nodes, else by its label)."""
    counts: dict[str, int] = {}
    for n in spec.nodes:
        if n.group == group.id:
            layer = archimate_layer(n.type)
            counts[layer] = counts.get(layer, 0) + 1
    if counts:
        return max(counts, key=lambda k: (counts[k], -ARCHIMATE_LAYER_ORDER.index(k)))
    key = compact(group.label)
    for layer, words in LAYER_KEYWORDS.items():
        if any(w in key for w in words):
            return layer
    return None


def _finish_groups(spec: DiagramSpec) -> None:
    """Drop empty groups, home orphan nodes in layered views, auto-layer plain ArchiMate views."""
    top_layers = [g for g in spec.groups if g.kind == "layer" and g.parent is None]
    labels = ARCHIMATE_LAYER_LABELS[spec.language]

    if spec.notation == "archimate" and not spec.groups:
        present = {archimate_layer(n.type) for n in spec.nodes}
        if len(present) >= 2:
            for layer in ARCHIMATE_LAYER_ORDER:
                if layer in present:
                    spec.groups.append(Group(f"layer-{layer}", labels[layer], "layer"))
            for n in spec.nodes:
                n.group = f"layer-{archimate_layer(n.type)}"
            top_layers = list(spec.groups)

    if top_layers:
        orphans = [n for n in spec.nodes if n.group is None]
        if orphans:
            layer_map: dict[str, str] = {}
            if spec.notation == "archimate":
                for g in top_layers:
                    layer = _layer_of_group(g, spec)
                    if layer:
                        layer_map.setdefault(layer, g.id)
            for n in orphans:
                layer = archimate_layer(n.type) if spec.notation == "archimate" else "other"
                gid = layer_map.get(layer)
                if gid is None:
                    gid = f"layer-{layer}"
                    if gid not in {g.id for g in spec.groups}:
                        label = labels.get(layer, labels["other"]) if spec.notation == "archimate" else (
                            "Overig" if spec.language == "nl" else "Other")
                        spec.groups.append(Group(gid, label, "layer"))
                    layer_map[layer] = gid
                n.group = gid
        # Order top-level layer bands canonically for ArchiMate (business above application ...).
        if spec.notation == "archimate":
            original = {g.id: i for i, g in enumerate(spec.groups)}
            keys: dict[str, tuple[int, int]] = {}
            for g in spec.groups:
                if g.kind != "layer" or g.parent is not None:
                    keys[g.id] = (len(ARCHIMATE_LAYER_ORDER), original[g.id])
                else:
                    layer = _layer_of_group(g, spec) or "other"
                    keys[g.id] = (ARCHIMATE_LAYER_ORDER.index(layer), original[g.id])
            spec.groups.sort(key=lambda g: keys[g.id])

    # Remove groups that contain nothing (recursively).
    while True:
        used = {n.group for n in spec.nodes} | {g.parent for g in spec.groups}
        empty = [g for g in spec.groups if g.id not in used]
        if not empty:
            break
        spec.groups = [g for g in spec.groups if g.id in used]
