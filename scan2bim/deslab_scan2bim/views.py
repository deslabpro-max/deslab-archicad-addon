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
            m = (t > lo) & (t < hi) & (np.abs(d) < half + 0.02) & (pts[:, 2] > 0.02) & (pts[:, 2] < max_height)
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
            if height < 0.04 or height > max_height - step:
                continue
            head = float(o.get("sill", 0)) + float(o["height"])
            o["sill"] = height
            o["height"] = round(head - height, 3)
            o["threshold"] = height
            notes.append(f"{o['id']}: balcony door threshold {int(round(height * 1000))} mm from the cloud")
    return notes


def _without_other_walls(pts, scene, wall):
    """Points that are not the body of another wall (a wall that meets this one would stand out of its face)."""
    keep = np.ones(len(pts), dtype=bool)
    for other in scene.get("walls", []):
        if other is wall:
            continue
        t, d, length = _wall_coords(pts, other)
        keep &= ~((np.abs(d) < float(other["thickness"]) / 2 + 0.04) & (t > -0.05) & (t < length + 0.05))
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
            out = front >= min_depth
            # one gap cell does not split a thing
            grown = out.copy()
            grown[:, 1:] |= out[:, :-1]
            grown[:, :-1] |= out[:, 1:]
            grown[1:, :] |= out[:-1, :]
            grown[:-1, :] |= out[1:, :]
            labels, count = label(grown)
            for k in range(1, count + 1):
                ys, xs = np.nonzero((labels == k) & out)
                if len(ys) < 12:
                    continue
                t0, t1 = float(xs.min()) * cell, float(xs.max() + 1) * cell
                z0, z1 = float(ys.min()) * cell, float(ys.max() + 1) * cell
                width, tall = t1 - t0, z1 - z0
                if width * tall < 0.02 or max(width, tall) < 0.25:
                    continue
                if z1 < 0.16 and tall < 0.14:
                    continue   # a skirting board
                stand = float(np.percentile(front[ys, xs], 80))
                fill = len(ys) / max(1.0, (width / cell) * (tall / cell))
                mid = (t0 + t1) / 2
                at = a + u * mid + n * side * (half + stand / 2)
                found.append({
                    "wallId": wall["id"], "side": "left" if side > 0 else "right",
                    "from": round(t0, 3), "to": round(t1, 3), "bottom": round(z0, 3), "top": round(z1, 3),
                    "depth": round(stand, 3), "fill": round(fill, 2),
                    "at": {"x": round(float(at[0]), 4), "y": round(float(at[1]), 4)},
                    "start": {"x": round(float((a + u * t0 + n * side * half)[0]), 4), "y": round(float((a + u * t0 + n * side * half)[1]), 4)},
                    "end": {"x": round(float((a + u * t1 + n * side * half)[0]), 4), "y": round(float((a + u * t1 + n * side * half)[1]), 4)},
                    "hint": _hint(width, z0, z1, stand, height),
                })
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
