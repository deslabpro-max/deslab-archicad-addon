"""deslab: engineering items and wall tracing on the measured plan (Игорь 05.11.2026, kv94 and JK):
vent shafts, sewer and water risers, radiators with their sizes, an electrical panel, and the
first row of blocks of new walls (tracing), which marks the wet points.

Scene keys (all optional):
  "mep": [
    {"kind": "shaft", "id": "SH1", "polygon": [...], "thickness": 0.1, "label": "Вытяжка"},
        (optional "walls": [[{x, y}, {x, y}], ...]: the wall axes as drawn, instead of the outline rule),
        the OUTER outline of the shaft box; its edges that lie on a room face (the shaft stands
        against that wall) get no wall, the others get a wall of `thickness` inside the outline;
    {"kind": "sewerRiser" | "waterRiser", "id": "K1", "at": {x, y}, "diameter": 0.16, "label": "..."},
    {"kind": "radiator", "id": "RD1", "start": {x, y}, "end": {x, y}, "depth": 0.07, "gap": 0.015,
        "height": 0.6, "elevation": 0.1}, start-end on the wall face, the radiator on its room side;
    {"kind": "panel", ...the same as a radiator..., "label": "Щиток"}
  ]
  "tracing": [{"id": "T1", "start": {x, y}, "end": {x, y}, "thickness": 0.08, "height": 0.25}]
        the axis of a first row of blocks (the helper finds them in the cloud)
"""

from .obmer import (_add, _box_hits, _seg_box, _dot, _inside, _left, _len, _mul, _pt, _rect_of_segment, _seg_hits_box,
                    _sub, _unit, _xy, polygon_area)

import math

RISERS = ("sewerRiser", "waterRiser", "heatingRiser")  # deslab: 13.11.2026 heating risers stand in the flat (Nagornaya)
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
        t = float(s.get("thickness", 0.1))
        if s.get("walls"):
            # deslab: the shaft's walls given by their axes, as drawn in Игорь's example (kv94: the
            # sewer box of the left shaft is open to the room at the top, the water risers stand
            # inside it, not on a wall, 06.11.2026)
            own = []
            for k, (a, b) in enumerate(s["walls"], 1):
                own.append({"id": f"{s['id']}.{k}", "start": _xy(_pt(a)), "end": _xy(_pt(b)), "thickness": t,
                            "height": float(s.get("height", height)), "kind": "partition", "shaft": s["id"],
                            "openings": []})
            walls.extend(snap_joints(own))
            continue
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
        # inner walls between the boxes of one shaft (kv94: risers box and vent box), by their axes
        for a, b in s.get("partitions", []):
            k += 1
            walls.append({"id": f"{s['id']}.{k}", "start": _xy(_pt(a)), "end": _xy(_pt(b)), "thickness": t,
                          "height": float(s.get("height", height)), "kind": "partition", "shaft": s["id"],
                          "openings": []})
    # deslab: the polygon walls and inner partitions of one shaft also meet on their axes
    out = []
    for sid in dict.fromkeys(w["shaft"] for w in walls):
        out.extend(snap_joints([w for w in walls if w["shaft"] == sid]))
    return out


def snap_joints(walls, tol=None):
    """deslab: Archicad joins walls only where an end lies on the other wall's axis (Игорь 06.11.2026:
    the kv94 left shaft came out as separate boxes, the ends stopped on a face or ran past it).
    An end within one thickness of a crossing wall's axis, near that wall, is moved along its own
    wall onto that axis: a T joint, or an L corner when both ends meet there."""
    out = [dict(w) for w in walls]
    for w in out:
        for key in ("start", "end"):
            p = _pt(w[key])
            a, b = _pt(w["start"]), _pt(w["end"])
            u = _unit(_sub(b, a))
            best = None
            for o in out:
                if o is w:
                    continue
                oa, ob = _pt(o["start"]), _pt(o["end"])
                ou = _unit(_sub(ob, oa))
                if abs(_dot(u, ou)) > 0.01:
                    continue
                reach = tol if tol is not None else max(w["thickness"], o["thickness"])
                q = _meet(p, u, oa, ou)
                if q is None:
                    continue
                shift = _len(_sub(q, p))
                along = _dot(_sub(q, oa), ou)
                olen = _len(_sub(ob, oa))
                if shift > reach + 1e-6 or along < -reach - 1e-6 or along > olen + reach + 1e-6:
                    continue
                if best is None or shift < best[0]:
                    best = (shift, q)
            if best and best[0] > 1e-6:
                w[key] = _xy(best[1])
    # a crossing wall whose end now sits past the other axis (L corner) stays as it is
    return out


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
            d = float(m.get("diameter", 0.16 if kind == "sewerRiser" else 0.05 if kind == "waterRiser" else 0.03))
            c = _pt(m["at"])
            lib = cfg.get("riserPart", "Цилиндр 27")
            out.append((m, {"libraryPartName": lib, "coordinates": {"x": c[0], "y": c[1], "z": 0.0},
                            "dimensions": {"x": d, "y": d, "z": float(m.get("height", height))}},
                        [{"name": "radius_1", "value": d / 2}, {"name": "length", "value": float(m.get("height", height))}]))
        elif kind in WALL_ITEMS:
            if kind == "panel" and not cfg.get("panelPart"):
                continue  # drawn as its outline (mep_outlines)
            origin, angle, _, _, length = _wall_item_frame(rooms, m)
            depth = float(m.get("depth", 0.07 if kind == "radiator" else 0.158))
            h = float(m.get("height", 0.6 if kind == "radiator" else 0.4))
            z = float(m.get("elevation", 0.1 if kind == "radiator" else 1.6))
            lib = cfg.get("radiatorPart" if kind == "radiator" else "panelPart",
                          "Панельный Радиатор 27" if kind == "radiator" else "Электрощиты 21")
            # the sizes as GDL A/B/ZZYZX too: «Электрощиты 21» ignores the element size (run kv94h,
            # 05.11.2026: a 0.5 x 0.5 m panel instead of 0.321 x 0.158)
            out.append((m, {"libraryPartName": lib, "coordinates": {"x": origin[0], "y": origin[1], "z": z},
                            "dimensions": {"x": length, "y": depth, "z": h}, "angle": angle},
                        [{"name": "A", "value": length}, {"name": "B", "value": depth}, {"name": "ZZYZX", "value": h}]))
    # the line pen of the drawing (Игорь: the windows' pen; run kv94i: radiators purple, risers green)
    pen = cfg.get("pen")
    if pen:
        for m, d, params in out:
            d["pen"] = pen
            params.append({"name": "gs_cont_pen", "value": pen})
    return out


PIPE_LABELS = {"heating": "Отопление", "water": "Вода", "sewer": "Канализация", "vent": "Вентиляция"}


def pipe_objects(scene, profile):
    """deslab: Игорь 13.11.2026, «научиться строить коммуникации, вентиляционные трубы, трубы отопления… по диаметрам, по
    направлению то, как это сделано в облаке точек». Scene "pipes": [{"id", "system": heating|water|sewer|vent,
    "diameter", "path": [{x, y, z}, …]}]: every leg of the path is a «Цилиндр 27» set by the two ends of its axis (its
    «Координаты» mode; checked to the millimetre in Archicad 27, where the MEP tools cannot be reached by an add-on). At a
    bend both legs run half a diameter on, so the corner is closed. Returns (pipe, CreateObjects data, GDL parameters)."""
    cfg = profile.get("mep", {})
    lib = cfg.get("pipePart", "Цилиндр 27")
    out = []
    for pipe in scene.get("pipes", []):
        r = float(pipe.get("diameter", 0.025)) / 2
        pts = [(float(p["x"]), float(p["y"]), float(p.get("z", 0.0))) for p in pipe.get("path", [])]
        for k, (a, b) in enumerate(zip(pts, pts[1:])):
            d = tuple(b[i] - a[i] for i in range(3))
            length = math.sqrt(sum(v * v for v in d))
            if length < 1e-4:
                continue
            u = tuple(v / length for v in d)
            if k > 0:
                a = tuple(a[i] - u[i] * r for i in range(3))
            if k < len(pts) - 2:
                b = tuple(b[i] + u[i] * r for i in range(3))
            out.append((pipe, {"libraryPartName": lib, "coordinates": {"x": a[0], "y": a[1], "z": a[2]}},
                        [{"name": "iEditMode", "value": 2}, {"name": "radius_1", "value": r},
                         {"name": "dist_x", "value": b[0] - a[0]}, {"name": "dist_y", "value": b[1] - a[1]},
                         {"name": "ZZYZX", "value": b[2] - a[2]}]))
    pen = cfg.get("pen")
    if pen:
        for _, d, params in out:
            d["pen"] = pen
            params.append({"name": "gs_cont_pen", "value": pen})
    return out


def pipe_label_items(scene):
    """One label per pipe that has a "label": the dot at its "labelAt" or at the first point of its path."""
    items = []
    for pipe in scene.get("pipes", []):
        if pipe.get("label") and pipe.get("path"):
            p = pipe.get("labelAt") or pipe["path"][0]
            items.append((pipe["id"], pipe["label"], (float(p["x"]), float(p["y"]))))
    return items


def mep_outlines(scene, profile):
    """A panel without a library part that takes its size («Электрощиты 21» draws 0.5 x 0.5 m
    whatever A and B are, run kv94i): its outline on the plan, a closed polyline."""
    if profile.get("mep", {}).get("panelPart"):
        return []
    rooms = _rooms(scene)
    out = []
    for m in scene.get("mep", []):
        if m.get("kind") != "panel":
            continue
        origin, _, ux, n, length = _wall_item_frame(rooms, m)
        depth = float(m.get("depth", 0.158))
        a = origin
        b = _add(a, _mul(ux, length))
        c = _add(b, _mul(n, depth))
        d = _add(a, _mul(n, depth))
        out.append([_xy(p) for p in (a, b, c, d, a)])
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
    split = profile.get("mep", {}).get("splitViews")
    for (rid, _), (room, (i, p, q, u, n), pts) in sorted(on_edge.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        own = [c for c in chains if c["room"] == rid and i in c.get("edges", [c["edge"]])]
        off = (max(c["offset"] for c in own) if own else base) + max(step, 2 * band + 0.02)
        if split:
            # deslab: on the engineering plan the radiators' chain comes FIRST, next to the wall, and
            # the overall size of the wall after it (Игорь 06.11.2026)
            off = base
            whole = _chain([p, q], u, n, _add(p, _mul(n, base + max(step, 2 * band + 0.02))), rid, f"оборуд.{i}.всего",
                           base + max(step, 2 * band + 0.02), extra="edge")
            if whole:
                out.append(whole)
        # from the nearer corner to the farthest item only (run kv94h: a chain corner to corner ran
        # through a window label at the far end)
        ts = [_dot(_sub(x, p), u) for x in pts]
        L = _dot(_sub(q, p), u)
        end = p if min(ts) <= L - max(ts) else q
        ch = _chain([end] + pts, u, n, _add(p, _mul(n, off)), rid, f"оборуд.{i}", off, extra="edge")
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
    pos_chains = []
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
            # deslab: strips placed from the same face on one line share one chain (JK v7: the chains of
            # T2 and T5, T3 and T6 lay on top of each other, Игорь 06.11.2026)
            pos_chains.append({"face": face, "near": near, "far": far, "en": en, "u": u, "pos": pos, "room": room["id"],
                               "id": t["id"]})
    groups = []
    for c in pos_chains:
        for g in groups:
            h = g[0]
            if h["room"] == c["room"] and _dot(h["en"], c["en"]) > 0.99 and _len(_sub(h["face"], c["face"])) < 0.6                     and abs(_dot(_sub(h["face"], c["face"]), h["en"])) < 0.005                     and abs(_dot(_sub(h["pos"], c["pos"]), _left(h["en"]))) < 1.0:
                g.append(c)
                break
        else:
            groups.append([c])
    for g in groups:
        h = g[0]
        pts = [h["face"]] + [p for c in g for p in (c["near"], c["far"])]
        ch = _chain(pts, h["en"], h["u"], h["pos"], h["room"], "+".join(c["id"] for c in g) if len(g) > 1 else f"{h['id']}.pos", 0.0)
        if ch:
            if len(g) > 1:
                ch["edge"] = "+".join(c["id"] for c in g) + ".pos"
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


def panel_boxes(scene):
    """deslab: the electrical panels (a room chain keeps off them when it can, kv94l-p: «2 493» ran
    across the panel)."""
    out = []
    for m in scene.get("mep", []):
        if m.get("kind") == "panel":
            a, b = _pt(m["start"]), _pt(m["end"])
            half = float(m.get("depth", 0.15))
            out.append((min(a[0], b[0]) - half, min(a[1], b[1]) - half, max(a[0], b[0]) + half, max(a[1], b[1]) + half))
    return out


def mep_marks(scene):
    """deslab: boxes no dimension text may cover: shafts, risers and the panel (run kv94l: «340»
    next to the water risers)."""
    out = mep_blockers(scene) + panel_boxes(scene)
    for m in scene.get("mep", []):
        if m.get("kind") in RISERS:
            c, r = _pt(m["at"]), float(m.get("diameter", 0.1)) / 2
            out.append((c[0] - r, c[1] - r, c[0] + r, c[1] + r))
    return out


def _segs_cross(a, b, c, d):
    """True when the segments a-b and c-d cross (touching ends do not count)."""
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2, d3, d4 = side(c, d, a), side(c, d, b), side(a, b, c), side(a, b, d)
    return d1 * d2 < -1e-12 and d3 * d4 < -1e-12


def plan_mep_labels(scene, profile, obstacles, taken=()):
    """A one-line label for every item with a "label" (and every tracing strip, «Трассировка»):
    the dot on the item, a 45° leader into the free room and a horizontal shelf; of eight places
    the one clear of walls, dimension lines, texts and the labels placed before."""
    cfg = profile["labels"]
    # deslab: "scale", not "k": the loop over the tracing groups below reused k, and with tracing
    # every label came out tens of metres wide and went behind the walls (run JK v7)
    scale = cfg.get("drawingScale", 45) / 1000.0
    line = cfg.get("lineMm", 4.32) * scale
    rooms = _rooms(scene)
    items = []
    # deslab: risers of one kind standing together (kv94 AI build: six «Стояк воды» in one shaft,
    # six piled labels) get ONE label at their middle, «Стояки воды (6)» (Игорь 06.11.2026: our call)
    risers = [m for m in scene.get("mep", []) if m.get("label") and m.get("kind") in RISERS]
    grouped = set()
    for m in risers:
        if m["id"] in grouped:
            continue
        group, todo = [m], [m]
        while todo:
            a = todo.pop()
            for b in risers:
                if b not in group and b.get("kind") == m.get("kind") and \
                        _len(_sub(_pt(a["at"]), _pt(b["at"]))) <= 0.4:
                    group.append(b)
                    todo.append(b)
        if len(group) > 1:
            grouped.update(g["id"] for g in group)
            mid = (sum(_pt(g["at"])[0] for g in group) / len(group), sum(_pt(g["at"])[1] for g in group) / len(group))
            many = {"waterRiser": "Стояки воды", "sewerRiser": "Стояки канализации", "heatingRiser": "Стояки отопления"}.get(m.get("kind"), m["label"])
            items.append((m["id"], f"{many} ({len(group)})", mid))
    for m in scene.get("mep", []):
        if not m.get("label") or m["id"] in grouped:
            continue
        if m.get("kind") == "shaft":
            poly = [_pt(p) for p in m["polygon"]]
            dot = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
        elif m.get("kind") in RISERS or m.get("kind") == "vent":  # deslab: a vent opening is a label with its dot on the wall face
            dot = _pt(m["at"])
        else:
            dot = _mul(_add(_pt(m["start"]), _pt(m["end"])), 0.5)
        items.append((m["id"], m["label"], dot))
    items += pipe_label_items(scene)
    tr_text = profile.get("mep", {}).get("tracingLabel", "Трассировка h={height_mm}")
    # one label per joined run of tracing (run JK v5: a label on each of 16 strips piled up)
    strips = scene.get("tracing", [])
    group = list(range(len(strips)))

    def root(k):
        while group[k] != k:
            k = group[k]
        return k

    def touches(a, b):
        pa, pb = _pt(a["start"]), _pt(a["end"])
        qa, qb = _pt(b["start"]), _pt(b["end"])
        return any(_seg_hits_box(qa, qb, _rect_of_segment(p, p, 0.15)) for p in (pa, pb)) or \
            any(_seg_hits_box(pa, pb, _rect_of_segment(p, p, 0.15)) for p in (qa, qb))
    for i, a in enumerate(strips):
        for j in range(i + 1, len(strips)):
            if touches(a, strips[j]):
                group[root(j)] = root(i)
    for k in sorted({root(i) for i in range(len(strips))}):
        members = [t for i, t in enumerate(strips) if root(i) == k]
        t = max(members, key=lambda t: _len(_sub(_pt(t["end"]), _pt(t["start"]))))
        dot = _mul(_add(_pt(t["start"]), _pt(t["end"])), 0.5)
        h = max(float(m.get("height", 0.25)) for m in members)
        items.append((t["id"], tr_text.format(height_mm=int(round(h * 100)) * 10), dot))
    placed = list(taken)
    # door swings (a square of the leaf width on both sides): no label text or leader over them
    # (run kv94h: «Щиток электрический» lay across the entrance door arc)
    from .obmer import _opening_points
    swings = []
    for w in scene.get("walls", []):
        for o, c, e1, e2, u, n in _opening_points(w):
            if o.get("kind") == "door":
                wd = float(o.get("width", 0.9))
                reach = float(w.get("thickness", 0.2)) / 2 + wd
                for sg in (1, -1):
                    pts = [e1, e2, _add(e1, _mul(n, sg * reach)), _add(e2, _mul(n, sg * reach))]
                    swings.append((min(p[0] for p in pts), min(p[1] for p in pts),
                                   max(p[0] for p in pts), max(p[1] for p in pts)))
    # the risers: no leader through another riser, no text on one (run kv94i)
    riser_boxes = [(m["id"], _rect_of_segment(_pt(m["at"]), _pt(m["at"]), float(m.get("diameter", 0.1)) / 2 + 0.02))
                   for m in scene.get("mep", []) if m.get("kind") in RISERS]
    # deslab: nor on a panel, its own one included (kv94: «Щиток» lay on its own panel)
    riser_boxes += [(None, bx) for bx in panel_boxes(scene)]
    labels = []
    leaders = []  # (start, end) of the leaders and shelves placed so far
    dirs = list(obstacles.get("dimDirs") or []) + [None] * len(obstacles["dims"])
    panel_ids = {m["id"] for m in scene.get("mep", []) if m.get("kind") == "panel"}
    for ident, text, dot in items:
        others = [b for i, b in riser_boxes if i != ident]
        # deslab: the width as Archicad draws it, measured on kv94l (06.11.2026, boxWidth at 1:45):
        # «Вытяжка» 13.70 mm, «Стояк канализации» 30.01, «Щиток электрический» 34.62, i.e. about
        # 1.8 mm a letter; the old estimate (1.4 x 1.15) put «Стояк канализации» over «Вытяжка»
        width = (len(text) * cfg.get("mepCharMm", 1.8) + cfg.get("mepPadMm", 1.0)) * scale
        gap = cfg.get("mepLabelGap", 0.06)  # between two labels, and a label and a dimension band
        best = None
        # the leader ends: on the eight 45° rays, and (deslab) on a 0.25 m grid around the item, so
        # that a long label finds a free spot in a crowded room (test room 4 x 3 m, JK v7)
        ends = [(length, (dx, dy)) for length in (0.5, 0.8, 1.1, 1.4, 1.8, 2.2, 2.6, 3.0, 3.5)
                for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (0, 1), (0, -1), (1, 0), (-1, 0))]
        reach = cfg.get("mepGridReach", 2.5)
        steps_ = int(reach / 0.25)
        for i in range(-steps_, steps_ + 1):
            for j in range(-steps_, steps_ + 1):
                off = (i * 0.25, j * 0.25)
                if 0.45 <= _len(off) <= reach and i and j and abs(i) != abs(j):
                    ends.append((_len(off), off))
        for length, (dx, dy) in ends:
            if True:
                d = _unit((dx, dy))
                mid = _add(dot, _mul(d, length))
                dx = (dx > 0) - (dx < 0)
                dy = (dy > 0) - (dy < 0)
                for way in (1.0, -1.0):  # deslab: both shelf directions, the back one costs more
                    x0 = mid[0] + 0.01 if way > 0 else mid[0] - 0.01 - width
                    box = (x0, mid[1], x0 + width, mid[1] + line)
                    wide = (box[0] - gap, box[1] - gap, box[2] + gap, box[3] + gap)
                    cost = length * 10 + (5 if dx and way != dx else 0) + (3 if not dx or not dy else 0)
                    # deslab: the office leader is short, at 45° or square, with a horizontal shelf; a long
                    # one at an odd angle reads as a mistake, and two leaders never cross (Игорь 06.11.2026,
                    # kv94 bottom shaft: «Вытяжка» and «Стояк канализации» on long crossed leaders)
                    if abs(abs(d[0]) - abs(d[1])) > 0.01 and abs(d[0]) > 0.01 and abs(d[1]) > 0.01:
                        cost += 25
                    cost += 20 * max(0.0, length - 1.1)
                    cost += 200 * sum(1 for a_, b_ in leaders if _segs_cross(dot, mid, a_, b_))
                    cost += 100 * sum(_box_hits(box, w) for w in obstacles["walls"])
                    # deslab: and a little air to the walls and strips (JK: «Трассировка h=310» right over
                    # the bottom wall, «h=280» against the strip T9)
                    cost += 15 * sum(_box_hits(wide, w) and not _box_hits(box, w) for w in obstacles["walls"])
                    cost += 100 * sum(_box_hits(wide, w) for w in obstacles["marks"] + placed)
                    # deslab: lying along a horizontal chain takes its whole text room; crossing a
                    # vertical one takes a narrow band, its texts slide past (test room 4 x 3 m)
                    cost += sum((100 if dirs[i] is None or abs(dirs[i][0]) >= abs(dirs[i][1]) else 35)
                                for i, w in enumerate(obstacles["dims"]) if _box_hits(wide, w))
                    # deslab: the panel's label crossed by a vertical dimension line is out (kv94: «Щиток»
                    # across the hall chains; Игорь shortened it so that it fits between them). Only
                    # for the panel: in a small room the other labels would end up on a horizontal
                    # chain instead (the test room 4 x 3 m).
                    cost += (150 if ident in panel_ids else 0) * sum(1 for i, w in enumerate(obstacles["dims"])
                                      if not (dirs[i] is None or abs(dirs[i][0]) >= abs(dirs[i][1]))
                                      and _seg_box(((w[0] + w[2]) / 2, w[1]), ((w[0] + w[2]) / 2, w[3]), box))
                    if ident in panel_ids:
                        cost += 150 * sum(1 for a_, b_ in obstacles.get("witness", []) if _seg_box(a_, b_, box))
                    # and never right on a horizontal dimension line: its texts would have nowhere to go
                    cost += 300 * sum(1 for i, w in enumerate(obstacles["dims"])
                                      if (dirs[i] is None or abs(dirs[i][0]) >= abs(dirs[i][1]))
                                      and _seg_box((w[0], (w[1] + w[3]) / 2), (w[2], (w[1] + w[3]) / 2), box))
                    # the shelf runs under the whole text: no other label text on it
                    cost += 100 * sum(_seg_hits_box((box[0], mid[1]), (box[2], mid[1]), w) for w in placed)
                    cost += 40 * sum(_seg_hits_box(_add(dot, _mul(d, 0.15)), mid, w) for w in obstacles["walls"])
                    # deslab: a leader through another label's text is as bad as a text on it (run kv94l:
                    # the leader of «Стояк канализации» through «Вытяжка»)
                    cost += 100 * sum(_seg_hits_box(dot, mid, w) for w in placed)
                    cost += 100 * sum(_box_hits(box, w) for w in swings + others)
                    cost += 40 * sum(_seg_hits_box(_add(dot, _mul(d, 0.1)), mid, w) for w in others)
                    # a long leader across dimension chains is worse than a short one (run kv94j:
                    # the panel label ran 3 m through the hall across three chains)
                    cost += 50 * sum(_seg_box(_add(dot, _mul(d, 0.1)), mid, w) for w in obstacles["dims"])
                    cost += 40 * sum(_seg_hits_box(_add(dot, _mul(d, 0.1)), mid, w) for w in swings)
                    if not all(_in_rooms(rooms, c) for c in ((box[0], box[1]), (box[2], box[3]), (box[0], box[3]), (box[2], box[1]))):
                        # deslab: the labels stay inside the flat, whatever it costs (run JK v7: three
                        # «Трассировка» labels behind the walls, kv94l: the panel's one)
                        cost += 1000
                    if best is None or cost < best[0]:
                        best = (cost, mid, way, box)
        cost, mid, way, box = best
        placed.append(box)
        leaders.append((dot, mid))
        leaders.append((mid, _add(mid, (way * (box[2] - box[0]), 0.0))))
        labels.append({"item": ident, "runs": [{"text": text}], "begCoordinate": _xy(dot), "midCoordinate": _xy(mid),
                       "endCoordinate": _xy(_add(mid, (way * 0.01, 0.0))), "box": box, "cost": round(cost, 1)})
    return labels
