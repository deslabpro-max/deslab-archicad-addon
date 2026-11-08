"""Measured plan (обмерный план) in the office style, from a scene of deslab-scan2bim.

The model (walls, openings, slab, zones) is built by the add-on command BuildModelFromScan in one
call. The drafting on top of it (height texts, opening labels, dimension chains, wet zones, layers)
is planned here in Python from the scene and the office profile, and sent as a few batch commands.
Claude makes one tool call; the rules live in office-profile.json, not in the prompt.
"""
import json
import math
import time
from pathlib import Path

SCENE_FORMAT = "deslab.scan2bim/1"
PROFILE_PATH = Path(__file__).resolve().parent.parent / "office-profile.json"


def load_profile(path=None):
    with open(path or PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- geometry

def _pt(p):
    return (float(p["x"]), float(p["y"]))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _mul(a, k):
    return (a[0] * k, a[1] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _len(a):
    return math.hypot(a[0], a[1])


def _unit(a):
    n = _len(a)
    return (a[0] / n, a[1] / n) if n else (0.0, 0.0)


def _left(u):
    return (-u[1], u[0])


def _snap_axis(u, max_deg):
    """u turned onto the nearest axis when it is within max_deg of it."""
    lim = math.sin(math.radians(max_deg))
    if abs(u[1]) <= lim:
        return (1.0 if u[0] > 0 else -1.0, 0.0)
    if abs(u[0]) <= lim:
        return (0.0, 1.0 if u[1] > 0 else -1.0)
    return u


def _xy(p):
    return {"x": round(p[0], 4), "y": round(p[1], 4)}


def polygon_area(poly):
    s = 0.0
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        s += a[0] * b[1] - b[0] * a[1]
    return s / 2.0


def polygon_centroid(poly):
    a = polygon_area(poly)
    if abs(a) < 1e-9:
        return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    cx = cy = 0.0
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        f = p[0] * q[1] - q[0] * p[1]
        cx += (p[0] + q[0]) * f
        cy += (p[1] + q[1]) * f
    return (cx / (6 * a), cy / (6 * a))


def zone_spot(scene, room, reach=2.5, step=0.1):
    """deslab: where the zone stamp goes, with «Н» under it: the room centroid, or the nearest
    point to it whose block (stamp and «Н») keeps clear of the tracing strips with their own
    chains, and of the shafts (run JK v7: the centroid fell on the joint of two strips, and the
    strip thickness texts «89», «114» landed on the number circle)."""
    poly = [_pt(p) for p in room["polygon"]]
    c = polygon_centroid(poly)
    keep = [_rect_of_segment(_pt(t["start"]), _pt(t["end"]), float(t.get("thickness", 0.08)) / 2 + 0.5)
            for t in scene.get("tracing", [])]
    from .mep import mep_blockers
    keep += [(b[0] - 0.3, b[1] - 0.3, b[2] + 0.3, b[3] + 0.3) for b in mep_blockers(scene)]
    if not keep:
        return c

    def block(p):
        return (p[0] - 0.5, p[1] - 0.8, p[0] + 0.5, p[1] + 0.4)

    n = int(reach / step)
    spots = sorted(((i * step, j * step) for i in range(-n, n + 1) for j in range(-n, n + 1)),
                   key=lambda o: o[0] * o[0] + o[1] * o[1])
    for o in spots:
        p = _add(c, o)
        b = block(p)
        if all(_inside(poly, q) for q in ((b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3]))) and                 not any(_box_hits(b, k) for k in keep):
            return p
    return c


def _mm(value):
    return int(round(float(value) * 1000))


# ---------------------------------------------------------------- scene

def apply_room_overrides(scene, rooms):
    """rooms: {"R1": {"name": "Санузел", "wet": "bathroom"}, ...} or a list with "id"."""
    if not rooms:
        return scene
    if isinstance(rooms, list):
        rooms = {r["id"]: r for r in rooms}
    for room in scene.get("rooms", []):
        override = rooms.get(room["id"]) or rooms.get(str(room.get("number")))
        if override:
            for key in ("name", "wet", "number", "height"):
                if key in override:
                    room[key] = override[key]
    return scene


def shift_scene(scene, origin):
    """Moves every coordinate of the scene by origin {"x", "y"}, so the model and its drafting land together."""
    dx, dy = float((origin or {}).get("x", 0)), float((origin or {}).get("y", 0))
    if not dx and not dy:
        return scene

    def move(p):
        p["x"] = float(p["x"]) + dx
        p["y"] = float(p["y"]) + dy

    for wall in scene.get("walls", []):
        move(wall["start"])
        move(wall["end"])
    for item in scene.get("rooms", []) + scene.get("slabs", []) + scene.get("mep", []) + scene.get("tracing", []):
        for key in ("polygon", "wetPolygon"):
            for p in item.get(key) or []:
                move(p)
        for key in ("start", "end", "at"):  # deslab: engineering items and tracing strips
            if key in item:
                move(item[key])
        for seg in item.get("walls") or []:  # deslab: shaft walls given by their axes
            for p in seg:
                move(p)
        for seg in item.get("partitions") or []:  # inner walls of a shaft (run kv94i: left at x=0)
            for p in seg:
                move(p)
    return scene


def summarize_scene(scene):
    """Short text of a scene for Claude: what was found and what was assumed."""
    lines = [f"Стен {len(scene.get('walls', []))}, помещений {len(scene.get('rooms', []))}."]
    for room in scene.get("rooms", []):
        lines.append(f"{room['id']} №{room.get('number')} «{room.get('name')}»: {room.get('area', 0):.2f} м², "
                     f"Н {_mm(room.get('height', 0))}" + (f", мокрая зона: {room['wet']}" if room.get("wet") else ""))
    for wall in scene.get("walls", []):
        for o in wall.get("openings", []):
            lines.append(f"{o['id']} {o['kind']}: {_mm(o['width'])}×{_mm(o['height'])}, подоконник {_mm(o.get('sill', 0))}")
    lines += [f"Допущение: {a}" for a in scene.get("assumptions", [])]
    lines += [f"Внимание: {w}" for w in scene.get("warnings", [])]
    return "\n".join(lines)


# ---------------------------------------------------------------- planning (pure, tested without Archicad)

def is_partition(wall, profile):
    return wall.get("kind") == "interior" and float(wall.get("thickness", 1)) <= profile["walls"]["partitionMaxThickness"]


def _opening_points(wall):
    """Opening centre and its edges on the wall axis, with the room side normal."""
    a, b = _pt(wall["start"]), _pt(wall["end"])
    u = _unit(_sub(b, a))
    for o in wall.get("openings", []):
        c = _add(a, _mul(u, float(o["centerOffset"])))
        half = float(o["width"]) / 2
        yield o, c, _sub(c, _mul(u, half)), _add(c, _mul(u, half)), u, _left(u)


def check_room_faces(scene, tol=0.005, where=None):
    """deslab: every edge of a room outline must lie on a wall face (Игорь, 04.11.2026: in kv94
    a bottom wall stood a wall thickness off its place, so the plan's outline dimensions were
    wrong). Returns the errors: an edge with no wall face on it within tol, with the nearest one."""
    errors = []
    # deslab: where (a list) gets (room index, edge index) of each error, for fit_walls_to_rooms
    where = where if where is not None else []
    walls = []
    for w in scene.get("walls", []):
        a, b = _pt(w["start"]), _pt(w["end"])
        if _len(_sub(b, a)) < 1e-6:
            continue
        walls.append((w, a, b, _unit(_sub(b, a)), float(w.get("thickness", 0.2)) / 2))
    for ri, room in enumerate(scene.get("rooms", [])):
        poly = [_pt(p) for p in room["polygon"]]
        ccw = polygon_area(poly) > 0
        for i, p in enumerate(poly):
            q = poly[(i + 1) % len(poly)]
            length = _len(_sub(q, p))
            if length < 0.05:
                continue
            e = _unit(_sub(q, p))
            inward = _left(e) if ccw else _mul(_left(e), -1.0)
            mid = _mul(_add(p, q), 0.5)
            best, inside = None, None
            for w, a, b, u, half in walls:
                if abs(abs(_dot(e, u)) - 1) > 1e-4:
                    continue
                # the wall must stand OUTSIDE the room: its axis behind the edge, away from the
                # room (the kv94 fault: the axis 0.1 into the room, the wall's far face on the edge)
                if _dot(_sub(a, mid), inward) > 0:
                    if abs(abs(_dot(_sub(mid, a), _left(u))) - half) <= tol:
                        inside = w["id"]
                    continue
                off = abs(_dot(_sub(mid, a), _left(u)))  # edge from the wall axis
                gap = abs(off - half)                     # edge from the nearer face
                t = sorted([_dot(_sub(p, a), u), _dot(_sub(q, a), u)])
                lo, hi = min(0.0, _dot(_sub(b, a), u)), max(0.0, _dot(_sub(b, a), u))
                cover = max(0.0, min(hi + half, t[1]) - max(lo - half, t[0]))
                if cover < 0.5 * length:
                    continue
                if best is None or gap < best[0]:
                    best = (gap, w["id"], off, half)
            if (best is None or best[0] > tol) and _on_wall_end(walls, p, q, mid, e, tol):
                continue  # a step of the outline on a wall's end face
            if best is None and inside:
                where.append((ri, i)) or errors.append(f"{room.get('id', '?')}: ребро {i + 1} ({p[0]:.3f}; {p[1]:.3f}) → ({q[0]:.3f}; {q[1]:.3f}): "
                              f"стена {inside} стоит внутри помещения, а должна снаружи (ось не в ту сторону)")
            elif best is None:
                where.append((ri, i)) or errors.append(f"{room.get('id', '?')}: ребро {i + 1} ({p[0]:.3f}; {p[1]:.3f}) → ({q[0]:.3f}; {q[1]:.3f}) "
                              f"без стены вдоль него")
            elif best[0] > tol:
                where.append((ri, i)) or errors.append(f"{room.get('id', '?')}: ребро {i + 1} ({p[0]:.3f}; {p[1]:.3f}) → ({q[0]:.3f}; {q[1]:.3f}) "
                              f"не на грани стены {best[1]}: ось в {best[2] * 1000:.0f} мм, половина толщины "
                              f"{best[3] * 1000:.0f} мм, промах {best[0] * 1000:.0f} мм")
    return errors



def _orthogonal_outline(poly, tol=0.02):
    """deslab: a scanned outline straightened: a vertex off its neighbour by less than tol across an axis takes
    the neighbour's coordinate, then repeated and collinear vertices are dropped (JK 08.11: a 6 cm slanted edge)."""
    out = [list(poly[0])]
    for x, y in poly[1:]:
        px, py = out[-1]
        if abs(x - px) < tol <= abs(y - py):
            x = px
        elif abs(y - py) < tol <= abs(x - px):
            y = py
        out.append([x, y])
    pts = [tuple(v) for v in out]
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if _len(_sub(b, a)) < 1e-4 or abs(cross) < 1e-7:
                del pts[i]
                changed = True
                break
    return pts


def fit_walls_to_rooms(scene, snap=0.25, nook=0.4, tol=0.005):
    """deslab (JK 08.11.2026): a scene from the scan helper whose room outline misses the wall faces by a few
    centimetres at wall ends and small nooks. Walls are never moved across (a wall a thickness off stays an error,
    Игорь 04.11): the outline is straightened, a wall end within snap of an outline edge square to it is moved
    onto that edge (with the walls joined at that end), and a nook up to nook long is filled by a wall from the
    edge to the wall face behind it. Returns (scene copy, notes)."""
    scene = json.loads(json.dumps(scene))
    notes = []
    for room in scene.get("rooms", []):
        poly = [_pt(p) for p in room["polygon"]]
        straight = _orthogonal_outline(poly)
        if len(straight) != len(poly) or any(_len(_sub(a, b)) > 1e-9 for a, b in zip(straight, poly)):
            room["polygon"] = [_xy(p) for p in straight]
            notes.append(f"{room.get('id', '?')}: контур выпрямлен ({len(poly)} → {len(straight)} вершин)")
    filled = 0
    for _ in range(40):
        where = []
        check_room_faces(scene, tol, where)
        if not where:
            break
        done = False
        for ri, i in where:
            room = scene["rooms"][ri]
            poly = [_pt(p) for p in room["polygon"]]
            p, q = poly[i], poly[(i + 1) % len(poly)]
            e, mid = _unit(_sub(q, p)), _mul(_add(p, q), 0.5)
            ccw = polygon_area(poly) > 0
            out = _mul(_left(e), -1.0) if ccw else _left(e)
            best = None
            for w in scene.get("walls", []):
                a, b = _pt(w["start"]), _pt(w["end"])
                if _len(_sub(b, a)) < 1e-6:
                    continue
                u, half = _unit(_sub(b, a)), float(w.get("thickness", 0.2)) / 2
                if abs(_dot(e, u)) > 1e-4:
                    continue
                for key, end in (("start", a), ("end", b)):
                    along = _dot(_sub(mid, end), u)
                    across = [_dot(_sub(x, end), _left(u)) for x in (p, q)]
                    if 1e-4 < abs(along) <= snap and min(across) >= -half - tol and max(across) <= half + tol:
                        if best is None or abs(along) < abs(best[0]):
                            best = (along, w, key, end, u)
            if best:
                along, w, key, end, u = best
                move = _mul(u, along)
                for other in scene["walls"]:
                    for k in ("start", "end"):
                        if other is w and k != key:
                            continue
                        if _len(_sub(_pt(other[k]), end)) < 0.002:
                            other[k] = _xy(_add(_pt(other[k]), move))
                notes.append(f"{w['id']}: торец ({key}) сдвинут на {along * 1000:+.0f} мм к контуру {room.get('id', '?')}")
                done = True
                break
            length = _len(_sub(q, p))
            if length > nook:
                continue
            depth = None
            for w in scene.get("walls", []):
                a, b = _pt(w["start"]), _pt(w["end"])
                if _len(_sub(b, a)) < 1e-6:
                    continue
                u, half = _unit(_sub(b, a)), float(w.get("thickness", 0.2)) / 2
                if abs(abs(_dot(e, u)) - 1) > 1e-4:
                    continue
                d = _dot(_sub(a, mid), out) - half
                t = sorted([_dot(_sub(p, a), u), _dot(_sub(q, a), u)])
                lo, hi = min(0.0, _dot(_sub(b, a), u)), max(0.0, _dot(_sub(b, a), u))
                if tol < d <= 0.3 and t[0] >= lo - half - tol and t[1] <= hi + half + tol:
                    if depth is None or d < depth[0]:
                        depth = (d, w)
            if depth:
                d, w = depth
                filled += 1
                scene["walls"].append({
                    "id": f"{w['id']}N{filled}", "start": _xy(_add(p, _mul(out, d / 2))), "end": _xy(_add(q, _mul(out, d / 2))),
                    "thickness": round(d, 4), "height": w.get("height"), "bottom": w.get("bottom", 0.0),
                    "measuredThickness": True, "kind": w.get("kind", "interior"), "openings": []})
                notes.append(f"{w['id']}N{filled}: ниша {length * 1000:.0f}×{d * 1000:.0f} мм у {w['id']} заполнена стеной")
                done = True
                break
        if not done:
            break
    return scene, notes


def _on_wall_end(walls, p, q, mid, e, tol):
    """True when the edge p-q lies on the end face of a wall square to it (a step of the room
    outline where a thicker wall ends)."""
    for w, a, b, u, half in walls:
        if abs(_dot(e, u)) > 1e-4:
            continue
        for end in (a, b):
            if abs(_dot(_sub(mid, end), u)) > tol:
                continue
            across = [_dot(_sub(x, end), _left(u)) for x in (p, q)]
            if min(across) >= -half - tol and max(across) <= half + tol:
                return True
    return False


def walls_from_outline(polygon, edges, height, tol=1e-6):
    """deslab: walls of a flat drawn from a measured plan (a PDF), placed from the INNER outline,
    so a wall can never stand a thickness off (Игорь, 04.11.2026, kv94). polygon: the room
    outline (the inner faces), edges[i]: the wall on the edge polygon[i] → polygon[i+1]:
    {"id", "thickness", "kind", "openings": [{"id", "kind", "from", "width", "sill", "height",
    ...}]}, "from" measured on the inner face from polygon[i]. The wall axis is the face moved
    out by half the thickness; neighbouring axes meet at the corners. Returns the walls."""
    poly = [_pt(p) for p in polygon]
    n = len(poly)
    ccw = polygon_area(poly) > 0
    axes = []
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        u = _unit(_sub(q, p))
        out = _mul(_left(u), -1.0) if ccw else _left(u)
        half = float(edges[i]["thickness"]) / 2
        axes.append((_add(p, _mul(out, half)), u))

    def meet(a1, u1, a2, u2, fallback):
        den = u1[0] * u2[1] - u1[1] * u2[0]
        if abs(den) < tol:
            return fallback
        w = _sub(a2, a1)
        t = (w[0] * u2[1] - w[1] * u2[0]) / den
        return _add(a1, _mul(u1, t))

    walls = []
    for i in range(n):
        a, u = axes[i]
        pa, pu = axes[i - 1]
        na, nu = axes[(i + 1) % n]
        start = meet(pa, pu, a, u, a)
        end = meet(a, u, na, nu, _add(a, _mul(u, _len(_sub(poly[(i + 1) % n], poly[i])))))
        e = edges[i]
        openings = []
        for o in e.get("openings", []):
            o = dict(o)
            c_face = _add(poly[i], _mul(u, float(o.pop("from")) + float(o["width"]) / 2))
            o["centerOffset"] = round(_dot(_sub(c_face, start), u), 4)
            openings.append(o)
        walls.append({"id": e["id"], "start": _xy(start), "end": _xy(end), "thickness": e["thickness"],
                      "height": height, "kind": e.get("kind", "exterior"), "openings": openings})
    return walls


def plan_height_texts(scene, profile):
    texts = []
    fmt = profile["zone"]["heightTextFormat"]
    for room in scene.get("rooms", []):
        c = zone_spot(scene, room)
        texts.append({"room": room["id"], "coordinate": {"x": round(c[0], 4), "y": round(c[1] + profile["zone"]["heightTextOffset"], 4), "z": 0},
                      "text": fmt.format(height_mm=_mm(room.get("height", room.get("ceiling", 0) - room.get("floor", 0))))})
    return texts


def _runs(template, values):
    runs = []
    for text, style in template:
        run = {"text": text.format(**values)}
        if style == "sub":
            run["effectSubscript"] = True
        runs.append(run)
    return runs


def _inside(poly, p):
    x, y = p
    hit = False
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            hit = not hit
    return hit


def _box_hits(box, other):
    return box[0] < other[2] and other[0] < box[2] and box[1] < other[3] and other[1] < box[3]


def _seg_hits_box(a, b, box, steps=12):
    return any(box[0] <= a[0] + (b[0] - a[0]) * k / steps <= box[2] and box[1] <= a[1] + (b[1] - a[1]) * k / steps <= box[3]
               for k in range(steps + 1))


def _rect_of_segment(a, b, half):
    return (min(a[0], b[0]) - half, min(a[1], b[1]) - half, max(a[0], b[0]) + half, max(a[1], b[1]) + half)


def label_obstacles(scene, profile, texts=None, chains=None):
    """Boxes (xmin, ymin, xmax, ymax) the labels must keep clear of: walls, dimension lines with
    their text band, height texts and zone stamps."""
    cfg = profile["labels"]
    walls, dims, marks, dim_dirs, witness = [], [], [], [], []
    for wall in scene.get("walls", []):
        a, b = _pt(wall["start"]), _pt(wall["end"])
        u = _unit(_sub(b, a))
        if abs(u[0]) > 0.99 or abs(u[1]) > 0.99:  # axis-aligned walls: their exact rectangle
            walls.append(_rect_of_segment(a, b, float(wall.get("thickness", 0.2)) / 2))
    for ch in chains or []:
        a, b = _chain_line(ch)
        dim_dirs.append(_pt(ch["direction"]))
        dims.append(_rect_of_segment(a, b, cfg["dimensionBand"]))
        # deslab: the witness lines too (kv94: «Щиток» across the panel chain's witness lines)
        d_, ref_ = _pt(ch["direction"]), _pt(ch["referencePoint"])
        for p_ in ch["points"]:
            p_ = _pt(p_)
            witness.append((p_, _add(ref_, _mul(d_, _dot(_sub(p_, ref_), d_)))))
        # deslab: the texts of short dimensions on their leaders (run JK: «220» under a door label)
        marks.extend(tuple(bx) for bx in ch.get("leaderBoxes", []))
    for t in texts or []:
        c = (t["coordinate"]["x"], t["coordinate"]["y"])
        marks.append((c[0] - 0.45, c[1] - 0.2, c[0] + 0.45, c[1] + 0.2))
    for room in scene.get("rooms", []):
        c = zone_spot(scene, room)
        r = cfg["stampRadius"]
        marks.append((c[0] - r, c[1] - r, c[0] + r, c[1] + r))
    return {"walls": walls, "dims": dims, "marks": marks, "dimDirs": dim_dirs, "witness": witness}


def plan_opening_labels(scene, profile, obstacles=None):
    """One label per window or door: «hокна 1950 / hподокон 690», «hдвери 2100».

    Placement rule (Игорь, 03.11.2026, runs 4 and 6):
    - every label is INSIDE the flat, in the room of its opening (windows too), so the drawing
      ends at the outline of the flat; between two rooms a door label goes into the larger one;
    - the dot is on the opening, at its centre on the room face of the wall;
    - the leader is square to the wall and as short as the free space allows; the text never
      lies on a wall, a dimension line with its text, a «Н» text, a zone stamp or another label;
    - all labels of one wall are placed alike (same side, same leader length, same shelf
      direction), so they stand in one even row; the text lines are left-aligned;
    - the shelf is horizontal: away from the wall for a vertical wall, to the right (+x) for a
      horizontal one, unless that side is taken; a 45° leader only when nothing square is free.
    """
    cfg = profile["labels"]
    obstacles = obstacles or {"walls": [], "dims": [], "marks": []}
    dim_dirs = obstacles.get("dimDirs") or [None] * len(obstacles["dims"])
    rooms = [[_pt(p) for p in r["polygon"]] for r in scene.get("rooms", [])]
    labels = []
    corners = [p for r in rooms for p in r]
    flat = (min(p[0] for p in corners), min(p[1] for p in corners), max(p[0] for p in corners), max(p[1] for p in corners)) if corners else None
    flat_mid = ((flat[0] + flat[2]) / 2, (flat[1] + flat[3]) / 2) if flat else None
    weight = cfg.get("lengthCost", 20)

    def text_of(o):
        window = o["kind"] in ("window", "balconyDoor")
        values = {"height_mm": _mm(o["height"]), "sill_mm": _mm(o.get("sill", 0))}
        if window and float(o.get("sill", 0)) > 0.05:
            template = cfg["window"]
        elif window:
            template = [["h", ""], ["окна", "sub"], [" {height_mm}", ""]]
        else:
            template = cfg["door"]
        runs = _runs(template, values)
        lines = "".join(r["text"] for r in runs).split("\n")
        # text block size on the plan, from the paper size measured in Archicad (label test,
        # 03.11.2026: «hподокон 962» 16.5 mm, «hокна 2744» 15.3 mm, a line 4.3 mm at 1:45)
        k = cfg.get("drawingScale", 45) / 1000.0
        width = (max(len(l) for l in lines) * cfg.get("charMm", 1.4) + cfg.get("padMm", 1.5)) * k
        return runs, width, len(lines), cfg.get("lineMm", 4.32) * k

    def inner_side(c, n, half_t):
        def room_area(side):
            p = _add(c, _mul(side, half_t + 0.3))
            return max([abs(polygon_area(r)) for r in rooms if _inside(r, p)] or [0.0])
        area_n, area_m = room_area(n), room_area(_mul(n, -1))
        if area_n or area_m:
            return n if area_n >= area_m else _mul(n, -1)
        # an entrance hall without a zone: the side facing the middle of the flat
        return n if flat_mid and _dot(_sub(flat_mid, c), n) >= 0 else _mul(n, -1)

    def one_cost(dot, mid, lead_dir, box, u, half_t, taken, leads=()):
        cost = 100 * sum(_box_hits(box, w) for w in obstacles["walls"])
        # deslab: no line through a label text, and no leader across another label's text or
        # line (kv94 corner window, 04.11.2026: the leaders of W1.4 and W2.3 crossed)
        shelf = ((box[0], mid[1]), (box[2], mid[1]))
        cost += 100 * sum(_seg_box(a, b, box) for a, b in leads)
        cost += 100 * sum(_seg_box(dot, mid, t) or _seg_box(shelf[0], shelf[1], t) for t in taken)
        cost += 15 * sum(_seg_cross(dot, mid, a, b) or _seg_cross(shelf[0], shelf[1], a, b) for a, b in leads)
        cost += 100 * sum(_box_hits(box, d) for d in obstacles["dims"])
        cost += 100 * sum(_box_hits(box, m) for m in obstacles["marks"] + taken)
        for d, dd in zip(obstacles["dims"], dim_dirs):
            # the chain along the opening's own wall face is crossed by every inner leader
            if dd is not None and abs(_dot(dd, u)) > 0.99 and _seg_hits_box(dot, _add(dot, _mul(lead_dir, 0.8)), d):
                continue
            cost += 40 * _seg_hits_box(dot, mid, d)
        cost += 100 * sum(_seg_hits_box(_add(dot, _mul(lead_dir, 0.05)), mid, w) for w in obstacles["walls"])
        cost += 8 * sum(_seg_hits_box(_add(dot, _mul(lead_dir, 0.05)), mid, m) for m in obstacles["marks"] + taken)
        if flat and not (flat[0] <= box[0] and box[2] <= flat[2] and flat[1] <= box[1] and box[3] <= flat[3]):
            cost += 60  # off the outline of the flat
        return cost

    corner_twins = {(o.get("corner") or {}).get("partner") for w in scene.get("walls", [])
                    for o in w.get("openings", [])} - {None}

    def choose(wall, others, every=False):
        half_t = float(wall.get("thickness", 0.2)) / 2
        items = []
        for o, c, _, _, u, n in _opening_points(wall):
            if o["id"] in corner_twins:
                continue  # deslab: a corner window is one window with one label, on its first part
            runs, width, nlines, line = text_of(o)
            items.append((o, c, u, n, runs, width, (nlines, line)))
        if not items:
            return None
        o_boxes = [b for r in others for b in r["boxes"]]
        o_leads = [ln for r in others for ln in r["leads"]]
        u, n = items[0][2], items[0][3]
        n_in = inner_side(items[0][1], n, half_t)
        vertical_wall = abs(u[1]) > abs(u[0])
        best = None
        options = []
        for side_rank, side in enumerate((n_in, _mul(n_in, -1))):
            # deslab: how Archicad draws the label (test of 03.11.2026): the text block is hung at
            # the shelf end (left edge for a shelf to the right, right edge for one to the left),
            # one line stands on the shelf, two lines sit above and below it. The shelf end is put
            # 1 cm past the elbow, so the block starts right at the elbow. A vertical wall: the
            # shelf runs on away from the wall; a horizontal one: to the right if free.
            shelves = ((1.0 if side[0] >= 0 else -1.0),) if vertical_wall else (1.0, -1.0)
            for lean in (0.0,):  # deslab: square leaders only (a slanted one drew an endless shelf, run 7b)
                lead_dir = _unit(_add(side, _mul(u, lean)))
                for length in cfg["leaderLengths"]:
                    for shelf in shelves:
                        total = side_rank * 200 + length * weight + (3 if shelf != shelves[0] else 0)
                        taken = list(o_boxes)
                        leads = list(o_leads)
                        result = []
                        for o, c, _, _, runs, width, (nlines, line) in items:
                            dot = _add(c, _mul(side, half_t))
                            mid = _add(dot, _mul(lead_dir, length))
                            # deslab: a two-line label stands mostly over its shelf: measured on kv94n
                            # (06.11.2026), «hокна 2500 / hподокон 550» from 0.16 m under the shelf
                            # to 0.40 m over it (the old +-0.19 let «801» onto «hокна 2500»)
                            y0, y1 = (mid[1], mid[1] + line) if nlines == 1 else                                 (mid[1] - cfg.get("twoLineBelow", 0.16), mid[1] + cfg.get("twoLineAbove", 0.4))
                            # the row keeps one shelf way; a label at the end of the row whose
                            # text would run onto a dimension chain or another label turns its
                            # shelf back (kv94 corner window, 04.11.2026)
                            ways = [shelf] if vertical_wall else [shelf, -shelf]
                            pick = None
                            for rank, way in enumerate(ways):
                                x0 = mid[0] + 0.01 if way > 0 else mid[0] - 0.01 - width
                                box = (x0, y0, x0 + width, y1)
                                cst = one_cost(dot, mid, lead_dir, box, u, half_t, taken, leads) + 30 * rank
                                if pick is None or cst < pick[0]:
                                    pick = (cst, way, box)
                            cst, way, box = pick
                            total += cst
                            taken.append(box)
                            leads += [(dot, mid), ((box[0], mid[1]), (box[2], mid[1]))]
                            result.append((o, runs, dot, mid, way, width, box))
                        options.append((total, result))
                        if best is None or total < best[0]:
                            best = (total, result)

        def pack(total, result):
            return {"wall": wall, "total": total, "result": result,
                    "boxes": [r[6] for r in result],
                    "leads": [ln for o, runs, dot, mid, shelf, width, box in result
                              for ln in ((dot, mid), ((box[0], mid[1]), (box[2], mid[1])))]}
        return [pack(*o) for o in options] if every else pack(*best)

    def near(a, b, gap=1.5):
        return any(x0 - gap < X1 and X0 - gap < x1 and y0 - gap < Y1 and Y0 - gap < y1
                   for x0, y0, x1, y1 in a["boxes"] for X0, Y0, X1, Y1 in b["boxes"])

    # each wall in turn, then again with the others' final places known, so two walls at a
    # corner settle on places that leave each other clear
    walls = scene.get("walls", [])
    chosen = {}
    for _ in range(cfg.get("labelRounds", 3)):
        for i, wall in enumerate(walls):
            ch = choose(wall, [c for j, c in chosen.items() if j != i])
            if ch is not None:
                chosen[i] = ch
    # two walls whose labels meet (a corner): their places are chosen together, since one wall
    # alone may be stuck where only a move of both clears them
    keys = sorted(chosen)
    for a_i, i in enumerate(keys):
        for j in keys[a_i + 1:]:
            if not near(chosen[i], chosen[j]):
                continue
            rest = [c for k, c in chosen.items() if k not in (i, j)]
            best = (chosen[i]["total"] + chosen[j]["total"], chosen[i], chosen[j])
            for opt in choose(walls[i], rest, every=True):
                other = choose(walls[j], rest + [opt])
                if opt["total"] + other["total"] < best[0] - 1e-6:
                    best = (opt["total"] + other["total"], opt, other)
            chosen[i], chosen[j] = best[1], best[2]
    for i in sorted(chosen):
        c = chosen[i]
        total, result = c["total"], c["result"]
        for o, runs, dot, mid, shelf, width, box in result:
            labels.append({"opening": o["id"], "wall": c["wall"]["id"], "runs": runs, "cost": round(total / len(result), 1),
                           "begCoordinate": _xy(dot), "midCoordinate": _xy(mid),
                           "endCoordinate": _xy(_add(mid, (shelf * 0.01, 0.0))), "box": box})
    return labels


def _chain_line(ch):
    """The dimension line of a chain as a segment (from the first to the last point)."""
    d = _pt(ch["direction"])
    ref = _pt(ch["referencePoint"])
    ts = [_dot(_sub(_pt(p), ref), d) for p in ch["points"]]
    return _add(ref, _mul(d, min(ts))), _add(ref, _mul(d, max(ts)))


def _parallel_overlap(c1, c2, band):
    """deslab: two parallel dimension lines closer than their text bands, along a common stretch."""
    d1, d2 = _pt(c1["direction"]), _pt(c2["direction"])
    return abs(_dot(d1, d2)) > 0.99 and _chain_conflict(c1, c2, band)


def _chain_conflict(c1, c2, band):
    """True when two dimension lines (with their text band) overlap or cross away from their
    ends. Inner chains of two neighbouring walls cross near the room corner; that is allowed."""
    a1, b1 = _chain_line(c1)
    a2, b2 = _chain_line(c2)
    d1, d2 = _pt(c1["direction"]), _pt(c2["direction"])
    if abs(_dot(d1, d2)) > 0.99:  # parallel: bands overlap?
        if abs(_dot(_sub(a2, a1), _left(d1))) >= 2 * band:
            return False
        t = sorted([_dot(_sub(a2, a1), d1), _dot(_sub(b2, a1), d1)])
        return t[0] < _len(_sub(b1, a1)) - 0.01 and t[1] > 0.01
    # crossing point of the two lines
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        return False
    w = _sub(a2, a1)
    t1 = (w[0] * d2[1] - w[1] * d2[0]) / den
    t2 = (w[0] * d1[1] - w[1] * d1[0]) / den
    l1, l2 = _len(_sub(b1, a1)), _len(_sub(b2, a2))
    if not (-band < t1 < l1 + band and -band < t2 < l2 + band):
        return False
    # near the corner: the crossing is no further from the end of one line than the other line
    # stands off its own wall face
    near_end1 = min(abs(t1), abs(l1 - t1)) <= c2.get("offset", 0.35) + 0.05
    near_end2 = min(abs(t2), abs(l2 - t2)) <= c1.get("offset", 0.35) + 0.05
    return not (near_end1 and near_end2)


def plan_dimension_chains(scene, profile):
    """Inner dimension chains of each room, corner to corner through the opening edges.

    Rule (Игорь, 03.11.2026: dimensions must not run onto other dimension lines):
    - one chain per room edge, on a line offsetFromFace inside the room;
    - a short step in a wall (up to mergeStep) does not get its own line next to the main one:
      the two parallel faces on either side of it go into ONE chain on the deeper line, and the
      step itself gets its own small chain only when it is at least minEdge long;
    - inner chains of neighbouring walls may cross at the room corner; any other overlap or
      crossing moves the shorter chain one offsetStep further into the room (up to maxShifts);
      what still conflicts is reported in the chain as "conflict".
    The caller places a hotspot at each point and dimensions the hotspots."""
    cfg = profile["dimensions"]
    merge_step = cfg.get("mergeStep", 0.45)
    step = cfg.get("offsetStep", 0.35)
    band = profile["labels"].get("dimensionBand", 0.18)
    openings = [(wall, o, c, e1, e2, u) for wall in scene.get("walls", []) for o, c, e1, e2, u, _ in _opening_points(wall)]

    def opening_points(a, u, length):
        pts = []
        for wall, o, c, e1, e2, wu in openings:
            if abs(_dot(wu, u)) < 0.99:
                continue
            if abs(_dot(_sub(c, a), _left(u))) > float(wall.get("thickness", 0.2)) / 2 + 0.06:
                continue
            for e in (e1, e2):
                t = _dot(_sub(e, a), u)
                if 0.02 < t < length - 0.02:
                    pts.append(_add(a, _mul(u, t)))
        return pts

    chains = []
    for room in scene.get("rooms", []):
        poly = [_pt(p) for p in room["polygon"]]
        ccw = polygon_area(poly) > 0
        count = len(poly)
        edges = []
        for i, a in enumerate(poly):
            b = poly[(i + 1) % count]
            length = _len(_sub(b, a))
            u = _unit(_sub(b, a)) if length > 1e-9 else (1.0, 0.0)
            n = _left(u) if ccw else _mul(_left(u), -1)
            edges.append({"i": i, "a": a, "b": b, "u": u, "n": n, "len": length})
        # group parallel faces split by a short step: edge i, short edge i+1, edge i+2
        group = list(range(count))

        def root(k):
            while group[k] != k:
                k = group[k]
            return k
        steps = set()
        # depth of each face along its normal; a group keeps its faces within merge_step of each
        # other in all (run JK, 05.11.2026: four steps of 0.1-0.2 m added up to 0.53 m, and the
        # chain hung 0.9 m off the main face)
        depth = {e["i"]: _dot(e["a"], e["n"]) for e in edges}
        span = {e["i"]: (depth[e["i"]], depth[e["i"]]) for e in edges}
        for e in edges:
            if count < 5:
                break
            s, f = edges[(e["i"] + 1) % count], edges[(e["i"] + 2) % count]
            if (s["len"] <= merge_step and _dot(e["u"], f["u"]) > 0.99 and abs(_dot(s["u"], e["u"])) < 0.1
                    and abs(_dot(_sub(f["a"], e["b"]), e["n"])) <= merge_step):
                ra, rb = root(e["i"]), root(f["i"])
                if ra == rb:
                    continue
                lo = min(span[ra][0], span[rb][0])
                hi = max(span[ra][1], span[rb][1])
                if hi - lo > merge_step + 1e-6:
                    continue
                group[rb] = ra
                span[ra] = (lo, hi)
                steps.add(s["i"])
        members = {}
        for e in edges:
            members.setdefault(root(e["i"]), []).append(e)
        for key in sorted(members):
            ms = members[key]
            u, n = ms[0]["u"], ms[0]["n"]
            if len(ms) == 1 and (ms[0]["len"] < cfg["minEdge"]):
                continue
            pts = []
            for e in ms:
                pts += [e["a"], e["b"]] + opening_points(e["a"], u, e["len"])
            origin = ms[0]["a"]
            depths = [_dot(_sub(e["a"], origin), n) for e in ms]
            depth = max(depths)
            ref = _add(origin, _mul(n, depth + cfg["offsetFromFace"]))
            ordered = sorted(pts, key=lambda p: _dot(_sub(p, origin), u))
            kept = []
            for p in ordered:
                if not kept or abs(_dot(_sub(p, kept[-1]), u)) > 0.002:
                    kept.append(p)
            if len(kept) < 2:
                continue
            # a chain off the axis by a hair (scanned walls lean 0.06-0.2°) reads top to bottom in
            # Archicad; a strictly vertical one reads bottom to top as in the template (run 15)
            du = _snap_axis(u, cfg.get("axisSnapDeg", 1.0))
            chains.append({"room": room["id"], "edge": ms[0]["i"], "edges": [e["i"] for e in ms],
                           "direction": _xy(du), "normal": _xy(n), "referencePoint": _xy(ref),
                           "offset": round(depth - min(depths) + cfg["offsetFromFace"], 4),
                           "points": [_xy(p) for p in kept]})
    # deslab: engineering items and tracing (Игорь 05.11.2026): a room chain does not run through a
    # shaft box, and the radiators, shafts and tracing strips get their own chains
    from .mep import mep_blockers, panel_boxes, plan_extra_chains
    blockers = mep_blockers(scene)
    panels = [] if profile.get("mep", {}).get("splitViews") else panel_boxes(scene)
    for ch in chains:
        for _ in range(4):
            a, b = _chain_line(ch)
            if not any(_seg_box(a, b, bx) for bx in blockers):
                break
            ch["referencePoint"] = _xy(_add(_pt(ch["referencePoint"]), _mul(_pt(ch["normal"]), step)))
            ch["offset"] = round(ch["offset"] + step, 4)
    # deslab: two plans (Игорь 06.11.2026): the measured plan has the room chains, the engineering
    # plan (view «2. Инженерные узлы») the chains of the radiators, panel, shafts and tracing
    split = profile.get("mep", {}).get("splitViews")
    for ch in chains:
        ch["plan"] = "obmer"
    extra = plan_extra_chains(scene, profile, [] if split else chains)
    for ch in extra:
        ch["plan"] = "eng" if split else "obmer"
    chains += extra

    def conflicting(c1):
        # any other chain, its own item chains included, with a wider band: the stepped line must
        # leave room for both chains' texts (test room: the radiator chain right over it)
        return any(c2 is not c1 and c2.get("plan") == c1.get("plan") and _chain_conflict(c1, c2, band + 0.1) for c2 in chains)
    # deslab: a room chain steps off an electrical panel too, but only when the new line makes no
    # conflict with the other chains (kv94: «2 493» across the panel; the test room has no room for it)
    for ch in chains:
        if ch.get("extra"):
            continue
        before = (ch["referencePoint"], ch["offset"])
        for _ in range(2):
            a, b = _chain_line(ch)
            if not any(_seg_box(a, b, bx) for bx in panels):
                break
            ch["referencePoint"] = _xy(_add(_pt(ch["referencePoint"]), _mul(_pt(ch["normal"]), step)))
            ch["offset"] = round(ch["offset"] + step, 4)
        if (ch["referencePoint"], ch["offset"]) != before:
            a, b = _chain_line(ch)
            if conflicting(ch) or any(_seg_box(a, b, bx) for bx in panels):
                ch["referencePoint"], ch["offset"] = before

    # move the shorter of two conflicting chains further in, a few times at most (a chain of the
    # radiators always gives way to a room chain; the short chains of shafts and tracing strips stay
    # at their item, the dimension texts keep clear of their lines)
    def length(ch):
        a, b = _chain_line(ch)
        return _len(_sub(b, a))
    for _ in range(cfg.get("maxShifts", 2)):
        moved = False
        for i, c1 in enumerate(chains):
            for c2 in chains[i + 1:]:
                if c1.get("plan") != c2.get("plan"):
                    continue
                # deslab: a shaft's or strip's own chain moves away from its item when it lies on
                # another chain (Игорь 06.11.2026, kv94: «1 010» of the left shaft right on «9 854»)
                if "item" in (c1.get("extra"), c2.get("extra")) and not _parallel_overlap(c1, c2, band):
                    continue
                if _chain_conflict(c1, c2, band):
                    if "item" in (c1.get("extra"), c2.get("extra")) and c1.get("extra") != c2.get("extra"):
                        short = c1 if c1.get("extra") == "item" else c2
                    elif c1.get("extra") != c2.get("extra"):
                        short = c1 if c1.get("extra") else c2
                    else:
                        short = c1 if length(c1) <= length(c2) else c2
                    if short.get("extra") and short.get("shifts", 0) >= cfg.get("maxShifts", 2):
                        continue
                    short["shifts"] = short.get("shifts", 0) + 1
                    short["referencePoint"] = _xy(_add(_pt(short["referencePoint"]), _mul(_pt(short["normal"]), step)))
                    short["offset"] = round(short["offset"] + step, 4)
                    moved = True
        if not moved:
            break
    for i, c1 in enumerate(chains):
        for c2 in chains[i + 1:]:
            if ("item" not in (c1.get("extra"), c2.get("extra")) or _parallel_overlap(c1, c2, band)) \
                    and c1.get("plan") == c2.get("plan") and _chain_conflict(c1, c2, band):
                c1.setdefault("conflict", []).append(c2["edge"])
    # deslab: a dimension text that does not fit between its witness lines goes on a leader, as
    # in the office template (Игорь, 03.11.2026; read from «Антон Крючков.pln»): the leader
    # starts at the middle of the short segment on the dimension line, goes square to the chain
    # into the room, then a short shelf runs along the chain, back over the chain, and the text
    # continues past the shelf end. Archicad keeps that text on the segment's END witness point.
    short = cfg.get("shortSegment", 0.3)
    rise = cfg.get("leaderRise", 0.22)
    shelf = cfg.get("leaderShelf", 0.1)
    pen = cfg.get("leaderPen", 120)  # pen of the leaders in the office template
    # Where the leader goes (Игорь's own fix of run 15, 04.11.2026): the shelf runs OUT past the
    # end of the chain, to the free side (over the wall hatch or into a doorway is fine), about
    # as long as the text, and the text starts at the shelf end; only when that side is taken
    # does the shelf run back over the chain, the text continuing past its end. The text keeps
    # clear of the other dimension texts and of the leader texts placed before it.
    lab = profile["labels"]
    text_half = cfg.get("leaderTextHalf", 0.07)
    clear = cfg.get("leaderClear", 0.1)
    char = lab.get("charMm", 1.4) * lab.get("drawingScale", 45) / 1000.0

    def text_width(seg):
        return dimension_text_width(seg, profile)

    texts = []  # (chain, box) of the ordinary dimension texts, over their segment middles
    for ch in chains:
        d, ref = _pt(ch["direction"]), _pt(ch["referencePoint"])
        ts = [_dot(_sub(_pt(p), ref), d) for p in ch["points"]]
        for k in range(len(ts) - 1):
            seg = abs(ts[k + 1] - ts[k])
            if seg < short:
                continue
            c = _add(ref, _mul(d, (ts[k] + ts[k + 1]) / 2))
            w = text_width(seg) / 2
            texts.append((ch, _rect_of_segment(_sub(c, _mul(d, w)), _add(c, _mul(d, w)), clear)))

    def text_hits(ch, a, b):
        box = _rect_of_segment(a, b, text_half)
        # deslab: its own chain's texts too (run kv94j: «215» on its leader ran into «340» → «215340»)
        return any(_box_hits(box, t) for c, t in texts if c.get("plan") == ch.get("plan"))

    for ch in chains:
        d, n = _pt(ch["direction"]), _pt(ch["normal"])
        ref = _pt(ch["referencePoint"])
        ts = [_dot(_sub(_pt(p), ref), d) for p in ch["points"]]
        last = len(ts) - 2
        # texts read from left to right and from bottom to top
        read = (1.0, 0.0) if abs(d[0]) >= abs(d[1]) else (0.0, 1.0)
        for k in range(len(ts) - 1):
            seg = abs(ts[k + 1] - ts[k])
            if seg >= short:
                continue
            width = text_width(seg)
            run = min(max(width - char, cfg.get("leaderShelfMin", 0.12)), cfg.get("leaderShelfMax", 0.22))
            beg = _add(ref, _mul(d, (ts[k] + ts[k + 1]) / 2))
            if k == last and k > 0:
                out = 1 if ts[k + 1] > ts[k] else -1
            elif k == 0 and last > 0:
                out = -1 if ts[k + 1] > ts[k] else 1
            else:
                out = 1
            options = [(rise, sg, run) for sg in (out, -out)]
            options += [(r, sg, run) for r in (rise + 0.15, rise + 0.3) for sg in (out, -out)]
            options += [(rise, sg, sh) for sg in (out, -out) for sh in (run + 0.15, run + 0.3)]
            chosen = None
            for r, sg, sh in options:
                mid = _add(beg, _mul(n, r))
                end = _add(mid, _mul(d, sg * sh))
                if not text_hits(ch, end, _add(end, _mul(d, sg * width))):
                    chosen = (mid, end, sg)
                    break
            if chosen is None:
                mid = _add(beg, _mul(n, rise))
                chosen = (mid, _add(mid, _mul(d, out * run)), out)
                ch.setdefault("leaderConflict", []).append(k + 1)
            mid, end, sg = chosen
            # the leader holds the end of the text when the shelf runs against the reading way
            at_end = _dot(_mul(d, sg), read) < 0
            ch.setdefault("textLeaders", {})[k + 1] = {"beg": _xy(beg), "mid": _xy(mid), "end": _xy(end), "pen": pen,
                                                       "anchorAtTextEnd": at_end}
            texts.append((ch, _rect_of_segment(end, _add(end, _mul(d, sg * width)), clear)))
            ch.setdefault("leaderBoxes", []).append(_rect_of_segment(end, _add(end, _mul(d, sg * width)), text_half))
    return chains


def dimension_text_width(seg, profile):
    """Width in metres of the dimension text of a segment as Archicad draws it (measured in run
    19 at 1:45: «269» 0.246 m, «1 498» 0.381 m): digits plus the thousands gap."""
    cfg, lab = profile["dimensions"], profile["labels"]
    scale = lab.get("drawingScale", 45) / 1000.0
    digits = len(str(int(round(seg * 1000))))
    gap = cfg.get("thousandsGapMm", 1.18) if digits > 3 else 0.0
    return (digits * cfg.get("digitMm", 1.82) + gap) * scale


def _parallel(a, b, c, d):
    u, v = _sub(b, a), _sub(d, c)
    lu, lv = _len(u), _len(v)
    return lu > 1e-9 and lv > 1e-9 and abs(u[0] * v[1] - u[1] * v[0]) < 1e-3 * lu * lv


def _seg_cross(a, b, c, d):
    """True when the segments a-b and c-d cross (touching ends do not count)."""
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2, d3, d4 = side(c, d, a), side(c, d, b), side(a, b, c), side(a, b, d)
    return d1 * d2 < -1e-12 and d3 * d4 < -1e-12


def _seg_box(a, b, box):
    """True when the segment a-b passes through the box (xmin, ymin, xmax, ymax)."""
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - box[0]), (dx, box[2] - a[0]), (-dy, a[1] - box[1]), (dy, box[3] - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                return False
    return True


def place_dimension_texts(chains, labels, profile, marks=(), inside=None):
    """Slide dimension texts along their line so that no line crosses them (Игорь, 04.11.2026:
    «2711» crossed by the line of «714», «661», «861», «1468» by a door label leader; the text
    need not stand in the middle of its segment). Lines: the other chains' dimension and witness
    lines, the opening label leaders; also the label texts and the leader texts. A moved text
    gets "textAt" (its middle on the line) on the segment's end point; one that finds no clear
    place stays in the middle and is listed in the chain as "textConflict"."""
    cfg = profile["dimensions"]
    lab = profile["labels"]
    char = lab.get("charMm", 1.4) * lab.get("drawingScale", 45) / 1000.0
    half = cfg.get("textHalf", 0.12)
    step = cfg.get("textSlideStep", 0.02)
    short = cfg.get("shortSegment", 0.3)
    over = cfg.get("lineOvershoot", 0.05)
    lines, boxes = [], []
    for ch in chains:
        d, ref = _pt(ch["direction"]), _pt(ch["referencePoint"])
        a, b = _chain_line(ch)
        own = [(_sub(a, _mul(d, over)), _add(b, _mul(d, over)))]
        for p in ch["points"]:
            p = _pt(p)
            q = _add(ref, _mul(d, _dot(_sub(p, ref), d)))
            own.append((p, _add(q, _mul(_unit(_sub(q, p)) if _len(_sub(q, p)) > 1e-9 else (0.0, 0.0), over))))
        lines += [(ch, s_) for s_ in own]
        boxes += [(ch, bx) for bx in ch.get("leaderBoxes", [])]
        # deslab: the legs of the short texts' leaders are lines too (JK v7: the leader of «86» through «2 166»)
        for info in ch.get("textLeaders", {}).values():
            lines += [(ch, (_pt(info["beg"]), _pt(info["mid"]))), (ch, (_pt(info["mid"]), _pt(info["end"])))]
    margin = cfg.get("labelMargin", 0.05)  # the real label text runs a little longer than estimated
    for lb in labels:
        beg, mid, end = _pt(lb["begCoordinate"]), _pt(lb["midCoordinate"]), _pt(lb["endCoordinate"])
        lines += [(None, (beg, mid)), (None, (mid, end))]
        if lb.get("box"):
            x0, y0, x1, y1 = lb["box"]
            # the shelf runs under the whole text (run 18: «1498» on the shelf of «hокна 1785»)
            lines.append((None, ((x0 - margin, mid[1]), (x1 + margin, mid[1]))))
            boxes.append((None, (x0 - margin, y0, x1 + margin, y1)))

    # deslab: and clear of zone stamps, «Н» texts, tracing strips, shafts (run JK v6: «86», «114»
    # on the zone circle, «91» on a tracing strip)
    # (owner "mark": a text keeps off them, a leader leg may cross them, the leader of a strip's
    # thickness starts on the strip itself)
    boxes += [("mark", tuple(m)) for m in marks]

    # clear of any line (run 18: 1 cm looked cramped; kv94e, 05.11.2026: with 4 cm the label
    # shelves still came 1.7-2.7 cm to «2 107», «1 550», «2 251» as drawn)
    pad = cfg.get("textPad", 0.07)

    # deslab: Archicad draws a dimension text on ONE side of its line, the left of the reading way
    # (above a horizontal line, left of a vertical one), 0.03-0.18 m off it at 1:45 (kv94l, 06.11.2026:
    # the ink of «1 201» from 0.033 to 0.178 m over its line). The old box of +-textHalf around the
    # line missed the upper part, and a leader over «2 493», a label leader by «801» went unnoticed.
    below, above = cfg.get("textBelow", 0.0), cfg.get("textAbove", 0.2)

    def side_box(a, b, read, gap=0.0):
        """Box of a text running from a to b along its line, drawn on the left of `read`."""
        sd = _left(read)
        u = _unit(_sub(b, a)) if _len(_sub(b, a)) > 1e-9 else read
        a, b = _sub(a, _mul(u, gap)), _add(b, _mul(u, gap))
        pts = [_add(a, _mul(sd, -below)), _add(a, _mul(sd, above)), _add(b, _mul(sd, -below)), _add(b, _mul(sd, above))]
        return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))

    def reading(d):
        return (1.0, 0.0) if abs(d[0]) >= abs(d[1]) else (0.0, 1.0)

    def text_box(c, d, width, gap=pad):
        # along the line: the text and a small gap; across: the text on its side of the line
        return side_box(_sub(c, _mul(d, width / 2)), _add(c, _mul(d, width / 2)), reading(d), gap)

    def clear(ch, box):
        return not any(c is not ch and _seg_box(s_[0], s_[1], box) for c, s_ in lines) and \
            not any(c is not ch and _box_hits(box, bx) for c, bx in boxes)

    rise = cfg.get("leaderRise", 0.22)
    pen = cfg.get("leaderPen", 120)

    def leader_spot(ch, d, ref, ts, k, width):
        n = _pt(ch["normal"])
        read = (1.0, 0.0) if abs(d[0]) >= abs(d[1]) else (0.0, 1.0)
        run = min(max(width - char, cfg.get("leaderShelfMin", 0.12)), cfg.get("leaderShelfMax", 0.22))
        out = 1 if ts[k + 1] > ts[k] else -1
        mid_t, room = (ts[k] + ts[k + 1]) / 2, max(0.0, abs(ts[k + 1] - ts[k]) / 2 - 0.05)
        # the leader starts in the segment middle, or off it when a line runs there
        offs = [0.0] + [o for i in range(1, int(room / step) + 1) for o in (i * step, -i * step)]
        for o in offs:
            beg = _add(ref, _mul(d, mid_t + o))
            # deslab: a little further out too, for the crowded joints of tracing strips (JK v7)
            for r in (rise, rise + 0.15, rise + 0.3, rise + 0.45, rise + 0.6, rise + 0.8, rise + 1.0):
                for sg in (out, -out):
                    for sh in (run, run + 0.15, run + 0.3, run + 0.45, run + 0.65):
                        mid = _add(beg, _mul(n, r))
                        end = _add(mid, _mul(d, sg * sh))
                        tbox = side_box(end, _add(end, _mul(d, sg * width)), read)
                        legs = [(beg, mid), (mid, end)]
                        if inside is not None and not all(inside(q) for q in ((tbox[0], tbox[1]), (tbox[2], tbox[3]),
                                                                              (tbox[0], tbox[3]), (tbox[2], tbox[1]))):
                            continue
                        if any(c is not ch and _seg_box(s_[0], s_[1], tbox) for c, s_ in lines):
                            continue
                        # deslab: its own chain's texts count too (run kv94: «215» and «340» of one
                        # shaft chain, both on leaders, one on the other)
                        if any(_box_hits(tbox, bx) for c, bx in boxes):
                            continue
                        if any(c is not ch and c != "mark" and _box_hits(_rect_of_segment(a_, b_, 0.01), bx)
                               for a_, b_ in legs for c, bx in boxes):
                            continue
                        # a leader may cross a line, never run along it
                        if any(c is not ch and _parallel(a_, b_, s_[0], s_[1])
                               and _seg_box(s_[0], s_[1], _rect_of_segment(a_, b_, 0.02))
                               for a_, b_ in legs for c, s_ in lines):
                            continue
                        info = {"beg": _xy(beg), "mid": _xy(mid), "end": _xy(end), "pen": pen,
                                "anchorAtTextEnd": _dot(_mul(d, sg), read) < 0}
                        return info, tbox
        return None

    fit = cfg.get("fitMargin", 0.12)

    def free(ch, tbox):
        return not any(c is not ch and _seg_box(s_[0], s_[1], tbox) for c, s_ in lines) and \
            not any(c is not ch and _box_hits(tbox, bx) for c, bx in boxes)

    def to_leader(ch, d, ref, ts, k, width):
        lead = leader_spot(ch, d, ref, ts, k, width)
        if lead is None:
            return False
        info, box = lead
        ch.setdefault("textLeaders", {})[k + 1] = info
        ch.setdefault("leaderBoxes", []).append(box)
        boxes.append((ch, box))
        # deslab: its legs are lines the texts placed later keep off (kv94m: the shelf of «1 550»
        # over «900»)
        lines.extend([(ch, (_pt(info["beg"]), _pt(info["mid"]))), (ch, (_pt(info["mid"]), _pt(info["end"])))])
        return True

    # deslab: first the texts already on leaders (their new spots must be known before the
    # ordinary texts look for theirs), then the ordinary ones
    for ch in chains:
        d, ref = _pt(ch["direction"]), _pt(ch["referencePoint"])
        ts = [_dot(_sub(_pt(p), ref), d) for p in ch["points"]]
        for k in range(len(ts) - 1):
            seg = abs(ts[k + 1] - ts[k])
            width = dimension_text_width(seg, profile)
            if k + 1 in ch.get("textLeaders", {}):
                # deslab: a short text put on its leader by plan_dimension_chains only knew the other
                # texts; when it lands on a zone stamp, an «Н» text, a tracing strip, a shaft, a label
                # or a line (run JK v7: «89», «114» on the zone circle, «117» on «616», «93» on a
                # strip), it gets a new spot the same way as a blocked ordinary text
                info = ch["textLeaders"][k + 1]
                m_, e_ = _pt(info["mid"]), _pt(info["end"])
                sg_d = _unit(_sub(e_, m_)) if _len(_sub(e_, m_)) > 1e-9 else d
                tbox = side_box(e_, _add(e_, _mul(sg_d, width)), reading(d))
                if not free(ch, tbox):
                    old = ch["textLeaders"].pop(k + 1)
                    # its old box out of the way while a new spot is looked for
                    mine = [i for i, (c, bx) in enumerate(boxes) if c is ch and bx[0] - 0.01 <= e_[0] <= bx[2] + 0.01
                            and bx[1] - 0.01 <= e_[1] <= bx[3] + 0.01]
                    kept = [boxes.pop(i) for i in reversed(mine)]
                    if not to_leader(ch, d, ref, ts, k, width):
                        boxes.extend(kept)
                        ch["textLeaders"][k + 1] = old
                        ch.setdefault("textConflict", []).append(int(round(seg * 1000)))
                    else:  # the old legs are gone
                        legs = {(_pt(old["beg"]), _pt(old["mid"])), (_pt(old["mid"]), _pt(old["end"]))}
                        lines[:] = [(c, s_) for c, s_ in lines if not (c is ch and tuple(s_) in legs)]
                continue
    for ch in chains:
        d, ref = _pt(ch["direction"]), _pt(ch["referencePoint"])
        ts = [_dot(_sub(_pt(p), ref), d) for p in ch["points"]]
        for k in range(len(ts) - 1):
            seg = abs(ts[k + 1] - ts[k])
            width = dimension_text_width(seg, profile)
            if k + 1 in ch.get("textLeaders", {}):
                continue
            if seg < short:
                continue
            mid_t = (ts[k] + ts[k + 1]) / 2
            if seg < width + fit:
                # deslab: Archicad itself moves a text that does not fit between the witness ticks
                # out of its segment, wherever it likes (run kv94l: «340» onto the chain «9 259»,
                # «321» onto «2 493»): such a text goes on our leader to a clear spot instead
                if to_leader(ch, d, ref, ts, k, width):
                    continue
            room = max(0.0, (seg - width) / 2)
            shifts = [0.0]
            i = 1
            while i * step <= room + 1e-9:
                shifts += [i * step, -i * step]
                i += 1
            placed = None
            for sh in shifts:
                c = _add(ref, _mul(d, mid_t + sh))
                box = text_box(c, d, width)
                if clear(ch, box):
                    placed = (sh, c, box)
                    break
            if placed is None:
                # deslab: no clear place on the line (runs JK and kv94: a label leader through
                # «315», «540», «1 370» in short segments): the text goes on a leader, as the
                # short ones do, to a spot where neither the leader nor the text is crossed
                if to_leader(ch, d, ref, ts, k, width):
                    continue
                ch.setdefault("textConflict", []).append(int(round(seg * 1000)))
                c = _add(ref, _mul(d, mid_t))
                boxes.append((ch, text_box(c, d, width, 0.0)))
                continue
            sh, c, box = placed
            # the gap is kept once between two texts: the next text's box carries it
            boxes.append((ch, text_box(c, d, width, 0.0)))
            if abs(sh) > 1e-9:
                ch.setdefault("textAt", {})[k + 1] = _xy(c)
    return chains


def plan_wet_zones(scene, profile):
    hatches = []
    for room in scene.get("rooms", []):
        kind = room.get("wet")
        if kind not in ("bathroom", "kitchen"):
            continue
        poly = room.get("wetPolygon") or room["polygon"]
        hatches.append({"room": room["id"], "kind": kind, "coordinates": [_xy(_pt(p)) for p in poly]})
    return hatches


# ---------------------------------------------------------------- building in Archicad

def _guid(item):
    return item["elementId"]["guid"]


def _elements(guids):
    return [{"elementId": {"guid": g}} for g in guids]


def _created(response, key, report=None, what=""):
    """Guids of the created elements; a refused item is None and its error goes to the report."""
    out = []
    for i, item in enumerate(response.get(key, [])):
        if "elementId" in item:
            out.append(item["elementId"]["guid"])
        else:
            out.append(None)
            if report is not None:
                report.errors.append(f"{what} #{i + 1}: {item.get('error', item)}")
    return out


class Report:
    def __init__(self):
        self.steps = []
        self.errors = []
        self._t = time.perf_counter()

    def step(self, name, **info):
        now = time.perf_counter()
        self.steps.append({"step": name, "seconds": round(now - self._t, 2), **info})
        self._t = now

    def as_dict(self):
        return {"seconds": round(sum(s["seconds"] for s in self.steps), 2), "steps": self.steps, "errors": self.errors}


def find_surface(ac, rule):
    """A surface by its exact name, else the first whose name contains one of rule["like"]."""
    if not rule:
        return None
    surfaces = ac.tapir("GetAttributesByType", {"attributeType": "Surface"}).get("attributes", [])
    if rule.get("name"):
        return next((a for a in surfaces if a.get("name") == rule["name"]), None)
    for part in rule.get("like", []):
        for a in surfaces:
            if part in a.get("name", "").lower():
                return a
    return None


def layer_indices(ac, names):
    result = ac.tapir("GetAttributesByType", {"attributeType": "Layer"})
    by_name = {a["name"]: a["index"] for a in result.get("attributes", []) if "name" in a}
    return {n: by_name.get(n) for n in names}


def open_layers(ac, names, report):
    """deslab: Archicad refuses changes on a hidden or locked layer, and that depends on the view
    open at the moment (run 7 opened in «22. Развертки кухни»: the zone stamps were not moved).
    The bridge's layers are shown and unlocked for the build. Returns the flags to put back."""
    attrs = ac.tapir("GetAttributesByType", {"attributeType": "Layer"}).get("attributes", [])
    ids = [a for a in attrs if (names is None or a.get("name") in names) and a.get("attributeId")]
    if not ids:
        return []
    info = ac.tapir("GetLayers", {"attributeIds": [{"attributeId": a["attributeId"]} for a in ids]}).get("layers", [])
    closed = [l for l in info if l.get("isHidden") or l.get("isLocked")]
    if closed:
        ac.tapir("CreateLayers", {"overwriteExisting": True, "layerDataArray": [
            {"attributeId": l["attributeId"], "name": l["name"], "isHidden": False, "isLocked": False} for l in closed]})
        report.steps.append({"step": "слои открыты на время построения", "seconds": 0,
                             "layers": [l["name"] for l in closed]})
    return closed


def restore_layers(ac, closed):
    if closed:
        ac.tapir("CreateLayers", {"overwriteExisting": True, "layerDataArray": [
            {"attributeId": l["attributeId"], "name": l["name"], "isHidden": bool(l.get("isHidden")),
             "isLocked": bool(l.get("isLocked"))} for l in closed]})


def _check_details(response, report, what):
    """SetDetailsOfElements answers per element; a refused one goes into the report."""
    bad = [r for r in (response or {}).get("executionResults", []) if not r.get("success", True)]
    if bad:
        report.errors.append(f"{what}: не применено к {len(bad)} элементам ({bad[0].get('error', {}).get('message', '')})")


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def _redo_endless_labels(ac, data, guids, report, max_mm=50.0):
    """deslab: now and then Archicad makes a label with a text block hundreds of metres wide,
    drawn as an endless shelf (runs 7b and the label test). Such a label is deleted and made
    again once; a normal block is 15-18 mm on paper."""
    def broken(i, d):
        width = _find_key(d, "boxWidth")
        # an endless shelf (hundreds of mm) or no text drawn at all (0 mm, run 8b)
        if width is not None and not (5.0 < width < max_mm):
            return True
        mid = _find_key(d, "midCoordinate")
        sent = data[i]["midCoordinate"]
        # run 8: a re-made label kept its block but got a mid point 1.5 km away
        return bool(mid) and abs(mid.get("x", 0) - sent["x"]) + abs(mid.get("y", 0) - sent["y"]) > 0.01

    guids = list(guids)
    redone = 0
    for attempt in range(4):
        live = [(i, g) for i, g in enumerate(guids) if g]
        if not live:
            break
        try:
            details = ac.tapir("GetDetailsOfElements", {"elements": _elements([g for _, g in live])}).get("detailsOfElements", [])
        except Exception:
            break
        bad = [i for (i, _), d in zip(live, details) if broken(i, d)]
        if not bad:
            break
        if attempt == 3:
            report.errors.append(f"Сломанная выноска (без текста или с бесконечной полкой) осталась после трёх попыток ({len(bad)}): удалить и поставить вручную")
            break
        ac.tapir("DeleteElements", {"elements": _elements([guids[i] for i in bad])})
        again = _created(ac.tapir("CreateLabels", {"labelsData": [data[i] for i in bad]}), "elements", report, "Выноска")
        for i, g in zip(bad, again):
            guids[i] = g
        redone += len(bad)
    if redone:
        report.steps.append({"step": "сломанные выноски пересозданы", "seconds": 0, "count": redone})
    return guids


def split_corner_windows(scene):
    """deslab: a corner window (Игорь 04.11.2026: one window round the corner, Archicad's own
    corner window, not two windows with the wall corner between) is two openings in the scene,
    one on each wall, the first with "corner": {"partner": "<id of the other>"}. They are taken
    out of the scene for BuildModelFromScan and built afterwards by build_corner_windows.
    Returns the scene without them and the list of (wall, opening, partner wall, partner)."""
    by_id = {o["id"]: (w, o) for w in scene.get("walls", []) for o in w.get("openings", [])}
    pairs, skip = [], set()
    for w in scene.get("walls", []):
        for o in w.get("openings", []):
            partner = (o.get("corner") or {}).get("partner")
            if partner and partner in by_id:
                pw, po = by_id[partner]
                pairs.append((w, o, pw, po))
                skip |= {o["id"], partner}
    if not pairs:
        return scene, []
    out = json.loads(json.dumps(scene))
    for w in out.get("walls", []):
        w["openings"] = [o for o in w.get("openings", []) if o["id"] not in skip]
    return out, pairs


def _corner_end(wall, other):
    """0 when the wall's start is at the corner with the other wall, 1 when its end is."""
    a, b = _pt(wall["start"]), _pt(wall["end"])
    oa, ob = _pt(other["start"]), _pt(other["end"])
    u = _unit(_sub(ob, oa))
    dist = lambda p: abs(_dot(_sub(p, oa), _left(u)))  # noqa: E731
    return 0 if dist(a) < dist(b) else 1


# deslab: the line pens of Archicad's corner window. The template's override rule «Окна чб» takes
# only the element type Window, not Corner Window, so the pair came out in its own colours; it
# gets the rule's pen 161 on all its line pens instead (test of 04.11.2026 on kv94)
CORNER_WINDOW_PENS = ("gs_penJoint", "gs_penPlug", "gs_custom_cc_separator_pen", "or_opLinePen", "gs_pen_2D",
                      "gs_frame_pen_cont", "gs_trim_pen_cont", "gs_wcont_line_pen", "gs_sill_pen_3D",
                      "gs_sill_pen_sectcont", "gs_sill_pen_ins_sectcont", "gs_board_pen_3D", "gs_board_pen_sectcont",
                      "gs_sunShade_cutLinesPen", "gs_sunShade_unCutLinesPen", "gs_frame_pen", "gs_sash_pen",
                      "gs_glass_pen", "gs_masonry_arch_pen", "gs_transom_display_pen", "gs_head_symbol_pen")


def build_corner_windows(ac, pairs, wall_guid, report, setback=0.12, joint="frame", favorite=None, two_sash_from=1.0,
                         pen=161, three_sash_favorite=None, three_sash_from=1.9, three_sash_source=None):
    """Builds each corner window as in the recipe tested on 27.10.2026 (тест-угол.pln): CreateWindows
    on the first wall with cornerWindow, Archicad adds the linked twin on the other wall; each
    width is then set so the far edge lands where the scene has it (Archicad keeps the corner
    edge); the frames meet frame to frame or at a corner post (joint)."""
    built = {}
    for w, o, pw, po in pairs:
        if w["id"] not in wall_guid or pw["id"] not in wall_guid:
            report.errors.append(f"Угловое окно {o['id']}/{po['id']}: стены не построены")
            continue
        before = {_guid(e) for e in ac.tapir("GetElementsByType", {"elementType": "Window"}).get("elements", [])}
        res = ac.tapir("CreateWindows", {"windowsData": [{
            "ownerWallId": {"guid": wall_guid[w["id"]]}, "centerOffset": float(o["centerOffset"]),
            "width": float(o["width"]), "height": float(o["height"]), "sillHeight": float(o.get("sill", 0)),
            "cornerWindow": True}]})
        guids = _created(res, "elements", report, f"Угловое окно {o['id']}")
        if not guids or not guids[0]:
            continue
        first = guids[0]
        after = {_guid(e) for e in ac.tapir("GetElementsByType", {"elementType": "Window"}).get("elements", [])}
        twins = list(after - before - {first})
        if len(twins) != 1:
            report.errors.append(f"Угловое окно {o['id']}: Archicad не поставил пару на {pw['id']} (новых окон {len(twins)})")
            continue
        second = twins[0]
        # Archicad's corner window is always the one-sash «Окно 27»: a wide part takes the two-sash
        # favorite (rule 6а of 27.10: the link holds, width and joint are set again below)
        # Archicad gives both parts of a linked pair one library part (test of 04.11.2026), so the
        # wider part decides: three sashes from three_sash_from, two from two_sash_from
        widest = max(float(o["width"]), float(po["width"]))
        fav = three_sash_favorite if three_sash_favorite and widest > three_sash_from else \
            (favorite if widest >= two_sash_from else None)
        if fav and fav == three_sash_favorite:
            # the office template copy may not have it (run of 05.11.2026): made from a three-sash
            # window of this model
            have = ac.tapir("GetFavoritesByType", {"elementType": "Window"}).get("favorites", [])
            if fav not in have:
                if three_sash_source:
                    _check_details(ac.tapir("CreateFavoritesFromElements", {"favoritesFromElements": [
                        {"elementId": {"guid": three_sash_source}, "favorite": fav}]}), report,
                        f"Избранное «{fav}»")
                else:
                    report.errors.append(f"Угловое окно {o['id']}: нет избранного «{fav}» и трёхстворчатого окна, "
                                         f"чтобы его сделать; поставлено двустворчатое")
                    fav = favorite
        if fav:
            _check_details(ac.tapir("ApplyFavoritesToElements", {"favoritesToApply": [
                {"elementId": {"guid": first}, "favorite": fav}]}), report, f"Угловое окно: избранное «{fav}»")
            if fav == three_sash_favorite:
                ac.tapir("SetGDLParametersOfElements", {"elementsWithGDLParameters": [
                    {"elementId": {"guid": g}, "gdlParameters": [{"name": "gs_iPanesConfig", "value": 1}]}
                    for g in (first, second)]})
        # the far edge of each part where the scene has it; the corner edge stays Archicad's
        mods = []
        for guid, wall, op, other in ((first, w, o, pw), (second, pw, po, w)):
            length = _len(_sub(_pt(wall["end"]), _pt(wall["start"])))
            at_end = _corner_end(wall, other) == 1
            c, half = float(op["centerOffset"]), float(op["width"]) / 2
            far = c - half if at_end else c + half
            det = ac.tapir("GetDetailsOfElements", {"elements": [{"elementId": {"guid": guid}}]})
            d = (det.get("detailsOfElements") or [{}])[0].get("details", {})
            cur_c, cur_w = float(d.get("centerOffset", c)), float(d.get("width", op["width"]))
            corner = cur_c + cur_w / 2 if at_end else cur_c - cur_w / 2
            width = abs(corner - far)
            mods.append({"elementId": {"guid": guid}, "width": round(width, 4),
                         "centerOffset": round((corner + far) / 2, 4), "revealDepthOffset": setback})
            if not (0 <= far <= length):
                report.errors.append(f"Угловое окно {op['id']}: дальний край вне стены {wall['id']}")
        ac.tapir("ModifyWindows", {"windowsWithDetails": mods})
        # opening into the room like the other windows: oSide on the FIRST part only, Archicad
        # turns the linked twin itself (test of 04.11.2026 on kv94)
        ac.tapir("ModifyWindows", {"windowsWithDetails": [{"elementId": {"guid": first}, "oSide": True}]})
        # the frames meet: 1 «Коробка» (frame to frame, Игорь 04.11.2026: «не с колонной»),
        # 2 «Угловой Импост» (a corner post); per window "corner": {"joint": ...} or the profile
        kind = {"frame": 1, "mullion": 2}.get((o.get("corner") or {}).get("joint", joint), 1)
        ac.tapir("SetGDLParametersOfElements", {"elementsWithGDLParameters": [
            {"elementId": {"guid": g}, "gdlParameters": [
                {"name": "iCornerConnTypeLeft", "value": kind}, {"name": "iCornerConnTypeRight", "value": kind},
                {"name": "gs_corner_column", "value": kind == 2}, {"name": "gs_window_handle", "value": True},
                {"name": "gs_sill_outside", "value": True}, {"name": "gs_board_inside", "value": False}]
                + [{"name": n, "value": pen} for n in CORNER_WINDOW_PENS]}
            for g in (first, second)]})
        # check: the twin is still linked, and the far edges where they should be
        check = ac.tapir("GetDetailsOfElements", {"elements": [{"elementId": {"guid": g}} for g in (first, second)]})
        for m, item in zip(mods, check.get("detailsOfElements", [])):
            d = item.get("details", {})
            if abs(float(d.get("width", 0)) - m["width"]) > 0.005:
                report.errors.append(f"Угловое окно: ширина {d.get('width')} вместо {m['width']}")
        built[o["id"]], built[po["id"]] = first, second
        report.step("угловое окно", pair=f"{o['id']}+{po['id']}", widths=[m["width"] for m in mods])
    return built


def set_layer(ac, guids, index, report, what):
    guids = [g for g in guids if g]
    if not guids or not index:
        if guids and not index:
            report.errors.append(f"Слой для «{what}» не найден в проекте: элементы остались на текущем слое.")
        return
    _check_details(ac.tapir("SetDetailsOfElements", {"elementsWithDetails": [
        {"elementId": {"guid": g}, "details": {"layerIndex": index}} for g in guids]}), report, f"Слой «{what}»")


def analyze_scan(ac, e57_path, **helper_options):
    """Runs the scan helper on the drafting PC through the add-on, builds nothing. Returns the scene path."""
    params = {"e57Path": e57_path, "buildWalls": False, "buildOpenings": False, "buildSlabs": False, "buildZones": False}
    params.update({k: v for k, v in helper_options.items() if v is not None})
    response = ac.tapir("BuildModelFromScan", params, timeout=600)
    return response.get("scenePath") or e57_path + ".scene.json"


def build_measured_plan(ac, scene, profile=None, rooms=None, origin=None, floor_index=0, import_favorites=True):
    """Builds the model and the measured-plan drafting from a scene. Returns a report dict.

    The bridge's layers are shown and unlocked during the build, whatever view is open, and
    put back as they were afterwards."""
    profile = profile or load_profile()
    if not ac.alive():
        raise RuntimeError("Archicad не отвечает. Откройте проект (копию шаблона) и закройте открытые диалоги.")
    opened = Report()
    names = set(profile["layers"].values())
    # deslab: 08.11.2026, in a project set up by ApplyOfficeStandard the favorites put dimensions and labels on the
    # template's layer indices (hidden «2D - Узловая Точка», «MEP - …ВК»), so their leaders and the layer change were
    # refused: all layers are opened for every build
    names = None
    if scene.get("mep") or scene.get("tracing"):
        # deslab: shaft and tracing walls and the objects are made on the tool's default layer
        # first, which may be hidden («009 …ПЛИТКА», run kv94i): then no layer change takes; all
        # layers are opened for the build
        names = None
    closed = open_layers(ac, names, opened)
    try:
        out = _build_measured_plan(ac, scene, profile, rooms, origin, floor_index, import_favorites)
    finally:
        restore_layers(ac, closed)
    out["steps"] = opened.steps + out["steps"]
    return out


def _build_measured_plan(ac, scene, profile, rooms, origin, floor_index, import_favorites):
    if scene.get("format") != SCENE_FORMAT:
        raise ValueError(f"Задание не в формате {SCENE_FORMAT}")
    faults = check_room_faces(scene)
    fitted = []
    if faults and not scene.get("allowLooseRoomEdges"):
        # deslab: small misses of a scanned scene are fitted first (JK 08.11.2026), what is left still stops the build
        scene, fitted = fit_walls_to_rooms(scene)
        faults = check_room_faces(scene)
    if faults and not scene.get("allowLooseRoomEdges"):
        raise ValueError("Контур помещения не лежит на гранях стен, план не строю (размеры по контуру "
                         "были бы неверны):\n" + "\n".join(faults))
    scene = shift_scene(apply_room_overrides(json.loads(json.dumps(scene)), rooms), origin)
    for room in scene.get("rooms", []):
        room.setdefault("name", f"{profile['zone']['defaultName']} {room.get('number', '')}".strip())
    report = Report()
    if fitted:
        report.step("Стены подогнаны к контуру помещения", notes=fitted)
    L, F = profile["layers"], profile["favorites"]

    if not ac.alive():
        raise RuntimeError("Archicad не отвечает. Откройте проект (копию шаблона) и закройте открытые диалоги.")
    if import_favorites and profile.get("favoritesFile"):
        # The office favorites («ОБМЕР шаблон …») are not in every template copy: import them, keep existing ones.
        try:
            ac.tapir("ImportFavorites", {"path": profile["favoritesFile"], "conflictPolicy": "Skip"})
        except Exception as error:
            report.errors.append(f"Избранное офиса не загружено из {profile['favoritesFile']}: {error}")
    idx = layer_indices(ac, set(L.values()))
    report.step("проверка проекта", missingLayers=[n for n, i in idx.items() if not i])

    # 1. Model in one add-on call.
    params = {"scene": scene, "floorIndex": floor_index,
              "favorites": {k: F[k] for k in ("wall", "door", "window", "zone") if F.get(k)},
              "layers": {"walls": L["bearingWalls"], "zones": L["zones"]},
              "materials": {"measuredWalls": profile["walls"]["material"], "assumedWalls": profile["walls"]["material"]}}
    model_scene, corner_pairs = split_corner_windows(scene)
    params["scene"] = model_scene
    model = ac.tapir("BuildModelFromScan", params, timeout=600)
    report.errors += model.get("errors", [])
    wall_guid = {w["id"]: _guid(w) for w in model.get("walls", [])}
    opening_guid = {o["id"]: _guid(o) for o in model.get("windows", []) + model.get("doors", [])}
    # deslab: three even panes in a three-sash window (Игорь 04.11.2026); the add-on does it too
    # from 240c9c5, this keeps older add-ons right
    wide = [opening_guid[o["id"]] for w in model_scene.get("walls", []) for o in w.get("openings", [])
            if o.get("kind") == "window" and float(o.get("width", 0)) > 1.9 and o["id"] in opening_guid]
    if wide and not F.get("window"):
        _check_details(ac.tapir("SetGDLParametersOfElements", {"elementsWithGDLParameters": [
            {"elementId": {"guid": g}, "gdlParameters": [{"name": "gs_iPanesConfig", "value": 1}]} for g in wide]}),
            report, "Равные створки")
    if corner_pairs:
        op_cfg = profile.get("openings", {})
        opening_guid.update(build_corner_windows(ac, corner_pairs, wall_guid, report,
                                                 joint=op_cfg.get("cornerJoint", "frame"),
                                                 favorite=op_cfg.get("cornerTwoSashFavorite"),
                                                 two_sash_from=op_cfg.get("doubleSashMinWidth", 1.0),
                                                 pen=op_cfg.get("cornerWindowPen", 161),
                                                 three_sash_favorite=op_cfg.get("cornerThreeSashFavorite"),
                                                 three_sash_source=wide[0] if wide and not F.get("window") else None))
    zone_guid = {z["id"]: _guid(z) for z in model.get("zones", [])}
    zone_guids = list(zone_guid.values())
    report.step("модель", walls=len(wall_guid), openings=len(opening_guid), zones=len(zone_guids))

    # deslab: the door swing of the scene (kv94 entrance door, Игорь 05.11.2026: «открывание
    # двери очень важно»): reflected = the hinges at the other jamb, oSide = opening to the other side
    swings = [{"elementId": {"guid": opening_guid[o["id"]]}, **{k: bool(o[k]) for k in ("reflected", "oSide") if k in o}}
              for w in model_scene.get("walls", []) for o in w.get("openings", [])
              if o.get("kind") == "door" and o["id"] in opening_guid and ("reflected" in o or "oSide" in o)]
    if swings:
        _check_details(ac.tapir("ModifyDoors", {"doorsWithDetails": swings}), report, "Открывание двери")
    report.step("открывание дверей", doors=len(swings))

    # deslab: shafts (walls of 100 mm blocks), the tracing (low walls of the first row of blocks),
    # risers, radiators and the panel (Игорь 05.11.2026)
    from .mep import mep_objects, shaft_walls, tracing_walls
    mep_layer = idx.get(L.get("mep", L["partitions"]))
    extra_walls = shaft_walls(scene) + tracing_walls(scene)
    if extra_walls:
        data = [{"begCoordinate": w["start"], "endCoordinate": w["end"], "floorIndex": floor_index, "zCoordinate": 0.0,
                 "height": w["height"], "thickness": w["thickness"], "referenceLineLocation": "Center",
                 "structureType": "Basic"} for w in extra_walls]
        # (no "id": the add-on 2ae7b30 creates the wall, then reports it as failed when it cannot
        # set the id, and the bridge lost it: run kv94h, 05.11.2026, shafts on a hidden layer)
        guids = _created(ac.tapir("CreateWalls", {"wallsData": data}), "elements", report, "Стена шахты/трассировки")
        for w, g in zip(extra_walls, guids):
            if g:
                wall_guid[w["id"]] = g
        set_layer(ac, [g for w, g in zip(extra_walls, guids) if g and w["kind"] != "tracing"], mep_layer, report, "шахты")
        # deslab: shaft walls in the template's material, «Штукатурка - Гипсовая» (Игорь 06.11.2026:
        # the shafts came out orange; his kv94 example has gypsum plaster walls 100 mm)
        mat_name = profile.get("mep", {}).get("shaftMaterial")
        if mat_name:
            mats = ac.tapir("GetAttributesByType", {"attributeType": "BuildingMaterial"}).get("attributes", [])
            mat = next((m for m in mats if m.get("name") == mat_name), None)
            shaft_g = [g for w, g in zip(extra_walls, guids) if g and w["kind"] != "tracing"]
            if mat is None:
                report.errors.append(f"Материал шахт «{mat_name}» не найден в проекте: шахты в материале по умолчанию.")
            elif shaft_g:
                _check_details(ac.tapir("ModifyWalls", {"wallsWithDetails": [
                    {"elementId": {"guid": g}, "structureType": "Basic", "buildingMaterialId": mat["attributeId"]}
                    for g in shaft_g]}), report, "Материал шахты")
        set_layer(ac, [g for w, g in zip(extra_walls, guids) if g and w["kind"] == "tracing"],
                  idx.get(L.get("tracing", L["partitions"])), report, "трассировка")
        scene["walls"] = scene.get("walls", []) + extra_walls
    from .mep import mep_outlines
    outlines = mep_outlines(scene, profile)
    if outlines:
        panel_layer = idx.get(L.get("panel")) or mep_layer  # deslab: the panel on the engineering plan
        guids = _created(ac.tapir("CreatePolylines", {"polylinesData": [
            {"coordinates": pts, "floorInd": floor_index, **({"layerIndex": panel_layer} if panel_layer else {}),
             **({"linePenIndex": profile.get("mep", {}).get("pen")} if profile.get("mep", {}).get("pen") else {})}
            for pts in outlines]}), "elements", report, "Контур щитка")
    objects = mep_objects(scene, profile)
    if objects:
        guids = _created(ac.tapir("CreateObjects", {"objectsData": [
            {**d, "floorIndex": floor_index, "id": m["id"]} for m, d, _ in objects]}), "elements", report, "Коммуникации")
        gdl = [{"elementId": {"guid": g}, "gdlParameters": params} for (m, d, params), g in zip(objects, guids) if g and params]
        if gdl:
            _check_details(ac.tapir("SetGDLParametersOfElements", {"elementsWithGDLParameters": gdl}), report, "Размер стояка")
        # deslab: risers (pipes) on «002 Инженерные узлы», shown on both plans; radiators and the panel
        # on the engineering plan only (Игорь 06.11.2026)
        for kinds, key in ((("sewerRiser", "waterRiser"), "risers"), (("radiator",), "radiators"), (("panel",), "panel")):
            set_layer(ac, [g for (m, _, _), g in zip(objects, guids) if g and m.get("kind") in kinds],
                      idx.get(L.get(key)) or mep_layer, report, "коммуникации")
    report.step("шахты, трассировка, стояки, радиаторы", walls=len(extra_walls), objects=len(objects))

    partitions = [wall_guid[w["id"]] for w in scene.get("walls", []) if is_partition(w, profile) and w["id"] in wall_guid]
    set_layer(ac, partitions, idx.get(L["partitions"]), report, "перегородки")
    if zone_guids:
        ac.tapir("UnlockElements", {"elements": _elements(zone_guids)})
    report.step("слои стен, зоны разблокированы", partitions=len(partitions))

    # Zone stamps in the middle of the room (the «Н» text goes under them).
    if profile["zone"].get("stampAtCentre") and zone_guid:
        items = []
        for room in scene.get("rooms", []):
            if room["id"] in zone_guid:
                c = zone_spot(scene, room)
                items.append({"elementId": {"guid": zone_guid[room["id"]]},
                              "details": {"typeSpecificDetails": {"stampPosition": _xy(c)}}})
        _check_details(ac.tapir("SetDetailsOfElements", {"elementsWithDetails": items}), report, "Штамп зоны в центр")

    # Walls painted on every side and on the ends with one plain surface (no grey concrete in 3D).
    surface = find_surface(ac, profile["walls"].get("surface"))
    if surface and wall_guid:
        paint = {"overridden": True, "attributeId": surface["attributeId"]}
        ac.tapir("ModifyWalls", {"wallsWithDetails": [
            {"elementId": {"guid": g}, "referenceMaterial": paint, "oppositeMaterial": paint, "sideMaterial": paint,
             "inheritEndSurface": False} for g in wall_guid.values()]})
    elif wall_guid:
        report.errors.append("Покрытие-краска для стен не найдено: стены остались в покрытии материала.")

    # No inner window board (sill) in a concrete flat.
    windows = [_guid(w) for w in model.get("windows", [])]
    if windows and profile["openings"].get("innerBoard") is False:
        ac.tapir("SetGDLParametersOfElements", {"elementsWithGDLParameters": [
            {"elementId": {"guid": g}, "gdlParameters": [{"name": "gs_board_inside", "value": False}]} for g in windows]})
    report.step("штампы зон, покраска стен, без подоконников", surface=(surface or {}).get("name"))

    # 2. Height texts under the zone stamps.
    texts = plan_height_texts(scene, profile)
    if texts:
        # deslab: «Н 3312» centred right under the number circle (the stamp is at the same x),
        # one block with it (Игорь, 03.11.2026)
        data = [{"coordinate": t["coordinate"], "text": t["text"], "floorIndex": floor_index,
                 "style": {"justification": "Center", "anchor": "MiddleTop"},
                 **({"favoriteName": F["heightText"]} if F.get("heightText") else {}),
                 **({"layerIndex": idx[L["zones"]]} if idx.get(L["zones"]) else {})} for t in texts]
        guids = _created(ac.tapir("CreateTexts", {"textsData": data}), "elements", report, "Текст Н")
        ac.tapir("UnlockElements", {"elements": _elements([g for g in guids if g])})
    report.step("тексты Н", count=len(texts))

    # 3. Opening labels, clear of walls, dimension chains, «Н» texts, stamps and each other.
    chains = plan_dimension_chains(scene, profile)
    obmer_chains = [c for c in chains if c.get("plan") != "eng"]
    eng_chains = [c for c in chains if c.get("plan") == "eng"]
    split = bool(profile.get("mep", {}).get("splitViews"))
    obstacles = label_obstacles(scene, profile, texts, obmer_chains)
    labels = plan_opening_labels(scene, profile, obstacles)
    from .mep import plan_mep_labels
    if split:
        # deslab: the engineering labels on their own plan: clear of its chains, not of the opening
        # labels, «Н» or the zone stamp, which are not shown there
        eng_obstacles = label_obstacles({**scene, "rooms": []}, profile, [], eng_chains)
        eng_labels = plan_mep_labels(scene, profile, eng_obstacles, taken=[])
    else:
        eng_labels = plan_mep_labels(scene, profile, obstacles, taken=[lab["box"] for lab in labels])
    from .mep import mep_marks
    strips = [_rect_of_segment(_pt(t["start"]), _pt(t["end"]), float(t.get("thickness", 0.08)) / 2)
              for t in scene.get("tracing", [])]
    stamps = [m for m in obstacles["marks"] if m not in [tuple(b) for ch in chains for b in ch.get("leaderBoxes", [])]]
    # deslab: a text on a leader stays on the flat or its walls (JK v7: «1 039» beyond the right wall)
    area = [[_pt(p) for p in r["polygon"]] for r in scene.get("rooms", [])]
    wall_rects = [_rect_of_segment(_pt(w["start"]), _pt(w["end"]), float(w.get("thickness", 0.2)) / 2)
                  for w in scene.get("walls", [])]

    def on_flat(q):
        return any(_inside(r, q) for r in area) or any(b[0] - 1e-3 <= q[0] <= b[2] + 1e-3 and b[1] - 1e-3 <= q[1] <= b[3] + 1e-3
                                                     for b in wall_rects)
    if split:
        place_dimension_texts(obmer_chains, labels, profile, marks=stamps + strips + mep_marks(scene), inside=on_flat)
        place_dimension_texts(eng_chains, eng_labels, profile, marks=strips + mep_marks(scene), inside=on_flat)
    else:
        place_dimension_texts(chains, labels + eng_labels, profile, marks=stamps + strips + mep_marks(scene), inside=on_flat)
    n_open = len(labels)
    labels = labels + eng_labels
    data = []
    for lab in labels:
        item = {k: lab[k] for k in ("runs", "begCoordinate", "midCoordinate", "endCoordinate")}
        # deslab: the lines of a label are aligned by their first letter «h» (Игорь 04.11.2026),
        # so Left; it holds when sent together with the runs (test of 04.11 on kv94, no endless
        # shelf; an endless one is redone by _redo_endless_labels anyway)
        item["style"] = {"justification": "Left"}
        if profile["labels"].get("textSpacing") is not None:  # Archicad's line spacing of the label text
            item["style"]["spacing"] = profile["labels"]["textSpacing"]
        if F.get("label"):
            item["favoriteName"] = F["label"]
        else:  # rules: no blue fill, frame or bold when the favorite is missing
            item["style"].update({"bold": False, "usedContour": False, "usedFill": False})
            item["leaderLine"] = {"framed": False}
        # Not associative: an associative label moves its dot to the element's own anchor.
        data.append(item)
    if data:
        guids = _created(ac.tapir("CreateLabels", {"labelsData": data}), "elements", report, "Выноска")
        guids = _redo_endless_labels(ac, data, guids, report)
        ac.tapir("UnlockElements", {"elements": _elements([g for g in guids if g])})
        eng_label_layer = idx.get(L.get("engLabels")) if split else None
        set_layer(ac, [g for g in guids[:n_open] if g], idx.get(L["labels"]), report, "выноски")
        set_layer(ac, [g for g in guids[n_open:] if g], eng_label_layer or idx.get(L["labels"]), report, "выноски")
    report.step("выноски проёмов", count=len(data))

    # 4. Dimension chains: hotspots at the points, dimensions on the hotspots.
    points = [p for ch in chains for p in ch["points"]]
    dim_guids = []
    if points:
        hs = _created(ac.tapir("CreateHotspots", {"hotspotsData": [
            {"position": p, **({"layerIndex": idx[L["dimensions"]]} if idx.get(L["dimensions"]) else {})} for p in points]}), "elements", report, "Точка размера")
        k = 0
        dims = []
        for ch in chains:
            ids = hs[k:k + len(ch["points"])]
            k += len(ch["points"])
            if None in ids or len(ids) < 2:
                report.errors.append(f"Размер {ch['room']}/{ch['edge']}: точки не созданы")
                continue
            ch["_made"] = True
            tl = ch.get("textLeaders", {})
            ta = ch.get("textAt", {})
            d = {"referencePoint": ch["referencePoint"], "direction": ch["direction"], "floorIndex": floor_index,
                 "witnessPoints": [{"elementId": {"guid": g}, **({"textLeader": tl[j]} if j in tl else {}),
                                    **({"textAt": ta[j]} if j in ta and j not in tl else {})}
                                   for j, g in enumerate(ids)]}
            if F.get("dimension"):
                d["favoriteName"] = F["dimension"]
            # deslab: 08.11.2026, the favorite does not set the dimension text, and a new project's tool defaults are not
            # the template's: font, size and pen of the texts are sent (profile "dimensionText")
            for key, value in (profile.get("dimensionText") or {}).items():
                d[{"font": "textFontName", "size": "textHeight", "pen": "textPen"}[key]] = value
            dims.append(d)
        made = _created(ac.tapir("CreateAssociativeDimensions", {"dimensionsData": dims}), "elements", report, "Размер")
        dim_guids = [g for g in made if g]
        ac.tapir("UnlockElements", {"elements": _elements(dim_guids)})
        # deslab: the engineering chains on the engineering plan's layer, with their hotspots
        made_plans = [ch.get("plan") for ch in chains if ch.get("_made")]
        eng_dim_layer = idx.get(L.get("engDimensions")) if split else None
        set_layer(ac, [g for g, pl in zip(made, made_plans) if g and pl != "eng"], idx.get(L["dimensions"]), report, "размеры")
        set_layer(ac, [g for g, pl in zip(made, made_plans) if g and pl == "eng"], eng_dim_layer or idx.get(L["dimensions"]),
                  report, "размеры")
        if eng_dim_layer:
            hs_plans = [ch.get("plan") for ch in chains for _ in ch["points"]]
            set_layer(ac, [g for g, pl in zip(hs, hs_plans) if g and pl == "eng"], eng_dim_layer, report, "точки размеров")
    # deslab: no dimension line on top of another one of the same plan (Игорь 06.11.2026, JK)
    for i, c1 in enumerate(chains):
        a1, b1 = _chain_line(c1)
        d1 = _pt(c1["direction"])
        for c2 in chains[i + 1:]:
            if c1.get("plan") != c2.get("plan"):
                continue
            a2, b2 = _chain_line(c2)
            if abs(_dot(d1, _pt(c2["direction"]))) > 0.99 and abs(_dot(_sub(a2, a1), _left(d1))) < 0.05:
                t = sorted([_dot(_sub(a2, a1), d1), _dot(_sub(b2, a1), d1)])
                if t[0] < _len(_sub(b1, a1)) - 0.01 and t[1] > 0.01:
                    report.errors.append(f"Размер {c1['room']}/{c1['edge']}: линия совпадает с линией {c2['edge']}, поправить")
    for ch in chains:
        if ch.get("conflict"):
            report.errors.append(f"Размер {ch['room']}/{ch['edge']}: наложение на цепочки {ch['conflict']}, поправить вручную")
        if ch.get("textConflict"):
            report.errors.append(f"Размер {ch['room']}/{ch['edge']}: текст {ch['textConflict']} пересекается линией и сдвинуть некуда, поправить вручную")
        if ch.get("leaderConflict"):
            report.errors.append(f"Размер {ch['room']}/{ch['edge']}: текст на выноске у точек {ch['leaderConflict']} не нашёл свободного места, поправить вручную")
    report.step("размерные цепочки", chains=len(chains), dimensions=len(dim_guids))

    # 5. Wet zones.
    wet = plan_wet_zones(scene, profile)
    if wet:
        data = []
        for h in wet:
            fav = F.get("wetBathroom" if h["kind"] == "bathroom" else "wetKitchen")
            item = {"coordinates": h["coordinates"], "floorInd": floor_index}
            if fav:
                item["favoriteName"] = fav
            else:
                item["foregroundColor"] = profile["wet"][h["kind"]]["foreground"]
            if idx.get(L["wetZones"]):
                item["layerIndex"] = idx[L["wetZones"]]
            data.append(item)
        _created(ac.tapir("CreateHatches", {"hatchesData": data}), "elements", report, "Мокрая зона")
    report.step("мокрые зоны", count=len(wet))

    # 6. Drawings on the layouts.
    try:
        drawings = ac.tapir("GetElementsByType", {"elementType": "Drawing"}).get("elements", [])
        if drawings:
            ac.tapir("UpdateDrawings", {"elements": drawings})
        report.step("чертежи на листах обновлены", drawings=len(drawings))
    except Exception as error:  # a missing drawing must not lose the built plan
        report.errors.append(f"Обновление чертежей: {error}")

    out = report.as_dict()
    out["archicadCalls"] = len(ac.log)
    out["assumptions"] = scene.get("assumptions", [])
    out["warnings"] = scene.get("warnings", [])
    return out
