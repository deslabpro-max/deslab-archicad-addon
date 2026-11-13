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
from .views import _frame, _wall_coords, model_points

CELL = 0.01        # plan and cross-section cell, m
LEVEL = 0.05       # step along the pipe, m


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
    tall = levels >= max(int(min_span / LEVEL * fill), 4)
    labels, count = label(tall)
    found = []
    for k in range(1, count + 1):
        ys, xs = np.nonzero(labels == k)
        w, h = (xs.max() - xs.min() + 1) * CELL, (ys.max() - ys.min() + 1) * CELL
        if max(w, h) > max_size or len(ys) < 2:
            continue
        cx, cy = x0 + (xs.min() + xs.max() + 1) / 2 * CELL, y0 + (ys.min() + ys.max() + 1) / 2 * CELL
        if _in_wall_body(scene, cx, cy):
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
            wide = p[near][:, 0 if w >= h else 1]
            d = max(float(np.percentile(wide, 98) - np.percentile(wide, 2)), 0.012)
            cx = float((np.percentile(p[near][:, 0], 98) + np.percentile(p[near][:, 0], 2)) / 2)
            cy = float((np.percentile(p[near][:, 1], 98) + np.percentile(p[near][:, 1], 2)) / 2)
        z_lo = 0.0 if z_lo < 0.2 else z_lo
        z_hi = height if z_hi > height - 0.2 else z_hi
        found.append({"kind": "riser", "diameter": round(d, 3),
                      "path": [{"x": round(float(cx), 4), "y": round(float(cy), 4), "z": round(float(z_lo), 3)},
                               {"x": round(float(cx), 4), "y": round(float(cy), 4), "z": round(float(z_hi), 3)}],
                      "fill": round(float(column[zs.min():zs.max() + 1].mean()), 2)})
    return found


def find_runs(pts, scene, reach=0.6, min_length=0.3, max_size=0.2):
    """Pipes along the walls: per wall face, spots of its cross-section (height x distance from the face) up to 20 cm
    across that are filled along 0.3 m of the wall or more."""
    height = float(scene["levels"]["ceiling"])
    found = []
    for wall in scene.get("walls", []):
        a, u, n, length = _frame(wall)
        if length < 0.4:
            continue
        t, d, _ = _wall_coords(pts, wall)
        half = float(wall["thickness"]) / 2
        for side in (1, -1):
            depth = side * d - half
            m = (t > 0) & (t < length) & (depth > 0.012) & (depth < reach) & (pts[:, 2] > 0.04) & (pts[:, 2] < height - 0.04)
            if m.sum() < 50:
                continue
            it = (t[m] / LEVEL).astype(np.int32)
            idp = (depth[m] / CELL).astype(np.int32)
            iz = (pts[m, 2] / CELL).astype(np.int32)
            nt, nd, nz = int(length / LEVEL) + 1, int(reach / CELL) + 1, int(height / CELL) + 1
            occ = np.zeros((nt, nz, nd), dtype=bool)
            occ[np.clip(it, 0, nt - 1), np.clip(iz, 0, nz - 1), np.clip(idp, 0, nd - 1)] = True
            grown = _grow(occ)
            along = grown.sum(axis=0)
            labels, count = label(along >= int(min_length / LEVEL))
            for k in range(1, count + 1):
                zs, ds = np.nonzero(labels == k)
                tall, deep = (zs.max() - zs.min() + 1) * CELL, (ds.max() - ds.min() + 1) * CELL
                if tall > max_size or deep > max_size or len(zs) < 2:
                    continue
                line = grown[:, zs.min():zs.max() + 1, ds.min():ds.max() + 1].any(axis=(1, 2))
                bins = np.nonzero(line)[0]
                runs, start, prev = [], bins[0], bins[0]
                for b in bins[1:]:
                    if b - prev > 3:
                        runs.append((start, prev))
                        start = b
                    prev = b
                runs.append((start, prev))
                dia = max(tall - 2 * CELL - CELL / 2, 0.012)
                zc = (zs.min() + zs.max() + 1) / 2 * CELL
                zz, dd = pts[m, 2], depth[m]
                near = (zz >= zs.min() * CELL) & (zz <= (zs.max() + 1) * CELL) & (dd >= ds.min() * CELL) & (dd <= (ds.max() + 1) * CELL)
                if near.sum() >= 30:
                    lo, hi = np.percentile(zz[near], 2), np.percentile(zz[near], 98)
                    dia, zc = max(float(hi - lo), 0.012), float((lo + hi) / 2)
                # the front of the pipe is seen: its axis is half a diameter behind the nearest points
                dc = min(ds.min() * CELL + CELL + dia / 2, (ds.min() + ds.max() + 1) / 2 * CELL + dia / 2)
                for b0, b1 in runs:
                    t0, t1 = b0 * LEVEL, (b1 + 1) * LEVEL
                    if t1 - t0 < min_length:
                        continue
                    p0 = a + u * t0 + n * side * (half + dc)
                    p1 = a + u * t1 + n * side * (half + dc)
                    found.append({"kind": "run", "diameter": round(dia, 3), "wallId": wall["id"],
                                  "side": "left" if side > 0 else "right",
                                  "path": [{"x": round(float(p0[0]), 4), "y": round(float(p0[1]), 4), "z": round(float(zc), 3)},
                                           {"x": round(float(p1[0]), 4), "y": round(float(p1[1]), 4), "z": round(float(zc), 3)}]})
    return found


def _hint(pipe, height):
    d = pipe["diameter"]
    z = pipe["path"][0]["z"]
    if pipe["kind"] == "riser":
        return "sewer riser?" if d >= 0.08 else "heating or water riser?"
    if d >= 0.08:
        return "vent duct?" if z > height - 0.7 else "sewer pipe?"
    return "heating or water pipe?"


def join_pipes(pipes, reach=0.15):
    """A run that ends at a riser ends on the riser's axis; two runs that end at one point (a corner) share it."""
    risers = [p for p in pipes if p["kind"] == "riser"]
    runs = [p for p in pipes if p["kind"] == "run"]
    for run in runs:
        for end in (run["path"][0], run["path"][-1]):
            best = None
            for r in risers:
                a, b = r["path"]
                if not a["z"] - 0.05 <= end["z"] <= b["z"] + 0.05:
                    continue
                gap = math.hypot(end["x"] - a["x"], end["y"] - a["y"])
                if gap <= reach + r["diameter"] and (best is None or gap < best[0]):
                    best = (gap, r)
            if best:
                end["x"], end["y"] = best[1]["path"][0]["x"], best[1]["path"][0]["y"]
                end["joins"] = best[1].get("id")
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
    pipes = find_risers(pts, scene) + find_runs(pts, scene)
    pipes.sort(key=lambda p: (p["kind"] != "riser", round(p["path"][0]["x"], 1), round(p["path"][0]["y"], 1), p["path"][0]["z"]))
    for k, p in enumerate(pipes, 1):
        p["id"] = f"P{k}"
        p["hint"] = _hint(p, height)
    return join_pipes(pipes)


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
        if x0 < x < x1 and y0 < y < y1:
            img.putpixel((int((x - x0) * scale), int((y1 - y) * scale)), (170, 170, 170))
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
