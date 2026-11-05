"""deslab: engineering items and wall tracing on the measured plan (Игорь 05.11.2026, kv94 and JK):
vent shafts, sewer and water risers, radiators with their sizes, an electrical panel, and the
first row of blocks of new walls (tracing), which marks the wet points.

Scene keys (all optional):
  "mep": [
    {"kind": "shaft", "id": "SH1", "polygon": [...], "thickness": 0.1, "label": "Вытяжка"},
        the OUTER outline of the shaft box; its edges that lie on a room face (the shaft stands
        against that wall) get no wall, the others get a wall of `thickness` inside the outline;
    {"kind": "sewerRiser" | "waterRiser", "id": "K1", "at": {x, y}, "diameter": 0.16, "label": "..."},
    {"kind": "radiator", "id": "RD1", "start": {x, y}, "end": {x, y}, "depth": 0.07, "gap": 0.015,
        "height": 0.6, "elevation": 0.1}, start-end on the wall face, the radiator on its room side;
    {"kind": "panel", ...the same as a radiator..., "label": "Щиток электрический"}
  ]
  "tracing": [{"id": "T1", "start": {x, y}, "end": {x, y}, "thickness": 0.08, "height": 0.25}]
        the axis of a first row of blocks (the helper finds them in the cloud)
"""

from .obmer import (_add, _box_hits, _dot, _inside, _left, _len, _mul, _pt, _rect_of_segment, _seg_hits_box,
                    _sub, _unit, _xy, polygon_area)

import math

RISERS = ("sewerRiser", "waterRiser")
WALL_ITEMS = ("radiator", "panel")


def _rooms(scene):
    return [[_pt(p) for p in r["polygon"]] for r in scene.get("rooms", [])]


def _in_rooms(rooms, p):
    return any(_inside(r, p) for r in rooms)


def _on_room_face(rooms, a, b, tol=0.005):
    """True when the segment a-b lies on an edge of a room outline (the shaft stands on that wall)."""
    u = _unit(_sub(b, a))
    for poly in rooms:
        for i, p in enumerate(poly):
            q = poly[(i + 1) % len(poly)]
            if _len(_sub(q, p)) < 1e-9:
                continue
            v = _unit(_sub(q, p))
            if abs(_dot(u, v)) < 0.999:
                continue
            n = _left(v)
            if abs(_dot(_sub(a, p), n)) > tol or abs(_dot(_sub(b, p), n)) > tol:
                continue
            lo, hi = sorted((0.0, _dot(_sub(q, p), v)))
            ta, tb = _dot(_sub(a, p), v), _dot(_sub(b, p), v)
            if lo - tol <= min(ta, tb) and max(ta, tb) <= hi + tol:
                return True
    return False


def _meet(a1, u1, a2, u2):
    den = u1[0] * u2[1] - u1[1] * u2[0]
    if abs(den) < 1e-9:
        return None
    w = _sub(a2, a1)
    t = (w[0] * u2[1] - w[1] * u2[0]) / den
    return _add(a1, _mul(u1, t))


def shaft_walls(scene, height=None):
    """Walls of the shaft boxes: inside the outer outline, none on an edge that lies on a room face."""
    rooms = _rooms(scene)
    height = height or scene.get("levels", {}).get("ceiling") or 2.7
    walls = []
    for s in (m for m in scene.get("mep", []) if m.get("kind") == "shaft"):
        poly = [_pt(p) for p in s["polygon"]]
        n = len(poly)
        ccw = polygon_area(poly) > 0
        t = float(s.get("thickness", 0.1))
        lines = []
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            u = _unit(_sub(b, a))
            inward = _left(u) if ccw else _mul(_left(u), -1)
            on_wall = _on_room_face(rooms, a, b)
            # the axis half a thickness in; an edge on a room wall stays on its face
            lines.append((_add(a, _mul(inward, 0 if on_wall else t / 2)), u, on_wall))
        k = 0
        for i in range(n):
            base, u, on_wall = lines[i]
            if on_wall:
                continue
            pb, pu, _ = lines[i - 1]
            nb, nu, _ = lines[(i + 1) % n]
            start = _meet(pb, pu, base, u) or base
            end = _meet(base, u, nb, nu) or _add(base, _mul(u, _len(_sub(poly[(i + 1) % n], poly[i]))))
            k += 1
            walls.append({"id": f"{s['id']}.{k}", "start": _xy(start), "end": _xy(end), "thickness": t,
                          "height": float(s.get("height", height)), "kind": "partition", "shaft": s["id"],
                          "openings": []})
    return walls


def tracing_walls(scene):
    return [{"id": t["id"], "start": t["start"], "end": t["end"], "thickness": float(t.get("thickness", 0.08)),
             "height": float(t.get("height", 0.25)), "kind": "tracing", "openings": []}
            for t in scene.get("tracing", [])]


def _wall_item_frame(rooms, item):
    """Origin and angle of a radiator / panel: local x along start-end, local y into the room
    (as «Панельный Радиатор 27» stands in the hand-drawn kv94: angle π on the top wall, π/2 on
    the right one, the insertion point at its right end seen from the room)."""
    a, b = _pt(item["start"]), _pt(item["end"])
    u = _unit(_sub(b, a))
    n = _left(u)
    mid = _mul(_add(a, b), 0.5)
    if not _in_rooms(rooms, _add(mid, _mul(n, 0.2))):
        n = _mul(n, -1)
    # local x = u' with left(u') = n
    ux = (n[1], -n[0])
    lo, hi = sorted((a, b), key=lambda p: _dot(p, ux))
    gap = float(item.get("gap", 0.015))
    return _add(lo, _mul(n, gap)), math.atan2(ux[1], ux[0]), ux, n, _len(_sub(hi, lo))


def mep_objects(scene, profile):
    """CreateObjects data and the GDL parameters to set after, one per item."""
    cfg = profile.get("mep", {})
    rooms = _rooms(scene)
    height = scene.get("levels", {}).get("ceiling") or 2.7
    out = []
    for m in scene.get("mep", []):
        kind = m.get("kind")
        if kind in RISERS:
            d = float(m.get("diameter", 0.16 if kind == "sewerRiser" else 0.05))
            c = _pt(m["at"])
            lib = cfg.get("riserPart", "Цилиндр 27")
            out.append((m, {"libraryPartName": lib, "coordinates": {"x": c[0], "y": c[1], "z": 0.0},
                            "dimensions": {"x": d, "y": d, "z": float(m.get("height", height))}},
                        [{"name": "radius_1", "value": d / 2}, {"name": "length", "value": float(m.get("height", height))}]))
        elif kind in WALL_ITEMS:
            origin, angle, _, _, length = _wall_item_frame(rooms, m)
            depth = float(m.get("depth", 0.07 if kind == "radiator" else 0.158))
            h = float(m.get("height", 0.6 if kind == "radiator" else 0.4))
            z = float(m.get("elevation", 0.1 if kind == "radiator" else 1.6))
            lib = cfg.get("radiatorPart" if kind == "radiator" else "panelPart",
                          "Панельный Радиатор 27" if kind == "radiator" else "Электрощиты 21")
            out.append((m, {"libraryPartName": lib, "coordinates": {"x": origin[0], "y": origin[1], "z": z},
                            "dimensions": {"x": length, "y": depth, "z": h}, "angle": angle}, []))
    return out


def _chain(pts, u, normal, ref, room_id, key, offset, extra="item"):
    """An extra chain along u through pts, its line through ref."""
    kept = []
    for p in sorted(pts, key=lambda p: _dot(p, u)):
        if not kept or abs(_dot(_sub(p, kept[-1]), u)) > 0.002:
            kept.append(p)
    if len(kept) < 2:
        return None
    return {"room": room_id, "edge": key, "edges": [], "direction": _xy(u), "normal": _xy(normal),
            "referencePoint": _xy(ref), "offset": offset, "points": [_xy(p) for p in kept], "extra": extra}


def _nearest_room(scene, p):
    for r in scene.get("rooms", []):
        if _inside([_pt(q) for q in r["polygon"]], p):
            return r
    return None


def _room_edges(room):
    poly = [_pt(p) for p in room["polygon"]]
    ccw = polygon_area(poly) > 0
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        if _len(_sub(b, a)) < 1e-9:
            continue
        u = _unit(_sub(b, a))
        yield i, a, b, u, (_left(u) if ccw else _mul(_left(u), -1))


def _edge_of(room, a, b, tol=0.02):
    """The room edge the segment a-b lies on (within tol), with the room's inward normal."""
    for i, p, q, u, n in _room_edges(room):
        if abs(abs(_dot(_unit(_sub(b, a)), u)) - 1) > 1e-3:
            continue
        if abs(_dot(_sub(a, p), n)) > tol or abs(_dot(_sub(b, p), n)) > tol:
            continue
        L = _dot(_sub(q, p), u)
        if -tol <= min(_dot(_sub(a, p), u), _dot(_sub(b, p), u)) and max(_dot(_sub(a, p), u), _dot(_sub(b, p), u)) <= L + tol:
            return i, p, q, u, n
    return None


def plan_extra_chains(scene, profile, chains):
    """Dimension chains of the engineering items and of the tracing:
    - per room edge with radiators / panels / shafts against it: one chain corner to corner through
      their ends, one step deeper than the edge's own chain (Игорь: the radiators have sizes);
    - per shaft: its depth from the wall and its sides that face the room;
    - per tracing strip: its length and its distance from the nearest parallel room face."""
    cfg = profile["dimensions"]
    step = cfg.get("offsetStep", 0.35)
    base = cfg["offsetFromFace"]
    band = profile["labels"].get("dimensionBand", 0.18)
    rooms = _rooms(scene)
    out = []
    # radiators and panels: per room edge, corner to corner through their ends
    on_edge = {}
    along_wall = set()  # (shaft, axis) whose extent is in the chain of its wall already
    for m in scene.get("mep", []):
        if m.get("kind") in WALL_ITEMS:
            a, b = _pt(m["start"]), _pt(m["end"])
            for room in scene.get("rooms", []):
                e = _edge_of(room, a, b)
                if e:
                    on_edge.setdefault((room["id"], e[0]), (room, e, []))[2].extend([a, b])
                    break
        elif m.get("kind") == "shaft":
            # a shaft: its extent along the nearest parallel room face up to 1 m off (its place
            # from the corners, kv94: «2842 от верхней стены»)
            poly = [_pt(p) for p in m["polygon"]]
            box = (min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly))
            centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            room = _nearest_room(scene, centre)
            if room is None:
                continue
            best = None
            for i, p, q, u, n in _room_edges(room):
                if abs(u[0]) > 0.999 or abs(u[1]) > 0.999:
                    axis = 0 if abs(u[0]) > 0.999 else 1  # the face runs along x (axis 0) or y
                    lo, hi = box[axis], box[axis + 2]
                    e_lo, e_hi = sorted((p[axis], q[axis]))
                    if lo < e_lo - 0.005 or hi > e_hi + 0.005:
                        continue
                    other = 1 - axis
                    gap = min(abs(box[other] - p[other]), abs(box[other + 2] - p[other]))
                    if gap > 1.0:
                        continue
                    if best is None or gap < best[0]:
                        pa = (lo, p[1]) if axis == 0 else (p[0], lo)
                        pb = (hi, p[1]) if axis == 0 else (p[0], hi)
                        best = (gap, room, (i, p, q, u, n), [pa, pb])
            if best:
                gap, room, e, pts = best
                on_edge.setdefault((room["id"], e[0]), (room, e, []))[2].extend(pts)
                along_wall.add((m["id"], 0 if abs(e[3][0]) > 0.999 else 1))
    for (rid, _), (room, (i, p, q, u, n), pts) in sorted(on_edge.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        own = [c for c in chains if c["room"] == rid and i in c.get("edges", [c["edge"]])]
        off = (max(c["offset"] for c in own) if own else base) + max(step, 2 * band + 0.02)
        ch = _chain([p, q] + pts, u, n, _add(p, _mul(n, off)), rid, f"оборуд.{i}", off, extra="edge")
        if ch:
            out.append(ch)
    # shafts: each side that faces the room, and the distance to the nearest room face square to
    # it when the shaft does not stand on a wall on that axis (kv94: «2842 от верхней стены»)
    for m in (m for m in scene.get("mep", []) if m.get("kind") == "shaft"):
        poly = [_pt(p) for p in m["polygon"]]
        box = (min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly))
        centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        room = _nearest_room(scene, centre)
        if room is None:
            continue
        rpoly = [_pt(q) for q in room["polygon"]]
        for axis in (0, 1):
            if (m["id"], axis) in along_wall:
                continue
            other = 1 - axis
            u = (1.0, 0.0) if axis == 0 else (0.0, 1.0)
            lo, hi = box[axis], box[axis + 2]
            faces = []
            for k, a in enumerate(rpoly):
                b = rpoly[(k + 1) % len(rpoly)]
                if abs(a[axis] - b[axis]) < 1e-6 and min(a[other], b[other]) - 1e-6 <= centre[other] <= max(a[other], b[other]) + 1e-6:
                    faces.append(a[axis])
            touches = any(abs(f - lo) < 0.005 or abs(f - hi) < 0.005 for f in faces)
            extra = []
            if not touches:  # free-standing: its gap to the nearest face square to u, up to 1 m
                below = [f for f in faces if f < lo - 0.005 and lo - f <= 1.0]
                above = [f for f in faces if f > hi + 0.005 and f - hi <= 1.0]
                cands = ([(lo - max(below), max(below))] if below else []) + ([(min(above) - hi, min(above))] if above else [])
                if cands:
                    extra.append(min(cands)[1])
            for sgn in (-1, 1):
                side = box[other] if sgn < 0 else box[other + 2]
                vals = sorted({round(p[axis], 4) for p in poly if abs(p[other] - side) < 1e-6} | set(extra))
                if len(vals) < 2:
                    continue
                line = side + sgn * 0.25
                probe = [(t, line) if axis == 0 else (line, t) for t in (vals[0] + 0.02, (vals[0] + vals[-1]) / 2, vals[-1] - 0.02)]
                if not all(_inside(rpoly, pp) for pp in probe):
                    continue
                nrm = (0.0, float(sgn)) if axis == 0 else (float(sgn), 0.0)
                pts = [(t, side) if axis == 0 else (side, t) for t in vals]
                ref = (vals[0], line) if axis == 0 else (line, vals[0])
                ch = _chain(pts, u, nrm, ref, room["id"], f"{m['id']}.{'xy'[axis]}", 0.25)
                if ch:
                    out.append(ch)
                break
    # tracing: length and the distance from the nearest parallel room face
    for t in scene.get("tracing", []):
        a, b = _pt(t["start"]), _pt(t["end"])
        u = _unit(_sub(b, a))
        if abs(u[0]) < abs(u[1]):
            u = (0.0, 1.0 if u[1] > 0 else -1.0)
        else:
            u = (1.0 if u[0] > 0 else -1.0, 0.0)
        a, b = sorted((a, b), key=lambda p: _dot(p, u))
        u = _unit(_sub(b, a))
        n = _left(u)
        half = float(t.get("thickness", 0.08)) / 2
        mid = _mul(_add(a, b), 0.5)
        room = _nearest_room(scene, _add(mid, _mul(n, half + 0.05))) or _nearest_room(scene, _add(mid, _mul(n, -half - 0.05)))
        if room is None:
            continue
        side = n if _in_rooms(rooms, _add(mid, _mul(n, half + 0.3))) else _mul(n, -1)
        ref = _add(a, _mul(side, half + 0.3))
        ch = _chain([a, b], u, side, ref, room["id"], f"{t['id']}.len", half + 0.3)
        if ch:
            out.append(ch)
        # nearest parallel face of the room on either side, within 3 m
        best = None
        for i, p, q, eu, en in _room_edges(room):
            if abs(abs(_dot(eu, u)) - 1) > 1e-3:
                continue
            d = _dot(_sub(mid, p), en)
            along = _dot(_sub(mid, p), eu)
            if d <= half or along < -0.01 or along > _len(_sub(q, p)) + 0.01:
                continue
            if best is None or d < best[0]:
                best = (d, en)
        if best:
            d, en = best
            # the chain at the middle of the strip, square to it, from the face to both block faces
            face = _sub(mid, _mul(en, d))
            near = _sub(mid, _mul(en, half))
            far = _add(mid, _mul(en, half))
            pos = _add(mid, _mul(u, min(0.4, _len(_sub(b, a)) / 4)))
            ch = _chain([face, near, far], en, u, pos, room["id"], f"{t['id']}.pos", 0.0)
            if ch:
                out.append(ch)
    return out


def mep_blockers(scene):
    """Boxes the room chains keep off (a chain must not run through a shaft box)."""
    out = []
    for m in scene.get("mep", []):
        if m.get("kind") == "shaft":
            poly = [_pt(p) for p in m["polygon"]]
            out.append((min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly)))
    return out


def plan_mep_labels(scene, profile, obstacles, taken=()):
    """A one-line label for every item with a "label" (and every tracing strip, «Трассировка»):
    the dot on the item, a 45° leader into the free room and a horizontal shelf; of eight places
    the one clear of walls, dimension lines, texts and the labels placed before."""
    cfg = profile["labels"]
    k = cfg.get("drawingScale", 45) / 1000.0
    line = cfg.get("lineMm", 4.32) * k
    rooms = _rooms(scene)
    items = []
    for m in scene.get("mep", []):
        if not m.get("label"):
            continue
        if m.get("kind") == "shaft":
            poly = [_pt(p) for p in m["polygon"]]
            dot = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
        elif m.get("kind") in RISERS:
            dot = _pt(m["at"])
        else:
            dot = _mul(_add(_pt(m["start"]), _pt(m["end"])), 0.5)
        items.append((m["id"], m["label"], dot))
    tr_text = profile.get("mep", {}).get("tracingLabel", "Трассировка h={height_mm}")
    for t in scene.get("tracing", []):
        dot = _mul(_add(_pt(t["start"]), _pt(t["end"])), 0.5)
        items.append((t["id"], tr_text.format(height_mm=int(round(float(t.get("height", 0.25)) * 1000))), dot))
    placed = list(taken)
    labels = []
    for ident, text, dot in items:
        width = (len(text) * cfg.get("charMm", 1.4) + cfg.get("padMm", 1.5)) * k
        best = None
        for length in (0.5, 0.8, 1.1, 1.4, 1.8):
            for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (0, 1), (0, -1), (1, 0), (-1, 0)):
                d = _unit((dx, dy))
                mid = _add(dot, _mul(d, length))
                for way in ((1.0, -1.0) if dx >= 0 else (-1.0, 1.0)):
                    x0 = mid[0] + 0.01 if way > 0 else mid[0] - 0.01 - width
                    box = (x0, mid[1], x0 + width, mid[1] + line)
                    cost = length * 10 + (5 if dx and way != dx else 0) + (3 if not dx or not dy else 0)
                    cost += 100 * sum(_box_hits(box, w) for w in obstacles["walls"])
                    cost += 100 * sum(_box_hits(box, w) for w in obstacles["dims"] + obstacles["marks"] + placed)
                    cost += 40 * sum(_seg_hits_box(_add(dot, _mul(d, 0.15)), mid, w) for w in obstacles["walls"])
                    cost += 20 * sum(_seg_hits_box(dot, mid, w) for w in placed)
                    if not all(_in_rooms(rooms, c) for c in ((box[0], box[1]), (box[2], box[3]))):
                        cost += 150  # the labels stay inside the flat
                    if best is None or cost < best[0]:
                        best = (cost, mid, way, box)
        cost, mid, way, box = best
        placed.append(box)
        labels.append({"item": ident, "runs": [{"text": text}], "begCoordinate": _xy(dot), "midCoordinate": _xy(mid),
                       "endCoordinate": _xy(_add(mid, (way * 0.01, 0.0))), "box": box, "cost": round(cost, 1)})
    return labels
