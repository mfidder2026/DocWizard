"""Serialise a Layout as an uncompressed draw.io (mxfile) document."""

from __future__ import annotations

import hashlib
import html
import re

from lxml import etree

from .layout import Layout
from .route import path_length
from .text import Run


def _num(v: float) -> str:
    return str(int(round(v))) if abs(v - round(v)) < 1e-6 else f"{v:.2f}"


def _runs_html(runs: list[Run], base_size: float) -> str:
    """HTML label (html=1) from runs; user text is escaped, lxml escapes the attribute again."""
    parts = []
    for i, r in enumerate(runs):
        text = html.escape(r.text, quote=False)
        if r.bold:
            text = f"<b>{text}</b>"
        style = []
        if abs(r.size - base_size) > 0.1:
            style.append(f"font-size:{_num(r.size)}px")
        if style or i:
            style.append(f"color:{r.color}")
        parts.append(f'<div style="{";".join(style)}">{text}</div>' if style else f"<div>{text}</div>")
    return "".join(parts)


def _geometry(parent: etree._Element, x: float, y: float, w: float, h: float) -> None:
    etree.SubElement(parent, "mxGeometry", x=_num(x), y=_num(y), width=_num(w), height=_num(h), **{"as": "geometry"})


def build_xml(layout: Layout, node_runs_fn, edge_runs_fn) -> str:
    spec = layout.spec
    digest = hashlib.sha1(repr(spec.to_dict()).encode("utf-8")).hexdigest()[:20]
    mxfile = etree.Element("mxfile", host="DocWizard", agent="DocWizard drawio module", version="24.0.0", type="device")
    diagram = etree.SubElement(mxfile, "diagram", name=spec.title or "Diagram", id=f"dw-{digest}")
    model = etree.SubElement(
        diagram, "mxGraphModel", dx="1200", dy="800", grid="1", gridSize="10", guides="1", tooltips="1",
        connect="1", arrows="1", fold="1", page="1", pageScale="1", pageWidth=_num(max(layout.width, 827)),
        pageHeight=_num(max(layout.height, 1169)), math="0", shadow="0",
    )
    root = etree.SubElement(model, "root")
    etree.SubElement(root, "mxCell", id="0")
    etree.SubElement(root, "mxCell", id="1", parent="0")

    def cell(cid: str, value: str, style: str, parent: str, **extra: str) -> etree._Element:
        return etree.SubElement(root, "mxCell", id=cid, value=value, style=style, parent=parent, **extra)

    # Title
    if layout.title_box and spec.title:
        tb = layout.title_box
        c = cell("title", html.escape(spec.title, quote=False),
                 "text;html=1;fontFamily=Helvetica;fontSize=18;fontStyle=1;fontColor=#1F2937;align=left;"
                 "verticalAlign=top;whiteSpace=wrap;strokeColor=none;fillColor=none;", "1", vertex="1")
        _geometry(c, tb.x, tb.y, max(tb.w + 20, 200), tb.h + 6)

    group_ids = {gid: f"g-{gid}" for gid in layout.groups}
    abs_box = {gid: gb.box for gid, gb in layout.groups.items()}

    # Groups: parents are emitted before children (layout.groups is ordered that way).
    for gid, gb in layout.groups.items():
        g = gb.group
        parent = group_ids.get(g.parent or "", "1")
        label = f"<b>{html.escape(gb.title, quote=False)}</b>" if not gb.visual.strip else html.escape(
            gb.title, quote=False)
        if gb.caption:
            label += f'<br><span style="font-weight:normal;font-size:10px;color:#666666">[{gb.caption}]</span>'
        c = cell(group_ids[gid], label, gb.visual.drawio, parent, vertex="1")
        px, py = (abs_box[g.parent].x, abs_box[g.parent].y) if g.parent in abs_box else (0.0, 0.0)
        _geometry(c, gb.box.x - px, gb.box.y - py, gb.box.w, gb.box.h)

    # Nodes (geometry relative to their container)
    node_ids = {nid: f"n-{nid}" for nid in layout.nodes}
    for nid, nb in layout.nodes.items():
        n = nb.node
        parent = group_ids.get(n.group or "", "1")
        px, py = (abs_box[n.group].x, abs_box[n.group].y) if n.group in abs_box else (0.0, 0.0)
        runs = node_runs_fn(n, spec.notation)
        style = nb.visual.drawio
        m = re.search(r"fontSize=(\d+)", style)
        value = _runs_html(runs, float(m.group(1)) if m else 12.0)
        if spec.notation == "c4" and n.description:
            style += "metaEdit=1;"
        c = cell(node_ids[nid], value, style, parent, vertex="1")
        a = nb.anchor
        _geometry(c, a.x - px, a.y - py, a.w, a.h)

    # Edges: parent "1", source/target ids, exit/entry constraints and waypoints from our router.
    for i, er in enumerate(layout.edges):
        e = er.edge
        sa, ta = layout.nodes[e.source].anchor, layout.nodes[e.target].anchor
        pts = er.points
        style = er.visual.drawio
        if len(pts) >= 2:
            (sx, sy), (tx, ty) = pts[0], pts[-1]
            # Constraints of rotated shapes (direction=...) live in rotated space; let draw.io choose there.
            if "direction=" not in layout.nodes[e.source].visual.drawio:
                style += (f"exitX={_num(round((sx - sa.x) / sa.w, 3))};"
                          f"exitY={_num(round((sy - sa.y) / sa.h, 3))};exitDx=0;exitDy=0;")
            if "direction=" not in layout.nodes[e.target].visual.drawio:
                style += (f"entryX={_num(round((tx - ta.x) / ta.w, 3))};"
                          f"entryY={_num(round((ty - ta.y) / ta.h, 3))};entryDx=0;entryDy=0;")
        runs = edge_runs_fn(e, spec.notation)
        value = _runs_html(runs, 10) if runs else ""
        c = cell(f"e-{i}", value, style, "1", edge="1", source=node_ids[e.source], target=node_ids[e.target])
        geo = etree.SubElement(c, "mxGeometry", relative="1", **{"as": "geometry"})
        if runs and abs(er.label_t - 0.5) > 1e-6 and path_length(pts) > 0:
            geo.set("x", _num(round((er.label_t - 0.5) * 2, 3)))
        bends = pts[1:-1]
        if bends:
            arr = etree.SubElement(geo, "Array", **{"as": "points"})
            for x, y in bends:
                etree.SubElement(arr, "mxPoint", x=_num(x), y=_num(y))

    # Legend (ArchiMate): plain cells, not connected to anything.
    if layout.legend:
        lg = layout.legend
        b = lg.box
        c = cell("legend", f"<b>{html.escape(lg.title, quote=False)}</b>",
                 "rounded=1;arcSize=4;absoluteArcSize=1;html=1;whiteSpace=wrap;fontFamily=Helvetica;fontSize=11;"
                 "align=left;verticalAlign=top;spacingLeft=12;spacingTop=4;fillColor=#FFFFFF;strokeColor=#B3B3B3;"
                 "container=1;collapsible=0;", "1", vertex="1")
        _geometry(c, b.x, b.y, b.w, b.h)
        for j, item in enumerate(lg.items):
            cy = 34 + j * lg.row_h
            ec = cell(f"legend-e{j}", "", item.visual.drawio, "legend", edge="1")
            geo = etree.SubElement(ec, "mxGeometry", relative="1", **{"as": "geometry"})
            etree.SubElement(geo, "mxPoint", x="12", y=_num(cy), **{"as": "sourcePoint"})
            etree.SubElement(geo, "mxPoint", x="56", y=_num(cy), **{"as": "targetPoint"})
            tc = cell(f"legend-t{j}", html.escape(item.name, quote=False),
                      "text;html=1;fontFamily=Helvetica;fontSize=10;fontColor=#333333;align=left;verticalAlign=middle;"
                      "strokeColor=none;fillColor=none;", "legend", vertex="1")
            _geometry(tc, 64, cy - 8, b.w - 70, 16)

    return etree.tostring(mxfile, encoding="unicode", pretty_print=True)
