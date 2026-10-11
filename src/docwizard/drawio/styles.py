"""Visual definitions per notation: draw.io style strings plus the matching render parameters."""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import ARCHIMATE_TYPES
from .spec import Edge, Group, Node
from .text import Run

FONT = "fontFamily=Helvetica;"

# --------------------------------------------------------------------------- nodes


@dataclass(frozen=True)
class NodeVisual:
    drawio: str  # draw.io style (complete)
    shape: str  # render primitive, see render.py
    fill: str
    stroke: str
    font_color: str
    width: int
    height: int
    icon: str | None = None  # ArchiMate appType icon drawn top-right
    dashed: bool = False
    stroke_width: float = 1.0
    insets: tuple[int, int, int, int] = (8, 8, 8, 8)  # text box insets l, t, r, b
    align: str = "center"


# Archi-tool default layer colours (ArchiMate 3.x), with a crisp darker stroke per layer.
ARCHIMATE_COLORS = {
    "business": ("#FFFFB5", "#B5B54A"),
    "application": ("#B5FFFF", "#3FA9B5"),
    "technology": ("#C9E7B7", "#5E9A4A"),
    "motivation": ("#CCCCFF", "#7A7AC2"),
    "strategy": ("#F5DEAA", "#B8964A"),
    "implementation": ("#FFE0E0", "#C27A7A"),
    "other": ("#FFFFFF", "#666666"),
}
ARCHIMATE_BAND = {  # (band fill, header strip fill)
    "business": ("#FFFFEB", "#FFFFB5"),
    "application": ("#EBFFFF", "#B5FFFF"),
    "technology": ("#EEF7E8", "#C9E7B7"),
    "motivation": ("#F2F2FF", "#CCCCFF"),
    "strategy": ("#FCF6E8", "#F5DEAA"),
    "implementation": ("#FFF5F5", "#FFE0E0"),
    "other": ("#FAFAFA", "#E6E6E6"),
}

C4_COLORS = {  # classic C4 palette: fill, stroke, font
    "person": ("#08427B", "#073B6F", "#FFFFFF"),
    "external-person": ("#686868", "#4D4D4D", "#FFFFFF"),
    "software-system": ("#1168BD", "#0B4884", "#FFFFFF"),
    "external-system": ("#999999", "#6B6B6B", "#FFFFFF"),
    "container": ("#438DD5", "#2E6295", "#FFFFFF"),
    "database": ("#438DD5", "#2E6295", "#FFFFFF"),
    "queue": ("#438DD5", "#2E6295", "#FFFFFF"),
    "browser": ("#438DD5", "#2E6295", "#FFFFFF"),
    "mobile": ("#438DD5", "#2E6295", "#FFFFFF"),
    "component": ("#85BBF0", "#5D82A8", "#000000"),
}
C4_TYPE_CAPTION = {
    "person": "Person", "external-person": "Person", "software-system": "Software System",
    "external-system": "Software System", "container": "Container", "database": "Container",
    "queue": "Container", "browser": "Container", "mobile": "Container", "component": "Component",
}

GENERIC_COLORS = {  # modern, calm palette: fill, stroke
    "app": ("#E8F0FE", "#3B6FD8"),
    "service": ("#E7F5EC", "#2F8F5B"),
    "api": ("#FFF4E5", "#D98324"),
    "gateway": ("#FFF4E5", "#D98324"),
    "database": ("#F1E9FB", "#8253C9"),
    "storage": ("#F1E9FB", "#8253C9"),
    "queue": ("#E3F6F6", "#1F9A9A"),
    "user": ("#F3F4F6", "#374151"),
    "external": ("#F3F4F6", "#6B7280"),
    "server": ("#EEF2F6", "#475569"),
    "cloud": ("#EFF6FF", "#5B8DEF"),
    "firewall": ("#FDECEC", "#C0392B"),
    "mobile": ("#FFF8E1", "#B7950B"),
    "browser": ("#FFF8E1", "#B7950B"),
}

TEXT_DARK = "#1F2937"
TEXT_MUTED = "#6B7280"


def _arch_visual(node_type: str) -> NodeVisual:
    layer, app_type, archi_type = ARCHIMATE_TYPES[node_type]
    fill, stroke = ARCHIMATE_COLORS[layer]
    base = "html=1;outlineConnect=0;whiteSpace=wrap;" + FONT + "fontSize=12;fontColor=#1A1A1A;"
    if node_type == "grouping":
        style = (base + "shape=mxgraph.archimate3.application;appType=grouping;archiType=square;dashed=1;"
                 "fillColor=none;strokeColor=#666666;")
        return NodeVisual(style, "rect", "none", "#666666", "#1A1A1A", 160, 70, "grouping", dashed=True,
                          insets=(8, 18, 8, 6))
    if node_type == "location":
        fill, stroke = "#EFD1E4", "#A86A92"
    shape_part = f"shape=mxgraph.archimate3.application;appType={app_type};"
    if archi_type:
        shape_part += f"archiType={archi_type};"
    style = (base + f"fillColor={fill};strokeColor={stroke};" + shape_part
             + "spacing=0;spacingLeft=8;spacingRight=8;spacingTop=14;spacingBottom=4;")
    shape = {"rounded": "round", "oct": "oct"}.get(archi_type or "", "rect")
    return NodeVisual(style, shape, fill, stroke, "#1A1A1A", 160, 70, app_type, insets=(8, 14, 8, 4))


def _c4_visual(node_type: str) -> NodeVisual:
    fill, stroke, font = C4_COLORS[node_type]
    common = (f"html=1;whiteSpace=wrap;{FONT}fontSize=11;fillColor={fill};strokeColor={stroke};fontColor={font};"
              "align=center;labelBackgroundColor=none;")
    if node_type in ("person", "external-person"):
        return NodeVisual(common + "shape=mxgraph.c4.person2;dashed=0;", "person", fill, stroke, font, 200, 160,
                          insets=(10, 4, 10, 8))
    if node_type == "database":
        return NodeVisual(common + "shape=cylinder3;size=12;boundedLbl=1;rounded=0;", "cylinder", fill, stroke,
                          font, 200, 130, insets=(10, 26, 10, 8))
    if node_type == "queue":
        return NodeVisual(common + "shape=cylinder3;size=12;direction=south;boundedLbl=1;rounded=0;", "hcylinder",
                          fill, stroke, font, 200, 120, insets=(10, 8, 28, 8))
    if node_type == "browser":
        return NodeVisual(common + "shape=mxgraph.c4.webBrowserContainer2;strokeColor2=" + stroke + ";", "browser",
                          fill, stroke, font, 200, 120, insets=(10, 22, 10, 6))
    if node_type == "mobile":
        return NodeVisual(common + "shape=mxgraph.c4.mobileDeviceContainer;strokeColor2=" + stroke + ";",
                          "mobile", fill, stroke, font, 200, 120, insets=(22, 10, 22, 10))
    arc = 6 if node_type == "component" else 10
    return NodeVisual(common + f"rounded=1;arcSize={arc};", "round", fill, stroke, font, 200, 120,
                      insets=(10, 8, 10, 8))


def _generic_visual(node_type: str) -> NodeVisual:
    fill, stroke = GENERIC_COLORS[node_type]
    common = (f"html=1;whiteSpace=wrap;{FONT}fontSize=12;fontColor={TEXT_DARK};fillColor={fill};"
              f"strokeColor={stroke};strokeWidth=1.5;")
    w, h = 160, 70
    if node_type == "database":
        return NodeVisual(common + "shape=cylinder3;size=10;boundedLbl=1;", "cylinder", fill, stroke, TEXT_DARK, w,
                          80, stroke_width=1.5, insets=(8, 22, 8, 6))
    if node_type == "storage":
        return NodeVisual(common + "shape=folder;tabWidth=46;tabHeight=12;tabPosition=left;", "folder", fill, stroke,
                          TEXT_DARK, w, h, stroke_width=1.5, insets=(8, 16, 8, 4))
    if node_type == "queue":
        return NodeVisual(common + "shape=cylinder3;size=10;direction=south;boundedLbl=1;", "hcylinder", fill,
                          stroke, TEXT_DARK, w, h, stroke_width=1.5, insets=(8, 6, 24, 6))
    if node_type == "user":
        return NodeVisual(common + "shape=umlActor;labelPosition=right;verticalLabelPosition=middle;align=left;"
                          "verticalAlign=middle;spacingLeft=8;outlineConnect=0;", "actor", fill, stroke, TEXT_DARK,
                          w, 60, stroke_width=1.5, insets=(0, 0, 0, 0), align="left")
    if node_type == "cloud":
        return NodeVisual(common + "ellipse;shape=cloud;", "cloud", fill, stroke, TEXT_DARK, w, 90,
                          stroke_width=1.5, insets=(26, 26, 26, 18))
    if node_type == "gateway":
        return NodeVisual(common + "shape=hexagon;perimeter=hexagonPerimeter2;size=0.12;", "hexagon", fill, stroke,
                          TEXT_DARK, w, h, stroke_width=1.5, insets=(22, 6, 22, 6))
    if node_type == "firewall":
        return NodeVisual(common + "shape=process;size=0.08;rounded=0;", "process", fill, stroke, TEXT_DARK, w, h,
                          stroke_width=1.5, insets=(18, 6, 18, 6))
    if node_type == "external":
        return NodeVisual(common + "rounded=1;arcSize=12;dashed=1;dashPattern=6 3;", "round", fill, stroke,
                          TEXT_DARK, w, h, dashed=True, stroke_width=1.5)
    if node_type == "server":
        return NodeVisual(common + "rounded=0;", "server", fill, stroke, TEXT_DARK, w, h, stroke_width=1.5,
                          insets=(8, 8, 8, 8))
    if node_type == "mobile":
        return NodeVisual(common + "rounded=1;arcSize=22;", "round_more", fill, stroke, TEXT_DARK, w, h,
                          stroke_width=1.5)
    if node_type == "browser":
        return NodeVisual(common + "shape=mxgraph.c4.webBrowserContainer2;strokeColor2=" + stroke + ";", "browser",
                          fill, stroke, TEXT_DARK, w, 80, stroke_width=1.5, insets=(8, 22, 8, 4))
    return NodeVisual(common + "rounded=1;arcSize=12;", "round", fill, stroke, TEXT_DARK, w, h, stroke_width=1.5)


def node_visual(node: Node, notation: str) -> NodeVisual:
    if notation == "archimate":
        return _arch_visual(node.type)
    if notation == "c4":
        return _c4_visual(node.type)
    return _generic_visual(node.type)


def node_runs(node: Node, notation: str) -> list[Run]:
    """Label content of a node as formatted paragraphs (same content in draw.io, SVG and PNG)."""
    if notation == "c4":
        fill, _, font = C4_COLORS[node.type]
        sub = "#E6E6E6" if font == "#FFFFFF" else "#333333"
        caption = C4_TYPE_CAPTION[node.type]
        runs = [Run(node.label, 14, True, font), Run(f"[{caption}: {node.tech}]" if node.tech else f"[{caption}]", 11,
                                                     False, font)]
        if node.description:
            runs.append(Run(node.description, 11, False, sub))
        return runs
    if notation == "archimate":
        runs = [Run(node.label, 12, False, "#1A1A1A")]
        if node.tech:
            runs.append(Run(f"[{node.tech}]", 10, False, "#555555"))
        return runs
    runs = [Run(node.label, 12, True, TEXT_DARK)]
    if node.tech:
        runs.append(Run(node.tech, 10, False, TEXT_MUTED))
    return runs


# --------------------------------------------------------------------------- groups


@dataclass(frozen=True)
class GroupVisual:
    drawio: str
    fill: str
    stroke: str
    font_color: str
    header_fill: str | None  # left title strip (layer bands) when set
    dashed: bool
    rounded: bool
    strip: bool  # vertical title strip on the left (layer band)
    dash: str = "6 4"
    stroke_width: float = 1.0


STRIP = 30  # width of a layer band's title strip
GROUP_HEADER = 30  # height reserved for a group's top-left label


def group_visual(group: Group, notation: str, layer: str | None) -> GroupVisual:
    if group.kind == "layer":
        if notation == "archimate":
            body, head = ARCHIMATE_BAND.get(layer or "other", ARCHIMATE_BAND["other"])
            stroke = ARCHIMATE_COLORS.get(layer or "other", ARCHIMATE_COLORS["other"])[1]
        else:
            body, head, stroke = "#F8FAFC", "#E2E8F0", "#CBD5E1"
        style = (f"swimlane;horizontal=0;startSize={STRIP};html=1;whiteSpace=wrap;{FONT}fontSize=12;fontStyle=1;"
                 f"fontColor=#1F2937;fillColor={head};swimlaneFillColor={body};strokeColor={stroke};"
                 "collapsible=0;container=1;swimlaneLine=1;")
        return GroupVisual(style, body, stroke, "#1F2937", head, False, False, True)
    if notation == "c4" or group.kind in ("system", "boundary"):
        stroke = "#666666" if notation != "generic" else "#94A3B8"
        style = (f"rounded=1;arcSize=4;absoluteArcSize=0;html=1;whiteSpace=wrap;{FONT}fontSize=12;fontStyle=1;"
                 f"fontColor=#333333;fillColor=none;strokeColor={stroke};dashed=1;dashPattern=8 4;container=1;"
                 "collapsible=0;verticalAlign=top;align=left;spacingLeft=10;spacingTop=4;")
        return GroupVisual(style, "none", stroke, "#333333", None, True, True, False, dash="8 4")
    if group.kind == "cloud":
        style = (f"rounded=1;arcSize=4;html=1;whiteSpace=wrap;{FONT}fontSize=12;fontStyle=1;fontColor=#2B5BB5;"
                 "fillColor=#F3F8FF;strokeColor=#8DB3EE;container=1;collapsible=0;verticalAlign=top;align=left;"
                 "spacingLeft=10;spacingTop=4;")
        return GroupVisual(style, "#F3F8FF", "#8DB3EE", "#2B5BB5", None, False, True, False)
    if notation == "archimate":  # ArchiMate grouping look
        style = (f"rounded=0;html=1;whiteSpace=wrap;{FONT}fontSize=12;fontStyle=1;fontColor=#333333;"
                 "fillColor=#FCFCFC;strokeColor=#808080;dashed=1;dashPattern=6 4;container=1;collapsible=0;"
                 "verticalAlign=top;align=left;spacingLeft=10;spacingTop=4;")
        return GroupVisual(style, "#FCFCFC", "#808080", "#333333", None, True, False, False)
    style = (f"rounded=1;arcSize=4;html=1;whiteSpace=wrap;{FONT}fontSize=12;fontStyle=1;fontColor=#374151;"
             "fillColor=#F9FAFB;strokeColor=#B8C0CC;dashed=1;dashPattern=6 4;container=1;collapsible=0;"
             "verticalAlign=top;align=left;spacingLeft=10;spacingTop=4;")
    return GroupVisual(style, "#F9FAFB", "#B8C0CC", "#374151", None, True, True, False)


def group_caption(group: Group, notation: str) -> str:
    if notation == "c4" and group.kind in ("system", "boundary"):
        return {"system": "Software System", "boundary": "Boundary"}[group.kind]
    return ""


# --------------------------------------------------------------------------- edges


@dataclass(frozen=True)
class EdgeVisual:
    drawio: str
    stroke: str
    width: float
    dash: str | None  # SVG dasharray, e.g. "6 4"
    start: str  # marker kind (see render.py)
    end: str
    font_color: str = "#333333"


_ARCHI_EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;" + FONT + "fontSize=10;fontColor=#333333;" \
    "strokeColor=#4D4D4D;labelBackgroundColor=#FFFFFF;endSize=8;startSize=8;"

# Arrowheads per relationship (draw.io Sidebar-ArchiMate3.js relationship templates; edge routing switched
# from elbowEdgeStyle to orthogonalEdgeStyle).
ARCHIMATE_EDGES: dict[str, tuple[str, str | None, str, str]] = {
    # type: (draw.io arrow part, dash, start marker, end marker)
    "composition": ("startArrow=diamondThin;startFill=1;startSize=12;endArrow=none;endFill=0;", None, "diamond",
                    "none"),
    "aggregation": ("startArrow=diamondThin;startFill=0;startSize=12;endArrow=none;endFill=0;", None,
                    "diamond_open", "none"),
    "assignment": ("endArrow=block;endFill=1;startArrow=oval;startFill=1;startSize=6;", None, "dot", "block"),
    "realization": ("endArrow=block;endFill=0;dashed=1;", "6 4", "none", "block_open"),
    "serving": ("endArrow=open;endFill=1;", None, "none", "open"),
    "access": ("endArrow=none;dashed=1;dashPattern=1 4;startFill=0;", "1.5 3", "none", "none"),
    "influence": ("endArrow=open;endFill=0;dashed=1;dashPattern=6 4;", "6 4", "none", "open"),
    "triggering": ("endArrow=block;endFill=1;dashed=0;", None, "none", "block"),
    "flow": ("endArrow=block;endFill=1;dashed=1;dashPattern=6 4;", "6 4", "none", "block"),
    "specialization": ("endArrow=block;endFill=0;", None, "none", "block_open"),
    "association": ("endArrow=none;", None, "none", "none"),
}


def edge_visual(edge: Edge, notation: str) -> EdgeVisual:
    if notation == "archimate":
        part, dash, start, end = ARCHIMATE_EDGES.get(edge.type, ARCHIMATE_EDGES["association"])
        if edge.type == "access":
            mode = edge.access or ""
            if mode in ("write", "readwrite"):
                part = part.replace("endArrow=none;", "endArrow=open;endFill=0;")
                end = "open"
            if mode in ("read", "readwrite"):
                part += "startArrow=open;startFill=0;"
                start = "open"
        return EdgeVisual(_ARCHI_EDGE + part, "#4D4D4D", 1.0, dash, start, end)
    if notation == "c4":
        stroke, font = "#707070", "#404040"
        base = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;{FONT}fontSize=10;fontColor={font};"
                f"strokeColor={stroke};strokeWidth=1;labelBackgroundColor=#FFFFFF;endSize=10;startSize=10;")
    else:
        stroke, font = "#64748B", "#334155"
        base = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;{FONT}fontSize=10;fontColor={font};"
                f"strokeColor={stroke};strokeWidth=1.25;labelBackgroundColor=#FFFFFF;endSize=8;startSize=8;")
    width = 1.0 if notation == "c4" else 1.25
    kind = edge.type
    if kind == "async":
        return EdgeVisual(base + "endArrow=blockThin;endFill=1;dashed=1;dashPattern=6 4;", stroke, width, "6 4",
                          "none", "block_thin", font)
    if kind == "bidirectional":
        return EdgeVisual(base + "endArrow=blockThin;endFill=1;startArrow=blockThin;startFill=1;", stroke, width,
                          None, "block_thin", "block_thin", font)
    if kind == "dependency":
        return EdgeVisual(base + "endArrow=open;endFill=0;dashed=1;dashPattern=4 3;", stroke, width, "4 3", "none",
                          "open", font)
    if kind == "association":
        return EdgeVisual(base + "endArrow=none;", stroke, width, None, "none", "none", font)
    return EdgeVisual(base + "endArrow=blockThin;endFill=1;", stroke, width, None, "none", "block_thin", font)


def edge_text(edge: Edge, notation: str) -> list[Run]:
    _ = notation
    runs: list[Run] = []
    if edge.label:
        runs.append(Run(edge.label, 10, False, "#333333"))
    if edge.tech:
        runs.append(Run(f"[{edge.tech}]", 9, False, "#666666"))
    return runs


RELATION_NAMES = {
    "nl": {"serving": "Serving (bedient)", "realization": "Realisatie", "assignment": "Toewijzing",
           "access": "Toegang", "flow": "Stroom", "triggering": "Triggering", "composition": "Compositie",
           "aggregation": "Aggregatie", "association": "Associatie", "influence": "Invloed",
           "specialization": "Specialisatie"},
    "en": {"serving": "Serving", "realization": "Realization", "assignment": "Assignment", "access": "Access",
           "flow": "Flow", "triggering": "Triggering", "composition": "Composition", "aggregation": "Aggregation",
           "association": "Association", "influence": "Influence", "specialization": "Specialization"},
}
