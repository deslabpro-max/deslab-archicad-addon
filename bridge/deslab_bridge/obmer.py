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


def plan_opening_labels(scene, profile):
    """One label per window or door: «hокна 1950 / hподокон 690», «hдвери 2100»."""
    cfg = profile["labels"]
    labels = []
    for wall in scene.get("walls", []):
        for o, c, _, _, u, n in _opening_points(wall):
            window = o["kind"] in ("window", "balconyDoor")
            values = {"height_mm": _mm(o["height"]), "sill_mm": _mm(o.get("sill", 0))}
            template = cfg["window"] if window and float(o.get("sill", 0)) > 0.05 else cfg["door"]
            if window and float(o.get("sill", 0)) <= 0.05:
                template = [["h", ""], ["окна", "sub"], [" {height_mm}", ""]]
            off = cfg["offset"] + float(wall.get("thickness", 0.2)) / 2
            mid = _add(c, _mul(n, off))
            labels.append({"opening": o["id"], "wall": wall["id"], "runs": _runs(template, values),
                           "begCoordinate": _xy(c), "midCoordinate": _xy(mid),
                           "endCoordinate": _xy(_add(mid, (cfg["shelf"], 0.0)))})
    return labels


def plan_dimension_chains(scene, profile):
    """Per room edge: a chain from corner to corner through the opening edges on that edge.

    Points are given as coordinates; the caller places a hotspot at each point and dimensions
    the hotspots (associative to them), as the rules say for free points."""
    cfg = profile["dimensions"]
    openings = [(wall, o, c, e1, e2, u) for wall in scene.get("walls", []) for o, c, e1, e2, u, _ in _opening_points(wall)]
    chains = []
    for room in scene.get("rooms", []):
        poly = [_pt(p) for p in room["polygon"]]
        ccw = polygon_area(poly) > 0
        for i, a in enumerate(poly):
            b = poly[(i + 1) % len(poly)]
            edge = _sub(b, a)
            length = _len(edge)
            if length < cfg["minEdge"]:
                continue
            u = _unit(edge)
            inward = _left(u) if ccw else _mul(_left(u), -1)
            points = [0.0, length]
            for wall, o, c, e1, e2, wu in openings:
                if abs(_dot(wu, u)) < 0.99:
                    continue
                dist = abs(_dot(_sub(c, a), _left(u)))
                if dist > float(wall.get("thickness", 0.2)) / 2 + 0.06:
                    continue
                for e in (e1, e2):
                    t = _dot(_sub(e, a), u)
                    if 0.02 < t < length - 0.02:
                        points.append(t)
            points = sorted(set(round(t, 4) for t in points))
            ref = _add(a, _mul(inward, cfg["offsetFromFace"]))
            chains.append({"room": room["id"], "edge": i, "direction": _xy(u), "referencePoint": _xy(ref),
                           "points": [_xy(_add(a, _mul(u, t))) for t in points]})
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


def layer_indices(ac, names):
    result = ac.tapir("GetAttributesByType", {"attributeType": "Layer"})
    by_name = {a["name"]: a["index"] for a in result.get("attributes", []) if "name" in a}
    return {n: by_name.get(n) for n in names}


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


def build_measured_plan(ac, scene, profile=None, rooms=None, origin=None, floor_index=0):
    """Builds the model and the measured-plan drafting from a scene. Returns a report dict."""
    profile = profile or load_profile()
    if scene.get("format") != SCENE_FORMAT:
        raise ValueError(f"Задание не в формате {SCENE_FORMAT}")
    scene = apply_room_overrides(json.loads(json.dumps(scene)), rooms)
    for room in scene.get("rooms", []):
        room.setdefault("name", f"{profile['zone']['defaultName']} {room.get('number', '')}".strip())
    report = Report()
    L, F = profile["layers"], profile["favorites"]

    if not ac.alive():
        raise RuntimeError("Archicad не отвечает. Откройте проект (копию шаблона) и закройте открытые диалоги.")
    if profile.get("favoritesFile"):
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
    if origin:
        params["origin"] = origin
    model = ac.tapir("BuildModelFromScan", params, timeout=600)
    report.errors += model.get("errors", [])
    wall_guid = {w["id"]: _guid(w) for w in model.get("walls", [])}
    opening_guid = {o["id"]: _guid(o) for o in model.get("windows", []) + model.get("doors", [])}
    zone_guids = [_guid(z) for z in model.get("zones", [])]
    report.step("модель", walls=len(wall_guid), openings=len(opening_guid), zones=len(zone_guids))

    partitions = [wall_guid[w["id"]] for w in scene.get("walls", []) if is_partition(w, profile) and w["id"] in wall_guid]
    set_layer(ac, partitions, idx.get(L["partitions"]), report, "перегородки")
    if zone_guids:
        ac.tapir("UnlockElements", {"elements": _elements(zone_guids)})
    report.step("слои стен, зоны разблокированы", partitions=len(partitions))

    # 2. Height texts under the zone stamps.
    texts = plan_height_texts(scene, profile)
    if texts:
        data = [{"coordinate": t["coordinate"], "text": t["text"], "floorIndex": floor_index,
                 **({"favoriteName": F["heightText"]} if F.get("heightText") else {}),
                 **({"layerIndex": idx[L["zones"]]} if idx.get(L["zones"]) else {})} for t in texts]
        guids = _created(ac.tapir("CreateTexts", {"textsData": data}), "elements", report, "Текст Н")
        ac.tapir("UnlockElements", {"elements": _elements([g for g in guids if g])})
    report.step("тексты Н", count=len(texts))

    # 3. Opening labels.
    labels = plan_opening_labels(scene, profile)
    data = []
    for lab in labels:
        item = {k: lab[k] for k in ("runs", "begCoordinate", "midCoordinate", "endCoordinate")}
        if F.get("label"):
            item["favoriteName"] = F["label"]
        else:  # rules: no blue fill, frame or bold when the favorite is missing
            item["style"] = {"bold": False, "usedContour": False, "usedFill": False}
            item["leaderLine"] = {"framed": False}
        if lab["opening"] in opening_guid:
            item["parentElementId"] = {"guid": opening_guid[lab["opening"]]}
        data.append(item)
    if data:
        guids = _created(ac.tapir("CreateLabels", {"labelsData": data}), "elements", report, "Выноска")
        guids = [g for g in guids if g]
        ac.tapir("UnlockElements", {"elements": _elements(guids)})
        set_layer(ac, guids, idx.get(L["labels"]), report, "выноски")
    report.step("выноски проёмов", count=len(data))

    # 4. Dimension chains: hotspots at the points, dimensions on the hotspots.
    chains = plan_dimension_chains(scene, profile)
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
            d = {"referencePoint": ch["referencePoint"], "direction": ch["direction"], "floorIndex": floor_index,
                 "witnessPoints": [{"elementId": {"guid": g}} for g in ids]}
            if F.get("dimension"):
                d["favoriteName"] = F["dimension"]
            dims.append(d)
        dim_guids = [g for g in _created(ac.tapir("CreateAssociativeDimensions", {"dimensionsData": dims}), "elements", report, "Размер") if g]
        ac.tapir("UnlockElements", {"elements": _elements(dim_guids)})
        set_layer(ac, dim_guids, idx.get(L["dimensions"]), report, "размеры")
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
