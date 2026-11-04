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
    for item in scene.get("rooms", []) + scene.get("slabs", []):
        for key in ("polygon", "wetPolygon"):
            for p in item.get(key) or []:
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


def plan_height_texts(scene, profile):
    texts = []
    fmt = profile["zone"]["heightTextFormat"]
    for room in scene.get("rooms", []):
        poly = [_pt(p) for p in room["polygon"]]
        c = polygon_centroid(poly)
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
    walls, dims, marks, dim_dirs = [], [], [], []
    for wall in scene.get("walls", []):
        a, b = _pt(wall["start"]), _pt(wall["end"])
        u = _unit(_sub(b, a))
        if abs(u[0]) > 0.99 or abs(u[1]) > 0.99:  # axis-aligned walls: their exact rectangle
            walls.append(_rect_of_segment(a, b, float(wall.get("thickness", 0.2)) / 2))
    for ch in chains or []:
        a, b = _chain_line(ch)
        dim_dirs.append(_pt(ch["direction"]))
        dims.append(_rect_of_segment(a, b, cfg["dimensionBand"]))
    for t in texts or []:
        c = (t["coordinate"]["x"], t["coordinate"]["y"])
        marks.append((c[0] - 0.45, c[1] - 0.2, c[0] + 0.45, c[1] + 0.2))
    for room in scene.get("rooms", []):
        c = polygon_centroid([_pt(p) for p in room["polygon"]])
        r = cfg["stampRadius"]
        marks.append((c[0] - r, c[1] - r, c[0] + r, c[1] + r))
    return {"walls": walls, "dims": dims, "marks": marks, "dimDirs": dim_dirs}


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
    placed = []
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

    def one_cost(dot, mid, lead_dir, box, u, half_t, taken):
        cost = 100 * sum(_box_hits(box, w) for w in obstacles["walls"])
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

    for wall in scene.get("walls", []):
        half_t = float(wall.get("thickness", 0.2)) / 2
        items = []
        for o, c, _, _, u, n in _opening_points(wall):
            runs, width, nlines, line = text_of(o)
            items.append((o, c, u, n, runs, width, (nlines, line)))
        if not items:
            continue
        u, n = items[0][2], items[0][3]
        n_in = inner_side(items[0][1], n, half_t)
        vertical_wall = abs(u[1]) > abs(u[0])
        best = None
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
                        taken = list(placed)
                        result = []
                        for o, c, _, _, runs, width, (nlines, line) in items:
                            dot = _add(c, _mul(side, half_t))
                            mid = _add(dot, _mul(lead_dir, length))
                            x0 = mid[0] + 0.01 if shelf > 0 else mid[0] - 0.01 - width
                            y0, y1 = (mid[1], mid[1] + line) if nlines == 1 else (mid[1] - line, mid[1] + line)
                            box = (x0, y0, x0 + width, y1)
                            total += one_cost(dot, mid, lead_dir, box, u, half_t, taken)
                            taken.append(box)
                            result.append((o, runs, dot, mid, shelf, width, box))
                        if best is None or total < best[0]:
                            best = (total, result)
        total, result = best
        for o, runs, dot, mid, shelf, width, box in result:
            placed.append(box)
            labels.append({"opening": o["id"], "wall": wall["id"], "runs": runs, "cost": round(total / len(result), 1),
                           "begCoordinate": _xy(dot), "midCoordinate": _xy(mid),
                           "endCoordinate": _xy(_add(mid, (shelf * 0.01, 0.0)))})
    return labels


def _chain_line(ch):
    """The dimension line of a chain as a segment (from the first to the last point)."""
    d = _pt(ch["direction"])
    ref = _pt(ch["referencePoint"])
    ts = [_dot(_sub(_pt(p), ref), d) for p in ch["points"]]
    return _add(ref, _mul(d, min(ts))), _add(ref, _mul(d, max(ts)))


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
        for e in edges:
            if count < 5:
                break
            s, f = edges[(e["i"] + 1) % count], edges[(e["i"] + 2) % count]
            if (s["len"] <= merge_step and _dot(e["u"], f["u"]) > 0.99 and abs(_dot(s["u"], e["u"])) < 0.1
                    and abs(_dot(_sub(f["a"], e["b"]), e["n"])) <= merge_step):
                group[root(f["i"])] = root(e["i"])
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
            chains.append({"room": room["id"], "edge": ms[0]["i"], "edges": [e["i"] for e in ms],
                           "direction": _xy(u), "normal": _xy(n), "referencePoint": _xy(ref),
                           "offset": round(depth - min(depths) + cfg["offsetFromFace"], 4),
                           "points": [_xy(p) for p in kept]})
    # move the shorter of two conflicting chains further in, a few times at most
    def length(ch):
        a, b = _chain_line(ch)
        return _len(_sub(b, a))
    for _ in range(cfg.get("maxShifts", 2)):
        moved = False
        for i, c1 in enumerate(chains):
            for c2 in chains[i + 1:]:
                if _chain_conflict(c1, c2, band):
                    short = c1 if length(c1) <= length(c2) else c2
                    short["referencePoint"] = _xy(_add(_pt(short["referencePoint"]), _mul(_pt(short["normal"]), step)))
                    short["offset"] = round(short["offset"] + step, 4)
                    moved = True
        if not moved:
            break
    for i, c1 in enumerate(chains):
        for c2 in chains[i + 1:]:
            if _chain_conflict(c1, c2, band):
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
    for ch in chains:
        d, n = _pt(ch["direction"]), _pt(ch["normal"])
        ref = _pt(ch["referencePoint"])
        ts = [_dot(_sub(_pt(p), ref), d) for p in ch["points"]]
        last = len(ts) - 2
        for k in range(len(ts) - 1):
            if abs(ts[k + 1] - ts[k]) >= short:
                continue
            beg = _add(ref, _mul(d, (ts[k] + ts[k + 1]) / 2))
            mid = _add(beg, _mul(n, rise))
            # back over the chain: at the last segment against the direction, else with it
            along = _mul(d, -1 if (k == last and k > 0) else 1)
            ch.setdefault("textLeaders", {})[k + 1] = {"beg": _xy(beg), "mid": _xy(mid), "end": _xy(_add(mid, _mul(along, shelf))), "pen": pen}
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
    ids = [a for a in attrs if a.get("name") in names and a.get("attributeId")]
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


def set_layer(ac, guids, index, report, what):
    guids = [g for g in guids if g]
    if not guids or not index:
        if guids and not index:
            report.errors.append(f"Слой для «{what}» не найден в проекте: элементы остались на текущем слое.")
        return
    ac.tapir("SetDetailsOfElements", {"elementsWithDetails": [
        {"elementId": {"guid": g}, "details": {"layerIndex": index}} for g in guids]})


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
    closed = open_layers(ac, set(profile["layers"].values()), opened)
    try:
        out = _build_measured_plan(ac, scene, profile, rooms, origin, floor_index, import_favorites)
    finally:
        restore_layers(ac, closed)
    out["steps"] = opened.steps + out["steps"]
    return out


def _build_measured_plan(ac, scene, profile, rooms, origin, floor_index, import_favorites):
    if scene.get("format") != SCENE_FORMAT:
        raise ValueError(f"Задание не в формате {SCENE_FORMAT}")
    scene = shift_scene(apply_room_overrides(json.loads(json.dumps(scene)), rooms), origin)
    for room in scene.get("rooms", []):
        room.setdefault("name", f"{profile['zone']['defaultName']} {room.get('number', '')}".strip())
    report = Report()
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
    model = ac.tapir("BuildModelFromScan", params, timeout=600)
    report.errors += model.get("errors", [])
    wall_guid = {w["id"]: _guid(w) for w in model.get("walls", [])}
    opening_guid = {o["id"]: _guid(o) for o in model.get("windows", []) + model.get("doors", [])}
    zone_guid = {z["id"]: _guid(z) for z in model.get("zones", [])}
    zone_guids = list(zone_guid.values())
    report.step("модель", walls=len(wall_guid), openings=len(opening_guid), zones=len(zone_guids))

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
                c = polygon_centroid([_pt(p) for p in room["polygon"]])
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
    labels = plan_opening_labels(scene, profile, label_obstacles(scene, profile, texts, chains))
    data = []
    for lab in labels:
        item = {k: lab[k] for k in ("runs", "begCoordinate", "midCoordinate", "endCoordinate")}
        # deslab: the lines of a label are aligned at one edge (Игорь: «hокна» and «hподокон»
        # even). Right, not Left: with Left Archicad now and then makes an endless shelf (label
        # test and runs 7b/8 of 03.11.2026; Right never did).
        item["style"] = {"justification": "Right"}
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
        guids = [g for g in guids if g]
        ac.tapir("UnlockElements", {"elements": _elements(guids)})
        set_layer(ac, guids, idx.get(L["labels"]), report, "выноски")
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
            tl = ch.get("textLeaders", {})
            d = {"referencePoint": ch["referencePoint"], "direction": ch["direction"], "floorIndex": floor_index,
                 "witnessPoints": [{"elementId": {"guid": g}, **({"textLeader": tl[j]} if j in tl else {})}
                                   for j, g in enumerate(ids)]}
            if F.get("dimension"):
                d["favoriteName"] = F["dimension"]
            dims.append(d)
        dim_guids = [g for g in _created(ac.tapir("CreateAssociativeDimensions", {"dimensionsData": dims}), "elements", report, "Размер") if g]
        ac.tapir("UnlockElements", {"elements": _elements(dim_guids)})
        set_layer(ac, dim_guids, idx.get(L["dimensions"]), report, "размеры")
    for ch in chains:
        if ch.get("conflict"):
            report.errors.append(f"Размер {ch['room']}/{ch['edge']}: наложение на цепочки {ch['conflict']}, поправить вручную")
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
