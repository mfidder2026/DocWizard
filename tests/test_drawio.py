"""Tests for the deterministic draw.io diagram module."""

from __future__ import annotations

import io
import json

import pytest
from lxml import etree
from PIL import Image

from docwizard.drawio import (
    SPEC_JSON_SCHEMA,
    SPEC_PROMPT_GUIDE,
    DiagramSpec,
    build_drawio,
    compute_layout,
    normalize_spec,
    render_png,
    render_svg,
)

ARCHIMATE = {
    "title": "Doelarchitectuur zaaksysteem & <archief> \"test\"",
    "notation": "archimate",
    "direction": "TB",
    "groups": [
        {"id": "bus", "label": "Bedrijfslaag", "kind": "layer"},
        {"id": "app", "label": "Applicatielaag", "kind": "layer"},
        {"id": "tech", "label": "Technologielaag", "kind": "layer"},
    ],
    "nodes": [
        {"id": "burger", "label": "Burger", "type": "business-actor", "group": "bus"},
        {"id": "proces", "label": "Aanvraag behandelen", "type": "business-process", "group": "bus"},
        {"id": "dienst", "label": "Vergunning verlenen", "type": "business-service", "group": "bus"},
        {"id": "zs", "label": "Zaaksysteem", "type": "application-component", "group": "app"},
        {"id": "zreg", "label": "Zaakregistratie", "type": "application-service", "group": "app"},
        {"id": "data", "label": "Zaakgegevens", "type": "data-object", "group": "app"},
        {"id": "dms", "label": "Documentmanagementsysteem", "type": "application-component", "group": "app"},
        {"id": "k8s", "label": "Kubernetes", "type": "node", "group": "tech"},
        {"id": "db", "label": "PostgreSQL", "type": "system-software", "group": "tech"},
    ],
    "edges": [
        {"from": "dienst", "to": "burger", "type": "serving"},
        {"from": "proces", "to": "dienst", "type": "realization"},
        {"from": "zreg", "to": "proces", "type": "serving"},
        {"from": "zs", "to": "zreg", "type": "realization"},
        {"from": "zs", "to": "data", "type": "access", "access": "write"},
        {"from": "zs", "to": "dms", "type": "flow", "label": "documenten"},
        {"from": "k8s", "to": "zs", "type": "serving"},
        {"from": "db", "to": "zs", "type": "serving"},
        {"from": "k8s", "to": "db", "type": "assignment"},
    ],
}

C4 = {
    "title": "Container diagram",
    "notation": "c4",
    "groups": [{"id": "sys", "label": "Loket", "kind": "system"}],
    "nodes": [
        {"id": "user", "label": "Burger", "type": "person", "description": "Dient aanvragen in"},
        {"id": "spa", "label": "SPA", "type": "container", "group": "sys", "tech": "React"},
        {"id": "api", "label": "API", "type": "container", "group": "sys", "tech": "Java"},
        {"id": "db", "label": "Database", "type": "database", "group": "sys", "tech": "PostgreSQL"},
        {"id": "mq", "label": "Bus", "type": "queue", "group": "sys"},
        {"id": "ext", "label": "DigiD", "type": "external-system"},
    ],
    "edges": [
        {"from": "user", "to": "spa", "label": "Gebruikt", "tech": "HTTPS"},
        {"from": "spa", "to": "api", "label": "JSON"},
        {"from": "api", "to": "db"},
        {"from": "api", "to": "mq", "type": "async"},
        {"from": "spa", "to": "ext"},
    ],
}

GENERIC = {
    "title": "Cloud",
    "notation": "generic",
    "direction": "LR",
    "groups": [
        {"id": "cloud", "label": "Azure", "kind": "cloud"},
        {"id": "dmz", "label": "DMZ", "kind": "zone", "parent": "cloud"},
        {"id": "data", "label": "Data", "kind": "zone", "parent": "cloud"},
    ],
    "nodes": [
        {"id": "u", "label": "Gebruiker", "type": "user"},
        {"id": "fw", "label": "Firewall", "type": "firewall", "group": "dmz"},
        {"id": "gw", "label": "Gateway", "type": "gateway", "group": "dmz"},
        {"id": "app", "label": "App", "type": "app", "group": "cloud"},
        {"id": "q", "label": "Queue", "type": "queue", "group": "cloud"},
        {"id": "sql", "label": "SQL", "type": "database", "group": "data"},
        {"id": "blob", "label": "Blob", "type": "storage", "group": "data"},
        {"id": "net", "label": "Internet", "type": "cloud"},
    ],
    "edges": [
        {"from": "u", "to": "net"},
        {"from": "net", "to": "fw"},
        {"from": "fw", "to": "gw"},
        {"from": "gw", "to": "app", "label": "REST"},
        {"from": "app", "to": "q", "type": "async"},
        {"from": "app", "to": "sql"},
        {"from": "app", "to": "blob"},
    ],
}

ALL = [ARCHIMATE, C4, GENERIC]


# --------------------------------------------------------------------------- normalisation


def test_messy_input_is_repaired() -> None:
    raw = {
        "notation": "ArchiMate",
        "direction": "left-right",
        "layers": [{"name": "Applicatielaag", "kind": "Layer"}, {"name": "Infra", "parent": "Infra"}],
        "elements": [
            {"name": "Zaaksysteem", "type": "Applicatie", "layer": "Applicatielaag"},
            {"name": "Zaaksysteem", "type": "component"},  # duplicate label -> unique id
            {"id": "db", "name": "Database", "type": "DBMS", "group": "Infra"},
            "Burger",
            {"label": "Orphan", "group": "g99"},
        ],
        "relations": [
            {"source": "Burger", "target": "zaaksysteem", "type": "uses"},
            {"source": "zaaksysteem", "target": "Archief"},  # unknown label -> node created
            {"source": "zaaksysteem", "target": "n42"},  # unknown id-like ref -> dropped
            {"src": "db", "dst": "db"},  # self loop -> dropped
            "Burger -> Zaaksysteem: belt",
            ["db", "zaaksysteem", "levert"],
        ],
    }
    spec = normalize_spec(raw)
    assert spec.notation == "archimate" and spec.direction == "LR"
    ids = [n.id for n in spec.nodes]
    assert len(ids) == len(set(ids))
    assert "archief" in ids
    labels = {n.label: n for n in spec.nodes}
    assert labels["Zaaksysteem"].type == "application-component"
    assert labels["Database"].type == "system-software"
    assert labels["Burger"].type == "business-actor"
    assert labels["Orphan"].group is None or labels["Orphan"].group in {g.id for g in spec.groups}
    node_ids = set(ids)
    for e in spec.edges:
        assert e.source in node_ids and e.target in node_ids and e.source != e.target
    # "Burger uses Zaaksysteem" becomes "Zaaksysteem serves Burger" in ArchiMate
    serving = [e for e in spec.edges if e.type == "serving" and e.target == labels["Burger"].id]
    assert serving
    group_ids = {g.id for g in spec.groups}
    for g in spec.groups:
        assert g.parent is None or g.parent in group_ids


def test_group_cycles_and_unknown_parents() -> None:
    raw = {
        "groups": [
            {"id": "a", "label": "A", "parent": "b"},
            {"id": "b", "label": "B", "parent": "a"},
            {"id": "c", "label": "C", "parent": "zzz"},
        ],
        "nodes": [{"id": "x", "label": "X", "group": "a"}, {"id": "y", "label": "Y", "group": "b"},
                  {"id": "z", "label": "Z", "group": "c"}],
        "edges": [{"from": "x", "to": "y"}, {"from": "y", "to": "z"}, {"from": "z", "to": "x"}],
    }
    spec = normalize_spec(raw)
    by_id = {g.id: g for g in spec.groups}
    for g in spec.groups:  # no cycles remain
        seen, cur = set(), g
        while cur.parent:
            assert cur.parent not in seen
            seen.add(cur.parent)
            cur = by_id[cur.parent]
    _check_layout(spec)


def test_list_of_strings_json_text_and_fences() -> None:
    spec = normalize_spec('```json\n["Webapp", "API", "Database",]\n```')
    assert [n.label for n in spec.nodes] == ["Webapp", "API", "Database"]
    assert spec.notation == "generic"
    assert [n.type for n in spec.nodes] == ["app", "api", "database"]
    build_drawio(spec)


def test_no_nodes_raises() -> None:
    with pytest.raises(ValueError):
        normalize_spec({"title": "leeg", "edges": []})


def test_auto_layers_for_plain_archimate() -> None:
    spec = normalize_spec({"notation": "archimate", "nodes": [
        {"label": "Medewerker", "type": "business-role"},
        {"label": "Zaaksysteem", "type": "application-component"},
        {"label": "Server", "type": "node"}]})
    assert [g.kind for g in spec.groups] == ["layer", "layer", "layer"]
    assert all(n.group for n in spec.nodes)


def test_fuzzy_types_per_notation() -> None:
    raw = {"nodes": [{"label": "a", "type": "Applicatiecomponent"}, {"label": "b", "type": "proces"},
                     {"label": "c", "type": "gegevensobject"}, {"label": "d", "type": "Technologiedienst"},
                     {"label": "e", "type": "BusinessService"}], "notation": "archimate"}
    types = [n.type for n in normalize_spec(raw).nodes]
    assert types == ["application-component", "business-process", "data-object", "technology-service",
                     "business-service"]
    c4 = normalize_spec({"notation": "c4", "nodes": [{"label": "u", "type": "gebruiker"},
                                                      {"label": "k", "type": "kafka"},
                                                      {"label": "x", "type": "nonsense"}]})
    assert [n.type for n in c4.nodes] == ["person", "queue", "container"]


# --------------------------------------------------------------------------- layout invariants


def _check_layout(spec: DiagramSpec) -> None:
    layout = compute_layout(spec)
    boxes = list(layout.nodes.values())
    assert len(boxes) == len(spec.nodes)
    for i, a in enumerate(boxes):
        assert a.box.w > 0 and a.box.h > 0
        assert a.box.x % 10 == 0 and a.box.y % 10 == 0  # snapped to the grid
        for b in boxes[i + 1:]:
            assert not a.box.overlaps(b.box), (a.node.id, b.node.id)
    for nb in boxes:
        if nb.node.group:
            assert layout.groups[nb.node.group].box.contains(nb.box), nb.node.id
    for gb in layout.groups.values():
        if gb.group.parent:
            assert layout.groups[gb.group.parent].box.contains(gb.box), gb.group.id
    # sibling groups never overlap
    gl = list(layout.groups.values())
    for i, a in enumerate(gl):
        for b in gl[i + 1:]:
            if a.group.parent == b.group.parent:
                assert not a.box.overlaps(b.box)
    # canvas holds everything
    for nb in boxes:
        assert nb.box.r <= layout.width and nb.box.b <= layout.height
    # every route starts on its source and ends on its target, orthogonally
    for er in layout.edges:
        assert len(er.points) >= 2
        for (x1, y1), (x2, y2) in zip(er.points, er.points[1:]):
            assert abs(x1 - x2) < 1e-6 or abs(y1 - y2) < 1e-6


@pytest.mark.parametrize("raw", ALL, ids=["archimate", "c4", "generic"])
def test_layout_no_overlap_and_containment(raw: dict) -> None:
    _check_layout(normalize_spec(raw))


def test_layer_bands_are_full_width_and_stacked() -> None:
    layout = compute_layout(normalize_spec(ARCHIMATE))
    bands = [layout.groups[g] for g in ("bus", "app", "tech")]
    assert len({(b.box.x, b.box.w) for b in bands}) == 1
    assert bands[0].box.b <= bands[1].box.y and bands[1].box.b <= bands[2].box.y


def test_deterministic() -> None:
    assert build_drawio(normalize_spec(ARCHIMATE)) == build_drawio(normalize_spec(ARCHIMATE))


# --------------------------------------------------------------------------- outputs


@pytest.mark.parametrize("raw", ALL, ids=["archimate", "c4", "generic"])
def test_drawio_xml_structure(raw: dict) -> None:
    spec = normalize_spec(raw)
    xml = build_drawio(spec)
    root = etree.fromstring(xml.encode("utf-8"))
    assert root.tag == "mxfile"
    model = root.find("diagram/mxGraphModel")
    assert model is not None
    cells = model.findall("root/mxCell")
    by_id = {c.get("id"): c for c in cells}
    assert len(by_id) == len(cells)  # unique ids
    assert by_id["0"].get("parent") is None and by_id["1"].get("parent") == "0"
    for c in cells[2:]:
        assert c.get("parent") in by_id
    edges = [c for c in cells if c.get("edge") == "1" and c.get("source")]
    assert len(edges) == len(spec.edges)
    for e in edges:
        assert e.get("source") in by_id and e.get("target") in by_id
        assert by_id[e.get("source")].get("vertex") == "1"
    # child geometry is relative to (and inside) its container
    for c in cells:
        parent = by_id.get(c.get("parent", ""))
        if c.get("vertex") != "1" or parent is None or parent.get("vertex") != "1":
            continue
        g, pg = c.find("mxGeometry"), parent.find("mxGeometry")
        x, y, w, h = (float(g.get(k)) for k in ("x", "y", "width", "height"))
        assert x >= 0 and y >= 0
        assert x + w <= float(pg.get("width")) + 0.5 and y + h <= float(pg.get("height")) + 0.5
    containers = [c for c in cells if "container=1" in (c.get("style") or "") or
                  (c.get("style") or "").startswith("swimlane")]
    assert len(containers) >= len(spec.groups)


def test_drawio_escaping_and_stencils() -> None:
    xml = build_drawio(normalize_spec(ARCHIMATE))
    root = etree.fromstring(xml.encode("utf-8"))
    assert root.find("diagram").get("name") == ARCHIMATE["title"]
    styles = [c.get("style") for c in root.iter("mxCell") if c.get("style")]
    assert any("shape=mxgraph.archimate3.application;appType=comp;archiType=square;" in s for s in styles)
    assert any("appType=serv;archiType=rounded;" in s for s in styles)
    assert all("edgeStyle=orthogonalEdgeStyle;rounded=1;" in c.get("style") for c in root.iter("mxCell")
               if c.get("edge") == "1" and c.get("source"))


@pytest.mark.parametrize("raw", ALL, ids=["archimate", "c4", "generic"])
def test_svg_parses(raw: dict) -> None:
    svg = render_svg(normalize_spec(raw))
    root = etree.fromstring(svg.encode("utf-8"))
    assert root.tag.endswith("svg")
    texts = "".join(root.itertext())
    for n in raw["nodes"]:
        assert n["label"].split()[0] in texts


@pytest.mark.parametrize("raw", ALL, ids=["archimate", "c4", "generic"])
def test_png_renders(raw: dict) -> None:
    spec = normalize_spec(raw)
    data = render_png(spec, scale=1.5)
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 2000
    img = Image.open(io.BytesIO(data))
    layout = compute_layout(spec)
    assert img.size == (round(layout.width * 1.5), round(layout.height * 1.5))


def test_api_accepts_raw_dicts_and_prompt_material() -> None:
    assert build_drawio(C4).startswith("<mxfile")
    assert "archimate" in SPEC_PROMPT_GUIDE and "{" in SPEC_PROMPT_GUIDE
    json.dumps(SPEC_JSON_SCHEMA)
    # The example embedded in the guide is itself a valid spec.
    example = SPEC_PROMPT_GUIDE[SPEC_PROMPT_GUIDE.index("Example:") + len("Example:"):].strip()
    spec = normalize_spec(json.loads(example))
    assert len(spec.nodes) == 5 and len(spec.edges) == 4
