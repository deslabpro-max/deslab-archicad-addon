"""deslab: pictures of the cloud for the model that reviews the scene, and the threshold of a balcony door.

Игорь 13.11.2026: «у окна, дверь на балкон, нет порога. На облаке точек он есть»; «инженерные узлы пустым. Хотя у нас
там есть радиаторы, стойки отопления, шахта вентиляции, канализационный стояк, холодная-горячая вода». The reviewing
model saw only the plan of the scene, where none of that is. Here the cloud itself is drawn the way a surveyor looks at
it: every wall face as an elevation (what stands in front of the wall is coloured by how far it stands out) and the plan
cut in three height bands. The axes are in the scene's metres, so the model can answer with coordinates.

Needs numpy; the pictures need Pillow."""

from __future__ import annotations

import json
import math
import os

import numpy as np

from .grid import label

ELEVATION_SCALE = 120      # px per metre
REACH = 0.45               # how far in front of the wall face things are still drawn, m
PLAN_BANDS = (("low", 0.03, 0.25, "floor band 0.03-0.25 m: risers, pipes at the floor, thresholds, tracing"),
              ("mid", 0.30, 0.90, "band 0.30-0.90 m: radiators, pipes, boxes"),
              ("high", -0.35, -0.03, "band under the ceiling: ducts, boxes, vent shafts"))


def model_points(points, scene):
    """The read points in the scene's coordinates: model = rotate(scan, rotationDeg) - offset."""
    t = scene["transform"]
    th = math.radians(float(t["rotationDeg"]))
    c, s = math.cos(th), math.sin(th)
    ox, oy, oz = (float(v) for v in t["offset"])
    p = np.asarray(points, dtype=np.float64)
    out = np.empty((len(p), 3))
    out[:, 0] = p[:, 0] * c - p[:, 1] * s - ox
    out[:, 1] = p[:, 0] * s + p[:, 1] * c - oy
    out[:, 2] = p[:, 2] - oz
    return out


def _frame(wall):
    a = np.array([wall["start"]["x"], wall["start"]["y"]], dtype=float)
    b = np.array([wall["end"]["x"], wall["end"]["y"]], dtype=float)
    length = float(np.linalg.norm(b - a))
    u = (b - a) / length if length > 0 else np.array([1.0, 0.0])
    n = np.array([-u[1], u[0]])   # the left of start -> end
    return a, u, n, length


def _wall_coords(pts, wall):
    a, u, n, length = _frame(wall)
    rel = pts[:, :2] - a
    return rel @ u, rel @ n, length


def measure_thresholds(pts, scene, max_height=0.6, step=0.02, column=0.05, cover=0.5):
    """deslab: the threshold of a balcony door, from the cloud. Inside the opening and inside the wall's thickness, a
    solid strip that starts at the floor and runs across the opening (the wall under the door block, the frame's bottom
    rail) is the threshold; its top is the door's sill, the head stays where it was. Returns the notes."""
    notes = []
    for wall in scene.get("walls", []):
        doors = [o for o in wall.get("openings", []) if o.get("kind") == "balconyDoor"]
        if not doors:
            continue
        t, d, _ = _wall_coords(pts, wall)
        half = float(wall["thickness"]) / 2
        for o in doors:
            lo = float(o["centerOffset"]) - float(o["width"]) / 2 + 0.08
            hi = float(o["centerOffset"]) + float(o["width"]) / 2 - 0.08
            if hi - lo < 0.2:
                continue
            # deslab: Nagornaya run 1, the threshold (about 0.2 m on the elevation) was missed: the outer wall has a default
            # thickness and its real face stands 16 cm in front of the modelled one, outside the old 2 cm margin
            m = (t > lo) & (t < hi) & (np.abs(d) < half + 0.25) & (pts[:, 2] > 0.02) & (pts[:, 2] < max_height)
            if not m.any():
                continue
            cols = np.floor((t[m] - lo) / column).astype(int)
            rows = np.floor((pts[m, 2] - 0.02) / step).astype(int)
            n_cols = int(math.ceil((hi - lo) / column))
            n_rows = int(math.ceil((max_height - 0.02) / step))
            grid = np.zeros((n_rows, n_cols), dtype=bool)
            ok = (cols >= 0) & (cols < n_cols) & (rows >= 0) & (rows < n_rows)
            grid[rows[ok], cols[ok]] = True
            # a column is solid up to the top of its highest hit; the threshold is where half the columns still are
            tops = np.where(grid.any(axis=0), n_rows - np.argmax(grid[::-1], axis=0), 0)
            level, top = 0, 0
            for level in range(1, n_rows + 1):
                if np.mean(tops >= level) < cover:
                    break
                top = level
            height = round(0.02 + top * step, 3) if top else 0.0
            if height > max_height - step:
                # deslab: Nagornaya run 2, the door stood shut: its leaf fills the opening with points to the top, so every
                # column was «solid». The leaf is set back in the wall and the threshold stands in front of it: the rows
                # where the cloud stands at least 5 cm off the leaf's plane, counted from the floor, are the threshold
                ref = (t > lo) & (t < hi) & (np.abs(d) < half + 0.25) & (pts[:, 2] > 0.9) & (pts[:, 2] < 1.8)
                if ref.sum() >= 50:
                    plane = float(np.median(d[ref]))
                    off = np.abs(d[m] - plane) > 0.05
                    grid2 = np.zeros((n_rows, n_cols), dtype=bool)
                    ok2 = ok & off
                    grid2[rows[ok2], cols[ok2]] = True
                    top = 0
                    for level in range(n_rows):
                        if grid2[level].mean() < cover:
                            break
                        top = level + 1
                    height = round(0.02 + top * step, 3) if top else 0.0
            if height < 0.04 or height > max_height - step:
                continue
            head = float(o.get("sill", 0)) + float(o["height"])
            o["sill"] = height
            o["height"] = round(head - height, 3)
            o["threshold"] = height
            notes.append(f"{o['id']}: balcony door threshold {int(round(height * 1000))} mm from the cloud")
    return notes


def _inside(polygon, p):
    x, y, inside = float(p[0]), float(p[1]), False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if (a["y"] > y) != (b["y"] > y) and x < (b["x"] - a["x"]) * (y - a["y"]) / (b["y"] - a["y"]) + a["x"]:
            inside = not inside
    return inside


def _without_thin_runs(out, rows=4, cols=8):
    """deslab: Nagornaya run 2: a pipe along the wall tied the riser in the corner to the radiator under the window into
    one ragged thing, which was dropped, and the edge of the ceiling gave fifty strips. Cells of a horizontal line
    thinner than 8 cm (a pipe, the ceiling's edge, a skirting) are taken out before the things are told apart."""
    h, w = out.shape
    up = np.zeros((h, w), dtype=np.int32)
    for y in range(h):
        up[y] = np.where(out[y], (up[y - 1] if y else 0) + 1, 0)
    run = up.copy()
    for y in range(h - 2, -1, -1):
        run[y] = np.where(out[y] & out[y + 1], run[y + 1], run[y])
    left = np.zeros((h, w), dtype=np.int32)
    for x in range(w):
        left[:, x] = np.where(out[:, x], (left[:, x - 1] if x else 0) + 1, 0)
    wide = left.copy()
    for x in range(w - 2, -1, -1):
        wide[:, x] = np.where(out[:, x] & out[:, x + 1], wide[:, x + 1], wide[:, x])
    return out & ~((run < rows) & (wide >= cols))


def mark_empty_doorways(pts, scene, cell=0.05, limit=0.15):
    """deslab: Игорь 13.11.2026, «ты рисуешь двери, а в проекте дверей нет. Там проемы». A doorway the scanner looks
    through (no points in the wall's plane inside the opening: no leaf, or the leaf stood open) is kind "opening"; a
    closed leaf fills the opening with points and stays a door. Returns the notes."""
    notes = []
    for wall in scene.get("walls", []):
        doors = [o for o in wall.get("openings", []) if o.get("kind") == "door"]
        if not doors:
            continue
        t, d, _ = _wall_coords(pts, wall)
        half = float(wall["thickness"]) / 2
        for o in doors:
            lo = float(o["centerOffset"]) - float(o["width"]) / 2 + 0.1
            hi = float(o["centerOffset"]) + float(o["width"]) / 2 - 0.1
            z0, z1 = float(o.get("sill", 0)) + 0.3, float(o.get("sill", 0)) + float(o["height"]) - 0.2
            if hi - lo < 0.2 or z1 - z0 < 0.5:
                continue
            m = (t > lo) & (t < hi) & (np.abs(d) < half + 0.05) & (pts[:, 2] > z0) & (pts[:, 2] < z1)
            n_cols, n_rows = int(math.ceil((hi - lo) / cell)), int(math.ceil((z1 - z0) / cell))
            grid = np.zeros((n_rows, n_cols), dtype=bool)
            grid[np.clip(((pts[m, 2] - z0) / cell).astype(int), 0, n_rows - 1), np.clip(((t[m] - lo) / cell).astype(int), 0, n_cols - 1)] = True
            share = float(grid.mean())
            o["leafShare"] = round(share, 2)
            # the entrance door (no room behind the wall) stays a door even when it stood open during the scan
            a, u, n, _ = _frame(wall)
            c = a + u * float(o["centerOffset"])
            between = all(any(_inside(r.get("polygon") or [], c + n * k * (half + 0.3)) for r in scene.get("rooms", [])) for k in (1, -1))
            if share < limit and between:
                o["kind"] = "opening"
                notes.append(f"{o['id']}: a doorway without a leaf (the cloud looks through it)")
    return notes


def _without_other_walls(pts, scene, wall):
    """Points that are not the body of another wall (a wall that meets this one would stand out of its face)."""
    keep = np.ones(len(pts), dtype=bool)
    for other in scene.get("walls", []):
        if other is wall:
            continue
        t, d, length = _wall_coords(pts, other)
        keep &= ~((np.abs(d) < float(other["thickness"]) / 2 + 0.02) & (t > -0.05) & (t < length + 0.05))
    return keep


def _hint(width, bottom, top, depth, height):
    tall = top - bottom
    if width <= 0.3 and tall >= 0.7 * height:
        return "riser?"
    if width <= 0.3 and tall >= 0.5:
        return "pipe?"
    if 0.3 <= width <= 3.0 and 0.2 <= tall <= 1.0 and top <= 1.2 and depth <= 0.25:
        return "radiator?"
    if tall >= 0.7 * height and width > 0.3:
        return "box or shaft?"
    return ""


def _components(front, out, tol=0.05):
    """Cells of `out` joined into things: neighbours (one empty cell may lie between) belong together when they stand
    about as far out of the wall. A radiator and the curtain over it, a pipe and the box behind it stay apart."""
    h, w = out.shape
    parent = np.arange(h * w)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    index = np.arange(h * w).reshape(h, w)
    for dy, dx in ((0, 1), (1, 0), (0, 2), (2, 0), (1, 1), (1, -1)):
        ya, yb = slice(0, h - dy), slice(dy, h)
        if dx >= 0:
            xa, xb = slice(0, w - dx), slice(dx, w)
        else:
            xa, xb = slice(-dx, w), slice(0, w + dx)
        ok = out[ya, xa] & out[yb, xb] & (np.abs(front[ya, xa] - front[yb, xb]) < tol)
        for i, j in zip(index[ya, xa][ok].tolist(), index[yb, xb][ok].tolist()):
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri
    cells = np.nonzero(out.ravel())[0]
    roots = np.array([find(i) for i in cells.tolist()], dtype=np.int64)
    order = np.argsort(roots, kind="stable")
    cells, roots = cells[order], roots[order]
    cuts = np.nonzero(np.diff(roots))[0] + 1
    return [(part // w, part % w) for part in np.split(cells, cuts)] if len(cells) else []


def _candidate(wall, side, a, u, n, half, t0, t1, z0, z1, stand, fill, height):
    mid = (t0 + t1) / 2
    at = a + u * mid + n * side * (half + stand / 2)
    p0, p1 = a + u * t0 + n * side * half, a + u * t1 + n * side * half
    return {"wallId": wall["id"], "side": "left" if side > 0 else "right",
            "from": round(t0, 3), "to": round(t1, 3), "bottom": round(z0, 3), "top": round(z1, 3),
            "depth": round(stand, 3), "fill": round(fill, 2),
            "at": {"x": round(float(at[0]), 4), "y": round(float(at[1]), 4)},
            "start": {"x": round(float(p0[0]), 4), "y": round(float(p0[1]), 4)},
            "end": {"x": round(float(p1[0]), 4), "y": round(float(p1[1]), 4)},
            "hint": _hint(t1 - t0, z0, z1, stand, height)}


def _join_pipe_pieces(found, height):
    """deslab: a thin riser is scanned in pieces (Nagornaya C4, C5, C6: three bits of one pipe at 0.2-0.44 m of W2). Narrow
    pieces of one face that stand over each other at one depth are one pipe."""
    found = list(found)
    joined = True
    while joined:
        joined = False
        for i, c in enumerate(found):
            for j in range(i + 1, len(found)):
                o = found[j]
                if c["wallId"] != o["wallId"] or c["side"] != o["side"] or max(c["to"] - c["from"], o["to"] - o["from"]) > 0.3:
                    continue
                if min(c["to"], o["to"]) - max(c["from"], o["from"]) < 0.02 or abs(c["depth"] - o["depth"]) > 0.08:
                    continue
                if max(c["bottom"], o["bottom"]) - min(c["top"], o["top"]) > 1.0:
                    continue
                t0, t1 = min(c["from"], o["from"]), max(c["to"], o["to"])
                z0, z1 = min(c["bottom"], o["bottom"]), max(c["top"], o["top"])
                k = (t0 + t1) / 2 - (c["from"] + c["to"]) / 2
                ux = (c["end"]["x"] - c["start"]["x"]) / max(c["to"] - c["from"], 1e-9)
                uy = (c["end"]["y"] - c["start"]["y"]) / max(c["to"] - c["from"], 1e-9)
                c.update({"from": t0, "to": t1, "bottom": z0, "top": z1, "depth": round(max(c["depth"], o["depth"]), 3),
                          "at": {"x": round(c["at"]["x"] + ux * k, 4), "y": round(c["at"]["y"] + uy * k, 4)},
                          "start": {"x": round(c["start"]["x"] + ux * (t0 - c["from"]), 4), "y": round(c["start"]["y"] + uy * (t0 - c["from"]), 4)},
                          "end": {"x": round(c["end"]["x"] + ux * (t1 - c["to"]), 4), "y": round(c["end"]["y"] + uy * (t1 - c["to"]), 4)},
                          "pieces": c.get("pieces", 1) + o.get("pieces", 1)})
                c["hint"] = "riser?" if z1 - z0 >= 0.5 * height else _hint(t1 - t0, z0, z1, c["depth"], height)
                del found[j]
                joined = True
                break
            if joined:
                break
    return found


def _split_at_seams(tm, zm, dm, t0, t1, z0, z1, height, step=0.01, min_part=0.1, max_groove=0.06, dip=0.03, jump=0.03,
                    window=7, sparse=0.4, spread=0.05, max_trim=0.25, seam_part=0.08, seam_depth=0.02):
    """deslab: 13.11.2026, Nagornaya bathroom corner: two rectangular vent ducts stand side by side from floor to ceiling
    (Игорь: «две квадратных трубы, одна широкая, другая узкая») and came out as one box 0.9 m long. A tall thing is
    looked at in columns 1 cm wide along the wall (`tm`, `zm`, `dm`: the points' place along the wall, height and depth;
    the frontmost point of a 2 cm cell hides a seam). First its ends lose up to 25 cm of columns that hold few points
    (a thin pipe in the gap to the next wall), that do not stand out of the wall, or whose points lie clearly deeper and
    spread in depth (a side wall seen at a slant). Then it is cut:
    - in the middle of a groove: a few columns (at most 6 cm) whose points lie clearly deeper than the face on both
      sides of them, the scanner looks between the two things;
    - at a step: the middle depth jumps by 3 cm or more within 2 columns between two flat stretches (Nagornaya: 26 cm
      for the narrow duct, 30 cm for the wide one; a face that slopes 5 cm over 45 cm is no step).
    Every part keeps at least 10 cm and is trimmed at its ends the same way. Returns the ranges along the wall, each with
    its faint seam (place along the wall, how deep or high in m) or None: see _weak_seam."""
    whole = [(t0, t1, None)]
    if z1 - z0 < 0.7 * height or t1 - t0 < 2 * min_part + step:
        return whole
    sel = (tm >= t0) & (tm < t1) & (zm >= z0) & (zm < z1)
    n = int(round((t1 - t0) / step))
    cols = np.clip(((tm[sel] - t0) / step).astype(int), 0, n - 1)
    order = np.argsort(cols, kind="stable")
    counts = np.bincount(cols, minlength=n)
    groups = np.split(dm[sel][order], np.cumsum(counts)[:-1])
    # deslab: the 80th percentile, not the median: behind a metal duct the scanner sees the wall at many heights
    # (Nagornaya y 8.20: the duct face at 1.0-1.5 and 3.0-3.3 m only), the front of the column is its face
    level = np.array([np.percentile(v, 80) if len(v) >= 10 else np.nan for v in groups])
    width = np.array([np.subtract(*np.percentile(v, [75, 25])) if len(v) >= 10 else np.nan for v in groups])
    if np.isnan(level).all():
        return whole
    part = int(round(min_part / step))

    def med(i, j):
        v = level[max(i, 0):min(j, n)]
        v = v[~np.isnan(v)]
        return float(np.median(v)) if len(v) >= 3 else None

    def trim(lo, hi):
        lo0, hi0, flat = lo, hi, med(lo, hi)
        c = counts[lo:hi][counts[lo:hi] >= 10]
        many = float(np.median(c)) if len(c) else 0.0

        def thin(i):
            return counts[i] < sparse * many or np.isnan(level[i]) or level[i] < 0.035 or \
                (flat is not None and level[i] < flat - dip and width[i] > spread)
        while hi - lo > part and (lo - lo0) * step < max_trim and thin(lo):
            lo += 1
        while hi - lo > part and (hi0 - hi) * step < max_trim and thin(hi - 1):
            hi -= 1
        return lo, hi

    lo, hi = trim(0, n)
    face = med(lo, hi)
    cuts = []
    if face is not None and hi - lo >= 2 * part:
        low = np.nan_to_num(level, nan=face) < face - dip
        k, last = lo, lo
        while k < hi:
            if not low[k]:
                k += 1
                continue
            j = k
            while j < hi and low[j]:
                j += 1
            left, right = med(max(k - window, last), k), med(j, min(j + window, hi))
            if (j - k) * step <= max_groove and k - last >= part and hi - j >= part and left is not None and \
                    right is not None and float(np.nanmedian(level[k:j])) < min(left, right) - dip:
                cuts.append((k + j) / 2)
                last = j
            k = j
        steps = []
        half = window // 2
        for b in range(lo + part, hi - part + 1):
            left, right = med(max(b - 1 - window, lo), b - 1), med(b + 1, min(b + 1 + window, hi))
            if left is None or right is None or abs(right - left) < jump:
                continue
            halves = (med(b - 1 - window, b - 1 - half), med(b - 1 - half, b - 1), med(b + 1, b + 1 + half),
                      med(b + 1 + half, b + 1 + window))
            if None in halves or abs(halves[1] - halves[0]) > jump / 2 or abs(halves[3] - halves[2]) > jump / 2:
                continue
            steps.append((abs(right - left), b, left, right))
        for _, b, left, right in sorted(steps, reverse=True):
            # the cut goes to the first column that stands as the far side does (the step may take two columns)
            for c in range(max(b - 2, lo + 1), min(b + 3, hi)):
                if not np.isnan(level[c]) and (level[c] - (left + right) / 2) * (right - left) > 0:
                    b = c
                    break
            if all(abs(b - c) >= part for c in cuts):
                cuts.append(b)
    edges = [lo] + sorted(cuts) + [hi]
    out = []
    for e0, e1 in zip(edges[:-1], edges[1:]):
        p0, p1 = trim(int(np.ceil(e0)), int(np.floor(e1)))
        seam = _weak_seam(level, med, p0, p1, int(round(seam_part / step)), window, seam_depth)
        out.append((t0 + p0 * step, t0 + p1 * step, None if seam is None else (t0 + seam[0] * step, seam[1])))
    return out


def _weak_seam(level, med, lo, hi, part, window, least):
    """deslab: 13.11.2026, the likeliest place in a tall part where two things meet, too faint to cut on its own
    (Nagornaya: the front of the narrow duct 28.4 cm, a groove 26.4 cm 2 cm wide, the wide duct 29.5-30.5 cm). The model
    looks at the picture and decides whether the candidate is two things (`parts`: 2). Returns (column, how deep or
    high in m) or None."""
    best = None
    for b in range(lo + part, hi - part + 1):
        for w in (1, 2, 3):
            if b + w > hi - part:
                break
            v = level[b:b + w]
            if np.isnan(v).all():
                continue
            left, right = med(max(b - window, lo), b), med(b + w, min(b + w + window, hi))
            if left is None or right is None:
                continue
            strength = min(left, right) - float(np.nanmedian(v))
            if strength >= least and (best is None or strength > best[1] + 1e-9):
                best = (b + w / 2, strength)
        left, right = med(max(b - 1 - window, lo), b - 1), med(b + 1, min(b + 1 + window, hi))
        if left is not None and right is not None:
            strength = abs(right - left)
            if strength >= least and (best is None or strength > best[1] + 1e-9):
                best = (b, strength)
    return None if best is None else (best[0], round(float(best[1]), 3))


def find_candidates(pts, scene, cell=0.02, min_depth=0.035):
    """deslab: what stands in front of a wall face, as numbered candidates (Set-of-Mark: the model names the candidate,
    the numbers stay the cloud's). One candidate is a patch of the face where the cloud stands 3.5..45 cm out of it:
    radiators, risers, pipes, boxes, panels, window boards. Each has its wall, face, extent along the wall, bottom and
    top over the floor, how far it stands out, and its place on the plan."""
    height = float(scene["levels"]["ceiling"])
    found = []
    for wall in scene.get("walls", []):
        a, u, n, length = _frame(wall)
        if length < 0.4:
            continue
        t, d, _ = _wall_coords(pts, wall)
        half = float(wall["thickness"]) / 2
        near = (t > 0.0) & (t < length) & (pts[:, 2] > 0.02) & (pts[:, 2] < height - 0.02) & (np.abs(d) < half + REACH)
        if not near.any():
            continue
        idx = np.nonzero(near)[0]
        idx = idx[_without_other_walls(pts[idx], scene, wall)]
        for side in (1, -1):
            depth = side * d[idx] - half
            m = (depth > -0.03) & (depth < REACH)
            if m.sum() < 200:
                continue
            cols = np.clip((t[idx][m] / cell).astype(int), 0, int(length / cell))
            rows = np.clip((pts[idx][m, 2] / cell).astype(int), 0, int(height / cell))
            front = np.full((int(height / cell) + 1, int(length / cell) + 1), -9.0)
            np.maximum.at(front, (rows, cols), depth[m])
            # deslab: 13.11.2026, Nagornaya run 1: whole walls came out as one «box» 15 cm deep (a wall with a default
            # thickness stands off its modelled face). The face is where most of the cloud is: depths count from it
            seen = front > -0.03
            if seen.sum() >= 50:
                hist, edges = np.histogram(front[seen], bins=np.arange(-0.03, REACH + 0.011, 0.01))
                k = int(np.argmax(hist))
                if hist[k] + hist[max(k - 1, 0)] + hist[min(k + 1, len(hist) - 1)] >= 0.35 * seen.sum() and edges[k] > 0.015:
                    front = np.where(seen, front - (edges[k] + 0.005), front)
            out = _without_thin_runs(front >= min_depth)
            for comp_y, comp_x in _components(front, out):
                ys, xs = comp_y, comp_x
                if len(ys) < 12:
                    continue
                t0, t1 = float(xs.min()) * cell, float(xs.max() + 1) * cell
                z0, z1 = float(ys.min()) * cell, float(ys.max() + 1) * cell
                width, tall = t1 - t0, z1 - z0
                if width * tall < 0.01 or max(width, tall) < 0.25:
                    continue
                if z1 < 0.16 and tall < 0.14:
                    continue   # a skirting board
                if z0 > height - 0.3:
                    continue   # the edge of the ceiling
                stand = float(np.percentile(front[ys, xs], 80))
                fill = len(ys) / max(1.0, (width / cell) * (tall / cell))
                if fill < 0.3 and width * tall > 0.5:
                    continue   # a curtain, a ragged cloud: not a thing with a shape
                for p0, p1, seam in _split_at_seams(t[idx][m], pts[idx][m, 2], depth[m], t0, t1, z0, z1, height):
                    along = (xs + 0.5) * cell
                    sub = (along >= p0) & (along < p1)
                    part = front[ys[sub], xs[sub]] if sub.any() else front[ys, xs]
                    found.append(_candidate(wall, side, a, u, n, half, p0, p1, z0, z1,
                                            float(np.percentile(part, 80)), fill, height))
                    if seam is not None:
                        # deslab: a faint seam for the model; the depths of the two sides as the candidate's own
                        sides = [front[ys[k], xs[k]] for k in (sub & (along < seam[0]), sub & (along >= seam[0]))]
                        if all(len(v) for v in sides):
                            found[-1].update({"seam": round(seam[0], 3), "seamStrength": seam[1],
                                              "seamDepths": [round(float(np.percentile(v, 80)), 3) for v in sides]})
    found = _join_pipe_pieces(found, height)
    # one thing near a corner is seen from both walls: it belongs to the wall it stands closer to
    drop = set()
    for i, c in enumerate(found):
        for j, o in enumerate(found):
            if i == j or c["wallId"] == o["wallId"] or j in drop:
                continue
            overlap = min(c["top"], o["top"]) - max(c["bottom"], o["bottom"])
            if overlap > 0.5 * min(c["top"] - c["bottom"], o["top"] - o["bottom"]) \
                    and math.hypot(c["at"]["x"] - o["at"]["x"], c["at"]["y"] - o["at"]["y"]) < 0.3 \
                    and (c["depth"], i) > (o["depth"], j):
                drop.add(i)
                break
    found = [c for i, c in enumerate(found) if i not in drop]
    found.sort(key=lambda c: (int(c["wallId"][1:]) if c["wallId"][1:].isdigit() else 0, c["side"], c["from"], c["bottom"]))
    for k, c in enumerate(found, 1):
        c["id"] = f"C{k}"
    return found


def _palette(depth):
    """Colour by how far a point stands in front of the wall face (metres); behind the face is blue."""
    if depth < -0.02:
        k = min(1.0, (-depth - 0.02) / 0.5)
        return (int(120 - 90 * k), int(170 - 110 * k), int(255 - 60 * k))
    if depth <= 0.025:
        return (205, 205, 205)
    k = min(1.0, (depth - 0.025) / (REACH - 0.025))
    if k < 0.5:
        return (255, int(215 - 250 * k), 30)
    return (int(255 - 230 * (k - 0.5)), int(90 - 120 * (k - 0.5)), int(30 + 280 * (k - 0.5)))


def _ticks(draw, x0, y0, x1, y1, to_px_x, to_px_y, xs, ys, fmt="{:g}"):
    for v in xs:
        px = to_px_x(v)
        draw.line([(px, y0), (px, y1)], fill=(232, 232, 232))
        draw.text((px + 2, y1 + 3), fmt.format(v), fill=(0, 0, 0))
    for v in ys:
        py = to_px_y(v)
        draw.line([(x0, py), (x1, py)], fill=(232, 232, 232))
        draw.text((2, py - 5), fmt.format(v), fill=(0, 0, 0))


def _steps(lo, hi, step):
    k = math.ceil(lo / step - 1e-9)
    out = []
    while k * step <= hi + 1e-9:
        out.append(round(k * step, 3))
        k += 1
    return out


def render_elevation(pts, wall, side, height, path, scale=ELEVATION_SCALE, candidates=()):
    """One face of a wall as an elevation: x is the offset along the wall from its start, y the height over the floor.
    `side` is +1 for the left of start -> end, -1 for the right. Returns the share of the face that has points."""
    from PIL import Image, ImageDraw

    t, d, length = _wall_coords(pts, wall)
    half = float(wall["thickness"]) / 2
    depth = side * d - half
    m = (t > -0.3) & (t < length + 0.3) & (pts[:, 2] > -0.1) & (pts[:, 2] < height + 0.1) \
        & (depth > -(2 * half + 0.15)) & (depth < REACH)
    left, bottom, right, top = 46, 30, 16, 40
    w_px, h_px = int((length + 0.6) * scale), int((height + 0.2) * scale)
    img = Image.new("RGB", (w_px + left + right, h_px + top + bottom), "white")
    draw = ImageDraw.Draw(img)

    def px_x(v):
        return left + (v + 0.3) * scale

    def px_y(v):
        return top + (height + 0.1 - v) * scale

    _ticks(draw, left, top, left + w_px, top + h_px, px_x, px_y, _steps(0, length, 0.5), _steps(0, height, 0.5))
    share = 0.0
    if m.any():
        cols = np.clip(((t[m] + 0.3) * scale).astype(int), 0, w_px - 1)
        rows = np.clip(((height + 0.1 - pts[m, 2]) * scale).astype(int), 0, h_px - 1)
        front = np.full((h_px, w_px), -9.0)
        np.maximum.at(front, (rows, cols), depth[m])
        hit = front > -8.0
        inside = np.zeros_like(hit)
        inside[int(0.1 * scale):int((height + 0.1) * scale), int(0.3 * scale):int((length + 0.3) * scale)] = True
        share = float(hit[inside].mean()) if inside.any() else 0.0
        rgb = np.asarray(img).copy()
        ys, xs = np.nonzero(hit)
        values = front[ys, xs]
        colours = np.array([_palette(float(v)) for v in np.round(values, 2)], dtype=np.uint8)
        rgb[ys + top, xs + left] = colours
        img = Image.fromarray(rgb)
        draw = ImageDraw.Draw(img)
    for o in wall.get("openings", []):
        a = float(o["centerOffset"]) - float(o["width"]) / 2
        b = float(o["centerOffset"]) + float(o["width"]) / 2
        z0, z1 = float(o.get("sill", 0)), float(o.get("sill", 0)) + float(o["height"])
        draw.rectangle([px_x(a), px_y(z1), px_x(b), px_y(z0)], outline=(0, 140, 0))
        draw.text((px_x(a) + 3, px_y(z1) + 3), f"{o['id']} {o['kind']}", fill=(0, 110, 0))
    for c in candidates:
        if c["wallId"] != wall["id"] or (c["side"] == "left") != (side > 0):
            continue
        draw.rectangle([px_x(c["from"]) - 2, px_y(c["top"]) - 2, px_x(c["to"]) + 2, px_y(c["bottom"]) + 2], outline=(0, 0, 0))
        draw.text((px_x(c["from"]), px_y(c["top"]) - 14), c["id"], fill=(0, 0, 0))
    draw.rectangle([px_x(0), px_y(height), px_x(length), px_y(0)], outline=(90, 90, 90))
    draw.text((left, 4), f"{wall['id']} elevation, {'left' if side > 0 else 'right'} face (of start->end). "
                         f"x: offset from the wall start, m; y: height over the floor, m", fill=(0, 0, 0))
    draw.text((left, 18), f"grey: wall face; yellow->red->violet: stands 0.03..{REACH:g} m in front of the face; "
                          "blue: behind the face (reveals, frames); white: no points (glass, opening); black boxes C..: candidates", fill=(0, 0, 0))
    img.save(path)
    return share


def render_plan_band(pts, scene, z0, z1, path, title, max_px=2400, candidates=()):
    """A horizontal cut of the cloud between two heights, with the walls of the scene and the model's axes."""
    from PIL import Image, ImageDraw

    corners = [(p["x"], p["y"]) for w in scene["walls"] for p in (w["start"], w["end"])]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    x0, y0, x1, y1 = min(xs) - 0.7, min(ys) - 0.7, max(xs) + 0.7, max(ys) + 0.7
    scale = min(150.0, max_px / max(x1 - x0, y1 - y0))
    left, top, bottom, right = 46, 40, 30, 16
    w_px, h_px = int((x1 - x0) * scale), int((y1 - y0) * scale)
    img = Image.new("RGB", (w_px + left + right, h_px + top + bottom), "white")
    m = (pts[:, 2] > z0) & (pts[:, 2] < z1) & (pts[:, 0] > x0) & (pts[:, 0] < x1) & (pts[:, 1] > y0) & (pts[:, 1] < y1)
    if m.any():
        cols = np.clip(((pts[m, 0] - x0) * scale).astype(int), 0, w_px - 1)
        rows = np.clip(((y1 - pts[m, 1]) * scale).astype(int), 0, h_px - 1)
        count = np.zeros((h_px, w_px))
        np.add.at(count, (rows, cols), 1)
        dense = np.percentile(count[count > 0], 90) if (count > 0).any() else 1.0
        shade = (255 - 235 * np.clip(count / max(dense, 1.0), 0, 1)).astype(np.uint8)
        rgb = np.asarray(img).copy()
        rgb[top:top + h_px, left:left + w_px] = shade[:, :, None]
        img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(img)

    def px_x(v):
        return left + (v - x0) * scale

    def px_y(v):
        return top + (y1 - v) * scale

    _ticks(draw, left, top, left + w_px, top + h_px, px_x, px_y, _steps(x0, x1, 1.0), _steps(y0, y1, 1.0))
    for w in scene["walls"]:
        a, b = w["start"], w["end"]
        draw.line([(px_x(a["x"]), px_y(a["y"])), (px_x(b["x"]), px_y(b["y"]))], fill=(230, 60, 60))
        draw.text((px_x((a["x"] + b["x"]) / 2) + 4, px_y((a["y"] + b["y"]) / 2) + 4), w["id"], fill=(200, 0, 0))
    for r in scene.get("rooms", []):
        poly = r["polygon"]
        cx, cy = sum(p["x"] for p in poly) / len(poly), sum(p["y"] for p in poly) / len(poly)
        draw.text((px_x(cx), px_y(cy)), r["id"], fill=(0, 120, 0))
    for c in candidates:
        if c["top"] > z0 and c["bottom"] < z1:
            x, y = px_x(c["at"]["x"]), px_y(c["at"]["y"])
            draw.ellipse([x - 3, y - 3, x + 3, y + 3], outline=(0, 0, 220))
            draw.text((x + 5, y - 12), c["id"], fill=(0, 0, 220))
    draw.text((left, 4), f"plan cut of the cloud, {title}. x, y: the scene's metres", fill=(0, 0, 0))
    draw.text((left, 18), "dark: points of the cloud; red lines: wall axes of the scene with their ids", fill=(0, 0, 0))
    img.save(path)


def write_views(points, scene, folder, min_share=0.12):
    """Writes the pictures and views.json (file, kind, description) into `folder`. Returns the list."""
    os.makedirs(folder, exist_ok=True)
    pts = model_points(points, scene)
    height = float(scene["levels"]["ceiling"])
    candidates = scene.get("candidates") or []
    views = []
    for name, z0, z1, text in PLAN_BANDS:
        lo, hi = (height + z0, height + z1) if z0 < 0 else (z0, z1)
        path = os.path.join(folder, f"plan-{name}.png")
        render_plan_band(pts, scene, lo, hi, path, text, candidates=candidates)
        views.append({"file": path, "kind": "plan", "band": [round(lo, 2), round(hi, 2)], "description": text})
    for wall in scene["walls"]:
        if _frame(wall)[3] < 0.4:
            continue
        for side in (1, -1):
            path = os.path.join(folder, f"wall-{wall['id']}-{'left' if side > 0 else 'right'}.png")
            share = render_elevation(pts, wall, side, height, path, candidates=candidates)
            if share < min_share:
                os.remove(path)   # this face was not scanned (the other side of an outer wall)
                continue
            views.append({"file": path, "kind": "elevation", "wallId": wall["id"], "side": "left" if side > 0 else "right",
                          "description": f"elevation of {wall['id']}, {'left' if side > 0 else 'right'} face"})
    with open(os.path.join(folder, "views.json"), "w", encoding="utf-8") as f:
        json.dump(views, f, ensure_ascii=False, indent=1)
    return views
