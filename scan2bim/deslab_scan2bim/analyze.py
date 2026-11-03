"""Point cloud of an apartment -> scene (walls, openings, rooms) for the deslab add-on.

Deterministic, numpy only. The scene format is documented in scan2bim/README.md.
Works in a frame rotated so the main walls run along X and Y ("model frame"); the scene is
written in that frame, shifted so the floor of the largest room is z = 0 and the model starts
near (0, 0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import grid

RES = 0.02  # raster cell, m


@dataclass
class Options:
    step: int = 4                       # keep every n-th point of the E57
    exterior_thickness: float = 0.64    # one-sided wall with a window (only its inner face is scanned)
    default_thickness: float = 0.38     # other one-sided walls
    min_thickness: float = 0.05
    max_thickness: float = 0.90
    min_face_length: float = 0.20
    min_room_area: float = 1.0
    outside_level: float = 0.08         # a space this much off the main floor level is outside the flat
    coverage: float = 0.55              # share of the wall height a cell must be scanned to count as wall


@dataclass
class Face:
    axis: int          # 0: line x = const (wall along Y), 1: line y = const (wall along X)
    c: float           # position across the line
    a: float           # start along the line
    b: float           # end along the line
    side: int          # +1: the room is on the + side of the line, -1: on the - side
    slope: float = 0.0 # dc/dt of the fitted line
    used: list = field(default_factory=list)   # [(a, b)] parts already turned into walls

    def at(self, t):
        return self.c + self.slope * (t - (self.a + self.b) / 2)


# ---------------------------------------------------------------- levels and orientation

def find_levels(z: np.ndarray):
    """Floor and ceiling heights: the lowest and the highest dense horizontal surface."""
    lo, hi = np.percentile(z, 0.05), np.percentile(z, 99.95)
    bins = np.arange(lo - 0.05, hi + 0.06, 0.01)
    h, e = np.histogram(z, bins=bins)
    thr = h.max() * 0.25
    peaks = [i for i in range(1, len(h) - 1) if h[i] >= thr and h[i] >= h[i - 1] and h[i] >= h[i + 1]]
    if len(peaks) < 2:
        raise ValueError("cannot find floor and ceiling in the point cloud")
    floor = (e[peaks[0]] + e[peaks[0] + 1]) / 2
    ceiling = (e[peaks[-1]] + e[peaks[-1] + 1]) / 2
    return float(floor), float(ceiling)


def find_orientation(xy: np.ndarray) -> float:
    """Angle (rad) that turns the main walls onto the X and Y axes."""
    rng = np.random.default_rng(0)
    if len(xy) > 300000:
        xy = xy[rng.choice(len(xy), 300000, replace=False)]

    def score(theta):
        c, s = math.cos(theta), math.sin(theta)
        u = xy[:, 0] * c - xy[:, 1] * s
        v = xy[:, 0] * s + xy[:, 1] * c
        hu = np.bincount(((u - u.min()) / 0.01).astype(int))
        hv = np.bincount(((v - v.min()) / 0.01).astype(int))
        return float((hu.astype(np.float64) ** 2).sum() + (hv.astype(np.float64) ** 2).sum())

    angles = np.radians(np.arange(-45, 45, 0.5))
    best = max(angles, key=score)
    fine = best + np.radians(np.arange(-0.5, 0.5001, 0.05))
    return float(max(fine, key=score))


# ---------------------------------------------------------------- faces

def _runs(mask1d: np.ndarray, max_gap: int, min_len: int):
    idx = np.flatnonzero(mask1d)
    if len(idx) == 0:
        return []
    runs = []
    start = prev = idx[0]
    for i in idx[1:]:
        if i - prev > max_gap + 1:
            runs.append((start, prev + 1))
            start = i
        prev = i
    runs.append((start, prev + 1))
    return [r for r in runs if r[1] - r[0] >= min_len]


def find_faces(pts, floor, ceiling, opt: Options):
    """Vertical planes along X and Y: the scanned faces of walls."""
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    x0, y0 = x.min() - 6 * RES, y.min() - 6 * RES
    W = int((x.max() - x0) / RES) + 8
    H = int((y.max() - y0) / RES) + 8
    ix = ((x - x0) / RES).astype(np.int64)
    iy = ((y - y0) / RES).astype(np.int64)

    zlo, zhi = floor + 0.2, ceiling - 0.1
    m = (z > zlo) & (z < zhi)
    nb = int((zhi - zlo) / 0.1) + 1
    key = (iy[m] * W + ix[m]) * nb + ((z[m] - zlo) / 0.1).astype(np.int64)
    cells = np.unique(key) // nb
    cover = np.bincount(cells, minlength=W * H).reshape(H, W) / nb
    wall = cover >= opt.coverage

    faces = []
    min_len = int(opt.min_face_length / RES)
    for axis in (0, 1):
        # axis 0: faces x = const; profile over x of the wall cells, runs along y
        prof = wall.sum(axis=0) if axis == 0 else wall.sum(axis=1)
        n = len(prof)
        for c in range(2, n - 2):
            if prof[c] < min_len or prof[c] < prof[c - 1] or prof[c] < prof[c + 1]:
                continue
            if prof[c] == prof[c - 1] and prof[c - 1] >= prof[c - 2]:
                continue  # plateau: keep its first cell only
            # +-2 cells: a wall a few tenths of a degree off the axis still gives one run
            band = wall[:, c - 2:c + 3].any(axis=1) if axis == 0 else wall[c - 2:c + 3, :].any(axis=0)
            for a, b in _runs(band, 3, min_len):
                cpos = (x0 if axis == 0 else y0) + (c + 0.5) * RES
                tpos0 = (y0 if axis == 0 else x0)
                faces.append(Face(axis, cpos, tpos0 + a * RES, tpos0 + b * RES, 0))
    # floor hits first; ceiling hits only where the floor says nothing (a ceiling can run over
    # the top of a partition, and furniture can hide the floor)
    hit_sets = [pts[z < floor + 0.25], pts[z > floor + 2.3]]
    good = []
    for f in faces:
        _refine_face(f, pts, floor, ceiling)
        # the room side has hits right next to the face, the wall side none
        # (away from the ends, where door reveals and corners bring the other side in)
        trim = min(0.12, (f.b - f.a) / 4)
        side = 0
        for hits in hit_sets:
            cc, tt = hits[:, f.axis], hits[:, 1 - f.axis]
            sel = (tt > f.a + trim) & (tt < f.b - trim)
            off = cc[sel] - f.at(tt[sel])
            plus = np.count_nonzero((off > 0.025) & (off < 0.07))
            minus = np.count_nonzero((off < -0.025) & (off > -0.07))
            if max(plus, minus) >= 10 and min(plus, minus) <= 0.25 * max(plus, minus):
                side = 1 if plus > minus else -1
                break
        if side == 0:
            continue  # nothing around, or room on both sides: furniture, a door leaf
        f.side = side
        good.append(f)
    # drop duplicates of the same face found from neighbouring peaks
    good.sort(key=lambda f: (f.axis, f.side, f.c))
    kept = []
    for f in good:
        dup = next((k for k in kept if k.axis == f.axis and k.side == f.side and abs(k.c - f.c) < 0.05
                    and min(k.b, f.b) - max(k.a, f.a) > -0.04), None)
        if dup is None:
            kept.append(f)
        elif f.b - f.a > dup.b - dup.a:
            f.a, f.b = min(dup.a, f.a), max(dup.b, f.b)
            kept[kept.index(dup)] = f
        else:
            dup.a, dup.b = min(dup.a, f.a), max(dup.b, f.b)
    return kept, (x0, y0, W, H), cover


def _refine_face(f: Face, pts, floor, ceiling):
    """Least-squares line through the face points (walls are rarely exactly square)."""
    cc = pts[:, f.axis]
    tt = pts[:, 1 - f.axis]
    z = pts[:, 2]
    m = (np.abs(cc - f.c) < 0.04) & (tt > f.a + 0.05) & (tt < f.b - 0.05) & (z > floor + 0.3) & (z < ceiling - 0.2)
    c, t = cc[m].astype(np.float64), tt[m].astype(np.float64)
    if len(c) < 50:
        return
    mid = (f.a + f.b) / 2
    for _ in range(3):
        A = np.stack([np.ones_like(t), t - mid], 1)
        sol, *_ = np.linalg.lstsq(A, c, rcond=None)
        r = c - A @ sol
        keep = np.abs(r) < max(0.005, 2.5 * np.std(r))
        c, t = c[keep], t[keep]
        if len(c) < 50:
            break
    f.c, f.slope = float(sol[0]), float(sol[1])
    if abs(f.slope) > 0.02:  # more than ~1 degree off: keep it straight, it is noise
        f.slope = 0.0


# ---------------------------------------------------------------- walls

@dataclass(eq=False)  # walls are compared by identity
class Wall:
    axis: int
    c: float            # centre line position at t = (a+b)/2
    a: float
    b: float
    thickness: float
    room_side: int      # side of the scanned room for a one-sided wall, 0 when both sides were scanned
    measured: bool
    slope: float = 0.0
    faces: list = field(default_factory=list)
    openings: list = field(default_factory=list)
    beg: tuple = None
    end: tuple = None

    def point(self, t):
        cc = self.c + self.slope * (t - (self.a + self.b) / 2)
        return (cc, t) if self.axis == 0 else (t, cc)


def _merge_collinear(faces):
    """Faces on one line with a gap may be one wall with an opening: join them, keep the gaps."""
    groups = []
    for f in sorted(faces, key=lambda f: (f.axis, f.side, f.a)):
        cands = [g for g in groups if g[0].axis == f.axis and g[0].side == f.side
                 and 0 <= f.a - g[-1].b <= 3.2
                 and abs(g[-1].at(f.a) - f.at(f.a)) < (0.08 if f.a - g[-1].b < 1.3 else 0.04)]
        if cands:
            min(cands, key=lambda g: abs(g[-1].at(f.a) - f.at(f.a))).append(f)
        else:
            groups.append([f])
    return groups


def split_lines(lines):
    """Split the lines again at the gaps that turned out not to be openings."""
    out = []
    for f in lines:
        cuts = [g for g in f.gaps if g not in getattr(f, "closed", [])
                and not any(o["a"] >= g[0] - 0.1 and o["b"] <= g[1] + 0.1 for o in f.openings)]
        bounds = [f.a] + [v for g in cuts for v in g] + [f.b]
        for a, b in zip(bounds[0::2], bounds[1::2]):
            part = Face(f.axis, f.at((a + b) / 2), a, b, f.side, f.slope, used=[])
            part.gaps = [g for g in f.gaps if g not in cuts and g[0] >= a and g[1] <= b]
            part.openings = [o for o in f.openings if o["a"] >= a - 0.1 and o["b"] <= b + 0.1]
            part.window_gaps = [g for g in f.window_gaps if g[0] >= a - 0.1 and g[1] <= b + 0.1]
            out.append(part)
    return out


def merge_lines(faces):
    lines = []
    for g in _merge_collinear(faces):
        a, b = g[0].a, g[-1].b
        n = sum(f.b - f.a for f in g)
        c = sum(f.at((a + b) / 2) * (f.b - f.a) for f in g) / n
        slope = sum(f.slope * (f.b - f.a) for f in g) / n
        line = Face(g[0].axis, c, a, b, g[0].side, slope, used=[])
        line.gaps = [(g[i].b, g[i + 1].a) for i in range(len(g) - 1)]
        lines.append(line)
    return lines


def _has_back(f, lines, lo, hi):
    """Another face of the same wall behind f over [lo, hi]: f is an interior wall there, not a
    facade seen from inside only."""
    return any(q.axis == f.axis and q.side == -f.side and -0.9 <= (q.c - f.c) * f.side <= -0.05
               and q.a < hi and q.b > lo for q in lines if q is not f)


def extend_to_corners(lines, pts, floor, ceiling, reach=3.2):
    """A facade line that stops at an opening next to a corner (the pier at the corner is too
    short to be found) runs on to the perpendicular wall when a lintel spans the gap; the gap
    from the reveal to the old end becomes an opening of the line."""
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    for f in lines:
        tt = y if f.axis == 0 else x
        cc = x if f.axis == 0 else y
        for end in ("a", "b"):
            e = getattr(f, end)
            out = -1 if end == "a" else 1
            perp = [p for p in lines if p.axis != f.axis
                    and 0.3 <= (p.c - e) * out <= reach
                    and p.a - 0.15 <= f.at(e) <= p.b + 0.15
                    and p.side == -out and p.b - p.a >= 1.0]
            if not perp:
                continue
            p = min(perp, key=lambda p: abs(p.c - e))
            lo, hi = sorted((p.c, e))
            if _has_back(f, lines, min(lo, f.a), max(hi, f.b)):
                continue  # facades only: an interior wall has its other face to go by
            near = (np.abs(cc - f.at(e)) < 0.03) & (tt > lo + 0.05) & (tt < hi - 0.05)
            q = (hi - lo) / 4  # the middle of the gap, away from the reveals
            head = _contiguous_from_top(z[near & (tt > lo + q) & (tt < hi - q)] - floor, 0.0, ceiling - floor)
            if head is None or head < 1.5 or head > ceiling - floor - 0.15:
                continue  # no lintel, or only the ceiling over the gap
            edges = np.arange(lo + 0.05, hi - 0.05, 0.1)
            top = near & (z - floor > head)
            covered = np.histogram(tt[top], bins=np.append(edges, hi - 0.05))[0] > 0
            if len(covered) == 0 or covered.mean() < 0.7:
                continue
            # the reveal next to the corner: a short face from this line into the wall
            reveals = [q for q in lines if q.axis == f.axis ^ 1 and q is not p
                       and lo + 0.05 < q.c < hi - 0.05 and q.b - q.a < 0.95
                       and min(abs(q.a - f.at(q.c)), abs(q.b - f.at(q.c))) < 0.06]
            r = min(reveals, key=lambda q: abs(q.c - p.c)).c if reveals else p.c
            na, nb = (p.c, f.b) if end == "a" else (f.a, p.c)
            f.c = f.at((na + nb) / 2)  # c is taken at the middle of the line
            if end == "a":
                f.gaps.insert(0, (r, f.a))
            else:
                f.gaps.append((f.b, r))
            f.a, f.b = na, nb


def _is_reveal(f, lines):
    """A short face that spans the depth of a wall between its two faces: a door or window reveal."""
    if f.b - f.a > 0.95:
        return False
    ends = [p for p in lines if p.axis != f.axis
            and (abs(p.a - f.c) < 0.08 or abs(p.b - f.c) < 0.08
                 or any(abs(g[0] - f.c) < 0.08 or abs(g[1] - f.c) < 0.08 for g in p.gaps))]
    at_a = [p for p in ends if abs(p.at(f.c) - f.a) < 0.06]
    at_b = [p for p in ends if abs(p.at(f.c) - f.b) < 0.06]
    return any(p.side != q.side for p in at_a for q in at_b)


def drop_inside(walls):
    """Drop one-sided walls whose face lies inside or on another wall (a frame in a niche,
    a stub of a partition whose other face was not found)."""
    keep = []
    for w in walls:
        if w.measured:
            keep.append(w)
            continue
        f = w.faces[0]
        mid = (f.a + f.b) / 2
        fp = np.array((f.at(mid), mid) if f.axis == 0 else (mid, f.at(mid)))
        inside = False
        for o in walls:
            if o is w or o.axis != w.axis:
                continue
            d = o.end - o.beg
            L = np.linalg.norm(d)
            if L < 1e-6:
                continue
            u = d / L
            along = float(np.dot(fp - o.beg, u))
            across = abs(float(u[0] * (fp - o.beg)[1] - u[1] * (fp - o.beg)[0]))
            margin = 0.03 if o.measured else -0.03
            if -0.02 <= along <= L + 0.02 and across < o.thickness / 2 + margin and \
                    (o.measured or o.thickness >= w.thickness):
                inside = True
                break
        if not inside:
            keep.append(w)
    return keep


def merge_overlaps(walls):
    """deslab: two walls on one line, of one thickness, that overlap (a measured piece and a
    one-sided wall from the rest of its face) are one wall."""
    merged = True
    while merged:
        merged = False
        for w in walls:
            for o in walls:
                if o is w or o.axis != w.axis or abs(o.thickness - w.thickness) > 0.03:
                    continue
                cw, lw, hw = _span(w)
                co, lo, ho = _span(o)
                if abs(cw - co) > 0.03 or min(hw, ho) - max(lw, lo) < 0.05:
                    continue
                keep, gone = (w, o) if hw - lw >= ho - lo else (o, w)
                t = 1 - keep.axis
                pts = [keep.beg, keep.end, gone.beg, gone.end]
                ends = sorted(pts, key=lambda p: p[t])
                if keep.beg[t] <= keep.end[t]:
                    keep.beg, keep.end = ends[0].copy(), ends[-1].copy()
                else:
                    keep.beg, keep.end = ends[-1].copy(), ends[0].copy()
                keep.a, keep.b = min(keep.a, gone.a), max(keep.b, gone.b)
                keep.measured = keep.measured or gone.measured
                if keep.measured:
                    keep.room_side = 0
                for op in gone.openings:
                    if not any(min(p["b"], op["b"]) - max(p["a"], op["a"]) > 0 for p in keep.openings):
                        keep.openings.append(op)
                keep.faces += [f for f in gone.faces if f not in keep.faces]
                walls.remove(gone)
                merged = True
                break
            if merged:
                break
    return walls


def merge_walls(walls):
    """Collinear walls of one thickness that meet end to end become one wall."""
    merged = True
    while merged:
        merged = False
        for w in walls:
            for o in walls:
                if o is w or o.axis != w.axis or abs(o.thickness - w.thickness) > 0.01 or o.room_side != w.room_side:
                    continue
                if abs(o.c + o.slope * (w.b - (o.a + o.b) / 2) - (w.c + w.slope * (w.b - (w.a + w.b) / 2))) > 0.02:
                    continue
                if abs(o.a - w.b) > 0.45:  # w ends where o begins (or across a perpendicular wall)
                    continue
                w.b = o.b
                w.end = o.end
                w.openings += o.openings
                w.faces += [f for f in o.faces if f not in w.faces]
                walls.remove(o)
                merged = True
                break
            if merged:
                break
    return walls


def build_walls(lines, opt: Options):
    walls = []
    lines = [f for f in lines if not _is_reveal(f, lines)]
    # pair faces that look at each other across a wall, thinnest walls first
    pairs = []
    for f in lines:
        for o in lines:
            if o.axis != f.axis or f.side != 1 or o.side != -1:
                continue
            d = f.c - o.c  # o (room on its - side) lies behind f (room on its + side)
            if opt.min_thickness <= d <= opt.max_thickness and min(f.b, o.b) - max(f.a, o.a) >= opt.min_face_length:
                pairs.append((d, f, o))
    pairs.sort(key=lambda p: p[0])
    for d, f, o in pairs:
        for a, b in _intersect_intervals(_free(f, f.a, f.b), _free(o, o.a, o.b)):
            if b - a < opt.min_face_length:
                continue
            w = Wall(f.axis, (f.c + o.c) / 2, a, b, d, 0, True, (f.slope + o.slope) / 2)
            w.faces = [f, o]
            walls.append(w)
            f.used.append((a, b))
            o.used.append((a, b))
    # what is left of a face is a wall scanned from one side only
    for f in lines:
        for a, b in _free_parts(f, opt.min_face_length):
            windows = [g for g in f.window_gaps if g[0] >= a - 0.01 and g[1] <= b + 0.01]
            t = opt.exterior_thickness if windows else opt.default_thickness
            # the rest of a face that is partly paired keeps the measured thickness
            paired = [w for w in walls if w.measured and f in w.faces]
            if paired and not windows:
                t = max(paired, key=lambda w: w.b - w.a).thickness
            w = Wall(f.axis, f.c - f.side * t / 2, a, b, t, f.side, False, f.slope)
            w.backed = bool(paired and not windows)  # deslab: its back face is a measured one
            w.faces = [f]
            walls.append(w)
            f.used.append((a, b))
    return walls


def drop_plates(walls, pts, floor):
    """deslab: drop one-sided walls that are thin plates seen from both sides (an open door
    leaf, a board leaning on a wall): a wall scanned from one side has nothing right behind
    its face, a plate has its other side a few centimetres back."""
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    zz = z - floor
    band = (zz > 0.5) & (zz < 2.0)
    keep = []
    for w in walls:
        if w.measured or w.b - w.a < 0.3:
            keep.append(w)
            continue
        f = w.faces[0]
        tt = y if f.axis == 0 else x
        cc = x if f.axis == 0 else y
        sel = band & (tt > w.a + 0.1) & (tt < w.b - 0.1)
        off = (cc[sel] - f.at(tt[sel])) * f.side
        behind = tt[sel][(off < -0.02) & (off > -0.12)]
        edges = np.arange(w.a + 0.1, w.b - 0.1, 0.1)
        if len(edges) and (np.histogram(behind, bins=np.append(edges, w.b - 0.1))[0] >= 2).mean() >= 0.5:
            continue
        keep.append(w)
    return keep


def _free(f, a, b):
    parts = [(a, b)]
    for ua, ub in sorted(f.used):
        parts = [q for pa, pb in parts for q in ((pa, min(pb, ua)), (max(pa, ub), pb)) if q[1] - q[0] > 1e-6]
    return parts


def _intersect_intervals(p, q):
    return [(max(a, c), min(b, d)) for a, b in p for c, d in q if min(b, d) > max(a, c)]


def _free_parts(f, min_len):
    parts = [(f.a, f.b)]
    for ua, ub in sorted(f.used):
        nxt = []
        for a, b in parts:
            if ub <= a or ua >= b:
                nxt.append((a, b))
                continue
            if ua - a >= min_len:
                nxt.append((a, ua))
            if b - ub >= min_len:
                nxt.append((ub, b))
        parts = nxt
    return parts


# ---------------------------------------------------------------- openings

def classify_gaps(lines, pts, floor, ceiling):
    """Turn the gaps of each face line into openings.

    Along the gap, in 5 cm bins, the lowest material behind the face plane decides: the floor
    (a doorway, a door leaf, a balcony door) or a sill board higher up (a window). A balcony
    block, a door next to a window in one niche, comes out as two openings.
    """
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    for f in lines:
        f.window_gaps = []
        f.openings = []
        f.closed = []
        tt = y if f.axis == 0 else x
        cc = x if f.axis == 0 else y
        for g, (g0, g1) in [(g, piece) for g in f.gaps for piece in _open_parts(f, g, lines)]:
            g0, g1 = _refine_gap(f, g0, g1, tt, cc, z, floor)
            width = g1 - g0
            if width < 0.4:
                continue
            near = (tt > g0 + 0.03) & (tt < g1 - 0.03)
            off = (cc - f.at((g0 + g1) / 2)) * f.side  # >0 room side, <0 into the wall
            sel = near & (off < 0.03) & (off > -0.9)
            zs, offs, ts = z[sel] - floor, off[sel], tt[sel]
            plane = np.abs(offs) < 0.04
            mid = plane & (ts > g0 + 0.25 * width) & (ts < g1 - 0.25 * width)
            # head: bottom of the material that hangs down from the ceiling in the face plane
            head = _contiguous_from_top(zs[mid], 0.0, ceiling - floor)
            if head is None or head < 1.5:
                continue  # no lintel: the face really ends here
            if head > ceiling - floor - 0.3 and _back_closed(f, lines, g0, g1):
                # deslab: a recess up to the ceiling (a wardrobe niche) with the other face of
                # the wall whole behind it is not a doorway: the wall stays closed there
                f.closed.append(g)
                continue
            behind = (offs < -0.03) & (zs < head - 0.08)
            edges = np.arange(g0, g1 + 1e-9, 0.05)
            kinds, sills = [], []
            for b0, b1 in zip(edges[:-1], edges[1:]):
                zb = zs[behind & (ts >= b0) & (ts < b1)]
                if len(zb) < 10:
                    kinds.append("solid")  # nothing seen through: wall, not an opening
                    sills.append(0.0)
                    continue
                low = float(np.percentile(zb, 2))
                kinds.append("window" if low > 0.3 else "door")
                sills.append(low)
            if kinds.count("solid") > len(kinds) / 2:
                continue
            kinds = ["door" if k == "solid" else k for k in kinds]
            glass = None if _has_back(f, lines, g0, g1) else _glass_plane(offs, zs, ts, g0, g1, head)
            if glass is not None and "door" in kinds:
                # glazing down to the floor (a French window, a balcony door): built as a window
                # without sill boards, like a balcony door
                kinds = ["glazed" if k == "door" else k for k in kinds]
            for kind, i0, i1 in _segments(kinds, min_bins=5):
                a, b = edges[i0], edges[i1]
                if i0 == 0:
                    a = g0
                if i1 == len(kinds):
                    b = g1
                sill = float(np.median(sills[i0:i1])) if kind == "window" else 0.0
                if kind == "glazed":
                    kind = "balconyDoor"
                o = {"kind": kind, "a": float(a), "b": float(b), "sill": round(sill, 3), "head": round(head, 3)}
                f.openings.append(o)
                if kind in ("window", "balconyDoor"):
                    f.window_gaps.append((g0, g1))


def _back_closed(f, lines, lo, hi):
    """deslab: the other face of the wall runs on behind [lo, hi] without a gap of its own."""
    for q in lines:
        if (q is not f and q.axis == f.axis and q.side == -f.side
                and -0.9 <= (q.c - f.c) * f.side <= -0.05 and q.a <= lo + 0.05 and q.b >= hi - 0.05
                and not any(min(b, hi) - max(a, lo) > 0.2 * (hi - lo) for a, b in q.gaps)):
            return True
    return False


def _open_parts(f, gap, lines, min_len=0.4):
    """deslab: the parts of a gap not closed by a face standing in front of it (a pier or a
    shaft box in front of the wall), each starting at the reveal next to it when there is one."""
    parts = [gap]
    for q in lines:
        if q.axis == f.axis and q.side == f.side and 0.05 <= (q.c - f.c) * f.side <= 0.9:
            parts = [r for a, b in parts for r in ((a, min(b, q.a)), (max(a, q.b), b)) if r[1] - r[0] > 1e-6]
    out = []
    for a, b in parts:
        if b - a < min_len:
            continue
        # a reveal: a short face across the line, from its plane into the wall
        rev = [r.c for r in lines if r.axis != f.axis and r.b - r.a < 0.95 and a - 0.01 < r.c < b + 0.01
               and min(abs(r.a - f.at(r.c)), abs(r.b - f.at(r.c))) < 0.06]
        if (a, b) != gap:
            near_a = [c for c in rev if c - a < 0.15]
            near_b = [c for c in rev if b - c < 0.15]
            a = max(near_a) if near_a else a
            b = min(near_b) if near_b else b
        if b - a >= min_len:
            out.append((a, b))
    return out


def _glass_plane(offs, zs, ts, g0, g1, head):
    """A glass plane in the depth of the opening: at mid height, the points behind the face lie
    in one thin plane at least 0.2 m deep in the wall (a closed door leaf sits at the face, an
    open one stands across, another room lies further back) along most of the gap."""
    mid = (offs < -0.05) & (zs > 0.8) & (zs < min(2.0, head - 0.3))
    if np.count_nonzero(mid) < 30:
        return None
    o, t = offs[mid], ts[mid]
    med = float(np.median(o))
    if med > -0.2:
        return None
    on = np.abs(o - med) < 0.04
    if np.count_nonzero(on) < 0.5 * len(o):
        return None
    edges = np.arange(g0, g1, 0.1)
    covered = np.histogram(t[on], bins=np.append(edges, g1))[0] > 0
    if covered.mean() < 0.4:
        return None
    return med


def _segments(kinds, min_bins):
    """Runs of equal labels; runs shorter than min_bins join their longer neighbour."""
    runs = []
    for i, k in enumerate(kinds):
        if runs and runs[-1][0] == k:
            runs[-1][2] = i + 1
        else:
            runs.append([k, i, i + 1])
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for j, (k, i0, i1) in enumerate(runs):
            if i1 - i0 >= min_bins:
                continue
            nb = [n for n in (j - 1, j + 1) if 0 <= n < len(runs)]
            n = max(nb, key=lambda n: runs[n][2] - runs[n][1])
            runs[n][1], runs[n][2] = min(runs[n][1], i0), max(runs[n][2], i1)
            del runs[j]
            changed = True
            break
    merged = []
    for r in runs:
        if merged and merged[-1][0] == r[0]:
            merged[-1][2] = r[2]
        else:
            merged.append(r)
    return [tuple(r) for r in merged]


def _refine_gap(f, g0, g1, tt, cc, z, floor):
    """Gap edges from the raw face points at door-leaf height instead of the 2 cm raster."""
    near = (np.abs(cc - f.at((g0 + g1) / 2)) < 0.025) & (z > floor + 1.25) & (z < floor + 1.85)
    left = near & (tt > g0 - 0.1) & (tt < g0 + 0.04)
    right = near & (tt > g1 - 0.04) & (tt < g1 + 0.1)
    a = float(np.percentile(tt[left], 99.5)) if np.count_nonzero(left) > 20 else g0
    b = float(np.percentile(tt[right], 0.5)) if np.count_nonzero(right) > 20 else g1
    return a, b


def _contiguous_from_top(zs, floor, ceiling, step=0.03):
    if len(zs) < 20:
        return None
    bins = np.arange(floor, ceiling + step, step)
    h, _ = np.histogram(zs, bins=bins)
    occ = h > max(2, 0.02 * h.max())
    i = len(occ) - 1
    while i > 0 and not occ[i]:
        i -= 1  # skip the empty top bins between the last bin and the ceiling
    while i > 0 and occ[i]:
        i -= 1
    # exact: the lowest point of the hanging material
    above = zs[zs >= bins[i + 1]]
    return float(above.min()) if len(above) else float(bins[i + 1])


def attach_openings(walls, lines):
    for w in walls:
        for f in w.faces:
            for o in getattr(f, "openings", []):
                over = min(o["b"], w.b) - max(o["a"], w.a)
                if over < 0.7 * (o["b"] - o["a"]):
                    continue
                # an opening at the end of a one-sided piece, where the measured part of the wall
                # begins, ends with the piece
                o = dict(o, a=max(o["a"], w.a), b=min(o["b"], w.b))
                # deslab: one opening seen from both faces of a wall: keep the narrower, the
                # reveal-to-reveal width (an opening never overlaps another one)
                same = [p for p in w.openings if min(p["b"], o["b"]) - max(p["a"], o["a"])
                        > 0.5 * min(p["b"] - p["a"], o["b"] - o["a"])]
                if not same:
                    w.openings.append(dict(o))
                elif o["b"] - o["a"] < same[0]["b"] - same[0]["a"]:
                    w.openings[w.openings.index(same[0])] = dict(o)


# ---------------------------------------------------------------- junctions

def _line(w):
    p = np.array(w.point(w.a))
    q = np.array(w.point(w.b))
    return p, q


def join_walls(walls, corner_reach=1.3, tee_reach=0.8, joined=None):
    """Close the walls: move the wall ends onto the centre lines of the walls they meet.

    deslab: walls are always closed. A scan leaves gaps at corners (furniture, shadows), so
    L corners are closed when both ends are within corner_reach of the corner, and a free end
    runs on to a crossing wall up to tee_reach away (T joints).
    """
    if joined is None:
        for w in walls:
            w.beg, w.end = _line(w)
        joined = set()

    def ends(w):
        return (("beg", w.beg), ("end", w.end))

    # L corners: the nearest ends of two crossing walls, closest pairs first
    pairs = []
    for i, w in enumerate(walls):
        for o in walls[i + 1:]:
            if o.axis == w.axis:
                continue
            x = _intersect(w, o)
            if x is None:
                continue
            we, wp = min(ends(w), key=lambda e: np.linalg.norm(e[1] - x))
            oe, op = min(ends(o), key=lambda e: np.linalg.norm(e[1] - x))
            dw, do = np.linalg.norm(wp - x), np.linalg.norm(op - x)
            # the corner must lie off the far end of both walls, not inside either of them
            if dw <= corner_reach and do <= corner_reach and _beyond_or_at(w, we, x) and _beyond_or_at(o, oe, x):
                pairs.append((dw + do, w, we, o, oe, x))
    for _, w, we, o, oe, x in sorted(pairs, key=lambda t: t[0]):
        if (id(w), we) in joined or (id(o), oe) in joined:
            continue
        setattr(w, we, x)
        setattr(o, oe, x)
        joined |= {(id(w), we), (id(o), oe)}

    # T joints: a free end runs on to the centre line of the wall in front of it
    for w in walls:
        for end_name in ("beg", "end"):
            if (id(w), end_name) in joined:
                continue
            e = getattr(w, end_name)
            other_end = w.end if end_name == "beg" else w.beg
            d = e - other_end
            d = d / np.linalg.norm(d)
            best = None
            for o in walls:
                if o is w or o.axis == w.axis:
                    continue
                p, q = o.beg, o.end
                r = q - p
                denom = d[0] * r[1] - d[1] * r[0]
                if abs(denom) < 1e-6:
                    continue
                s = ((p[0] - e[0]) * r[1] - (p[1] - e[1]) * r[0]) / denom  # along d from e
                u = ((p[0] - e[0]) * d[1] - (p[1] - e[1]) * d[0]) / denom  # along r from p (0..1)
                if not (-(o.thickness / 2 + 0.08) <= s <= tee_reach + o.thickness / 2):
                    continue
                slack = (w.thickness / 2 + 0.15) / np.linalg.norm(r)
                if not (-slack <= u <= 1 + slack):
                    continue
                if best is None or abs(s) < abs(best[0]):
                    best = (s, e + s * d)
            if best is not None:
                setattr(w, end_name, best[1])
                joined.add((id(w), end_name))

    # collinear pieces of one wall with a short gap between them become one wall
    for i, w in enumerate(walls):
        for o in walls[i + 1:]:
            if o.axis != w.axis or abs(o.thickness - w.thickness) > 0.03 or abs(o.c - w.c) > 0.05:
                continue
            for we, wp in ends(w):
                for oe, op in ends(o):
                    if (id(w), we) in joined or (id(o), oe) in joined:
                        continue
                    if np.linalg.norm(wp - op) <= tee_reach and np.dot(wp - op, _center(w) - _center(o)) > 0:
                        m = (wp + op) / 2
                        setattr(w, we, m)
                        setattr(o, oe, m)
                        joined |= {(id(w), we), (id(o), oe)}
    return joined


def straighten(walls, max_gap=0.3, min_len=0.25):
    """deslab: no jogs. A wall that continues a longer parallel wall end to end, off its line
    by less than the walls' half thickness, goes onto that line (a one-sided wall also takes the
    thickness of a measured one); short connectors left between them are dropped."""
    def length(w):
        return float(np.linalg.norm(w.end - w.beg))

    # walls with a measured back face lead, then the longer ones
    for o in sorted(walls, key=lambda w: (not (w.measured or getattr(w, "backed", False)), -length(w))):
        co, lo, ho = _span(o)
        for w in walls:
            if w is o or w.axis != o.axis or length(w) > length(o):
                continue
            cw, lw, hw = _span(w)
            gap = max(lw - ho, lo - hw)
            off = abs(cw - co)
            if gap > max_gap or gap < -max(w.thickness, o.thickness) or off < 1e-3 or off > max(w.thickness, o.thickness) / 2 + 0.05:
                continue
            if w.measured and o.measured and abs(w.thickness - o.thickness) > 0.03:
                continue
            o_backed = o.measured or getattr(o, "backed", False)
            if o_backed and not w.measured and w.room_side:
                # a deep step on the room side (a shaft box, a pier, 0.3 m or more) stays: the
                # one-sided wall keeps its scanned face and takes the back face of the measured one
                s = w.room_side
                face, o_face, o_back = cw + s * w.thickness / 2, co + s * o.thickness / 2, co - s * o.thickness / 2
                t = (face - o_back) * s
                if abs(face - o_face) >= 0.3 and 0.08 <= t <= 0.9:
                    w.c, w.thickness = (face + o_back) / 2, t
                    w.backed = True
                    w.beg, w.end = _line(w)
                    continue
            mid_w, mid_o = (w.a + w.b) / 2, (o.a + o.b) / 2
            w.c = o.c + o.slope * (mid_w - mid_o)
            w.slope = o.slope
            if o_backed and not w.measured:
                w.thickness = o.thickness
                w.backed = True
            w.beg, w.end = _line(w)
    return [w for w in walls if w.openings or length(w) >= min_len]


def align_backs(walls, reach=0.6, min_t=0.08, max_t=0.9):
    """deslab: one straight back face (Игорь, 03.11.2026: a jog at the entrance door and a step
    on the outer face of the left wall). Two walls on one line, end to end (across a crossing
    wall up to `reach`), keep their scanned room faces, real steps included, but share one back
    face: a one-sided wall takes the back face of a measured wall, else of the longer one, and
    its thickness follows. Returns True when a wall moved, so the joints are redone."""
    def length(w):
        return float(np.linalg.norm(w.end - w.beg))

    def lead(w):
        return (w.measured or getattr(w, "backed", False), length(w))

    moved = False
    for o in sorted(walls, key=lead, reverse=True):
        co, lo, ho = _span(o)
        for w in walls:
            if w is o or w.axis != o.axis or w.measured or not w.room_side or lead(w) > lead(o):
                continue
            if o.room_side and o.room_side != w.room_side:
                continue
            cw, lw, hw = _span(w)
            if max(lw - ho, lo - hw) > reach or abs(cw - co) > max(w.thickness, o.thickness) / 2 + 0.05:
                continue
            if min(hw, ho) - max(lw, lo) > 0.05:  # side by side, not end to end
                continue
            s = w.room_side
            face, back = cw + s * w.thickness / 2, co - s * o.thickness / 2
            t = (face - back) * s
            if not (min_t <= t <= max_t) or abs((cw - s * w.thickness / 2) - back) < 0.005:
                continue
            delta = (face + back) / 2 - cw
            w.thickness = t
            w.c += delta
            w.beg = np.array(w.beg, dtype=float)
            w.end = np.array(w.end, dtype=float)
            w.beg[w.axis] += delta
            w.end[w.axis] += delta
            w.backed = getattr(w, "backed", False) or o.measured or getattr(o, "backed", False)
            moved = True
    return moved


def _joined_ends(walls, tol=0.005):
    """The wall ends that lie on another wall's centre line."""
    joined = set()
    for w in walls:
        for k in ("beg", "end"):
            e = getattr(w, k)
            for o in walls:
                if o is w:
                    continue
                d = o.end - o.beg
                dd = float(np.dot(d, d))
                if dd < 1e-12:
                    continue
                t = np.clip(np.dot(e - o.beg, d) / dd, 0, 1)
                if np.linalg.norm(o.beg + t * d - e) < tol:
                    joined.add((id(w), k))
                    break
    return joined


def _span(w):
    """(position across, low, high along) of the wall's centre line, in model axes."""
    t = 1 - w.axis  # coordinate along the wall
    lo, hi = sorted((float(w.beg[t]), float(w.end[t])))
    return float((w.beg[w.axis] + w.end[w.axis]) / 2), lo, hi


def prune_walls(walls, joined):
    """deslab: walls are closed and clean. Drop, as scan noise:
    - a one-sided wall that overlaps a longer parallel wall (a frame, a reveal, the same wall
      seen twice); its openings move to that wall;
    - a short piece (under 0.6 m, no openings) with a free end, alongside a parallel wall within
      0.6 m (a ledge), or with both ends free."""
    def free_ends(w):
        return sum((id(w), e) not in joined for e in ("beg", "end"))

    drop = set()
    for w in sorted(walls, key=lambda w: np.linalg.norm(w.end - w.beg)):
        if w.measured:
            continue
        cw, lw, hw = _span(w)
        L = hw - lw
        for o in walls:
            if o is w or id(o) in drop or o.axis != w.axis:
                continue
            co, lo, ho = _span(o)
            if not o.measured and np.linalg.norm(o.end - o.beg) < L:
                continue
            overlap = min(hw, ho) - max(lw, lo)
            if abs(cw - co) < (w.thickness + o.thickness) / 2 - 0.02 and overlap >= 0.7 * L:
                o.openings += [op for op in w.openings if lo - 0.05 <= op["a"] and op["b"] <= ho + 0.05]
                drop.add(id(w))
                break
    for w in walls:
        if id(w) in drop or w.openings:
            continue
        cw, lw, hw = _span(w)
        L = hw - lw
        if L >= 0.6 or free_ends(w) == 0:
            continue
        ledge = any(o is not w and id(o) not in drop and o.axis == w.axis
                    and abs(_span(o)[0] - cw) < 0.6 and _span(o)[1] - 0.05 <= lw and hw <= _span(o)[2] + 0.05
                    for o in walls)
        if ledge or free_ends(w) == 2:
            drop.add(id(w))
    return [w for w in walls if id(w) not in drop]


def _center(w):
    return (np.asarray(w.beg) + np.asarray(w.end)) / 2


def _beyond_or_at(w, end_name, x, tol=0.05):
    """True when x lies on the outer side of the wall end end_name (or at it)."""
    e = getattr(w, end_name)
    other = w.end if end_name == "beg" else w.beg
    d = e - other
    L = np.linalg.norm(d)
    return L > 1e-9 and np.dot(x - e, d / L) >= -(w.thickness / 2 + tol)


def _intersect(w, o):
    p, r = w.beg, w.end - w.beg
    q, s = o.beg, o.end - o.beg
    denom = r[0] * s[1] - r[1] * s[0]
    if abs(denom) < 1e-9:
        return None
    t = ((q[0] - p[0]) * s[1] - (q[1] - p[1]) * s[0]) / denom
    return p + t * r


# ---------------------------------------------------------------- rooms

def find_rooms(pts, floor, ceiling, walls, lines, opt: Options):
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    x0, y0 = x.min() - 0.5, y.min() - 0.5
    W = int((x.max() + 0.5 - x0) / RES) + 1
    H = int((y.max() + 0.5 - y0) / RES) + 1
    ix = ((x - x0) / RES).astype(np.int64)
    iy = ((y - y0) / RES).astype(np.int64)
    hits = (z < floor + 0.25) | (z > floor + 2.3)
    scanned = np.bincount(iy[hits] * W + ix[hits], minlength=W * H).reshape(H, W) > 0
    scanned = grid.close(scanned, 4)
    barrier = np.zeros((H, W), bool)
    for w in walls:
        # deslab: an end runs on by half the thickness of the wall it meets (a mitred corner,
        # as Archicad joins them), a free end stops where it is; no square caps sticking out
        ext = [_partner_half(w, e, walls) / RES for e in (w.beg, w.end)]
        grid.fill_box(barrier, (w.beg - (x0, y0)) / RES, (w.end - (x0, y0)) / RES, w.thickness / RES, *ext)
    # an opening left between two free wall ends (a doorway without a lintel) is closed for the
    # rooms and the slab, so a room does not run out through it
    joined = _joined_ends(walls)
    ends = [getattr(w, k) for w in walls for k in ("beg", "end") if (id(w), k) not in joined]
    for i, p in enumerate(ends):
        q = min(ends[i + 1:], key=lambda e: np.linalg.norm(e - p), default=None)
        if q is not None and np.linalg.norm(q - p) <= 1.0:
            grid.fill_segment(barrier, (p - (x0, y0)) / RES, (q - (x0, y0)) / RES, 0.1 / RES)
    labels, n = grid.label(scanned & ~barrier)
    # deslab: a room fills its walls: it grows up to 0.4 m from its scanned floor over unscanned
    # spots (furniture, shadows) without crossing a wall, and holes inside are part of it, so the
    # zone outline follows the walls
    labels = _grow(labels, ~barrier, int(round(0.4 / RES)))
    rooms = []
    masks = []
    for k in range(1, n + 1):
        mk = grid.fill_holes(labels == k) & ~barrier
        area = mk.sum() * RES * RES
        if area < opt.min_room_area:
            continue
        sel = mk.ravel()[iy * W + ix]
        zr = z[sel]
        masks.append((mk, area, _mode(zr[zr < floor + 0.3], floor), _mode(zr[zr > floor + 2.0], ceiling)))
    # deslab: a space on another floor level than the main room (a finished common corridor or
    # landing seen through the entrance door) is outside the flat: it is left out, with the walls
    # that bound only it
    outside = []
    if masks:
        main_floor = max(masks, key=lambda m: m[1])[2]
        outside = [m for m in masks if abs(m[2] - main_floor) >= opt.outside_level]
        masks = [m for m in masks if abs(m[2] - main_floor) < opt.outside_level]
    kept = np.zeros((H, W), bool)
    for m in masks:
        kept |= m[0]
    if outside:
        # a wall stays when a good part of its length borders the flat (a corridor wall that only
        # touches the flat's wall with its end does not)
        keep_walls = []
        for w in walls:
            d = w.end - w.beg
            L = float(np.linalg.norm(d))
            if L < 1e-6:
                continue
            n = np.array([-d[1], d[0]]) / L
            ts = np.arange(0.025, L, 0.05) / L
            reach = w.thickness / 2 + 0.1
            hit = 0
            borders = []
            for t in ts:
                c = w.beg + t * d
                for sgn in (1, -1):
                    q = ((c + sgn * reach * n) - (x0, y0)) / RES
                    i, j = int(q[1]), int(q[0])
                    if 0 <= i < H and 0 <= j < W and kept[i, j]:
                        hit += 1
                        borders.append(t * L)
                        break
            if hit >= max(1, 0.25 * len(ts)):
                keep_walls.append((w, borders))
        walls[:] = [w for w, _ in keep_walls]
        for w, borders in keep_walls:
            _trim_outside(w, borders, walls)
        barrier = np.zeros((H, W), bool)
        for w in walls:
            ext = [_partner_half(w, e, walls) / RES for e in (w.beg, w.end)]
            grid.fill_box(barrier, (w.beg - (x0, y0)) / RES, (w.end - (x0, y0)) / RES, w.thickness / RES, *ext)
    for mk, area, rf, rc in masks:
        cells = np.flatnonzero(mk.ravel())
        contour = grid.trace(mk)
        poly = [(x0 + (cx) * RES, y0 + (cy) * RES) for cx, cy in contour]
        poly = grid.clean(grid.simplify(poly, 0.04))
        snapped = grid.clean(_snap_polygon(_square(poly), walls))
        # deslab: Archicad refuses self-intersecting slab and zone outlines
        poly = snapped if grid.is_simple(snapped) else poly
        rooms.append({"polygon": poly, "floor": rf, "ceiling": rc, "area": area, "cells": len(cells),
                      "simple": grid.is_simple(poly)})
    rooms.sort(key=lambda r: -r["area"])
    left_out = [{"area": float(m[1]), "floor": float(m[2])} for m in outside]
    return rooms, _outline(grid.close(kept, 3) | barrier, x0, y0, walls), left_out


def _grow(labels, allowed, steps):
    """Grow the labelled regions by one cell per step into free allowed cells."""
    labels = labels.copy()
    for _ in range(steps):
        grown = labels.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            shifted = np.roll(np.roll(labels, dy, 0), dx, 1)
            take = (grown == 0) & (shifted > 0) & allowed
            grown[take] = shifted[take]
        labels = grown
    return labels


def _junctions(w, walls):
    """Positions along beg -> end of w where another wall meets or crosses it."""
    d = w.end - w.beg
    L = float(np.linalg.norm(d))
    u = d / L
    n = np.array([-u[1], u[0]])
    out = []
    for o in walls:
        if o is w:
            continue
        od = o.end - o.beg
        OL = float(np.linalg.norm(od))
        if OL < 1e-6:
            continue
        ou = od / OL
        den = float(np.dot(ou, n))
        if abs(den) < 0.5:
            continue
        # where the centre line of o crosses the centre line of w
        k = -float(np.dot(o.beg - w.beg, n)) / den
        if -(w.thickness / 2 + 0.05) <= k <= OL + w.thickness / 2 + 0.05:
            s = float(np.dot(o.beg + k * ou - w.beg, u))
            if -0.05 <= s <= L + 0.05:
                out.append(s)
    return sorted(out)


def _trim_outside(w, borders, walls, tail=0.3):
    """deslab: cut the end of a wall that runs on past the flat into a space that was left out
    (a common corridor) back to the last wall it meets, so no free tail is left."""
    d = w.end - w.beg
    L = float(np.linalg.norm(d))
    if not borders or L < 1e-6:
        return
    u = d / L
    js = _junctions(w, walls)
    lo, hi = 0.0, L
    s_lo, s_hi = min(borders), max(borders)
    if s_lo > tail:
        cut = [j for j in js if j < s_lo + 0.1]
        # only when nothing meets the tail beyond the cut
        if cut and max(cut) > tail and len(cut) == 1:
            lo = max(cut)
    if L - s_hi > tail:
        cut = [j for j in js if j > s_hi - 0.1]
        if cut and L - min(cut) > tail and len(cut) == 1:
            hi = min(cut)
    if lo == 0.0 and hi == L:
        return
    beg, end = w.beg + lo * u, w.beg + hi * u
    k = 1 - w.axis
    ta, tb = float(beg[k]), float(end[k])
    keep = []
    for o in w.openings:
        m = (o["a"] + o["b"]) / 2
        if min(ta, tb) < m < max(ta, tb):
            keep.append(o)
    w.openings = keep
    w.beg, w.end = beg, end
    mid = (ta + tb) / 2
    w.c = w.c + w.slope * (mid - (w.a + w.b) / 2)
    w.a, w.b = min(ta, tb), max(ta, tb)


def _partner_half(w, e, walls, tol=0.005):
    """Half the thickness of the crossing wall whose centre line the end e lies on, else 0."""
    best = 0.0
    for o in walls:
        if o is w or o.axis == w.axis:
            continue
        d = o.end - o.beg
        dd = float(np.dot(d, d))
        if dd < 1e-12:
            continue
        t = np.clip(np.dot(e - o.beg, d) / dd, 0, 1)
        if np.linalg.norm(o.beg + t * d - e) < tol:
            best = max(best, o.thickness / 2)
    return best


def _outline(mask, x0, y0, walls):
    """deslab: outer outline of the rooms and their walls (the floor slab), snapped to the outer
    wall faces. None when there are no rooms."""
    if not mask.any():
        return None
    labels, n = grid.label(mask)
    if n == 0:
        return None
    sizes = np.bincount(labels.ravel())[1:]
    blob = labels == (int(np.argmax(sizes)) + 1)
    # fill the holes: background not connected to the border
    back, nb = grid.label(~blob)
    border = set(np.unique(np.concatenate([back[0], back[-1], back[:, 0], back[:, -1]]))) - {0}
    blob |= (back > 0) & ~np.isin(back, list(border))
    poly = [(x0 + cx * RES, y0 + cy * RES) for cx, cy in grid.trace(blob)]
    poly = grid.clean(grid.simplify(poly, 0.04))
    snapped = grid.clean(_snap_polygon(_square(poly), walls))
    return snapped if grid.is_simple(snapped) else poly


def _square(poly, tol=0.01):
    """Turn short slanted edges (corners cut by the simplification) into an axis step that
    turns off the previous edge."""
    out = []
    n = len(poly)
    for i in range(n):
        p, q = np.array(poly[i]), np.array(poly[(i + 1) % n])
        out.append(tuple(p))
        dx, dy = abs(q[0] - p[0]), abs(q[1] - p[1])
        if dx <= tol or dy <= tol or max(dx, dy) > 0.5:
            continue
        prev = p - np.array(poly[i - 1])
        horizontal = abs(prev[0]) >= abs(prev[1])
        out.append((float(p[0]), float(q[1])) if horizontal else (float(q[0]), float(p[1])))
    return grid.clean(out)


def _even_door_heights(scene, lo=1.95, hi=2.45, default=2.1):
    """deslab: interior doors get one height (the median of the plausible measured ones).
    A door next to a window (balcony block) keeps the window head."""
    doors = [o for w in scene["walls"] if not any(x["kind"] == "window" for x in w["openings"])
             for o in w["openings"] if o["kind"] == "door"]
    if not doors:
        return
    measured = [o["sill"] + o["height"] for o in doors if lo <= o["sill"] + o["height"] <= hi]
    head = round(float(np.median(measured)), 2) if measured else default
    changed = []
    for o in doors:
        if abs(o["sill"] + o["height"] - head) > 0.005:
            changed.append(f"{o['id']} {int(round((o['sill'] + o['height']) * 1000))}")
        o["height"] = round(head - o["sill"], 3)
    if changed:
        scene["assumptions"].append(
            f"doors: one height {int(round(head * 1000))} mm (measured {', '.join(changed)})")


def _mode(v, default):
    if len(v) < 50:
        return default
    h, e = np.histogram(v, bins=np.arange(v.min(), v.max() + 0.01, 0.005))
    i = int(np.argmax(h))
    return float((e[i] + e[i + 1]) / 2)


def _snap_polygon(poly, walls):
    """Put every polygon edge on the wall face it runs along, then rebuild the corners."""
    faces = []
    for w in walls:
        d = w.end - w.beg
        L = np.linalg.norm(d)
        if L < 1e-6:
            continue
        d = d / L
        n = np.array([-d[1], d[0]])
        for s in (1, -1):
            faces.append((w.beg + s * n * w.thickness / 2, d, L, w.thickness))
    n = len(poly)
    lines = []
    for i in range(n):
        p, q = np.array(poly[i]), np.array(poly[(i + 1) % n])
        e = q - p
        L = np.linalg.norm(e)
        if L < 1e-9:
            lines.append((p, np.array([1.0, 0.0])))
            continue
        e = e / L
        mid = (p + q) / 2
        best = None
        for o, d, fl, th in faces:
            if abs(abs(np.dot(e, d)) - 1) > 0.002:
                continue
            nrm = np.array([-d[1], d[0]])
            dist = abs(np.dot(mid - o, nrm))
            along = np.dot(mid - o, d)
            if dist < 0.05 and -0.1 - th / 2 <= along <= fl + 0.1 + th / 2 and (best is None or dist < best[0]):
                best = (dist, o, d)
        lines.append((best[1], best[2]) if best else (p, e))
    out = []
    for i in range(n):
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]
        denom = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(denom) < 0.05:
            out.append(tuple(poly[i]))
            continue
        t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / denom
        v = p1 + t * d1
        if np.linalg.norm(v - np.array(poly[i])) > 0.15:
            out.append(tuple(poly[i]))
        else:
            out.append((float(v[0]), float(v[1])))
    return grid.simplify(out, 0.005)


# ---------------------------------------------------------------- scene

def analyze(points: np.ndarray, opt: Options, source: str = "") -> dict:
    pts = points.astype(np.float64)
    floor, ceiling = find_levels(pts[:, 2])
    band = (pts[:, 2] > floor + 0.5) & (pts[:, 2] < ceiling - 0.3)
    theta = find_orientation(pts[band, :2])
    c, s = math.cos(theta), math.sin(theta)
    rot = pts.copy()
    rot[:, 0] = pts[:, 0] * c - pts[:, 1] * s
    rot[:, 1] = pts[:, 0] * s + pts[:, 1] * c

    faces, _, _ = find_faces(rot, floor, ceiling, opt)
    lines = merge_lines(faces)
    extend_to_corners(lines, rot, floor, ceiling)
    classify_gaps(lines, rot, floor, ceiling)
    lines = split_lines(lines)
    walls = build_walls(lines, opt)
    walls = drop_plates(walls, rot, floor)
    attach_openings(walls, lines)
    for w in walls:
        w.beg, w.end = _line(w)
    walls = drop_inside(walls)
    walls = straighten(walls)
    joined = join_walls(walls)
    walls = prune_walls(walls, joined)
    # ends that were joined to a dropped piece run on to the next wall
    join_walls(walls, joined=_joined_ends(walls))
    walls = merge_overlaps(walls)
    walls = merge_walls(walls)
    if align_backs(walls):
        # ends that sat on a moved wall's centre line follow it
        join_walls(walls, joined=_joined_ends(walls))
    rooms, outline, left_out = find_rooms(rot, floor, ceiling, walls, lines, opt)

    # model origin: main floor at z = 0, lower-left corner of the walls at (0, 0)
    allpts = np.array([p for w in walls for p in (w.beg, w.end)])
    ox = float(np.floor((allpts[:, 0].min() - 1.0) * 10) / 10)
    oy = float(np.floor((allpts[:, 1].min() - 1.0) * 10) / 10)
    main_floor = rooms[0]["floor"] if rooms else floor

    def xy(p):
        return {"x": round(float(p[0]) - ox, 4), "y": round(float(p[1]) - oy, 4)}

    scene = {
        "format": "deslab.scan2bim/1",
        "source": {"file": source, "points": int(len(points)), "step": opt.step},
        "transform": {
            "note": "model = rotate(scan, rotationDeg) - offset",
            "rotationDeg": round(math.degrees(theta), 4),
            "offset": [ox, oy, round(main_floor, 4)],
        },
        "levels": {"floor": 0.0, "ceiling": round(ceiling - main_floor, 3)},
        "walls": [],
        "rooms": [],
        "slabs": [],
        "assumptions": [],
        "warnings": [],
    }
    height = round(ceiling - main_floor, 3)
    i = 0
    for w in walls:
        beg, end = w.beg, w.end
        # one-sided walls: the scanned room on the left of beg -> end (openings open inwards)
        if w.room_side:
            d = end - beg
            left = np.array([-d[1], d[0]])
            n = np.zeros(2)
            n[w.axis] = w.room_side
            if np.dot(left, n) < 0:
                beg, end = end, beg
        d = end - beg
        L = float(np.linalg.norm(d))
        if L < 0.05:
            continue
        i += 1
        u = d / L
        item = {
            "id": f"W{i}",
            "start": xy(beg), "end": xy(end),
            "thickness": round(float(w.thickness), 3),
            "height": height,
            "bottom": 0.0,
            "measuredThickness": w.measured,
            "kind": "interior" if w.measured else ("exterior" if w.thickness >= opt.exterior_thickness - 1e-6 else "unknown"),
            "openings": [],
        }
        for j, o in enumerate(sorted(w.openings, key=lambda o: o["a"]), 1):
            pa = np.array(w.point(o["a"]))
            pb = np.array(w.point(o["b"]))
            ca, cb = float(np.dot(pa - beg, u)), float(np.dot(pb - beg, u))
            lo, hi = min(ca, cb), max(ca, cb)
            item["openings"].append({
                "id": f"W{i}.{j}",
                "kind": o["kind"],
                "centerOffset": round((lo + hi) / 2, 4),
                "width": round(hi - lo, 3),
                "sill": o["sill"],
                "height": round(o["head"] - o["sill"], 3),
            })
        if any(o["kind"] in ("window", "balconyDoor") for o in item["openings"]):
            # deslab: a door in a wall with windows is a balcony door (built as a window)
            for o in item["openings"]:
                if o["kind"] == "door":
                    o["kind"] = "balconyDoor"
        scene["walls"].append(item)
        if not w.measured:
            scene["assumptions"].append(
                f"W{i}: only one side scanned, thickness {int(round(w.thickness * 1000))} mm by default")
    for r in left_out:
        scene["assumptions"].append(
            f"a space of {r['area']:.1f} m2 with its floor {int(round((r['floor'] - main_floor) * 1000))} mm off the "
            "main floor is outside the flat (common corridor) and left out")
    _even_door_heights(scene)
    if outline is not None:
        # deslab: one floor slab under the rooms and the walls, its top at the main floor
        scene["slabs"].append({"id": "S1", "polygon": [xy(p) for p in outline], "floor": 0.0})
        other = [k for k, r in enumerate(rooms, 1) if abs(r["floor"] - main_floor) > 0.02]
        if other:
            scene["assumptions"].append(
                "S1: one slab at the main floor; rooms " + ", ".join(f"R{k}" for k in other) + " have their own floor level")
    for k, r in enumerate(rooms, 1):
        scene["rooms"].append({
            "id": f"R{k}",
            "number": k,
            "name": f"Помещение {k}",
            "polygon": [xy(p) for p in r["polygon"]],
            "floor": round(r["floor"] - main_floor, 3),
            "ceiling": round(r["ceiling"] - main_floor, 3),
            "height": round(r["ceiling"] - r["floor"], 3),
            "area": round(float(r["area"]), 2),
        })
        if not r["simple"]:
            scene["warnings"].append(f"R{k}: outline crosses itself, slab and zone may fail")
    return scene
