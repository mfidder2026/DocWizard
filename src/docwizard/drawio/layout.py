"""Deterministic compound layered layout.

Every container (the canvas root or a group) lays out its direct children (nodes and child groups) as a
layered graph: ranks by longest path after cycle breaking, barycentric ordering and positioning, with long
edges reserved through dummy slots. Child groups are laid out first (recursively) and act as big blocks.
Sibling groups of kind "layer" are stacked as full-width horizontal bands instead.

The whole layout is iterated a few times so that blocks are also pulled towards neighbours outside their own
container (e.g. an application component lines up under the business process it serves).
Coordinates are computed in "rank space" (ranks go down) and transposed for direction LR.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .catalog import archimate_layer
from .route import Box, route_edges, place_labels
from .spec import DiagramSpec, Edge, Group, Node
from .styles import (
    GROUP_HEADER,
    STRIP,
    EdgeVisual,
    GroupVisual,
    NodeVisual,
    edge_text,
    edge_visual,
    group_caption,
    group_visual,
    node_runs,
    node_visual,
)
from .text import Line, Run, layout_runs, lines_height, longest_word_width, text_width

GRID = 10
MARGIN = 30
ITERATIONS = 3

Key = tuple[str, str]  # ("n", node id) | ("g", group id) | ("d", dummy id)


def snap(v: float) -> float:
    return float(round(v / GRID) * GRID)


def snap_up(v: float) -> float:
    return float(math.ceil(v / GRID - 1e-9) * GRID)


@dataclass
class NodeBox:
    node: Node
    visual: NodeVisual
    box: Box  # slot occupied in the layout
    anchor: Box  # where edges attach / the draw.io cell geometry
    lines: list[Line]
    text_box: Box


@dataclass
class GroupBox:
    group: Group
    visual: GroupVisual
    box: Box
    layer: str | None
    title: str
    caption: str


@dataclass
class EdgeRoute:
    edge: Edge
    visual: EdgeVisual
    points: list[tuple[float, float]]
    lines: list[Line]
    label_box: Box | None = None
    label_t: float = 0.5  # relative position of the label along the path


@dataclass
class LegendItem:
    name: str
    visual: EdgeVisual


@dataclass
class Legend:
    box: Box
    title: str
    items: list[LegendItem]
    row_h: float = 20


@dataclass
class Layout:
    spec: DiagramSpec
    nodes: dict[str, NodeBox]
    groups: dict[str, GroupBox]  # parents before children
    edges: list[EdgeRoute]
    width: float
    height: float
    title_lines: list[Line] = field(default_factory=list)
    title_box: Box | None = None
    legend: Legend | None = None


@dataclass
class _Result:
    pos: dict[Key, tuple[float, float]]  # child top-left in the container's free frame (real axes)
    x0: float
    y0: float
    x1: float
    y1: float


class _Engine:
    def __init__(self, spec: DiagramSpec) -> None:
        self.spec = spec
        self.lr = spec.direction == "LR"
        self.nodes = {n.id: n for n in spec.nodes}
        self.groups = {g.id: g for g in spec.groups}
        self.node_order = {n.id: i for i, n in enumerate(spec.nodes)}
        self.chain: dict[str, list[str]] = {n.id: self._chain(n.group) for n in spec.nodes}
        self.child_groups: dict[str | None, list[str]] = {}
        for g in spec.groups:
            self.child_groups.setdefault(g.parent, []).append(g.id)
        self.child_nodes: dict[str | None, list[str]] = {}
        for n in spec.nodes:
            self.child_nodes.setdefault(n.group, []).append(n.id)
        self.has_labels = any(e.label or e.tech for e in spec.edges)
        # Distance between ranks: room for the widest (LR) or tallest (TB) edge label.
        label_w = max((text_width(t, 10) for e in spec.edges for t in (e.label, f"[{e.tech}]" if e.tech else "")),
                      default=0.0)
        if self.lr:
            self.gap_main = min(max(60.0, snap_up(min(label_w, 150) + 40)), 150.0)
        else:
            lines = max((bool(e.label) + bool(e.tech) for e in spec.edges), default=0)
            self.gap_main = max(60.0, snap_up(lines * 13 + 2 * 20 + 16)) if lines else 60.0
        # Sizes
        self.size: dict[Key, tuple[float, float]] = {}
        self.visuals: dict[str, NodeVisual] = {}
        self._size_nodes()
        # Per-container state carried across iterations
        self.prev_cross: dict[Key, float] = {}
        self.frame_abs: dict[str | None, tuple[float, float]] = {}
        self.abs_center: dict[str, tuple[float, float]] = {}
        self.content_off: dict[str | None, tuple[float, float]] = {}
        self.results: dict[str | None, _Result] = {}
        self.band_index: dict[str, int] = {}
        self.group_layer: dict[str, str | None] = {g.id: self._group_layer(g.id) for g in spec.groups}

    # ------------------------------------------------------------------ structure helpers
    def _chain(self, gid: str | None) -> list[str]:
        out: list[str] = []
        while gid:
            out.append(gid)
            gid = self.groups[gid].parent if gid in self.groups else None
        return list(reversed(out))

    def _in_subtree(self, nid: str, cid: str | None) -> bool:
        return cid is None or cid in self.chain[nid]

    def _block_of(self, nid: str, cid: str | None) -> Key:
        chain = self.chain[nid]
        if cid is None:
            return ("g", chain[0]) if chain else ("n", nid)
        i = chain.index(cid)
        return ("g", chain[i + 1]) if i + 1 < len(chain) else ("n", nid)

    def _group_layer(self, gid: str) -> str | None:
        if self.spec.notation != "archimate":
            return None
        counts: dict[str, int] = {}
        for n in self.spec.nodes:
            if gid in self.chain[n.id]:
                layer = archimate_layer(n.type)
                counts[layer] = counts.get(layer, 0) + 1
        return max(counts, key=lambda k: counts[k]) if counts else None

    def _order_index(self, key: Key) -> int:
        if key[0] == "n":
            return self.node_order[key[1]]
        return min((self.node_order[n] for n in self.nodes if key[1] in self.chain[n]), default=10**6)

    # ------------------------------------------------------------------ node sizing
    def _size_nodes(self) -> None:
        notation = self.spec.notation
        needed = 0.0
        for n in self.spec.nodes:
            vis = node_visual(n, notation)
            if vis.shape == "actor" and self.lr:
                # Edges leave sideways in LR, so the actor's label goes underneath instead of to the right.
                vis = replace(vis, align="center", drawio=vis.drawio.replace(
                    "labelPosition=right;verticalLabelPosition=middle;align=left;verticalAlign=middle;spacingLeft=8;",
                    "labelPosition=center;verticalLabelPosition=bottom;align=center;verticalAlign=top;spacingTop=2;"))
            self.visuals[n.id] = vis
            if vis.shape != "actor":
                l, _, r, _ = vis.insets
                needed = max(needed, vis.width, longest_word_width(node_runs(n, notation)[:1]) + l + r + 6)
        uniform = min(snap_up(needed), 280.0)
        self.node_w: dict[str, float] = {}
        self.node_lines: dict[str, list[Line]] = {}
        for n in self.spec.nodes:
            vis = self.visuals[n.id]
            runs = node_runs(n, notation)
            l, t, r, b = vis.insets
            if vis.shape == "actor":
                w = uniform
                if self.lr:
                    lines = layout_runs(runs, w - 8, max_lines=3)
                    h = snap_up(50 + 6 + lines_height(lines))
                else:
                    lines = layout_runs(runs, w - 28 - 8 - 4, max_lines=4)
                    h = max(vis.height, snap_up(lines_height(lines) + 10))
            else:
                w = max(uniform, float(vis.width))
                if vis.shape == "person":
                    t = int(min(w * 0.45, vis.height * 0.45) * 0.8) + 6
                lines = layout_runs(runs, w - l - r, max_lines=8)
                h = max(float(vis.height), snap_up(lines_height(lines) + t + b + 8))
            self.node_w[n.id] = w
            self.node_lines[n.id] = lines
            self.size[("n", n.id)] = (w, h)

    # ------------------------------------------------------------------ containers
    def _padding(self, gid: str | None) -> tuple[float, float, float, float]:
        """Real-space padding (left, top, right, bottom) of a container."""
        if gid is None:
            return (0.0, 0.0, 0.0, 0.0)
        if self.groups[gid].kind == "layer" and self.groups[gid].parent is None:
            return (STRIP + 30.0, 30.0, 30.0, 30.0)
        if self.groups[gid].kind == "layer":
            return (STRIP + 20.0, 20.0, 20.0, 20.0)
        return (20.0, GROUP_HEADER + 10.0, 20.0, 20.0)

    def _children(self, cid: str | None) -> list[Key]:
        kids: list[Key] = [("n", n) for n in self.child_nodes.get(cid, [])]
        kids += [("g", g) for g in self.child_groups.get(cid, [])]
        return sorted(kids, key=self._order_index)

    def _is_stack(self, cid: str | None) -> bool:
        kids = self._children(cid)
        return bool(kids) and all(k[0] == "g" and self.groups[k[1]].kind == "layer" for k in kids)

    def layout(self, cid: str | None) -> _Result:
        kids = self._children(cid)
        for k in kids:
            if k[0] == "g":
                self.layout(k[1])
                self._size_group(k[1])
        res = self._stack(cid, kids) if self._is_stack(cid) else self._layered(cid, kids)
        self.results[cid] = res
        return res

    def _size_group(self, gid: str) -> None:
        res = self.results[gid]
        pl, pt, pr, pb = self._padding(gid)
        group = self.groups[gid]
        w = (res.x1 - res.x0) + pl + pr
        h = (res.y1 - res.y0) + pt + pb
        title_w = text_width(group.label, 12, True)
        cap = group_caption(group, self.spec.notation)
        if cap:
            title_w += text_width(f" [{cap}]", 11) + 4
        if group.kind == "layer":
            h = max(h, snap_up(title_w + 30))
        else:
            w = max(w, snap_up(title_w + 30))
        self.size[("g", gid)] = (snap_up(w), snap_up(h))
        self.content_off[gid] = (pl - res.x0, pt - res.y0)

    def _stack(self, cid: str | None, kids: list[Key]) -> _Result:
        """Full-width layer bands stacked top to bottom, sharing one horizontal frame."""
        gap = 20.0
        x0 = min(self.results[k[1]].x0 for k in kids)
        x1 = max(self.results[k[1]].x1 for k in kids)
        pos: dict[Key, tuple[float, float]] = {}
        y = 0.0
        width = 0.0
        for i, k in enumerate(kids):
            gid = k[1]
            self.band_index[gid] = i
            pl, pt, pr, pb = self._padding(gid)
            res = self.results[gid]
            self.content_off[gid] = (pl - x0, pt - res.y0)
            width = max(width, snap_up(x1 - x0 + pl + pr), self.size[k][0])
        for k in kids:
            self.size[k] = (width, self.size[k][1])
            pos[k] = (0.0, y)
            y += self.size[k][1] + gap
        return _Result(pos, 0.0, 0.0, width, y - gap)

    # ------------------------------------------------------------------ layered layout of one container
    def _ls(self, key: Key) -> tuple[float, float]:
        """(cross, main) size of a block in rank space."""
        w, h = self.size[key]
        return (h, w) if self.lr else (w, h)

    def _to_local_cross(self, cid: str | None, abs_xy: tuple[float, float]) -> float | None:
        origin = self.frame_abs.get(cid)
        if origin is None:
            return None
        dx, dy = abs_xy[0] - origin[0], abs_xy[1] - origin[1]
        return dy if self.lr else dx

    def _layered(self, cid: str | None, kids: list[Key]) -> _Result:
        if not kids:
            return _Result({}, 0, 0, 0, 0)
        idx = {k: i for i, k in enumerate(kids)}
        # Collapsed sibling edges and external pulls.
        sib: list[tuple[Key, Key]] = []
        pulls: dict[Key, list[float]] = {k: [] for k in kids}
        vpref: dict[Key, int] = {k: 0 for k in kids}
        my_band = self._band_of(cid)
        for e in self.spec.edges:
            su, sv = self._in_subtree(e.source, cid), self._in_subtree(e.target, cid)
            if su and sv:
                bu, bv = self._block_of(e.source, cid), self._block_of(e.target, cid)
                if bu != bv:
                    sib.append((bu, bv))
            elif su or sv:
                inner, outer = (e.source, e.target) if su else (e.target, e.source)
                b = self._block_of(inner, cid)
                if outer in self.abs_center:
                    c = self._to_local_cross(cid, self.abs_center[outer])
                    if c is not None:
                        pulls[b].append(c)
                ob = self._band_of_node(outer)
                if my_band is not None and ob is not None and ob != my_band:
                    vpref[b] += 1 if ob < my_band else -1

        rank = self._rank(kids, idx, sib, vpref)
        layers = self._wrap(kids, rank)
        rank = {k: r for r, layer in enumerate(layers) for k in layer}

        # Dummy chains for edges spanning several ranks.
        up: dict[Key, list[Key]] = {k: [] for k in kids}
        down: dict[Key, list[Key]] = {k: [] for k in kids}
        dummy_n = 0
        for a, b in sib:
            if rank[a] > rank[b]:
                a, b = b, a
            if rank[a] == rank[b]:
                continue
            prev = a
            for r in range(rank[a] + 1, rank[b]):
                d: Key = ("d", f"{cid}:{dummy_n}")
                dummy_n += 1
                layers[r].append(d)
                up[d], down[d] = [prev], []
                down[prev].append(d)
                prev = d
            down[prev].append(b)
            up[b].append(prev)

        gap_x = 30.0 if self.lr else 40.0
        sizes = {k: (self._ls(k)[0] if k[0] != "d" else 10.0) for layer in layers for k in layer}

        def sep(a: Key, b: Key) -> float:
            g = gap_x if (a[0] != "d" and b[0] != "d") else 20.0
            return (sizes[a] + sizes[b]) / 2 + g

        # Initial positions: previous iteration, else packed in input order.
        pos: dict[Key, float] = {}
        for layer in layers:
            layer.sort(key=lambda k: (self.prev_cross.get(k, math.inf), idx.get(k, len(idx))))
            x = 0.0
            for i, k in enumerate(layer):
                if i:
                    x += sep(layer[i - 1], k)
                pos[k] = self.prev_cross.get(k, x)
            self._place(layer, {k: pos[k] for k in layer}, {k: 1.0 for k in layer}, sep, pos)

        def desired(k: Key, nbrs: list[Key]) -> tuple[float, float]:
            vals = [pos[n] for n in nbrs] + (pulls.get(k, []) if k[0] != "d" else [])
            if not vals:
                return pos[k], 0.05
            return sum(vals) / len(vals), 1.0

        # Barycentric ordering + positioning sweeps.
        for sweep in range(6):
            order = range(1, len(layers)) if sweep % 2 == 0 else range(len(layers) - 2, -1, -1)
            if sweep == 0 and len(layers) > 0:
                order = range(0, len(layers))
            for r in order:
                layer = layers[r]
                d_w = {k: desired(k, (up[k] if sweep % 2 == 0 else down[k])) for k in layer}
                if sweep < 4:
                    layer.sort(key=lambda k: (d_w[k][0], pos[k]))
                self._place(layer, {k: d_w[k][0] for k in layer}, {k: d_w[k][1] for k in layer}, sep, pos)
        for r, layer in enumerate(layers):  # final balanced pass using both sides
            d_w = {k: desired(k, up[k] + down[k]) for k in layer}
            self._place(layer, {k: d_w[k][0] for k in layer}, {k: d_w[k][1] for k in layer}, sep, pos)

        # Unconnected blocks keep no pull of their own: tuck them against their neighbours.
        def lonely(k: Key) -> bool:
            return k[0] != "d" and not up[k] and not down[k] and not pulls.get(k)

        for layer in layers:
            for i in range(1, len(layer)):
                a, b = layer[i - 1], layer[i]
                if lonely(b):
                    pos[b] = pos[a] + sep(a, b)
            for i in range(len(layer) - 2, -1, -1):
                a, b = layer[i], layer[i + 1]
                if lonely(a) and all(lonely(k) for k in layer[: i + 1]):
                    pos[a] = pos[b] - sep(a, b)

        # Snap left edges to the grid and restore minimum gaps.
        for layer in layers:
            prev_k: Key | None = None
            for k in layer:
                left = snap(pos[k] - sizes[k] / 2)
                if prev_k is not None:
                    gap = sep(prev_k, k) - (sizes[prev_k] + sizes[k]) / 2
                    left = max(left, snap_up(pos[prev_k] + sizes[prev_k] / 2 + gap))
                pos[k] = left + sizes[k] / 2
                prev_k = k

        for k, c in pos.items():
            if k[0] != "d":
                self.prev_cross[k] = c

        # Main axis: rank thickness and offsets.
        gap_main = self.gap_main
        out: dict[Key, tuple[float, float]] = {}
        main = 0.0
        for layer in layers:
            real = [k for k in layer if k[0] != "d"]
            thick = max((self._ls(k)[1] for k in real), default=0.0)
            for k in real:
                m = main if k[0] == "g" else main + snap((thick - self._ls(k)[1]) / 2)
                left = pos[k] - sizes[k] / 2
                out[k] = (m, left) if self.lr else (left, m)
            if real:
                main += thick + gap_main
        x0 = min(p[0] for p in out.values())
        y0 = min(p[1] for p in out.values())
        x1 = max(p[0] + self.size[k][0] for k, p in out.items())
        y1 = max(p[1] + self.size[k][1] for k, p in out.items())
        return _Result(out, x0, y0, x1, y1)

    def _band_of(self, cid: str | None) -> int | None:
        if cid is None:
            return None
        top = self._chain(cid)[0]
        return self.band_index.get(top) if self.groups[top].kind == "layer" else None

    def _band_of_node(self, nid: str) -> int | None:
        chain = self.chain[nid]
        if not chain or self.groups[chain[0]].kind != "layer":
            return None
        return self.band_index.get(chain[0])

    def _rank(self, kids: list[Key], idx: dict[Key, int], sib: list[tuple[Key, Key]],
              vpref: dict[Key, int]) -> dict[Key, int]:
        # Inside layer bands (TB), orient edges so that blocks linked to the band above come first.
        edges: list[tuple[Key, Key]] = []
        for a, b in sib:
            if not self.lr and vpref[b] > vpref[a]:
                a, b = b, a
            edges.append((a, b))
        succ: dict[Key, list[Key]] = {k: [] for k in kids}
        for a, b in edges:
            if b not in succ[a]:
                succ[a].append(b)
        # Break cycles with an ordered DFS.
        state: dict[Key, int] = {}
        dag: dict[Key, list[Key]] = {k: [] for k in kids}

        def dfs(v: Key) -> None:
            state[v] = 1
            for w in succ[v]:
                if state.get(w) == 1:
                    dag[w].append(v)  # reverse back edge
                else:
                    dag[v].append(w)
                    if w not in state:
                        dfs(w)
            state[v] = 2

        for k in kids:
            if k not in state:
                dfs(k)
        preds: dict[Key, list[Key]] = {k: [] for k in kids}
        for a, ws in dag.items():
            for w in ws:
                preds[w].append(a)
        rank: dict[Key, int] = {}

        def longest(v: Key) -> int:
            if v not in rank:
                rank[v] = 0
                rank[v] = max((longest(p) + 1 for p in preds[v]), default=0)
            return rank[v]

        for k in kids:
            longest(k)
        # Pull sources down next to their first successor.
        for k in sorted(kids, key=lambda k: -rank[k]):
            if not preds[k] and dag[k]:
                rank[k] = max(rank[k], min(rank[w] for w in dag[k]) - 1)
        # Blocks without internal edges whose external links point down sit in the bottom rank.
        if not self.lr:
            bottom = max(rank.values())
            for k in kids:
                if not preds[k] and not dag[k] and vpref[k] < 0 and bottom > 0:
                    rank[k] = bottom
        return rank

    def _wrap(self, kids: list[Key], rank: dict[Key, int]) -> list[list[Key]]:
        """Split over-full ranks into several rows/columns."""
        limit = 4 if self.lr else 6
        n_ranks = max(rank.values()) + 1
        layers: list[list[Key]] = [[k for k in kids if rank[k] == r] for r in range(n_ranks)]
        out: list[list[Key]] = []
        for layer in layers:
            if len(layer) <= limit:
                out.append(layer)
                continue
            rows = math.ceil(len(layer) / limit)
            per = math.ceil(len(layer) / rows)
            for i in range(0, len(layer), per):
                out.append(layer[i:i + per])
        return [layer for layer in out if layer]

    @staticmethod
    def _place(layer: list[Key], want: dict[Key, float], weight: dict[Key, float], sep, pos: dict[Key, float]) -> None:
        """Weighted isotonic regression (pool adjacent violators) honouring minimum separations."""
        if not layer:
            return
        off = [0.0]
        for i in range(1, len(layer)):
            off.append(off[-1] + sep(layer[i - 1], layer[i]))
        blocks: list[list[float]] = []  # [weighted sum, weight, count]
        for i, k in enumerate(layer):
            w = max(weight[k], 1e-6)
            blocks.append([w * (want[k] - off[i]), w, 1])
            while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
                s, wt, c = blocks.pop()
                blocks[-1][0] += s
                blocks[-1][1] += wt
                blocks[-1][2] += c
        i = 0
        for s, wt, c in blocks:
            y = s / wt
            for _ in range(int(c)):
                pos[layer[i]] = y + off[i]
                i += 1

    # ------------------------------------------------------------------ absolute coordinates
    def absolute(self, title_h: float) -> tuple[dict[str, Box], dict[str, Box]]:
        node_abs: dict[str, Box] = {}
        group_abs: dict[str, Box] = {}
        root = self.results[None]
        self.content_off[None] = (MARGIN - root.x0, MARGIN + title_h - root.y0)

        def visit(cid: str | None, origin: tuple[float, float]) -> None:
            off = self.content_off[cid]
            frame = (origin[0] + off[0], origin[1] + off[1])
            self.frame_abs[cid] = frame
            for key, (x, y) in self.results[cid].pos.items():
                w, h = self.size[key]
                box = Box(frame[0] + x, frame[1] + y, w, h)
                if key[0] == "n":
                    node_abs[key[1]] = box
                    self.abs_center[key[1]] = (box.cx, box.cy)
                else:
                    group_abs[key[1]] = box
                    visit(key[1], (box.x, box.y))

        visit(None, (0.0, 0.0))
        return node_abs, group_abs


def compute_layout(spec: DiagramSpec) -> Layout:
    """Lay out a normalised spec; deterministic for identical input."""
    eng = _Engine(spec)
    title_lines = layout_runs([Run(spec.title, 18, True, "#1F2937")], 900) if spec.title else []
    title_h = lines_height(title_lines) + 20 if title_lines else 0.0
    title_h = snap_up(title_h)
    node_abs: dict[str, Box] = {}
    group_abs: dict[str, Box] = {}
    for _ in range(ITERATIONS):
        eng.layout(None)
        node_abs, group_abs = eng.absolute(title_h)

    notation = spec.notation
    nodes: dict[str, NodeBox] = {}
    for n in spec.nodes:
        box = node_abs[n.id]
        vis = eng.visuals[n.id]
        lines = eng.node_lines[n.id]
        if vis.shape == "actor" and eng.lr:
            anchor = Box(snap(box.cx - 14), box.y, 28, 50)
            text_box = Box(box.x, box.y + 54, box.w, box.h - 54)
        elif vis.shape == "actor":
            tw = max((ln.width for ln in lines), default=0.0)
            total = 28 + 8 + tw
            ax = snap(box.x + (box.w - total) / 2)
            anchor = Box(ax, box.cy - 25, 28, 50)
            text_box = Box(ax + 36, box.y, box.w - (ax + 36 - box.x), box.h)
        else:
            anchor = box
            l, t, r, b = vis.insets
            if vis.shape == "person":
                t = int(min(box.w * 0.45, box.h * 0.45) * 0.8) + 6
            text_box = Box(box.x + l, box.y + t, box.w - l - r, box.h - t - b)
        nodes[n.id] = NodeBox(n, vis, box, anchor, lines, text_box)

    groups: dict[str, GroupBox] = {}

    def add_groups(parent: str | None) -> None:
        for gid in eng.child_groups.get(parent, []):
            if gid not in group_abs:
                continue
            g = eng.groups[gid]
            layer = eng.group_layer.get(gid)
            groups[gid] = GroupBox(g, group_visual(g, notation, layer), group_abs[gid], layer, g.label,
                                   group_caption(g, notation))
            add_groups(gid)

    add_groups(None)

    # Edges
    routes: list[EdgeRoute] = []
    obstacles: dict[str, Box] = {}
    for nid, nb in nodes.items():
        obstacles[nid] = nb.anchor
        if nb.visual.shape == "actor" and nb.lines:  # the actor's free-standing label
            tw = max(ln.width for ln in nb.lines)
            th = lines_height(nb.lines)
            tb = nb.text_box
            if eng.lr:
                obstacles[nid + "#label"] = Box(tb.cx - tw / 2, tb.y, tw, th)
            else:
                obstacles[nid + "#label"] = Box(tb.x, tb.cy - th / 2, tw, th)
    anchors = {nid: nb.anchor for nid, nb in nodes.items()}
    for gid, gb in groups.items():  # keep edges off group titles
        if gb.visual.strip:
            obstacles[f"#title-{gid}"] = Box(gb.box.x, gb.box.y, STRIP - 10, gb.box.h)
        else:
            tw = text_width(gb.title, 12, True) + (text_width(f"[{gb.caption}]", 10) if gb.caption else 0)
            obstacles[f"#title-{gid}"] = Box(gb.box.x + 6, gb.box.y + 4, min(tw + 8, gb.box.w - 12), 16)
    shapes = {nid: nb.visual.shape for nid, nb in nodes.items()}
    kinds = [e.type + (e.access or "") for e in spec.edges]
    paths = route_edges([(e.source, e.target) for e in spec.edges], anchors, obstacles, eng.lr, shapes, kinds)
    for e, pts in zip(spec.edges, paths):
        lines = layout_runs(edge_text(e, notation), 150, max_lines=3)
        routes.append(EdgeRoute(e, edge_visual(e, notation), pts, lines))
    blocked = [nb.box.inflate(4) for nb in nodes.values()]
    placements = place_labels([(r.points, (max((ln.width for ln in r.lines), default=0.0),
                                           lines_height(r.lines))) if r.lines else None for r in routes], blocked)
    for r, pl in zip(routes, placements):
        if pl:
            r.label_box, r.label_t = pl

    # Extent
    boxes = [nb.box for nb in nodes.values()] + [gb.box for gb in groups.values()]
    boxes += [r.label_box for r in routes if r.label_box]
    for r in routes:
        xs = [p[0] for p in r.points]
        ys = [p[1] for p in r.points]
        boxes.append(Box(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
    x1 = max(b.r for b in boxes)
    y1 = max(b.b for b in boxes)
    title_box = None
    if title_lines:
        tw = max(ln.width for ln in title_lines)
        title_box = Box(MARGIN, MARGIN - 6, tw + 4, lines_height(title_lines))
        x1 = max(x1, title_box.r)

    legend = None
    if notation == "archimate" and spec.legend and spec.edges:
        legend = _legend(spec, routes, x1, y1)
        x1 = max(x1, legend.box.r)
        y1 = max(y1, legend.box.b)

    width = snap_up(x1 + MARGIN)
    height = snap_up(y1 + MARGIN)
    return Layout(spec, nodes, groups, routes, width, height, title_lines, title_box, legend)


def _legend(spec: DiagramSpec, routes: list[EdgeRoute], x1: float, y1: float) -> Legend:
    from .styles import RELATION_NAMES

    names = RELATION_NAMES[spec.language]
    seen: dict[str, LegendItem] = {}
    for r in routes:
        key = r.edge.type + (f":{r.edge.access}" if r.edge.access else "")
        if key not in seen:
            label = names.get(r.edge.type, r.edge.type)
            if r.edge.access:
                label += {"nl": {"read": " (lezen)", "write": " (schrijven)", "readwrite": " (lezen/schrijven)"},
                          "en": {"read": " (read)", "write": " (write)", "readwrite": " (read/write)"}}[
                    spec.language][r.edge.access]
            seen[key] = LegendItem(label, r.visual)
    items = list(seen.values())
    row_h = 20.0
    w = snap_up(max(text_width(i.name, 10) for i in items) + 60 + 24)
    w = max(w, 140.0)
    h = snap_up(30 + row_h * len(items) + 8)
    box = Box(snap_up(x1 - w), snap_up(y1 + 20), w, h)
    if box.x < MARGIN:
        box.x = MARGIN
    return Legend(box, "Legenda" if spec.language == "nl" else "Legend", items, row_h)
