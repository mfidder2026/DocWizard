"""Geometry primitives, orthogonal edge routing (A* on a sparse visibility grid) and label placement."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

Point = tuple[float, float]

CLEARANCE = 10.0  # distance kept between edges and node boxes
STUB = 20.0  # straight segment leaving / entering a node
BEND_COST = 40.0
MAX_EXPANSIONS = 40000


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def r(self) -> float:
        return self.x + self.w

    @property
    def b(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def inflate(self, m: float) -> Box:
        return Box(self.x - m, self.y - m, self.w + 2 * m, self.h + 2 * m)

    def overlaps(self, o: Box, eps: float = 0.0) -> bool:
        return self.x < o.r - eps and o.x < self.r - eps and self.y < o.b - eps and o.y < self.b - eps

    def contains(self, o: Box, eps: float = 0.5) -> bool:
        return o.x >= self.x - eps and o.y >= self.y - eps and o.r <= self.r + eps and o.b <= self.b + eps


_DIRS = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}
_OPP = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}


def _sides(s: Box, t: Box, lr: bool) -> tuple[str, str]:
    vert = ("bottom", "top") if t.y >= s.b + 10 else (("top", "bottom") if s.y >= t.b + 10 else None)
    horiz = ("right", "left") if t.x >= s.r + 10 else (("left", "right") if s.x >= t.r + 10 else None)
    first, second = (horiz, vert) if lr else (vert, horiz)
    return first or second or ("bottom", "top")


def _port(box: Box, side: str, frac: float) -> Point:
    if side in ("top", "bottom"):
        x = box.x + box.w * frac
        return (x, box.y if side == "top" else box.b)
    y = box.y + box.h * frac
    return (box.x if side == "left" else box.r, y)


def _on_outline(p: Point, box: Box, side: str, shape: str) -> Point:
    """Move a port from the bounding box onto the visible outline of non-rectangular shapes."""
    x, y = p
    if side in ("left", "right"):
        t = min(1.0, abs(y - box.cy) / (box.h / 2)) if box.h else 0.0
        if shape == "hexagon":
            inset = box.w * 0.12 * t
        elif shape == "hcylinder":
            rx = min(12.0, box.w / 2)
            inset = rx * (1 - math.sqrt(max(0.0, 1 - t * t)))
        elif shape == "cloud":
            inset = box.w * 0.08 + box.w * 0.1 * t * t
        else:
            inset = 0.0
        return (x + inset, y) if side == "left" else (x - inset, y)
    t = min(1.0, abs(x - box.cx) / (box.w / 2)) if box.w else 0.0
    if shape == "cylinder":
        inset = 12.0 * (1 - math.sqrt(max(0.0, 1 - t * t)))
    elif shape == "cloud":
        inset = box.h * 0.06 + box.h * 0.12 * t * t
    elif shape == "person" and side == "top":
        hs = min(box.w * 0.45, box.h * 0.45)
        inset = hs * 0.8 if abs(x - box.cx) > hs * 0.3 else 0.0
    else:
        inset = 0.0
    return (x, y + inset) if side == "top" else (x, y - inset)


def _assign_ports(pairs: list[tuple[str, str]], anchors: dict[str, Box], lr: bool,
                  shapes: dict[str, str]) -> list[tuple[Point, str, Point, str]]:
    sides = [_sides(anchors[s], anchors[t], lr) for s, t in pairs]
    slots: dict[tuple[str, str], list[tuple[float, int, int]]] = {}
    for i, ((s, t), (ss, ts)) in enumerate(zip(pairs, sides)):
        other_t, other_s = anchors[t], anchors[s]
        key_s = other_t.cx if ss in ("top", "bottom") else other_t.cy
        key_t = other_s.cx if ts in ("top", "bottom") else other_s.cy
        slots.setdefault((s, ss), []).append((key_s, i, 0))
        slots.setdefault((t, ts), []).append((key_t, i, 1))
    fracs: dict[tuple[int, int], float] = {}
    for (_, _side), items in slots.items():
        items.sort()
        n = len(items)
        for j, (_, i, end) in enumerate(items):
            fracs[(i, end)] = 0.5 if n == 1 else 0.25 + 0.5 * j / (n - 1)
    out = []
    for i, ((s, t), (ss, ts)) in enumerate(zip(pairs, sides)):
        sb, tb = anchors[s], anchors[t]
        ps, pt = _port(sb, ss, fracs[(i, 0)]), _port(tb, ts, fracs[(i, 1)])
        lone = len(slots[(s, ss)]) == 1 and len(slots[(t, ts)]) == 1
        if lone and ss in ("top", "bottom") and ts in ("top", "bottom"):
            lo, hi = max(sb.x, tb.x) + 10, min(sb.r, tb.r) - 10
            if lo <= hi:  # overlapping spans: use one straight line
                x = round(((lo + hi) / 2) / 10) * 10
                x = min(max(x, lo), hi)
                ps, pt = (x, ps[1]), (x, pt[1])
        elif lone and ss in ("left", "right") and ts in ("left", "right"):
            lo, hi = max(sb.y, tb.y) + 10, min(sb.b, tb.b) - 10
            if lo <= hi:
                y = round(((lo + hi) / 2) / 10) * 10
                y = min(max(y, lo), hi)
                ps, pt = (ps[0], y), (pt[0], y)
        ps = _snap_port(ps, sb, ss)
        pt = _snap_port(pt, tb, ts)
        out.append((_on_outline(ps, sb, ss, shapes.get(s, "")), ss, _on_outline(pt, tb, ts, shapes.get(t, "")), ts))
    return out


def _snap_port(p: Point, box: Box, side: str) -> Point:
    """Snap a port to the 10px grid along its side (keeps parallel edges aligned)."""
    if side in ("top", "bottom"):
        x = min(max(round(p[0] / 10) * 10, box.x + 6), box.r - 6)
        return (x, p[1])
    y = min(max(round(p[1] / 10) * 10, box.y + 6), box.b - 6)
    return (p[0], y)


class _Grid:
    def __init__(self, obstacles: list[Box], extra_x: list[float], extra_y: list[float],
                 thin: list[Box] | None = None) -> None:
        self.obs = [o.inflate(CLEARANCE) for o in obstacles] + [o.inflate(5) for o in thin or []]
        xs: set[float] = set(extra_x)
        ys: set[float] = set(extra_y)
        raw_x = sorted({v for o in obstacles for v in (o.x, o.r)})
        raw_y = sorted({v for o in obstacles for v in (o.y, o.b)})
        for o in self.obs:
            xs.update((o.x, o.r))
            ys.update((o.y, o.b))
        obstacles = obstacles + list(thin or [])
        for coords, target in ((raw_x, xs), (raw_y, ys)):
            for a, b in zip(coords, coords[1:]):
                target.add((a + b) / 2)
                if b - a >= 50:  # wide gaps get extra parallel channels
                    target.update((a + (b - a) * 0.3, a + (b - a) * 0.7))
        lo_x, hi_x = min(raw_x + extra_x) - 40, max(raw_x + extra_x) + 40
        lo_y, hi_y = min(raw_y + extra_y) - 40, max(raw_y + extra_y) + 40
        xs.update((lo_x, hi_x))
        ys.update((lo_y, hi_y))
        self.xs = sorted(round(v, 2) for v in xs)
        self.ys = sorted(round(v, 2) for v in ys)

        self._rows: dict[int, list[bool]] = {}
        self._cols: dict[int, list[bool]] = {}
        self._rects = [(o.x, o.y, o.r, o.b) for o in self.obs]

    def point_free(self, p: Point) -> bool:
        return not any(x0 < p[0] < x1 and y0 < p[1] < y1 for x0, y0, x1, y1 in self._rects)

    def _row(self, yi: int) -> list[bool]:
        """free[i]: may an edge run along row yi from xs[i] to xs[i+1]?"""
        row = self._rows.get(yi)
        if row is None:
            y = self.ys[yi]
            spans = [(x0, x1) for x0, y0, x1, y1 in self._rects if y0 < y < y1]
            xs = self.xs
            row = [not any(x0 < xs[i + 1] and xs[i] < x1 for x0, x1 in spans) for i in range(len(xs) - 1)]
            self._rows[yi] = row
        return row

    def _col(self, xi: int) -> list[bool]:
        col = self._cols.get(xi)
        if col is None:
            x = self.xs[xi]
            spans = [(y0, y1) for x0, y0, x1, y1 in self._rects if x0 < x < x1]
            ys = self.ys
            col = [not any(y0 < ys[i + 1] and ys[i] < y1 for y0, y1 in spans) for i in range(len(ys) - 1)]
            self._cols[xi] = col
        return col

    def free_seg(self, a: tuple[int, int], b: tuple[int, int]) -> bool:
        """Adjacent grid points only."""
        if a[1] == b[1]:
            return self._row(a[1])[min(a[0], b[0])]
        return self._col(a[0])[min(a[1], b[1])]


def segment_clear(obstacles: list[Box], a: Point, b: Point) -> bool:
    """True when the axis-parallel segment a-b does not pass through the interior of any obstacle."""
    lx, hx, ly, hy = min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1])
    for o in obstacles:
        if hx - lx < 1e-6:  # vertical
            if o.x < lx < o.r and o.y < hy and ly < o.b:
                return False
        elif o.y < ly < o.b and o.x < hx and lx < o.r:
            return False
    return True


def _astar(grid: _Grid, start: Point, start_dir: str, goal: Point, goal_dir: str,
           used: dict[tuple, set], owner: tuple[str, str, str]) -> list[Point] | None:
    si = (grid.xs.index(round(start[0], 2)), grid.ys.index(round(start[1], 2)))
    gi = (grid.xs.index(round(goal[0], 2)), grid.ys.index(round(goal[1], 2)))
    dirs = {"right": (1, 0), "left": (-1, 0), "bottom": (0, 1), "top": (0, -1)}
    gx, gy = goal

    def h(i: tuple[int, int]) -> float:
        dx, dy = abs(grid.xs[i[0]] - gx), abs(grid.ys[i[1]] - gy)
        return dx + dy + (BEND_COST if dx > 1e-6 and dy > 1e-6 else 0.0)

    if not (grid.point_free(start) and grid.point_free(goal)):
        return None
    pops = 0

    open_heap: list[tuple[float, float, int, tuple[int, int], str]] = []
    counter = 0
    heapq.heappush(open_heap, (h(si), 0.0, counter, si, start_dir))
    best: dict[tuple[tuple[int, int], str], float] = {(si, start_dir): 0.0}
    parent: dict[tuple[tuple[int, int], str], tuple[tuple[int, int], str] | None] = {(si, start_dir): None}
    while open_heap:
        _, g, _, cur, d = heapq.heappop(open_heap)
        pops += 1
        if pops > MAX_EXPANSIONS:
            return None
        if best.get((cur, d), math.inf) < g:
            continue
        if cur == gi:
            if d != goal_dir:
                g += BEND_COST
            state: tuple[tuple[int, int], str] | None = (cur, d)
            pts: list[Point] = []
            while state is not None:
                pts.append((grid.xs[state[0][0]], grid.ys[state[0][1]]))
                state = parent[state]
            return list(reversed(pts))
        for nd, (dx, dy) in dirs.items():
            if nd == _OPP[d]:
                continue
            nxt = (cur[0] + dx, cur[1] + dy)
            if not (0 <= nxt[0] < len(grid.xs) and 0 <= nxt[1] < len(grid.ys)):
                continue
            if not grid.free_seg(cur, nxt):
                continue
            length = abs(grid.xs[nxt[0]] - grid.xs[cur[0]]) + abs(grid.ys[nxt[1]] - grid.ys[cur[1]])
            cost = g + length + (BEND_COST if nd != d else 0.0)
            seg_key = (min(cur, nxt), max(cur, nxt))
            others = used.get(seg_key)
            # Sharing a channel is fine for identical edges fanning out of / into one node (a bus);
            # anything else overlapping is ambiguous and penalised.
            if others and any(not ((o[0] == owner[0] or o[1] == owner[1]) and o[2] == owner[2]) for o in others):
                cost += length * 1.5 + 25
            if cost < best.get((nxt, nd), math.inf):
                best[(nxt, nd)] = cost
                parent[(nxt, nd)] = (cur, d)
                counter += 1
                heapq.heappush(open_heap, (cost + h(nxt), cost, counter, nxt, nd))
    return None


def _simplify(points: list[Point]) -> list[Point]:
    """Drop duplicate and collinear points."""
    dedup: list[Point] = []
    for p in points:
        if not dedup or abs(dedup[-1][0] - p[0]) > 1e-6 or abs(dedup[-1][1] - p[1]) > 1e-6:
            dedup.append(p)
    out: list[Point] = []
    for p in dedup:
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (abs(a[0] - b[0]) < 1e-6 and abs(b[0] - p[0]) < 1e-6) or (
                    abs(a[1] - b[1]) < 1e-6 and abs(b[1] - p[1]) < 1e-6):
                out[-1] = p
                continue
        out.append(p)
    return out


def _fallback(ps: Point, ss: str, pt: Point) -> list[Point]:
    if ss in ("top", "bottom"):
        my = (ps[1] + pt[1]) / 2
        return [ps, (ps[0], my), (pt[0], my), pt]
    mx = (ps[0] + pt[0]) / 2
    return [ps, (mx, ps[1]), (mx, pt[1]), pt]


def route_edges(pairs: list[tuple[str, str]], anchors: dict[str, Box], obstacles: dict[str, Box],
                lr: bool, shapes: dict[str, str] | None = None, kinds: list[str] | None = None) -> list[list[Point]]:
    """Orthogonal routes (absolute polylines from source port to target port) for every (source, target).

    `shapes` (node id -> render shape) lets ports sit on curved/slanted outlines; `kinds` (one per edge)
    allows identical edges to share channels.
    """
    shapes = shapes or {}
    kinds = kinds or [""] * len(pairs)
    ports = _assign_ports(pairs, anchors, lr, shapes)
    obs_list = list(obstacles.values())
    order = sorted(range(len(pairs)), key=lambda i: abs(ports[i][0][0] - ports[i][2][0]) + abs(
        ports[i][0][1] - ports[i][2][1]))
    used: dict[tuple, set] = {}
    results: list[list[Point]] = [[] for _ in pairs]
    # Every port's stub is reserved so that other edges do not run into (or along) a foreign arrowhead.
    stubs: list[list[Box]] = []
    for ps, ss, pt, ts in ports:
        boxes = []
        for p, side in ((ps, ss), (pt, ts)):
            dx, dy = _DIRS[side]
            q = (p[0] + dx * STUB, p[1] + dy * STUB)
            boxes.append(Box(min(p[0], q[0]), min(p[1], q[1]), abs(q[0] - p[0]), abs(q[1] - p[1])))
        stubs.append(boxes)
    for i in order:
        ps, ss, pt, ts = ports[i]
        owner = (pairs[i][0], pairs[i][1], kinds[i])
        dsx, dsy = _DIRS[ss]
        dtx, dty = _DIRS[ts]
        s_stub = (ps[0] + dsx * STUB, ps[1] + dsy * STUB)
        t_stub = (pt[0] + dtx * STUB, pt[1] + dty * STUB)
        straight = (abs(ps[0] - pt[0]) < 1e-6 or abs(ps[1] - pt[1]) < 1e-6) and ss == _OPP[ts]
        thin = [b for j, bs in enumerate(stubs) if j != i for b in bs]
        grid = _Grid(obs_list, [ps[0], pt[0], s_stub[0], t_stub[0]], [ps[1], pt[1], s_stub[1], t_stub[1]], thin)
        path: list[Point] | None = None
        if straight and segment_clear(grid.obs, s_stub, t_stub):
            path = [s_stub, t_stub]  # a direct line is ideal when nothing is in the way
        if path is None:
            path = _astar(grid, s_stub, ss, t_stub, _OPP[ts], used, owner)
        if path is None:
            pts = _simplify(_fallback(ps, ss, pt))
        else:
            pts = _simplify([ps] + path + [pt])
            for a, b in zip(path, path[1:]):
                for seg in _unit_steps(grid, a, b):
                    used.setdefault(seg, set()).add(owner)
        results[i] = pts
    _straighten(results, pairs, ports, anchors, obs_list)
    return results


def _straighten(results: list[list[Point]], pairs: list[tuple[str, str]],
                ports: list[tuple[Point, str, Point, str]], anchors: dict[str, Box], obstacles: list[Box]) -> None:
    """Remove small jogs (Z-routes with an offset <= 20px) by sliding one end's port when that is free."""
    taken: dict[tuple[str, str], list[float]] = {}
    for (s, t), (ps, ss, pt, ts) in zip(pairs, ports):
        taken.setdefault((s, ss), []).append(ps[0] if ss in ("top", "bottom") else ps[1])
        taken.setdefault((t, ts), []).append(pt[0] if ts in ("top", "bottom") else pt[1])
    inflated = [o.inflate(CLEARANCE - 2) for o in obstacles]
    for i, pts in enumerate(results):
        if len(pts) != 4:
            continue
        (s, t), (ps, ss, pt, ts) = pairs[i], ports[i]
        vertical = ss in ("top", "bottom") and ts in ("top", "bottom")
        horizontal = ss in ("left", "right") and ts in ("left", "right")
        if not (vertical or horizontal):
            continue
        axis = 0 if vertical else 1
        off = pts[3][axis] - pts[0][axis]
        if off == 0 or abs(off) > 20:
            continue
        for node, side, keep, move, idx in ((t, ts, pts[0], pts[3], 3), (s, ss, pts[3], pts[0], 0)):
            box = anchors[node]
            lo, hi = (box.x, box.r) if vertical else (box.y, box.b)
            v = keep[axis]
            if not (lo + 6 <= v <= hi - 6):
                continue
            mine = move[axis]
            if any(abs(o - v) < 10 and abs(o - mine) > 1e-6 for o in taken[(node, side)]):
                continue
            if vertical:
                new_pt = (v, move[1])
            else:
                new_pt = (move[0], v)
            other = pts[0] if idx == 3 else pts[3]
            blockers = [o for o, src in zip(inflated, obstacles) if not (src.contains(anchors[s]) or
                                                                        src.contains(anchors[t]))]
            if not segment_clear(blockers, other, new_pt):
                continue
            new = [pts[0], new_pt] if idx == 3 else [new_pt, pts[3]]
            results[i] = new
            lst = taken[(node, side)]
            lst[lst.index(mine)] = v
            break


def _unit_steps(grid: _Grid, a: Point, b: Point) -> list[tuple]:
    ax, ay = grid.xs.index(round(a[0], 2)), grid.ys.index(round(a[1], 2))
    bx, by = grid.xs.index(round(b[0], 2)), grid.ys.index(round(b[1], 2))
    steps = []
    if ax == bx:
        for y in range(min(ay, by), max(ay, by)):
            steps.append(((ax, y), (ax, y + 1)))
    else:
        for x in range(min(ax, bx), max(ax, bx)):
            steps.append(((x, ay), (x + 1, ay)))
    return steps


# --------------------------------------------------------------------------- labels


def path_length(points: list[Point]) -> float:
    return sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(points, points[1:]))


def point_at(points: list[Point], t: float) -> Point:
    total = path_length(points)
    target = total * t
    for a, b in zip(points, points[1:]):
        seg = abs(a[0] - b[0]) + abs(a[1] - b[1])
        if seg >= target and seg > 0:
            f = target / seg
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        target -= seg
    return points[-1]


def _hits_path(box: Box, pts: list[Point]) -> bool:
    for a, b in zip(pts, pts[1:]):
        lx, hx, ly, hy = min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1])
        if lx < box.r and box.x < hx + 0.5 and ly < box.b and box.y < hy + 0.5:
            return True
    return False


def place_labels(items: list[tuple[list[Point], tuple[float, float]] | None],
                 blocked: list[Box]) -> list[tuple[Box, float] | None]:
    """Choose a position along each path where the label box overlaps no node, other label or other edge."""
    placed: list[Box] = []
    all_paths = [item[0] if item else None for item in items]
    out: list[tuple[Box, float] | None] = []
    for idx, item in enumerate(items):
        if item is None:
            out.append(None)
            continue
        others = [p for j, p in enumerate(all_paths) if p and j != idx]
        pts, (w, h) = item
        w, h = w + 8, h + 4
        choice: tuple[Box, float] | None = None
        fallback: tuple[Box, float] | None = None
        for t in (0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74, 0.18, 0.82):
            cx, cy = point_at(pts, t)
            box = Box(cx - w / 2, cy - h / 2, w, h)
            if any(box.overlaps(o) for o in blocked) or any(box.overlaps(o) for o in placed):
                continue
            if any(_hits_path(box, p) for p in others):
                fallback = fallback or (box, t)
                continue
            choice = (box, t)
            break
        choice = choice or fallback
        if choice is None:
            cx, cy = point_at(pts, 0.5)
            choice = (Box(cx - w / 2, cy - h / 2, w, h), 0.5)
        placed.append(choice[0])
        out.append(choice)
    return out


