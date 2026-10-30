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

@dataclass
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
        cuts = [g for g in f.gaps if not any(o["a"] >= g[0] - 0.1 and o["b"] <= g[1] + 0.1 for o in f.openings)]
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
            w.faces = [f]
            walls.append(w)
            f.used.append((a, b))
    return walls


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
        tt = y if f.axis == 0 else x
        cc = x if f.axis == 0 else y
        for g0, g1 in f.gaps:
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
            for kind, i0, i1 in _segments(kinds, min_bins=5):
                a, b = edges[i0], edges[i1]
                if i0 == 0:
                    a = g0
                if i1 == len(kinds):
                    b = g1
                sill = float(np.median(sills[i0:i1])) if kind == "window" else 0.0
                o = {"kind": kind, "a": float(a), "b": float(b), "sill": round(sill, 3), "head": round(head, 3)}
                f.openings.append(o)
                if kind == "window":
                    f.window_gaps.append((g0, g1))


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
                if o["a"] >= w.a - 0.01 and o["b"] <= w.b + 0.01 and not any(
                        abs(p["a"] - o["a"]) < 0.1 and abs(p["b"] - o["b"]) < 0.1 for p in w.openings):
                    w.openings.append(dict(o))


# ---------------------------------------------------------------- junctions

def _line(w):
    p = np.array(w.point(w.a))
    q = np.array(w.point(w.b))
    return p, q


def join_walls(walls):
    """Move the wall ends onto the centre lines of the walls they run into (L and T joints)."""
    for w in walls:
        p, q = _line(w)
        w.beg, w.end = p, q
    for w in walls:
        for end_name in ("beg", "end"):
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
                lr = np.linalg.norm(r)
                reach = o.thickness / 2 + 0.08
                if not (-reach <= s <= reach + w.thickness * 0.0 + 0.05):
                    continue
                slack = (w.thickness / 2 + 0.15) / lr
                if not (-slack <= u <= 1 + slack):
                    continue
                if best is None or abs(s) < abs(best[0]):
                    best = (s, e + s * d)
            if best is not None:
                setattr(w, end_name, best[1])
    # a wall end that now lies beyond the other wall's end pulls that end along (L corners)
    for w in walls:
        for end_name in ("beg", "end"):
            e = getattr(w, end_name)
            for o in walls:
                if o is w or o.axis == w.axis:
                    continue
                for oe in ("beg", "end"):
                    pe = getattr(o, oe)
                    if np.linalg.norm(pe - e) < max(w.thickness, o.thickness) / 2 + 0.1:
                        # snap both to the intersection of the centre lines
                        x = _intersect(w, o)
                        if x is not None:
                            setattr(w, end_name, x)
                            setattr(o, oe, x)


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
        grid.fill_segment(barrier, (w.beg - (x0, y0)) / RES, (w.end - (x0, y0)) / RES, w.thickness / RES)
    free = scanned & ~barrier
    labels, n = grid.label(free)
    rooms = []
    for k in range(1, n + 1):
        mk = labels == k
        area = mk.sum() * RES * RES
        if area < opt.min_room_area:
            continue
        cells = np.flatnonzero(mk.ravel())
        sel = mk.ravel()[iy * W + ix]
        zr = z[sel]
        rf = _mode(zr[zr < floor + 0.3], floor)
        rc = _mode(zr[zr > floor + 2.0], ceiling)
        contour = grid.trace(mk)
        poly = [(x0 + (cx) * RES, y0 + (cy) * RES) for cx, cy in contour]
        poly = grid.simplify(poly, 0.04)
        poly = _snap_polygon(poly, walls)
        rooms.append({"polygon": poly, "floor": rf, "ceiling": rc, "area": area, "cells": len(cells)})
    rooms.sort(key=lambda r: -r["area"])
    return rooms


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
            faces.append((w.beg + s * n * w.thickness / 2, d, L))
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
        for o, d, fl in faces:
            if abs(abs(np.dot(e, d)) - 1) > 0.002:
                continue
            nrm = np.array([-d[1], d[0]])
            dist = abs(np.dot(mid - o, nrm))
            along = np.dot(mid - o, d)
            if dist < 0.05 and -0.1 <= along <= fl + 0.1 and (best is None or dist < best[0]):
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
    classify_gaps(lines, rot, floor, ceiling)
    lines = split_lines(lines)
    walls = build_walls(lines, opt)
    attach_openings(walls, lines)
    join_walls(walls)
    walls = merge_walls(drop_inside(walls))
    rooms = find_rooms(rot, floor, ceiling, walls, lines, opt)

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
        scene["walls"].append(item)
        if not w.measured:
            scene["assumptions"].append(
                f"W{i}: only one side scanned, thickness {int(round(w.thickness * 1000))} mm by default")
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
    return scene
