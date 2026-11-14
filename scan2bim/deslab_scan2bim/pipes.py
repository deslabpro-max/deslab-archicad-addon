"""deslab: pipes in the cloud, by their axes and diameters.

Игорь 13.11.2026: «от стояка идут трубы к радиаторам отопления… в санузле стояки с отводами, стояк канализации и
вентиляционная труба… нужно научиться строить коммуникации, чтобы повторялось по диаметрам, по направлению то, как это
сделано в облаке точек». The candidates of views.py are patches in front of a wall; a group of pipes came out as a
«box». Here the pipes themselves are found:

- a riser is a small spot of the plan that is filled at most heights (a thin thing standing from floor to ceiling);
- a run is a small spot of a wall's cross-section (height x distance from the face) that is filled along the wall;
- ends that meet (a run at a riser, two runs in a corner) are joined, so the pipes come out as connected paths.

Every number is measured in the cloud; what the pipe carries is for the reviewing model to say (scene "pipes":
id, kind riser|run, diameter, path of {x, y, z}, hint). Needs numpy; the picture needs Pillow.

Dev run on a thinned cloud (the helper's --voxels file): python -m deslab_scan2bim.pipes CLOUD.npz SCENE.json [--png P]
"""

from __future__ import annotations

import json
import math
import sys

import numpy as np

from .grid import label
from .views import _frame, _inside, _wall_coords, model_points

CELL = 0.01        # plan and cross-section cell, m
LEVEL = 0.05       # step along the pipe, m
SKIN_COVER = 0.15  # deslab: share of the wall's height filled at a depth past the cut that is still its skin
SKIN_DEPTH = 3     # deslab: cells past the cut looked at for the skin


def _grow(mask):
    out = mask.copy()
    out[..., 1:, :] |= mask[..., :-1, :]
    out[..., :-1, :] |= mask[..., 1:, :]
    out[..., :, 1:] |= mask[..., :, :-1]
    out[..., :, :-1] |= mask[..., :, 1:]
    return out


def _in_wall_body(scene, x, y, margin=0.0):
    for wall in scene.get("walls", []):
        a, u, n, length = _frame(wall)
        t = (x - a[0]) * u[0] + (y - a[1]) * u[1]
        d = (x - a[0]) * n[0] + (y - a[1]) * n[1]
        if -0.02 < t < length + 0.02 and abs(d) < float(wall["thickness"]) / 2 + margin:
            return True
    return False


def _in_rooms(scene, x, y, margin=0.05):
    """Inside a room of the flat (the stair hall and the neighbours behind the walls are scanned through the doors)."""
    rooms = scene.get("rooms") or []
    if not rooms:
        return True
    return any(_inside(r["polygon"], (x + dx, y + dy)) for r in rooms
               for dx, dy in ((0, 0), (margin, 0), (-margin, 0), (0, margin), (0, -margin)))


def find_risers(pts, scene, min_span=1.2, max_size=0.22, fill=0.5):
    """Thin things standing in the room: spots of the plan up to 22 cm across, filled at half of the heights or more
    over 1.2 m or more, not inside a wall (window and door frames stand there)."""
    height = float(scene["levels"]["ceiling"])
    z = pts[:, 2]
    keep = (z > 0.05) & (z < height - 0.05)
    p = pts[keep]
    if len(p) < 100:
        return []
    x0, y0 = p[:, 0].min(), p[:, 1].min()
    ix = ((p[:, 0] - x0) / CELL).astype(np.int32)
    iy = ((p[:, 1] - y0) / CELL).astype(np.int32)
    iz = ((p[:, 2] - 0.05) / LEVEL).astype(np.int32)
    nx, ny, nz = int(ix.max()) + 1, int(iy.max()) + 1, int(iz.max()) + 1
    occ = np.zeros((nz, ny, nx), dtype=bool)
    occ[iz, iy, ix] = True
    levels = _grow(occ).sum(axis=0)
    # A scan leaves a thin veil of stray points between a pipe and the wall behind it, which ties the pipe's spot
    # to the wall's when few filled heights are asked for. So the spots are taken at a strict share of the heights
    # first (a pipe standing free from floor to ceiling), then at looser ones (a pipe hidden in part).
    spots, taken = [], np.zeros(levels.shape, dtype=bool)
    for share in (0.8, 0.6, 0.4):
        labels, count = label(levels >= max(int(nz * share), int(min_span / LEVEL * fill), 4))
        for k in range(1, count + 1):
            ys, xs = np.nonzero(labels == k)
            if (xs.max() - xs.min() + 1) * CELL > max_size or (ys.max() - ys.min() + 1) * CELL > max_size or len(ys) < 2:
                continue
            box = (slice(max(ys.min() - 2, 0), ys.max() + 3), slice(max(xs.min() - 2, 0), xs.max() + 3))
            if taken[box].any():
                continue
            taken[box] = True
            spots.append((ys, xs))
    spots = _merge_arcs(spots, p, x0, y0, max_size)
    found = []
    for ys, xs in spots:
        w, h = (xs.max() - xs.min() + 1) * CELL, (ys.max() - ys.min() + 1) * CELL
        cx, cy = x0 + (xs.min() + xs.max() + 1) / 2 * CELL, y0 + (ys.min() + ys.max() + 1) / 2 * CELL
        if _in_wall_body(scene, cx, cy) or not _in_rooms(scene, cx, cy):
            continue
        column = occ[:, max(ys.min() - 1, 0):ys.max() + 2, max(xs.min() - 1, 0):xs.max() + 2].any(axis=(1, 2))
        zs = np.nonzero(column)[0]
        z_lo, z_hi = 0.05 + zs.min() * LEVEL, 0.05 + (zs.max() + 1) * LEVEL
        if z_hi - z_lo < min_span or column[zs.min():zs.max() + 1].mean() < fill:
            continue
        # the spot was grown by a cell on every side; the scanner sees the half of the pipe that faces the room,
        # which is as wide as the pipe along the wall
        d = max(max(w, h) - 2 * CELL - CELL / 2, 0.012)
        near = (np.abs(p[:, 0] - cx) < w / 2) & (np.abs(p[:, 1] - cy) < h / 2)
        if near.sum() >= 30:
            # the points themselves measure finer than the cells
            # level by level, and the middle one of the sizes: the veil of stray points to the wall, a clamp or a
            # branch widen the spot at some heights only
            q = p[near]
            lv = ((q[:, 2] - 0.05) / LEVEL).astype(np.int32)
            order = np.argsort(lv, kind="stable")
            q, lv = q[order], lv[order]
            sizes = []
            for part in np.split(q, np.flatnonzero(np.diff(lv)) + 1):
                if len(part) >= 4:
                    ex = np.percentile(part[:, 0], 98) - np.percentile(part[:, 0], 2)
                    ey = np.percentile(part[:, 1], 98) - np.percentile(part[:, 1], 2)
                    sizes.append(max(ex, ey))
            wide = q[:, 0 if w >= h else 1]
            d = float(np.median(sizes)) if len(sizes) >= 5 else float(np.percentile(wide, 98) - np.percentile(wide, 2))
            d = max(d, 0.012)
            if d >= 0.06:
                shape = _roundness(q, lv)
                if shape is not None and shape[0] == "flat":
                    continue
                if shape is not None and shape[0] == "round":
                    d = min(shape[1], max_size)
            cx = float((np.percentile(p[near][:, 0], 98) + np.percentile(p[near][:, 0], 2)) / 2)
            cy = float((np.percentile(p[near][:, 1], 98) + np.percentile(p[near][:, 1], 2)) / 2)
        z_lo = 0.0 if z_lo < 0.2 else z_lo
        z_hi = height if z_hi > height - 0.2 else z_hi
        found.append({"kind": "riser", "diameter": round(d, 3),
                      "path": [{"x": round(float(cx), 4), "y": round(float(cy), 4), "z": round(float(z_lo), 3)},
                               {"x": round(float(cx), 4), "y": round(float(cy), 4), "z": round(float(z_hi), 3)}],
                      "fill": round(float(column[zs.min():zs.max() + 1].mean()), 2)})
    return found


def _merge_arcs(spots, p, x0, y0, max_size, gap=2):
    """deslab: 14.11.2026, Nagornaya: the sewer riser 108 mm, seen over a narrow arc, fell apart into three thin spots
    (12, 12 and 50 mm). Spots at most `gap` cells apart are taken together when their points, level by level, lie on one
    circle of 6 cm or more (two thin pipes side by side do not)."""
    if len(spots) < 2:
        return spots
    boxes = [(int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())) for ys, xs in spots]
    parent = list(range(len(spots)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(spots)):
        for j in range(i + 1, len(spots)):
            a, b = boxes[i], boxes[j]
            if max(a[0], b[0]) - min(a[1], b[1]) - 1 <= gap and max(a[2], b[2]) - min(a[3], b[3]) - 1 <= gap:
                parent[find(j)] = find(i)
    groups = {}
    for i in range(len(spots)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        if len(members) == 1:
            out.append(spots[members[0]])
            continue
        ys = np.concatenate([spots[i][0] for i in members])
        xs = np.concatenate([spots[i][1] for i in members])
        merged = None
        if max(xs.max() - xs.min() + 1, ys.max() - ys.min() + 1) * CELL <= max_size:
            near = (p[:, 0] >= x0 + (xs.min() - 1) * CELL) & (p[:, 0] < x0 + (xs.max() + 2) * CELL) & \
                   (p[:, 1] >= y0 + (ys.min() - 1) * CELL) & (p[:, 1] < y0 + (ys.max() + 2) * CELL)
            q = p[near]
            if len(q) >= 30:
                lv = ((q[:, 2] - 0.05) / LEVEL).astype(np.int32)
                order = np.argsort(lv, kind="stable")
                shape = _roundness(q[order], lv[order])
                if shape is not None and shape[0] == "round" and shape[1] >= 0.06:
                    merged = (ys, xs)
        out.extend([merged] if merged is not None else [spots[i] for i in members])
    return out


def _circle(xy):
    """Circle through plan points: the algebraic fit, then a few Gauss-Newton steps on the points' distances (the
    algebraic one alone makes a short arc's circle too small). Centre, radius, middle distance of the points from it."""
    a = np.column_stack([xy[:, 0], xy[:, 1], np.ones(len(xy))])
    (cx, cy, c), *_ = np.linalg.lstsq(a, (xy ** 2).sum(axis=1), rcond=None)
    cx, cy = cx / 2, cy / 2
    r = float(np.sqrt(max(c + cx * cx + cy * cy, 1e-12)))
    for _ in range(20):
        dx, dy = xy[:, 0] - cx, xy[:, 1] - cy
        dist = np.maximum(np.hypot(dx, dy), 1e-9)
        jac = np.column_stack([-dx / dist, -dy / dist, -np.ones(len(xy))])
        step, *_ = np.linalg.lstsq(jac, -(dist - r), rcond=None)
        cx, cy, r = cx + step[0], cy + step[1], abs(r + step[2])
        if np.abs(step).max() < 1e-6:
            break
    err = np.abs(np.hypot(xy[:, 0] - cx, xy[:, 1] - cy) - r)
    return cx, cy, float(r), float(np.median(err))


def _roundness(q, lv, least=8):
    """deslab: 14.11.2026, Nagornaya: the narrow vent duct's south face (a flat strip 16 cm wide seen edge on) came out
    as a sewer riser 162 mm, and the real sewer riser (108 mm by a circle through the slices) as 56 mm. The half of a
    pipe the scanner sees is an arc: level by level a circle goes through its points closely, the same circle at every
    level, as wide as the spot or up to 2.5 times (seen over 60 degrees only). A flat face hardly bends off its straight
    line and gives no such circle. Returns ("round", diameter),
    ("flat", None) or None when too few levels can tell."""
    radii, errors, spans, bends = [], [], [], []
    for part in np.split(q, np.flatnonzero(np.diff(lv)) + 1):
        if len(part) < least:
            continue
        xy = part[:, :2].astype(np.float64)
        xy = xy - xy.mean(axis=0)
        span = float(max(np.ptp(xy[:, 0]), np.ptp(xy[:, 1])))
        if span < 0.03:
            continue
        # how far the points bend off their straight line: a half pipe about 0.44, a quarter of it 0.2, a face 0.05
        low, high = np.linalg.eigvalsh(np.cov(xy.T))
        bends.append(float(np.sqrt(max(low, 0.0) / max(high, 1e-12))))
        _, _, r, err = _circle(xy)
        radii.append(r)
        errors.append(err)
        spans.append(span)
    if len(radii) < 5:
        return None
    radii = np.array(radii)
    r, err, span, bend = float(np.median(radii)), float(np.median(errors)), float(np.median(spans)), float(np.median(bends))
    steady = np.subtract(*np.percentile(radii, [75, 25])) <= 0.25 * r
    # a circle as wide as the spot or up to 2.5 times (a pipe seen over 60 degrees only), the same at every level,
    # close to its points
    if steady and err <= 0.1 * r and 0.9 * span <= 2 * r <= 2.5 * span + 0.01:
        return ("round", round(2 * r, 3))
    if bend < 0.12 or 2 * r > 4 * span:
        return ("flat", None)
    return None


def _without_risers(pts, risers, margin=0.03):
    keep = np.ones(len(pts), dtype=bool)
    for r in risers:
        a, b = r["path"]
        keep &= ~((np.hypot(pts[:, 0] - a["x"], pts[:, 1] - a["y"]) < r["diameter"] / 2 + margin)
                  & (pts[:, 2] > a["z"] - 0.05) & (pts[:, 2] < b["z"] + 0.05))
    return pts[keep]


def find_runs(pts, scene, risers=(), reach=0.6, min_length=0.4, max_size=0.2, window=0.6, step=0.2, fill=0.8):
    """Pipes along the walls: per wall face, spots of its cross-section (height x distance from the face) up to 20 cm
    across that are filled along 0.4 m of the wall or more without a break.

    The cross-section is taken per 0.6 m stretch of the wall, not of the whole wall: a radiator further along stands
    at the height and the distance of the pipe that feeds it and would swallow the pipe's spot. The pieces found in
    neighbouring stretches at one height and distance are one run. The risers' own points are left out (a riser by a
    wall was cut into a ladder of short «runs»)."""
    height = float(scene["levels"]["ceiling"])
    pts = _without_risers(pts, risers)
    found = []
    for wall in scene.get("walls", []):
        a, u, n, length = _frame(wall)
        if length < min_length:
            continue
        t, d, _ = _wall_coords(pts, wall)
        half = float(wall["thickness"]) / 2
        for side in (1, -1):
            depth = side * d - half
            # deslab: 14.11.2026, Nagornaya v17 on: the cubes of every point fill the wall's skin (0-3 cm off its face) at
            # every height, and the stray points under a pipe tie the pipe's spot to it: the spot came out a metre tall
            # and both pipes from the second radiator to the corner were lost. The cross-section starts 2.5 cm off the
            # face where the cloud has it (the commonest depth near the modelled face)
            skin = (t > 0) & (t < length) & (depth > -0.03) & (depth < 0.1) & (pts[:, 2] > 0.2) & (pts[:, 2] < height - 0.2)
            cut = 0.012
            if skin.sum() >= 200:
                hist, edges = np.histogram(depth[skin], bins=np.arange(-0.03, 0.105, 0.01))
                cut = max(cut, float(edges[int(np.argmax(hist))]) + 0.005 + 0.025)
            m = (t > 0) & (t < length) & (depth > cut) & (depth < reach) & (pts[:, 2] > 0.04) & (pts[:, 2] < height - 0.04)
            if m.sum() < 50:
                continue
            it = (t[m] / LEVEL).astype(np.int32)
            idp = (depth[m] / CELL).astype(np.int32)
            iz = (pts[m, 2] / CELL).astype(np.int32)
            nt, nd, nz = int(length / LEVEL) + 1, int(reach / CELL) + 1, int(height / CELL) + 1
            occ = np.zeros((nt, nz, nd), dtype=bool)
            occ[np.clip(it, 0, nt - 1), np.clip(iz, 0, nz - 1), np.clip(idp, 0, nd - 1)] = True
            # deslab: 14.11.2026, Nagornaya v20: here and there the skin goes on past the cut (at 2-3 cm off the face
            # between the second radiator and the corner), above and below the pipes: it read as the radiator they
            # enter and cut both pipes to 40 cm of their 1.1 m. Per stretch of the wall, the depths next to the cut
            # that are filled over a good share of the wall's height are still its skin and are left out (a pipe, a
            # radiator or a box fills a small share of the height, and none of them lies on the wall)
            zl = int(0.2 / CELL)
            cover = occ[:, zl:max(int((height - 0.2) / CELL), zl + 1), :].mean(axis=1)
            c0 = int(cut / CELL + 1e-6)
            alive = np.ones(nt, dtype=bool)
            for k in range(c0, min(c0 + SKIN_DEPTH, nd)):
                here = cover[:, k] >= SKIN_COVER
                if k > c0:   # (the first cell is cut through by the cut and may be filled in part only)
                    alive &= here
                occ[alive & here, :, k] = False
            grown = _grow(occ)
            zz, dd, tt = pts[m, 2], depth[m], t[m]
            wn, st = int(round(window / LEVEL)), int(round(step / LEVEL))
            pieces = []   # (t0, t1, z centre, depth of the axis, diameter)
            for w0 in range(0, max(nt - wn, 0) + 1, st):
                part = grown[w0:w0 + wn]
                labels, count = label(part.sum(axis=0) >= int(min_length / LEVEL * fill))
                for k in range(1, count + 1):
                    zs, ds = np.nonzero(labels == k)
                    tall, deep = (zs.max() - zs.min() + 1) * CELL, (ds.max() - ds.min() + 1) * CELL
                    if tall > max_size or deep > max_size or len(zs) < 2:
                        continue
                    # how far the spot runs on along the whole wall, without a break of more than a bin
                    line = grown[:, zs.min():zs.max() + 1, ds.min():ds.max() + 1].any(axis=(1, 2))
                    # where the cloud goes on above or below the spot, the pipe has entered something bigger
                    # (the radiator it feeds): the run ends there
                    d0, d1 = ds.min(), ds.max() + 1
                    # (a panel radiator shows its front and its top, nothing inside: looked for up to 6 cm nearer
                    # to the room, and as a block 4 cm high, which a second pipe alongside is not)
                    e0, e1 = max(d0 - 3, 0), d1 + 6
                    above = occ[:, zs.max() + 3:zs.max() + 7, e0:e1].any(axis=2)
                    below = occ[:, max(zs.min() - 6, 0):max(zs.min() - 2, 0), e0:e1].any(axis=2)
                    block = (above.sum(axis=1) >= 3) | (below.sum(axis=1) >= 3)
                    line &= ~block
                    if not line[w0:w0 + wn].any():
                        continue
                    here = np.nonzero(line[w0:w0 + wn])[0]
                    b0 = b1 = w0 + int(here[len(here) // 2])
                    while b0 > 0 and (line[b0 - 1] or (b0 > 1 and line[b0 - 2])):
                        b0 -= 1
                    while b1 < nt - 1 and (line[b1 + 1] or (b1 < nt - 2 and line[b1 + 2])):
                        b1 += 1
                    while not line[b0]:
                        b0 += 1
                    while not line[b1]:
                        b1 -= 1
                    t0, t1 = b0 * LEVEL, (b1 + 1) * LEVEL
                    if t1 - t0 < min_length or line[b0:b1 + 1].mean() < fill:
                        continue
                    dia = max(tall - 2 * CELL - CELL / 2, 0.012)
                    zc = (zs.min() + zs.max() + 1) / 2 * CELL
                    near = (zz >= zs.min() * CELL) & (zz <= (zs.max() + 1) * CELL) & (dd >= ds.min() * CELL) & \
                           (dd <= (ds.max() + 1) * CELL) & (tt >= t0) & (tt <= t1)
                    seen = None
                    if near.sum() >= 30:
                        lo, hi = np.percentile(zz[near], 2), np.percentile(zz[near], 98)
                        dia, zc = max(float(hi - lo), 0.012), float((lo + hi) / 2)
                        seen = _front_diameter(zz[near], dd[near])
                        if seen is not None:
                            dia, zc = max(seen[0], 0.012), seen[1]
                        t0, t1 = max(t0, float(tt[near].min())), min(t1, float(tt[near].max()))
                    # the front of the pipe is seen: its axis is half a diameter behind the nearest points
                    dc = min(ds.min() * CELL + CELL + dia / 2, (ds.min() + ds.max() + 1) / 2 * CELL + dia / 2)
                    if near.sum() >= 30 and seen is not None:
                        # deslab: the stray points behind the pipe make the spot reach back to the wall
                        dc = max(seen[2] - dia / 2, dc)
                    pieces.append([t0, t1, zc, dc, dia])
            runs = []
            for p in sorted(pieces, key=lambda p: -(p[1] - p[0])):
                same = [r for r in runs if abs(r[2] - p[2]) < 0.03 and abs(r[3] - p[3]) < 0.03
                        and p[0] <= r[1] + 0.1 and r[0] <= p[1] + 0.1]
                if same:
                    same[0][0], same[0][1] = min(same[0][0], p[0]), max(same[0][1], p[1])
                else:
                    runs.append(p)
            # the joints of the wall with the floor and the ceiling (skirting, cornice) are no pipes
            runs = [r for r in runs if 0.15 <= r[2] <= height - 0.2]
            # four or more pieces one above another over the same stretch are the tiers of a flat thing standing
            # by the wall (the side of a box, a panel), not pipes
            # (over a metre of height or more: the two pipes of a radiator and their doubles stay)
            def over(o, r):
                return min(o[1], r[1]) - max(o[0], r[0]) >= 0.7 * min(o[1] - o[0], r[1] - r[0])

            def stacked(r):
                zs = sorted(o[2] for o in runs if abs(o[3] - r[3]) < 0.1 and over(o, r))
                # the pieces around this one that follow each other within 1.2 m of height
                k = zs.index(r[2])
                lo = hi = k
                while lo > 0 and zs[lo] - zs[lo - 1] <= 1.2:
                    lo -= 1
                while hi < len(zs) - 1 and zs[hi + 1] - zs[hi] <= 1.2:
                    hi += 1
                return hi - lo + 1 >= 4 and zs[hi] - zs[lo] >= 0.8
            runs = [r for r in runs if not stacked(r)]
            # one pipe seen as two spots a few centimetres apart: the longer piece stays
            single = []
            for r in sorted(runs, key=lambda r: -(r[1] - r[0])):
                if not any(abs(o[2] - r[2]) < 0.06 and abs(o[3] - r[3]) < 0.06 and over(o, r) for o in single):
                    single.append(r)
            runs = single
            for t0, t1, zc, dc, dia in runs:
                p0 = a + u * t0 + n * side * (half + dc)
                p1 = a + u * t1 + n * side * (half + dc)
                mid = (p0 + p1) / 2
                if not _in_rooms(scene, mid[0], mid[1]) or _in_wall_body(scene, mid[0], mid[1]):
                    continue
                found.append({"kind": "run", "diameter": round(dia, 3), "wallId": wall["id"],
                              "side": "left" if side > 0 else "right",
                              "path": [{"x": round(float(p0[0]), 4), "y": round(float(p0[1]), 4), "z": round(float(zc), 3)},
                                       {"x": round(float(p1[0]), 4), "y": round(float(p1[1]), 4), "z": round(float(zc), 3)}]})
    return _without_doubles(found)


def _off_walls(pts, scene, margin=0.012):
    """The points that are neither in a wall nor on its face."""
    keep = np.ones(len(pts), dtype=bool)
    for wall in scene.get("walls", []):
        t, d, length = _wall_coords(pts, wall)
        keep &= ~((np.abs(d) < float(wall["thickness"]) / 2 + margin) & (t > -0.05) & (t < length + 0.05))
    return pts[keep]


def find_branches(pts, scene, risers, runs=(), least=0.018, reach=0.8, min_length=0.15, max_size=0.07, band=0.03,
                  step_deg=5, start=0.04):
    """deslab: 14.11.2026, Игорь: «там ещё есть отводы. Вот отводы бы тоже было бы здорово фиксировать». A branch leaves a
    riser sideways at some height in any direction (to a tap, a meter, a towel rail), often shorter than the 40 cm of a run
    along a wall. Around each riser of 18 mm and more, the points within `band` of a horizontal ray from its axis are looked
    at in the ray's cross-section (distance along the ray x height, 1 cm cells): a branch is a band of heights 2 to 7 cm thick
    that is filled from the riser's skin on (within 4 cm of it) for 15 cm or more, with little just above and below it and
    on rays 25 degrees to either side (a wall, a box, a shelf, a curtain are filled there too). Returns runs with
    "branch": True whose first point stands on the riser's axis and holds the riser in "_riser". A sewer riser's branches
    may be as thick as it (up to 13 cm)."""
    height = float(scene["levels"]["ceiling"])
    free = _off_walls(_without_risers(pts, risers, margin=0.02), scene)
    found = []
    for r in risers:
        if float(r["diameter"]) < least:
            continue
        a, b = r["path"]
        cx, cy, rad = float(a["x"]), float(a["y"]), float(r["diameter"]) / 2
        z0, z1 = max(float(a["z"]), 0.1), min(float(b["z"]), height - 0.15)
        dx, dy = free[:, 0] - cx, free[:, 1] - cy
        near = (np.hypot(dx, dy) < reach + band) & (free[:, 2] > z0) & (free[:, 2] < z1)
        if near.sum() < 20:
            continue
        q, qx, qy = free[near], dx[near], dy[near]
        nz, ns = int((z1 - z0) / CELL) + 1, int((reach - rad) / CELL) + 1
        angles = np.radians(np.arange(0, 360, step_deg))
        lengths = np.zeros((len(angles), nz), dtype=np.int32)
        grids = []
        for k, th in enumerate(angles):
            s = qx * math.cos(th) + qy * math.sin(th) - rad
            e = np.abs(-qx * math.sin(th) + qy * math.cos(th))
            m = (s > 0) & (s < reach - rad) & (e < band)
            occ = np.zeros((nz, ns), dtype=bool)
            occ[((q[m, 2] - z0) / CELL).astype(np.int32).clip(0, nz - 1), (s[m] / CELL).astype(np.int32).clip(0, ns - 1)] = True
            # one empty cell may lie in a branch (a joint, a missed strip)
            occ[:, 1:-1] |= occ[:, :-2] & occ[:, 2:]
            grids.append(occ)
            first = np.where(occ.any(axis=1), occ.argmax(axis=1), ns)
            # filled run from the first cell on, per height
            gaps = ~occ
            gaps[:, -1] = True
            rows = np.arange(nz)
            after = np.array([gaps[i, first[i]:].argmax() if first[i] < ns else 0 for i in rows])
            lengths[k] = np.where(first <= int(start / CELL), after, 0)
        need = int(min_length / CELL)
        side = max(1, int(round(25 / step_deg)))
        mine = []
        for k in range(len(angles)):
            row = lengths[k]
            good = row >= need
            if not good.any():
                continue
            for lo, hi in _bands(good):
                thick = hi - lo
                # a sewer riser's branch to the toilet is as thick as the riser
                if thick < 2 or thick * CELL > max(max_size, min(1.2 * 2 * rad, 0.13)):
                    continue
                n = int(np.median(row[lo:hi]))
                # little just above and below, and on the rays to either side
                around = np.concatenate([row[max(lo - 5, 0):max(lo - 2, 0)], row[hi + 2:hi + 5]])
                if len(around) and around.max() >= 0.5 * n:
                    continue
                if max(lengths[(k - side) % len(angles), lo:hi].max(), lengths[(k + side) % len(angles), lo:hi].max()) >= 0.5 * n:
                    continue
                mine.append((n, k, lo, hi))
        kept = []
        for n, k, lo, hi in sorted(mine, reverse=True):
            zc = z0 + (lo + hi) / 2 * CELL
            if any(abs(zc - o["zc"]) < 0.06 and min(abs(k - o["k"]), len(angles) - abs(k - o["k"])) * step_deg < 30 for o in kept):
                continue
            th = angles[k]
            length = n * CELL
            # a run along a wall from this riser at this height and in this direction, or one that passes the riser on
            # this line, is the same pipe
            # (Nagornaya: the run to the radiator was joined to the other riser of the pair, 10 cm off, and goes slanted by
            # up to 7 cm from this one's branch along it)
            tip = (cx + math.cos(th) * (rad + n * CELL), cy + math.sin(th) * (rad + n * CELL))
            mid = ((cx + tip[0]) / 2, (cy + tip[1]) / 2)
            if any(abs(o["path"][0]["z"] - zc) < 0.05 and (
                    any(abs(e["x"] - cx) < 0.02 and abs(e["y"] - cy) < 0.02 for e in o["path"]) and _towards(o, cx, cy, th)
                    or max(_off_segment(p, o) for p in (mid, tip)) < 0.08) for o in runs):
                continue
            # the diameter from the points of the branch, as for a run
            s = qx * math.cos(th) + qy * math.sin(th) - rad
            e = np.abs(-qx * math.sin(th) + qy * math.cos(th))
            m = (s > 0.02) & (s < length) & (e < band) & (np.abs(q[:, 2] - zc) < max_size / 2 + CELL)
            dia = (hi - lo) * CELL - CELL / 2
            if m.sum() >= 20:
                lo_z, hi_z = np.percentile(q[m, 2], 2), np.percentile(q[m, 2], 98)
                dia, zc = float(hi_z - lo_z), float((lo_z + hi_z) / 2)
                # the direction by the branch's own points (the rays go every 5 degrees: Nagornaya P23 ended 4 cm aside)
                far = m & (s > length / 2)
                if far.sum() >= 10:
                    th = math.atan2(float(qy[far].mean()), float(qx[far].mean()))
            end = (cx + math.cos(th) * (rad + length), cy + math.sin(th) * (rad + length))
            kept.append({"zc": zc, "k": k})
            found.append({"kind": "run", "branch": True, "diameter": round(max(dia, 0.012), 3), "_riser": r,
                          "path": [{"x": round(cx, 4), "y": round(cy, 4), "z": round(zc, 3)},
                                   {"x": round(end[0], 4), "y": round(end[1], 4), "z": round(zc, 3)}]})
    return found


def _bands(mask):
    """(start, stop) of the runs of True in a 1-D mask."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()))


def _off_segment(p, run):
    """How far the plan point p lies from the run's axis."""
    a, b = run["path"][0], run["path"][-1]
    vx, vy = b["x"] - a["x"], b["y"] - a["y"]
    k = max(0.0, min(1.0, ((p[0] - a["x"]) * vx + (p[1] - a["y"]) * vy) / (vx * vx + vy * vy or 1.0)))
    return math.hypot(p[0] - a["x"] - k * vx, p[1] - a["y"] - k * vy)


def _towards(run, cx, cy, th, tol=30):
    """The run leaves the point (cx, cy) in the direction th (within tol degrees)."""
    a, b = run["path"][0], run["path"][-1]
    far = b if math.hypot(a["x"] - cx, a["y"] - cy) < math.hypot(b["x"] - cx, b["y"] - cy) else a
    ang = math.degrees(math.atan2(far["y"] - cy, far["x"] - cx) - th) % 360
    return min(ang, 360 - ang) < tol


def _front_diameter(z, depth, row=0.01, band=0.012):
    """deslab: 14.11.2026, a run's diameter from the front of the pipe, which the scanner sees whole: the rows (1 cm of
    height) whose frontmost point is within `band` of the pipe's front make a chord of the circle, 2 sqrt(2 r band - band²)
    long. On 1 cm cubes this tells 20-50 mm pipes apart only roughly (31-45 mm); `band` 12 mm takes in two layers of cubes,
    so a thin pipe comes out thicker rather than thinner than it is. The stray points that run from the pipe's edges back to the wall stand behind its axis and are not counted
    (Nagornaya: the radiator pipes came out 60-70 mm on the cubes, 40 mm on every 4th point, by the full height of the
    spot). Returns (diameter, height of the axis, depth of the pipe's front) or None."""
    rows = np.floor(z / row).astype(np.int64)
    uniq, inv = np.unique(rows, return_inverse=True)
    if len(uniq) < 2:
        return None
    front = np.full(len(uniq), -9.0)
    np.maximum.at(front, inv, depth)
    k = int(np.argmax(front))
    ok = front >= front[k] - band + 1e-6
    lo = hi = k
    while lo > 0 and ok[lo - 1] and uniq[lo - 1] == uniq[lo] - 1:
        lo -= 1
    while hi < len(uniq) - 1 and ok[hi + 1] and uniq[hi + 1] == uniq[hi] + 1:
        hi += 1
    # the chord by the points themselves (a row of 1 cm touched by a chord of 2 cm makes three rows)
    sel = (rows >= uniq[lo]) & (rows <= uniq[hi]) & (depth >= front[k] - band + 1e-6)
    zs = z[sel]
    if len(zs) >= 5:
        chord, mid = float(np.percentile(zs, 98) - np.percentile(zs, 2)), float((np.percentile(zs, 98) + np.percentile(zs, 2)) / 2)
    else:
        chord, mid = (hi - lo) * row, float((uniq[lo] + uniq[hi] + 1) / 2 * row)
    r = ((chord / 2) ** 2 + band ** 2) / (2 * band)
    return 2 * r, mid, float(front[k])


def _without_doubles(runs, tol=0.04):
    """A pipe in a corner is seen from both walls of the corner: the longer piece stays."""
    keep = []
    for r in sorted(runs, key=lambda r: -math.dist(*[(p["x"], p["y"]) for p in r["path"]])):
        a, b = [np.array((p["x"], p["y"])) for p in r["path"]]
        double = False
        for o in keep:
            p, q = [np.array((e["x"], e["y"])) for e in o["path"]]
            v = q - p
            if abs(o["path"][0]["z"] - r["path"][0]["z"]) > tol:
                continue
            off = [np.linalg.norm(p + np.clip(np.dot(e - p, v) / np.dot(v, v), 0, 1) * v - e) for e in (a, b)]
            double |= max(off) < tol
        if not double:
            keep.append(r)
    return keep


def _hint(pipe, height):
    d = pipe["diameter"]
    z = pipe["path"][0]["z"]
    if d < 0.016:
        # deslab: 14.11.2026, Игорь: «по 12 миллиметров труб у нас не бывает»: thinner than the thinnest pipe (16 mm)
        return "too thin for a pipe: wire or artefact?"
    if pipe["kind"] == "riser":
        return "sewer riser?" if d >= 0.08 else "heating or water riser?"
    if d >= 0.08:
        return "vent duct?" if z > height - 0.7 else "sewer pipe?"
    return "heating or water pipe?"


def join_pipes(pipes, reach=0.15):
    """A run that ends at a riser ends on the riser's axis; two runs that end at one point (a corner) share it."""
    risers = [p for p in pipes if p["kind"] == "riser"]
    runs = [p for p in pipes if p["kind"] == "run"]
    used = {}
    for run in runs:
        for end in (run["path"][0], run["path"][-1]):
            best = None
            for r in risers:
                a, b = r["path"]
                if not a["z"] - 0.05 <= end["z"] <= b["z"] + 0.05:
                    continue
                gap = math.hypot(end["x"] - a["x"], end["y"] - a["y"])
                if gap <= reach + r["diameter"] and (best is None or (used.get(id(r), 0), gap) < best[:2]):
                    best = (used.get(id(r), 0), gap, r)
            if best:
                r = best[2]
                used[id(r)] = used.get(id(r), 0) + 1
                end["x"], end["y"] = r["path"][0]["x"], r["path"][0]["y"]
                end["joins"] = r.get("id")
    # a run that passes a riser close to its end (it was followed on into the corner) ends at the riser
    for run in runs:
        for k in (0, -1):
            end, other = run["path"][k], run["path"][0 if k else -1]
            if "joins" in end:
                continue
            vx, vy = other["x"] - end["x"], other["y"] - end["y"]
            length = math.hypot(vx, vy)
            near = []
            for r in risers:
                a, b = r["path"]
                if length < 1e-6 or not a["z"] - 0.05 <= end["z"] <= b["z"] + 0.05:
                    continue
                along = ((a["x"] - end["x"]) * vx + (a["y"] - end["y"]) * vy) / length
                across = abs((a["x"] - end["x"]) * vy - (a["y"] - end["y"]) * vx) / length
                if 0 < along <= min(0.5, length / 2) and across <= reach + r["diameter"]:
                    near.append((used.get(id(r), 0), along, r))
            if near:
                # two pipes of a radiator go to the two risers that stand side by side
                _, _, r = min(near, key=lambda c: c[:2])
                used[id(r)] = used.get(id(r), 0) + 1
                end["x"], end["y"], end["joins"] = r["path"][0]["x"], r["path"][0]["y"], r.get("id")
    ends = [(run, k) for run in runs for k in (0, -1) if "joins" not in run["path"][k]]
    for i, (ra, ka) in enumerate(ends):
        for rb, kb in ends[i + 1:]:
            if ra is rb:
                continue
            pa, pb = ra["path"][ka], rb["path"][kb]
            if math.dist((pa["x"], pa["y"], pa["z"]), (pb["x"], pb["y"], pb["z"])) <= reach:
                # the corner: where the two axes cross on the plan
                qa, qb = ra["path"][0 if ka else -1], rb["path"][0 if kb else -1]
                ux, uy = pa["x"] - qa["x"], pa["y"] - qa["y"]
                vx, vy = pb["x"] - qb["x"], pb["y"] - qb["y"]
                det = ux * vy - uy * vx
                if abs(det) > 1e-6 * (math.hypot(ux, uy) * math.hypot(vx, vy) + 1e-12) and abs(det) > 1e-9:
                    s = ((qb["x"] - qa["x"]) * vy - (qb["y"] - qa["y"]) * vx) / det
                    x, y = qa["x"] + ux * s, qa["y"] + uy * s
                    if math.hypot(x - pa["x"], y - pa["y"]) <= 2 * reach:
                        for p in (pa, pb):
                            p["x"], p["y"] = round(x, 4), round(y, 4)
                        pa["joins"], pb["joins"] = rb.get("id"), ra.get("id")
    return pipes


def find_pipes(pts, scene):
    """Scene "pipes": risers and runs with ids P1…, joined where they meet."""
    height = float(scene["levels"]["ceiling"])
    risers = find_risers(pts, scene)
    runs = find_runs(pts, scene, risers)
    pipes = risers + runs
    pipes.sort(key=lambda p: (p["kind"] != "riser", round(p["path"][0]["x"], 1), round(p["path"][0]["y"], 1), p["path"][0]["z"]))
    for k, p in enumerate(pipes, 1):
        p["id"] = f"P{k}"
        p["hint"] = _hint(p, height)
    pipes = join_pipes(pipes)
    # a short piece between two risers that stand side by side is the risers themselves, seen from a wall
    def between_risers(p):
        a, b = p["path"][0], p["path"][-1]
        return p["kind"] == "run" and a.get("joins") and b.get("joins") and math.dist((a["x"], a["y"]), (b["x"], b["y"])) < 0.3
    pipes = [p for p in pipes if not between_risers(p)]
    # deslab: 14.11.2026, the branches of the risers (after the runs, so that a run along a wall is not found twice)
    ids = {id(r): r["id"] for r in risers}
    for k, p in enumerate(find_branches(pts, scene, risers, [p for p in pipes if p["kind"] == "run"]), len(pipes) + 1):
        p["id"] = f"P{k}"
        p["path"][0]["joins"] = ids[id(p.pop("_riser"))]
        p["hint"] = "branch of " + p["path"][0]["joins"] + ": " + _hint(p, height)
        pipes.append(p)
    return pipes


def render(pts, scene, pipes, path, scale=100):
    from PIL import Image, ImageDraw
    xs = [c for w in scene["walls"] for c in (w["start"]["x"], w["end"]["x"])]
    ys = [c for w in scene["walls"] for c in (w["start"]["y"], w["end"]["y"])]
    x0, x1, y0, y1 = min(xs) - 0.6, max(xs) + 0.6, min(ys) - 0.6, max(ys) + 0.6
    img = Image.new("RGB", (int((x1 - x0) * scale), int((y1 - y0) * scale)), "white")
    draw = ImageDraw.Draw(img)

    def px(x, y):
        return (x - x0) * scale, (y1 - y) * scale
    m = (pts[:, 2] > 0.3) & (pts[:, 2] < 0.9)
    for x, y in pts[m][::7, :2]:
        i, j = int((x - x0) * scale), int((y1 - y) * scale)
        if 0 <= i < img.width and 0 <= j < img.height:
            img.putpixel((i, j), (170, 170, 170))
    for p in pipes:
        a, b = p["path"][0], p["path"][-1]
        if p["kind"] == "riser":
            cx, cy = px(a["x"], a["y"])
            r = max(p["diameter"] / 2 * scale, 3)
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(220, 0, 0), width=2)
        else:
            draw.line((*px(a["x"], a["y"]), *px(b["x"], b["y"])), fill=(0, 0, 220), width=2)
        draw.text(px(a["x"] + 0.04, a["y"] + 0.04), f"{p['id']} d{int(round(p['diameter'] * 1000))}", fill=(0, 0, 0))
    img.save(path)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    png = argv[argv.index("--png") + 1] if "--png" in argv else None
    data = np.load(argv[0])
    scene = json.load(open(argv[1], encoding="utf-8"))
    pts = model_points(data["xyz"].astype(np.float64) * float(data["size"]) + data["origin"], scene)
    pipes = find_pipes(pts, scene)
    for p in pipes:
        a, b = p["path"][0], p["path"][-1]
        print(p["id"], p["kind"], f"d{int(round(p['diameter'] * 1000))}", p.get("wallId", "-"),
              f"({a['x']:.2f},{a['y']:.2f},{a['z']:.2f})-({b['x']:.2f},{b['y']:.2f},{b['z']:.2f})",
              p["hint"], "joins:" + ",".join(str(e["joins"]) for e in (a, b) if e.get("joins")))
    json.dump(pipes, open(argv[1] + ".pipes.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if png:
        render(pts, scene, pipes, png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
