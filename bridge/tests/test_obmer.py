"""Tests of the measured-plan planning, without Archicad (python bridge/tests/test_obmer.py)."""
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deslab_bridge import obmer  # noqa: E402

PROFILE = obmer.load_profile()

# One room 4 x 3 m, walls on the faces' outside, a window in the south wall, a door in the east one.
SCENE = {
    "format": "deslab.scan2bim/1",
    "levels": {"floor": 0.0, "ceiling": 2.97},
    "walls": [
        {"id": "W1", "start": {"x": 0, "y": -0.2}, "end": {"x": 4, "y": -0.2}, "thickness": 0.4, "height": 2.97, "kind": "exterior",
         "openings": [{"id": "W1.1", "kind": "window", "centerOffset": 2.0, "width": 1.2, "sill": 0.69, "height": 1.95}]},
        {"id": "W2", "start": {"x": 4.05, "y": 0}, "end": {"x": 4.05, "y": 3}, "thickness": 0.1, "height": 2.97, "kind": "interior",
         "openings": [{"id": "W2.1", "kind": "door", "centerOffset": 1.0, "width": 0.8, "sill": 0, "height": 2.1}]},
        {"id": "W3", "start": {"x": 4, "y": 3.2}, "end": {"x": 0, "y": 3.2}, "thickness": 0.4, "height": 2.97, "kind": "exterior", "openings": []},
        {"id": "W4", "start": {"x": -0.2, "y": 3}, "end": {"x": -0.2, "y": 0}, "thickness": 0.4, "height": 2.97, "kind": "exterior", "openings": []},
    ],
    "rooms": [{"id": "R1", "number": 1, "name": "Помещение 1", "polygon": [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 3}, {"x": 0, "y": 3}],
               "floor": 0, "ceiling": 2.97, "height": 2.97, "area": 12.0}],
    "slabs": [], "assumptions": ["W4: только одна сторона"], "warnings": [],
}


def test_height_text():
    t = obmer.plan_height_texts(SCENE, PROFILE)
    assert t[0]["text"] == "Н 2970", t
    assert abs(t[0]["coordinate"]["x"] - 2.0) < 1e-6


def test_labels():
    texts = obmer.plan_height_texts(SCENE, PROFILE)
    chains = obmer.plan_dimension_chains(SCENE, PROFILE)
    obstacles = obmer.label_obstacles(SCENE, PROFILE, texts, chains)
    labels = {l["opening"]: l for l in obmer.plan_opening_labels(SCENE, PROFILE, obstacles)}
    text = "".join(r["text"] for r in labels["W1.1"]["runs"])
    assert text == "hокна 1950\nhподокон 690", text
    assert any(r.get("effectSubscript") for r in labels["W1.1"]["runs"])
    # window: the dot on the room face, the leader into the room (labels stay inside the flat);
    # this 4 x 3 m room is too small to clear the «Н» text, but no wall is hit
    assert labels["W1.1"]["begCoordinate"] == {"x": 2.0, "y": 0.0}, labels["W1.1"]
    assert labels["W1.1"]["midCoordinate"]["y"] > 0
    assert labels["W1.1"]["cost"] < 200, labels["W1.1"]
    # door: into the room, clear of the dimension band and the «Н» text
    assert labels["W2.1"]["midCoordinate"]["x"] < 4.0 and labels["W2.1"]["cost"] < 100, labels["W2.1"]
    assert "".join(r["text"] for r in labels["W2.1"]["runs"]) == "hдвери 2100"
    # deslab: Игорь 13.11.2026, a doorway without a leaf is «hпроёма», a balcony door with a threshold «hдвери / hпорога»
    import copy
    scene = copy.deepcopy(SCENE)
    by_id = {o["id"]: o for w in scene["walls"] for o in w["openings"]}
    by_id["W2.1"]["kind"] = "opening"
    by_id["W1.1"].update(kind="balconyDoor", sill=0.14, height=2.5)
    labels = {l["opening"]: l for l in obmer.plan_opening_labels(scene, PROFILE, obstacles)}
    assert "".join(r["text"] for r in labels["W2.1"]["runs"]) == "hпроёма 2100"
    assert "".join(r["text"] for r in labels["W1.1"]["runs"]) == "hдвери 2500\nhпорога 140"


def test_dimensions():
    chains = obmer.plan_dimension_chains(SCENE, PROFILE)
    assert len(chains) == 4
    south = chains[0]
    xs = [p["x"] for p in south["points"]]
    assert xs == [0.0, 1.4, 2.6, 4.0], xs
    assert south["referencePoint"]["y"] > 0
    east = chains[1]
    assert [p["y"] for p in east["points"]] == [0.0, 0.6, 1.4, 3.0]


def test_dimension_step_is_one_chain():
    # a 0.27 m step in the south wall: one chain on the deeper face, no crossing with the west chain
    poly = [(0, 0), (3, 0), (3, 0.27), (6, 0.27), (6, 4), (0, 4)]
    scene = {"walls": [], "rooms": [{"id": "R", "polygon": [{"x": x, "y": y} for x, y in poly]}]}
    chains = obmer.plan_dimension_chains(scene, PROFILE)
    south = [c for c in chains if c["edges"] == [0, 2]]
    assert south and abs(south[0]["referencePoint"]["y"] - 0.62) < 1e-6, chains
    assert [p["x"] for p in south[0]["points"]] == [0.0, 3.0, 6.0]
    assert len(chains) == 4 and not any(c.get("conflict") for c in chains), chains


def test_labels_of_one_wall_in_a_row():
    # two windows on one long wall of a big room: same leader length, same shelf direction
    import copy
    scene = copy.deepcopy(SCENE)
    scene["walls"][0] = {"id": "W1", "start": {"x": 0, "y": -0.2}, "end": {"x": 9, "y": -0.2}, "thickness": 0.4, "kind": "exterior",
                         "openings": [{"id": "A", "kind": "window", "centerOffset": 2.0, "width": 1.2, "sill": 0.9, "height": 1.8},
                                      {"id": "B", "kind": "window", "centerOffset": 6.5, "width": 1.2, "sill": 0.9, "height": 1.8}]}
    scene["rooms"][0]["polygon"] = [{"x": 0, "y": 0}, {"x": 9, "y": 0}, {"x": 9, "y": 6}, {"x": 0, "y": 6}]
    texts = obmer.plan_height_texts(scene, PROFILE)
    chains = obmer.plan_dimension_chains(scene, PROFILE)
    labels = {l["opening"]: l for l in obmer.plan_opening_labels(scene, PROFILE, obmer.label_obstacles(scene, PROFILE, texts, chains))}
    a, b = labels["A"], labels["B"]
    assert a["midCoordinate"]["y"] == b["midCoordinate"]["y"] > 0, (a, b)
    assert (a["endCoordinate"]["x"] - a["midCoordinate"]["x"]) * (b["endCoordinate"]["x"] - b["midCoordinate"]["x"]) > 0
    assert a["cost"] < 100, a


def test_door_labels_stay_inside():
    # an entrance door in the outer wall of a hall without a zone, next to the room: the label
    # goes into the flat (up), never out of it; the room door goes into the room
    import copy
    scene = copy.deepcopy(SCENE)
    scene["walls"].append({"id": "W5", "start": {"x": 4.05, "y": -0.2}, "end": {"x": 6, "y": -0.2}, "thickness": 0.4,
                           "kind": "exterior", "openings": [{"id": "W5.1", "kind": "door", "centerOffset": 1.0, "width": 0.9, "sill": 0, "height": 2.1}]})
    scene["walls"].append({"id": "W6", "start": {"x": 6.2, "y": 0}, "end": {"x": 6.2, "y": 3}, "thickness": 0.4, "kind": "exterior", "openings": []})
    labels = {l["opening"]: l for l in obmer.plan_opening_labels(scene, PROFILE)}
    assert labels["W5.1"]["midCoordinate"]["y"] > 0, labels["W5.1"]
    assert labels["W2.1"]["midCoordinate"]["x"] < 4.05, labels["W2.1"]


def test_short_segment_text_is_placed():
    # a door 0.25 m from the corner: the 250 text goes on a leader, as in the office template:
    # from the segment middle on the dimension line square into the room, then a shelf; it sits
    # on the segment's end witness point
    import copy
    scene = copy.deepcopy(SCENE)
    scene["walls"][1]["openings"][0]["centerOffset"] = 0.65  # door 0.25 .. 1.05 on the east wall
    east = [c for c in obmer.plan_dimension_chains(scene, PROFILE) if c["edges"] == [1]]
    assert len(east) == 1 and [p["y"] for p in east[0]["points"]] == [0.0, 0.25, 1.05, 3.0], east
    lead = east[0]["textLeaders"]
    assert list(lead) == [1], lead
    x = east[0]["referencePoint"]["x"]
    assert lead[1]["beg"] == {"x": x, "y": 0.125}
    assert lead[1]["mid"]["x"] < x and lead[1]["mid"]["y"] == 0.125
    # Игорь's rule (run 15): the shelf runs out past the chain end, here past the first point
    # down over the wall, about as long as the text; reading bottom-up, the leader holds its end
    assert lead[1]["end"]["y"] < 0.0, lead
    assert lead[1]["anchorAtTextEnd"] is True
    assert not east[0].get("leaderConflict")


def test_blocked_dimension_text_goes_on_a_leader():
    # runs JK and kv94 (04.11.2026): a label leader through a text that cannot slide clear of
    # it; the text goes on a leader that starts off that line and never runs along it
    chains = obmer.plan_dimension_chains(SCENE, PROFILE)
    lab = [{"begCoordinate": {"x": 3.0, "y": 0.3}, "midCoordinate": {"x": 3.9, "y": 0.3},
            "endCoordinate": {"x": 3.91, "y": 0.3}}]
    obmer.place_dimension_texts(chains, lab, PROFILE)
    east = [c for c in chains if c["edges"] == [1]][0]
    assert not any(c.get("textConflict") for c in chains)
    lead = east["textLeaders"][1]
    assert abs(lead["beg"]["y"] - 0.3) > 0.02 and lead["beg"]["x"] == 3.65, lead
    assert 1 not in east.get("textAt", {})

def test_room_outline_on_wall_faces():
    # Игорь 04.11.2026 (kv94): a wall a thickness off its place must never get built
    assert obmer.check_room_faces(SCENE) == []
    import copy
    bad = copy.deepcopy(SCENE)
    bad["walls"][0]["start"]["y"] = bad["walls"][0]["end"]["y"] = -0.4  # moved out by its thickness/2
    errs = obmer.check_room_faces(bad)
    assert len(errs) == 1 and "W1" in errs[0] and "200 мм" in errs[0], errs
    # the kv94 fault: the wall moved INTO the room by its thickness, its far face on the edge
    bad = copy.deepcopy(SCENE)
    bad["walls"][0]["start"]["y"] = bad["walls"][0]["end"]["y"] = 0.2
    errs = obmer.check_room_faces(bad)
    assert len(errs) == 1 and "W1 стоит внутри помещения" in errs[0], errs


def test_walls_from_outline():
    # an L-shaped flat drawn from a PDF: walls from the inner outline, axes meeting at corners
    poly = [(0, 0), (2.493, 0), (2.493, 2.27), (11.752, 2.27), (11.752, 9.854), (0, 9.854)]
    th = [0.44, 0.2, 0.44, 0.439, 0.441, 0.44]
    edges = [{"id": f"W{i + 1}", "thickness": t, "openings": []} for i, t in enumerate(th)]
    edges[4]["openings"] = [{"id": "W5.1", "kind": "window", "from": 0.0, "width": 2.107, "sill": 0.55, "height": 2.5}]
    walls = obmer.walls_from_outline([{"x": x, "y": y} for x, y in poly], edges, 3.39)
    scene = {"walls": walls, "rooms": [{"id": "R1", "polygon": [{"x": x, "y": y} for x, y in poly]}]}
    assert obmer.check_room_faces(scene) == [], obmer.check_room_faces(scene)
    w3 = walls[2]  # the bottom wall at y 2.27: its axis 0.22 below the face
    assert abs(w3["start"]["y"] - 2.05) < 1e-9 and abs(w3["end"]["y"] - 2.05) < 1e-9, w3
    w5 = walls[4]  # the top wall, run right to left; the window starts at the corner 11.752
    c = w5["openings"][0]["centerOffset"]
    sx = w5["start"]["x"]
    assert abs((sx - c) - (11.752 - 2.107 / 2)) < 1e-6, (sx, c)

def test_corner_window_split():
    # Игорь 04.11.2026: a corner window is one Archicad corner window, built after the model
    import copy
    scene = copy.deepcopy(SCENE)
    scene["walls"][0]["openings"].append({"id": "W1.2", "kind": "window", "centerOffset": 3.6, "width": 0.8,
                                          "sill": 0.69, "height": 1.95, "corner": {"partner": "W2.2"}})
    scene["walls"][1]["openings"].append({"id": "W2.2", "kind": "window", "centerOffset": 0.4, "width": 0.8,
                                          "sill": 0.69, "height": 1.95})
    model, pairs = obmer.split_corner_windows(scene)
    assert [(a["id"], o["id"], b["id"], q["id"]) for a, o, b, q in pairs] == [("W1", "W1.2", "W2", "W2.2")]
    assert [o["id"] for w in model["walls"] for o in w["openings"]] == ["W1.1", "W2.1"]
    assert obmer._corner_end(scene["walls"][0], scene["walls"][1]) == 1
    assert obmer._corner_end(scene["walls"][1], scene["walls"][0]) == 0
    labels = [l["opening"] for l in obmer.plan_opening_labels(scene, PROFILE)]
    assert "W1.2" in labels and "W2.2" not in labels, labels  # one window, one label

def test_dimension_texts_clear_of_lines():
    # Игорь, 04.11.2026: no line may cross a dimension text; the text slides along its line.
    # The door label leader runs through the middle of the 1200 and the 800, the south chain's
    # line crosses the 600 at the corner.
    chains = obmer.plan_dimension_chains(SCENE, PROFILE)
    labels = obmer.plan_opening_labels(SCENE, PROFILE, obmer.label_obstacles(SCENE, PROFILE, [], chains))
    obmer.place_dimension_texts(chains, labels, PROFILE)
    south = [c for c in chains if c["edges"] == [0]][0]
    east = [c for c in chains if c["edges"] == [1]][0]
    assert not any(c.get("textConflict") for c in chains)
    assert abs(south["textAt"][2]["x"] - 2.0) > 0.15          # off the label leader at x 2.0
    assert east["textAt"][1]["y"] + 0.12 < 0.35               # below the south chain's line
    assert abs(east["textAt"][2]["y"] - 1.0) > 0.15           # off the label leader at y 1.0
    assert 2 not in [c for c in chains if c["edges"] == [2]][0].get("textAt", {})


def test_chain_direction_on_the_axis():
    # a scanned wall leaning 0.1°: its chain still runs strictly along the axis, so Archicad
    # writes a vertical dimension from bottom to top as in the template (Игорь, run 15)
    assert obmer._snap_axis((-0.0011, 0.9999994), 1.0) == (0.0, 1.0)
    assert obmer._snap_axis((0.9999, 0.0053), 1.0) == (1.0, 0.0)
    assert obmer._snap_axis((0.7071, 0.7071), 1.0) == (0.7071, 0.7071)


def test_partitions_and_overrides():
    scene = obmer.apply_room_overrides(dict(SCENE, rooms=[dict(SCENE["rooms"][0])]), {"R1": {"name": "Санузел", "wet": "bathroom"}})
    assert scene["rooms"][0]["name"] == "Санузел"
    assert len(obmer.plan_wet_zones(scene, PROFILE)) == 1
    assert [w["id"] for w in SCENE["walls"] if obmer.is_partition(w, PROFILE)] == ["W2"]


class FakeArchicad:
    def __init__(self):
        self.calls = []
        self.log = []
        self.ids = itertools.count(1)
        self.dimension_prefs = {"dimensionStandardIndex": 4, "linear": {"unit": "Meter", "decimals": 3}}
        self.view_dimensions = {}

    def alive(self):
        return True

    def _new(self, n):
        return [{"elementId": {"guid": f"G{next(self.ids)}"}} for _ in range(n)]

    def api(self, command, params=None):
        self.calls.append((command, params))
        assert command == "API.GetNavigatorItemTree"
        return {"navigatorTree": {"rootItem": {"name": "Виды", "type": "FolderItem", "children": [
            {"name": item["view"], "navigatorItemId": {"guid": f"V{i}"}, "type": "StoryItem"}
            for i, item in enumerate(PROFILE["layouts"]["items"]) ]}}}

    def tapir(self, command, params=None, timeout=None):
        self.calls.append((command, params))
        self.log.append((command, 0))
        if command == "SetProjectPreferences":
            self.dimension_prefs.update(params["dimensions"])
            return {"dimensions": {"success": True}}
        if command == "GetProjectPreferences":
            return {"dimensions": self.dimension_prefs}
        if command == "SetViewSettings":
            for view in params["navigatorItemIdsWithViewSettings"]:
                self.view_dimensions[view["navigatorItemId"]["guid"]] = view["viewSettings"].get("dimensionStyle")
            return {"executionResults": [{"success": True} for _ in params["navigatorItemIdsWithViewSettings"]]}
        if command == "GetViewSettings":
            return {"viewSettings": [{"dimensionStyle": self.view_dimensions.get(v["navigatorItemId"]["guid"])}
                                     for v in params["navigatorItemIds"]]}
        if command == "GetAttributesByType" and params["attributeType"] == "ZoneCategory":
            return {"attributes": [{"attributeId": {"guid": "CAT"}}]}
        if command == "GetZoneCategories":
            return {"zoneCategories": [{"name": "Полы", "attributeId": {"guid": "CAT"},
                                         "stampName": "Маркировка помещения", "stampMainGuid": "STAMP", "stampRevGuid": "REV"}]}
        if command == "GetAvailableLibraryParts":
            return {"libraryParts": [{"guid": "STAMP", "typeId": "Object"}]}
        if command == "GetAttributesByType" and params["attributeType"] == "Fill":
            return {"attributes": [{"name": "Фон", "attributeId": {"guid": "EMPTY-FILL"}}]}
        if command == "GetAttributesByType" and params["attributeType"] == "Surface":
            return {"attributes": [{"name": "Бетон - Гладкий", "attributeId": {"guid": "S0"}}, {"name": "Краска - Белая", "attributeId": {"guid": "S1"}}]}
        if command == "GetAttributesByType" and params["attributeType"] == "BuildingMaterial":
            return {"attributes": [{"name": "Штукатурка - Гипсовая", "attributeId": {"guid": "BM1"}}]}
        if command == "GetAttributesByType":
            return {"attributes": [{"name": n, "index": i + 10, "attributeId": {"guid": f"L{i}"}}
                                   for i, n in enumerate(sorted(set(PROFILE["layers"].values())))]}
        if command == "GetLayers":  # the open view hides the first layer
            return {"layers": [{"attributeId": a["attributeId"], "name": "слой", "isHidden": a["attributeId"]["guid"] == "L0", "isLocked": False}
                               for a in params["attributeIds"]]}
        if command == "BuildModelFromScan":
            s = params["scene"]
            return {"walls": [{"id": w["id"], "elementId": {"guid": "W" + w["id"]}} for w in s["walls"]],
                    "windows": [{"id": "W1.1", "elementId": {"guid": "OW"}}], "doors": [{"id": "W2.1", "elementId": {"guid": "OD"}}],
                    "zones": [{"id": "R1", "elementId": {"guid": "Z1"}}], "errors": []}
        key = {"CreateTexts": "textsData", "CreateLabels": "labelsData", "CreateHotspots": "hotspotsData",
               "CreateAssociativeDimensions": "dimensionsData", "CreateHatches": "hatchesData",
               "CreateWalls": "wallsData", "CreateObjects": "objectsData", "CreatePolylines": "polylinesData"}.get(command)
        if key:
            return {"elements": self._new(len(params[key]))}
        if command == "GetElementsByType":
            return {"elements": [{"elementId": {"guid": "D1"}}]}
        if command == "GetFavoritesByType":
            return {"favorites": [n for n in PROFILE["favorites"].values() if n]}
        return {}


def test_build_calls():
    ac = FakeArchicad()
    report = obmer.build_measured_plan(ac, SCENE, PROFILE, rooms={"R1": {"name": "Кухня", "wet": "kitchen"}})
    names = [c for c, _ in ac.calls if c != "GetFavoritesByType"]
    # a layer hidden in the open view is shown for the build and hidden again at the end
    assert names[:11] == ["GetAttributesByType", "GetLayers", "CreateLayers", "SetProjectPreferences", "GetProjectPreferences",
                          "API.GetNavigatorItemTree", "SetViewSettings", "GetViewSettings", "ImportFavorites", "GetAttributesByType", "BuildModelFromScan"], names
    assert names[-1] == "CreateLayers" and ac.calls[-1][1]["layerDataArray"][0]["isHidden"] is True
    assert ac.calls[2][1]["layerDataArray"] == [{"attributeId": {"guid": "L0"}, "name": "слой", "isHidden": False, "isLocked": False}]
    assert len(names) < 35, names  # includes five calls to set, verify and save the reference dimension format
    build = next(p for c, p in ac.calls if c == "BuildModelFromScan")
    assert build["scene"]["rooms"][0]["name"] == "Кухня"
    assert build["favorites"]["zone"] == "ОБМЕР шаблон Зона"
    labels = next(p for c, p in ac.calls if c == "CreateLabels")["labelsData"]
    assert labels[0]["favoriteName"] == "ОБМЕР шаблон Выноска" and "parentElementId" not in labels[0]
    walls = next(p for c, p in ac.calls if c == "ModifyWalls")["wallsWithDetails"]
    assert all(w["sideMaterial"]["attributeId"] == {"guid": "S1"} for w in walls)
    boards = next(p for c, p in ac.calls if c == "SetGDLParametersOfElements")["elementsWithGDLParameters"]
    assert boards[0]["gdlParameters"] == [{"name": "gs_board_inside", "value": False}]
    assert "UpdateDrawings" in names and not report["errors"], report


def test_engineering_and_tracing():
    """kv94 / JK (Игорь 05.11.2026): door swing from the scene, a shaft box of 100 mm walls, risers,
    a radiator with its sizes, and a tracing strip as a low wall with its own dimensions."""
    import copy
    scene = copy.deepcopy(SCENE)
    scene["walls"][1]["openings"][0]["reflected"] = True
    scene["mep"] = [
        {"kind": "shaft", "id": "SH1", "label": "Вытяжка", "thickness": 0.1,
         "polygon": [{"x": 0, "y": 2.0}, {"x": 0.6, "y": 2.0}, {"x": 0.6, "y": 3.0}, {"x": 0, "y": 3.0}]},
        {"kind": "sewerRiser", "id": "K1", "at": {"x": 0.2, "y": 2.5}, "diameter": 0.16, "label": "Стояк канализации"},
        {"kind": "radiator", "id": "RD1", "start": {"x": 0.3, "y": 0}, "end": {"x": 1.3, "y": 0}, "depth": 0.07},
    ]
    scene["tracing"] = [{"id": "T1", "start": {"x": 2.5, "y": 1.2}, "end": {"x": 2.5, "y": 3.0}, "thickness": 0.08, "height": 0.25}]
    scene["mep"][0]["partitions"] = [[{"x": 0.0, "y": 2.5}, {"x": 0.55, "y": 2.5}]]
    scene["mep"].append({"kind": "panel", "id": "EL1", "start": {"x": 4, "y": 0.3}, "end": {"x": 4, "y": 0.62}, "depth": 0.158})
    # deslab: 14.11.2026, a vent channel through the wall and its outlet: dashed outlines, the outlet a box, two labels
    scene["mep"] += [{"kind": "penetration", "id": "PN1", "polygon": [{"x": 3.3, "y": 3.4}, {"x": 3.45, "y": 3.4}, {"x": 3.45, "y": 3.0}, {"x": 3.3, "y": 3.0}],
                      "elevation": 2.6, "height": 0.13, "label": "Вент. проходка 150×130 h=2600"},
                     {"kind": "ventOutlet", "id": "VV1", "polygon": [{"x": 3.25, "y": 3.0}, {"x": 3.5, "y": 3.0}, {"x": 3.5, "y": 2.92}, {"x": 3.25, "y": 2.92}],
                      "elevation": 2.55, "height": 0.25, "label": "Вент. выход"}]
    moved = obmer.shift_scene(copy.deepcopy(scene), {"x": 100, "y": 0})
    assert moved["mep"][0]["partitions"][0][0]["x"] == 100  # run kv94i: the partition stayed at x=0
    assert not obmer.check_room_faces(scene)
    ac = FakeArchicad()
    report = obmer.build_measured_plan(ac, scene, PROFILE)
    calls = dict(ac.calls)
    assert calls["ModifyDoors"]["doorsWithDetails"] == [{"elementId": {"guid": "OD"}, "reflected": True}]
    walls = calls["CreateWalls"]["wallsData"]
    # a shaft in the corner: walls on its two free sides only, inside its outline; the tracing low
    assert len(walls) == 4 and all("id" not in w for w in walls), walls  # no id: add-on 2ae7b30 loses such walls
    assert walls[0]["begCoordinate"] == {"x": 0.0, "y": 2.05} and walls[1]["endCoordinate"] == {"x": 0.55, "y": 3.0}
    assert walls[3]["height"] == 0.25 and walls[3]["thickness"] == 0.08
    objects = {o["id"]: o for o in calls["CreateObjects"]["objectsData"]}
    rad = objects["RD1"]
    assert rad["angle"] == 0 and abs(rad["coordinates"]["y"] - 0.015) < 1e-9 and abs(rad["dimensions"]["x"] - 1.0) < 1e-9
    assert objects["K1"]["dimensions"]["x"] == 0.16
    sizes = [g["gdlParameters"] for g in next(p for c, p in ac.calls if c == "SetGDLParametersOfElements"
                                                and any(x["gdlParameters"][0]["name"] == "radius_1" for x in p["elementsWithGDLParameters"]))["elementsWithGDLParameters"]]
    assert {"name": "A", "value": 1.0} in sizes[1], sizes  # the radiator's size as GDL A too
    panel = next(p for c, p in ac.calls if c == "CreatePolylines")["polylinesData"][0]["coordinates"]  # «Электрощиты 21» drew 0.5 x 0.5
    assert panel[0] == {"x": 3.985, "y": 0.3} and panel[2] == {"x": 3.827, "y": 0.62} and panel[4] == panel[0], panel
    assert rad["pen"] == 161
    dims = calls["CreateAssociativeDimensions"]["dimensionsData"]
    assert len(dims) >= 7, len(dims)  # 4 room chains + radiators + shaft depth + tracing length and place
    texts = ["".join(r["text"] for r in l["runs"]) for l in calls["CreateLabels"]["labelsData"]]
    assert "Вытяжка" in texts and "Стояк канализации" in texts and "Трассировка h=250" in texts, texts
    assert "Вент. проходка 150×130 h=2600" in texts and "Вент. выход" in texts, texts
    outlines = [p for c, p in ac.calls if c == "CreatePolylines"]
    assert any(len(p["polylinesData"]) == 2 and p["polylinesData"][0]["coordinates"][0] == {"x": 3.3, "y": 3.4} for p in outlines), outlines
    morphs = [d for c, p in ac.calls if c == "CreateMorphs" for d in p["morphsData"]]
    assert any(abs(d["basePoint"]["z"] - 2.55) < 1e-9 and abs(d["size"]["z"] - 0.25) < 1e-9 for d in morphs), morphs
    assert not report["errors"], report["errors"]
    # Игорь 06.11.2026: shafts in the template's gypsum plaster, not the default orange material
    mods = [p for c, p in ac.calls if c == "ModifyWalls" and any("buildingMaterialId" in w for w in p["wallsWithDetails"])]
    assert mods and all(w["buildingMaterialId"] == {"guid": "BM1"} for w in mods[0]["wallsWithDetails"])
    # two plans: the radiators' chain first at the wall, the overall size of the wall after it,
    # both on the engineering plan; the room chains on the measured plan
    chains = obmer.plan_dimension_chains(scene, PROFILE)
    rad = [c for c in chains if c["edge"] == "оборуд.0"][0]
    whole = [c for c in chains if c["edge"] == "оборуд.0.всего"][0]
    room = [c for c in chains if c["edge"] == 0][0]
    assert rad["plan"] == whole["plan"] == "eng" and room["plan"] == "obmer"
    assert rad["offset"] < whole["offset"], (rad["offset"], whole["offset"])


def test_origin_moves_model_and_drafting():
    ac = FakeArchicad()
    obmer.build_measured_plan(ac, SCENE, PROFILE, origin={"x": 90, "y": 0})
    build = next(p for c, p in ac.calls if c == "BuildModelFromScan")
    assert "origin" not in build and build["scene"]["walls"][0]["start"]["x"] == 90
    text = next(p for c, p in ac.calls if c == "CreateTexts")["textsData"][0]
    assert abs(text["coordinate"]["x"] - 92) < 1e-6
    assert SCENE["walls"][0]["start"]["x"] == 0  # the caller's scene is not changed


def test_refused_items_are_reported():
    class Refusing(FakeArchicad):
        def tapir(self, command, params=None, timeout=None):
            if command == "CreateLabels":
                return {"elements": [{"error": {"code": 1, "message": "no favorite with this exact name"}} for _ in params["labelsData"]]}
            return super().tapir(command, params, timeout)
    ac = Refusing()
    report = obmer.build_measured_plan(ac, SCENE, PROFILE)
    assert "ImportFavorites" in [c for c, _ in ac.calls]
    assert any("Выноска" in e and "no favorite" in e for e in report["errors"]), report["errors"]


def test_missing_favorites_build_with_defaults():
    # deslab: 14.11.2026, Игорь's Archicad 29: the favorites of 27 are not imported, and every zone, label and dimension
    # asked with one was refused (no zones, no dimensions, an empty explication)
    class NoFavorites(FakeArchicad):
        def tapir(self, command, params=None, timeout=None):
            if command == "ImportFavorites":
                raise RuntimeError("ACAPI_Favorite_Import failed.")
            if command == "GetFavoritesByType":
                return {"favorites": []}
            return super().tapir(command, params, timeout)
    ac = NoFavorites()
    report = obmer.build_measured_plan(ac, SCENE, PROFILE)
    build = next(p for c, p in ac.calls if c == "BuildModelFromScan")
    assert "zone" not in build["favorites"], build["favorites"]
    for command, key in (("CreateLabels", "labelsData"), ("CreateAssociativeDimensions", "dimensionsData"), ("CreateTexts", "textsData")):
        sent = [x for c, p in ac.calls if c == command for x in p[key]]
        assert sent and not any("favoriteName" in x for x in sent), (command, sent[:1])
    assert any("Нет избранного" in e and "ОБМЕР шаблон Размер" in e for e in report["errors"]), report["errors"]
    # deslab: 15.11.2026, fallback must retain the reference's GOST text and pens, not AC29's red/blue defaults.
    height_text = next(p for c, p in ac.calls if c == "CreateTexts")["textsData"][0]
    assert height_text["style"]["fontName"] == "GOST Type BU Кириллический"
    assert height_text["style"]["height"] == 3 and height_text["style"]["penIndex"] == 81
    label = next(p for c, p in ac.calls if c == "CreateLabels")["labelsData"][0]
    assert label["style"]["fontName"] == "GOST Type BU Кириллический"
    assert label["style"]["height"] == 2.5 and label["style"]["penIndex"] == 161
    assert not label["style"]["bold"] and not label["style"]["italic"]
    assert label["leaderLine"]["penIndex"] == 167 and label["leaderLine"]["arrowPenIndex"] == 167
    assert label["leaderLine"]["arrowType"] == "FullCircle" and label["leaderLine"]["arrowSize"] == 0.5
    dimensions = next(p for c, p in ac.calls if c == "CreateAssociativeDimensions")["dimensionsData"]
    assert all(d["linePen"] == 120 and d["markerPen"] == 120 for d in dimensions)
    assert all(d["markerType"] == 6 and d["markerSize"] == 1.25 for d in dimensions)
    assert all("standard" not in d for d in dimensions), "The view's standard name is not an element-creation property"
    zones = [x for c, p in ac.calls if c == "SetDetailsOfElements" for x in p["elementsWithDetails"]
             if x["elementId"]["guid"] == "Z1"]
    assert zones and zones[0]["details"]["typeSpecificDetails"]["useFloorFill"] is True
    assert zones[0]["details"]["typeSpecificDetails"]["floorFillAttributeId"] == {"guid": "EMPTY-FILL"}
    assert zones[0]["details"]["typeSpecificDetails"]["floorFillBackgroundPenIndex"] == 0


def test_zone_fill_style_without_moving_stamp():
    # deslab: zone fill must match the reference even if a profile keeps the stamp at its original position.
    import copy
    profile = copy.deepcopy(PROFILE)
    profile["zone"]["stampAtCentre"] = False
    ac = FakeArchicad()
    obmer.build_measured_plan(ac, SCENE, profile)
    zones = [x for c, p in ac.calls if c == "SetDetailsOfElements" for x in p["elementsWithDetails"]
             if x["elementId"]["guid"] == "Z1"]
    details = zones[0]["details"]["typeSpecificDetails"]
    assert details["useFloorFill"] is True and "stampPosition" not in details
    assert details["floorFillPenIndex"] == 86 and details["floorFillBackgroundPenIndex"] == 0


def test_portable_zone_number_circle():
    # deslab: AC29's data-table stamp is hidden only after a complete reference circle and number are created.
    class KnownStamp(FakeArchicad):
        def tapir(self, command, params=None, timeout=None):
            if command == "GetGDLParametersOfElements":
                self.calls.append((command, params))
                return {"gdlParametersOfElements": [{"parameters": [
                    {"name": prefix + group, "type": kind, "value": value}
                    for group in ("0", "50", "100", "200")
                    for prefix, kind, value in (("bShowZoneNum_", "Boolean", True), ("bShowArea_", "Boolean", True),
                                                ("iFrameStyle_", "Integer", 5))]}]}
            if command == "SetGDLParametersOfElements":
                self.calls.append((command, params))
                return {"executionResults": [{"success": True}]}
            return super().tapir(command, params, timeout)
    ac, report = KnownStamp(), obmer.Report()
    obmer.fallback_zone_markers(ac, SCENE, {"R1": "Z1"}, PROFILE, 42, 0, report)
    assert not report.errors, report.errors
    text = next(p for c, p in ac.calls if c == "CreateTexts")["textsData"][0]
    circle = next(p for c, p in ac.calls if c == "CreatePolylines")["polylinesData"][0]
    assert text["text"] == "1" and text["layerIndex"] == 42
    assert text["style"]["fontName"] == "GOST type B Кириллический" and text["style"]["height"] == 3
    assert text["style"]["penIndex"] == circle["linePenIndex"] == 120
    assert circle["coordinates"][0] == circle["coordinates"][-1] and len(circle["arcs"]) == 4
    center = text["coordinate"]
    assert all(abs((p["x"] - center["x"]) ** 2 + (p["y"] - center["y"]) ** 2 - 0.15 ** 2) < 1e-8 for p in circle["coordinates"])
    hide = next(p for c, p in ac.calls if c == "SetGDLParametersOfElements")["elementsWithGDLParameters"][0]
    assert all(p["value"] is False if p["name"].startswith("bShow") else p["value"] == 1 for p in hide["gdlParameters"])
    assert [c for c, _ in ac.calls].index("CreatePolylines") < [c for c, _ in ac.calls].index("SetGDLParametersOfElements")

    class FailedCircle(KnownStamp):
        def tapir(self, command, params=None, timeout=None):
            if command == "CreatePolylines":
                self.calls.append((command, params))
                return {"elements": [{"error": {"message": "refused"}}]}
            return super().tapir(command, params, timeout)
    ac, report = FailedCircle(), obmer.Report()
    obmer.fallback_zone_markers(ac, SCENE, {"R1": "Z1"}, PROFILE, 42, 0, report)
    assert report.errors and any(c == "DeleteElements" for c, _ in ac.calls)
    assert not any(c == "SetGDLParametersOfElements" for c, _ in ac.calls)

    class CircleException(KnownStamp):
        def tapir(self, command, params=None, timeout=None):
            if command == "CreatePolylines":
                raise RuntimeError("circle creation failed")
            return super().tapir(command, params, timeout)
    ac, report = CircleException(), obmer.Report()
    obmer.fallback_zone_markers(ac, SCENE, {"R1": "Z1"}, PROFILE, 42, 0, report)
    assert report.errors and "circle creation failed" in report.errors[-1]
    assert len(next(p for c, p in ac.calls if c == "DeleteElements")["elements"]) == 1

    class FailedHide(KnownStamp):
        def tapir(self, command, params=None, timeout=None):
            if command == "SetGDLParametersOfElements":
                self.calls.append((command, params))
                return {"executionResults": [{"success": False, "error": {"message": "refused"}}]}
            return super().tapir(command, params, timeout)
    ac, report = FailedHide(), obmer.Report()
    obmer.fallback_zone_markers(ac, SCENE, {"R1": "Z1"}, PROFILE, 42, 0, report)
    assert report.errors and "штамп не скрыт" in report.errors[-1]
    removed = next(p for c, p in ac.calls if c == "DeleteElements")["elements"]
    assert len(removed) == 2, "Failed hiding must remove both newly created marker elements"


def test_unknown_zone_stamp_is_not_hidden():
    ac, report = FakeArchicad(), obmer.Report()
    obmer.fallback_zone_markers(ac, SCENE, {"R1": "Z1"}, PROFILE, 42, 0, report)
    assert report.errors and "неизвестный тип штампа" in report.errors[0]
    assert [c for c, _ in ac.calls] == ["GetGDLParametersOfElements"]


def test_reference_dimension_preferences_verify_format_not_preset_index():
    # deslab: AC29 can retain the informational named-preset index while the actual format is already integer mm.
    class Views(FakeArchicad):
        def tapir(self, command, params=None, timeout=None):
            if command == "SetProjectPreferences":
                self.calls.append((command, params))
                assert params["dimensions"]["dimensionStandardIndex"] == 0
                self.dimension_prefs["linear"] = params["dimensions"]["linear"]
                return {"dimensions": {"success": True}}
            return super().tapir(command, params, timeout)
        def api(self, command, params=None):
            tree = super().api(command, params)
            tree["navigatorTree"]["rootItem"]["children"].append({
                "name": "Другой вид", "type": "StoryItem", "navigatorItemId": {"guid": "OTHER"}})
            return tree
    ac, report = Views(), obmer.Report()
    assert ac.dimension_prefs["dimensionStandardIndex"] == 4
    obmer.apply_dimension_preferences(ac, PROFILE, report)
    assert not report.errors, report.errors
    assert ac.dimension_prefs == {"dimensionStandardIndex": 4, "linear": {"unit": "Millimeter", "decimals": 0}}
    calls = next(p for c, p in ac.calls if c == "SetViewSettings")["navigatorItemIdsWithViewSettings"]
    assert {v["navigatorItemId"]["guid"] for v in calls} == {"V0", "V1"}
    assert all(v["viewSettings"] == {"dimensionStyle": "Миллиметры"} for v in calls)


def test_reference_dimension_preferences_errors_are_reported():
    class IgnoredPreferences(FakeArchicad):
        def tapir(self, command, params=None, timeout=None):
            if command == "SetProjectPreferences":
                self.calls.append((command, params))
                return {"dimensions": {"success": True}}
            return super().tapir(command, params, timeout)
    ac, report = IgnoredPreferences(), obmer.Report()
    obmer.apply_dimension_preferences(ac, PROFILE, report)
    assert report.errors and "после записи" in report.errors[0]
    assert not any(c == "SetViewSettings" for c, _ in ac.calls)

    class RefusedView(FakeArchicad):
        def tapir(self, command, params=None, timeout=None):
            if command == "SetViewSettings":
                return {"executionResults": [{"success": True}, {"success": False, "error": {"message": "refused"}}]}
            return super().tapir(command, params, timeout)
    ac, report = RefusedView(), obmer.Report()
    obmer.apply_dimension_preferences(ac, PROFILE, report)
    assert report.errors and "во всех видах" in report.errors[0]

    class RefusedPreferences(FakeArchicad):
        def tapir(self, command, params=None, timeout=None):
            if command == "SetProjectPreferences":
                return {"dimensions": {"success": False, "error": {"message": "refused"}}}
            return super().tapir(command, params, timeout)
    ac, report = RefusedPreferences(), obmer.Report()
    obmer.apply_dimension_preferences(ac, PROFILE, report)
    assert report.errors and "refused" in report.errors[0]

    class IgnoredNamedStandard(FakeArchicad):
        def tapir(self, command, params=None, timeout=None):
            if command == "SetViewSettings":
                return {"executionResults": [{"success": True}, {"success": True}]}
            return super().tapir(command, params, timeout)
    ac, report = IgnoredNamedStandard(), obmer.Report()
    obmer.apply_dimension_preferences(ac, PROFILE, report)
    assert report.errors and "«Миллиметры» не найден" in report.errors[0]


def test_zone_category_repairs_unloaded_reference_stamp():
    import copy
    class Categories(FakeArchicad):
        def __init__(self):
            super().__init__()
            self.categories = [
                {"name": "Полы", "attributeId": {"guid": "C1"}, "categoryCode": "ГОСТ", "color": {"red": 0.5, "green": 0.5, "blue": 0.5},
                 "stampName": "Маркировка помещения 27", "stampMainGuid": "UNLOADED", "stampRevGuid": "OLD"},
                {"name": "A stock category", "attributeId": {"guid": "C2"}, "stampName": "Паспорт Зоны", "stampMainGuid": "STOCK", "stampRevGuid": "R2"},
                {"name": "Z matching category", "attributeId": {"guid": "C3"}, "stampName": "Маркировка помещения 29", "stampMainGuid": "MATCH", "stampRevGuid": "R3"}]
        def tapir(self, command, params=None, timeout=None):
            self.calls.append((command, params))
            if command == "GetAttributesByType":
                return {"attributes": [{"attributeId": c["attributeId"]} for c in self.categories]}
            if command == "GetZoneCategories":
                ids = [a["attributeId"] for a in params["attributeIds"]]
                return {"zoneCategories": [copy.deepcopy(c) for c in self.categories if c["attributeId"] in ids]}
            if command == "GetAvailableLibraryParts":
                return {"libraryParts": [{"guid": g, "typeId": "Object"} for g in ("STOCK", "MATCH")]}
            if command == "CreateZoneCategories":
                self.categories[0].update(params["zoneCategoryDataArray"][0])
                return {"attributeIds": [{"attributeId": self.categories[0]["attributeId"]}]}
            return {}
    ac, report = Categories(), obmer.Report()
    original = copy.deepcopy(ac.categories[0])
    assert obmer.ensure_zone_category(ac, PROFILE, report) == {"guid": "C1"}
    assert not report.errors, report.errors
    assert all(ac.categories[0][k] == original[k] for k in ("name", "attributeId", "categoryCode", "color"))
    assert ac.categories[0]["stampMainGuid"] == "MATCH" and ac.categories[0]["stampRevGuid"] == "R3"
    assert next(p for c, p in ac.calls if c == "CreateZoneCategories")["overwriteExisting"] is True
    ac.calls.clear()
    assert obmer.ensure_zone_category(ac, PROFILE, report) == {"guid": "C1"}
    assert not any(c == "CreateZoneCategories" for c, _ in ac.calls), "An already loaded stamp must remain unchanged"


def test_missing_zone_stamp_stops_before_model_creation():
    class NoStamp(FakeArchicad):
        def tapir(self, command, params=None, timeout=None):
            if command == "GetFavoritesByType":
                return {"favorites": []}
            if command == "GetAvailableLibraryParts":
                return {"libraryParts": []}
            return super().tapir(command, params, timeout)
    ac = NoStamp()
    try:
        obmer.build_measured_plan(ac, SCENE, PROFILE)
    except ValueError as error:
        assert "загруженным штампом" in str(error)
    else:
        raise AssertionError("Cannot build zones using an unloaded stamp")
    assert not any(c == "BuildModelFromScan" for c, _ in ac.calls)


def test_axonometry_on_white():
    # deslab: 14.11.2026, Игорь's Archicad 29: the axonometry on a blue background (the style takes the rendering's sky)
    from deslab_bridge import layouts

    class Styles:
        def __init__(self, styles):
            self.styles, self.calls = styles, []

        def tapir(self, command, params=None, timeout=None):
            self.calls.append((command, params))
            if command == "Get3DStyleDetails":
                return {"styles": [self.styles.get(n, {"error": {"code": 1, "message": "no"}}) for n in params["names"]]}
            for st in params["styles"]:
                self.styles[st["name"]] = st
            return {"executionResults": [{"success": True}]}
    white = {"red": 1.0, "green": 1.0, "blue": 1.0}
    ac = Styles({"Тени": {"name": "Тени", "backgroundAsInRendering": False, "chainBackgroundColors": True, "backgroundColor": white,
                          "backgroundSkyColor": {"red": 0.5, "green": 0.7, "blue": 1.0}, "castShadowPercent": 40}})
    assert layouts.axonometry_style(ac, "Тени") == ("Тени", None) and len(ac.calls) == 1
    ac.styles["Тени"]["backgroundAsInRendering"] = True
    name, note = layouts.axonometry_style(ac, "Тени")
    made = ac.styles[layouts.AXONOMETRY_STYLE]
    assert name == layouts.AXONOMETRY_STYLE and note is None and ac.calls[-1][0] == "Create3DStyles"
    assert made["backgroundAsInRendering"] is False and made["backgroundColor"] == white and made["castShadowPercent"] == 40
    name, note = layouts.axonometry_style(ac, "Тени")
    assert ac.calls[-1][0] == "Change3DStyles" and ac.styles["Тени"]["backgroundAsInRendering"] is True
    name, note = layouts.axonometry_style(ac, "Нет такого")
    assert name == layouts.AXONOMETRY_STYLE and "Нет такого" in note


def test_shaft_walls_join_on_axes():
    from deslab_bridge import mep
    walls = mep.snap_joints([
        {"start": {"x": 0.24, "y": 0.963}, "end": {"x": 0.56, "y": 0.963}, "thickness": 0.1},
        {"start": {"x": 0.56, "y": 0.963}, "end": {"x": 0.56, "y": 0.053}, "thickness": 0.1},
        {"start": {"x": 0.29, "y": 0.913}, "end": {"x": 0.29, "y": 0.584}, "thickness": 0.1},
        {"start": {"x": 0.24, "y": 0.534}, "end": {"x": 0.51, "y": 0.534}, "thickness": 0.1}])
    # ends that stopped on a face or ran past an axis are put on the axis (L and T joints)
    assert walls[0]["start"] == {"x": 0.29, "y": 0.963}, walls[0]
    assert walls[2]["start"] == {"x": 0.29, "y": 0.963} and walls[2]["end"] == {"x": 0.29, "y": 0.534}, walls[2]
    assert walls[3]["start"] == {"x": 0.29, "y": 0.534} and walls[3]["end"] == {"x": 0.56, "y": 0.534}, walls[3]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


def test_fit_walls_to_rooms_scan_misses():
    """deslab: JK 08.11.2026, a wall end 46 mm short of the outline and a 0.11 m nook are fitted, a wall a thickness off is not."""
    def wall(i, a, b, t):
        return {"id": i, "start": {"x": a[0], "y": a[1]}, "end": {"x": b[0], "y": b[1]}, "thickness": t, "height": 3, "openings": []}
    room = [(0.1, 0.1), (2.0, 0.1), (2.0, 0.3), (4.9, 0.3), (4.9, 3.9), (0.1, 3.9)]
    scene = {"format": obmer.SCENE_FORMAT, "rooms": [{"id": "R1", "polygon": [{"x": x, "y": y} for x, y in room]}], "walls": [
        wall("S1", (0, 0), (2.0, 0), 0.2), wall("S2", (2.046, 0), (5, 0), 0.6), wall("E", (5, 0), (5, 4), 0.2),
        wall("N", (5, 4), (0, 4), 0.2), wall("W", (0, 4), (0, 0), 0.2)]}
    assert obmer.check_room_faces(scene)
    fitted, notes = obmer.fit_walls_to_rooms(scene)
    assert not obmer.check_room_faces(fitted), notes
    assert any("S2" in n for n in notes)
    scene["walls"][3] = wall("N", (5, 4.2), (0, 4.2), 0.2)  # a whole thickness off: stays an error
    assert obmer.check_room_faces(obmer.fit_walls_to_rooms(scene)[0])


def test_ai_keeps_a_box_the_outline_goes_round():
    """deslab: JK 08.11.2026, the model called the entrance box clutter; the room outline runs along it, so it stays."""
    from deslab_bridge import ai
    def wall(i, a, b, t):
        return {"id": i, "start": {"x": a[0], "y": a[1]}, "end": {"x": b[0], "y": b[1]}, "thickness": t, "height": 3, "openings": []}
    room = [(0.1, 0.1), (2.0, 0.1), (2.0, 0.5), (2.6, 0.5), (2.6, 0.1), (4.9, 0.1), (4.9, 3.9), (0.1, 3.9)]
    scene = {"format": obmer.SCENE_FORMAT, "rooms": [{"id": "R1", "polygon": [{"x": x, "y": y} for x, y in room]}], "walls": [
        wall("S", (0, 0), (5, 0), 0.2), wall("BOX", (2.3, 0.1), (2.3, 0.5), 0.6), wall("E", (5, 0), (5, 4), 0.2),
        wall("N", (5, 4), (0, 4), 0.2), wall("W", (0, 4), (0, 0), 0.2), wall("JUNK", (3.0, 2.0), (3.3, 2.0), 0.1)]}
    assert not obmer.check_room_faces(scene)
    new, report = ai.apply_review(scene, {"clutter": [{"wallId": "BOX"}, {"wallId": "JUNK"}]})
    assert [w["id"] for w in new["walls"]] == ["S", "BOX", "E", "N", "W"], report


def test_note_text():
    import json
    from deslab_bridge import layouts
    cfg = json.load(open(Path(__file__).resolve().parent.parent / "office-profile.json", encoding="utf-8"))["layouts"]["note"]
    text = layouts.note_text(cfg, {"kind": "scan", "name": "Nagornaya_7.e57", "date": "11.11.2026"}, 49.786)
    assert text.splitlines() == ["ПРИМЕЧАНИЕ", "- все чертежи выполнены на основании 3D-скана «Nagornaya_7.e57» от 11.11.2026",
                                 "- размеры и высоты даны от уровня чернового пола", "- фактическая площадь при замерах 49,79 м2"]
    assert "на основе обмеров" in layouts.note_text(cfg, None, 10)


def test_scheme_xml():
    from deslab_bridge import layouts
    criterion = lambda name, value: ("\t\t\t<Criterion>\n\t\t\t\t<UniValue>\n\t\t\t\t\t<Value>\n\t\t\t\t\t\t"
                                     f'<Variant Type="{value[0]}">\n\t\t\t\t\t\t\t<Status>Normal</Status>\n\t\t\t\t\t\t\t<Value>{value[1]}</Value>\n'
                                     f"\t\t\t\t\t\t</Variant>\n\t\t\t\t\t</Value>\n\t\t\t\t</UniValue>\n\t\t\t\t{name}\n\t\t\t</Criterion>\n")
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
           '<Scheme_Settings ID="ИКЭ-07" Last_Modified="1" Name="Экспликация 1-й этаж" Scheme_Type="Element_List" Version="27.0.0">\n'
           '\t<View_Settings>\n\t\t<Hide_Main_Header value="true"/>\n\t</View_Settings>\n\t<Criteria_Settings>\n\t\t<Complex_Criteria>\n'
           + criterion("<Name/>", ("NumVariant", 17)) + criterion("<Name>Слой</Name>", ("Property::AttributeIndexValue", 4)) +
           '\t\t</Complex_Criteria>\n\t</Criteria_Settings>\n\t<Header_Items>\n\t\t<Header_Item>\n\t\t\t<Caption>Экспликация 1-й этаж</Caption>\n'
           '\t\t</Header_Item>\n\t\t<Header_Item>\n\t\t\t<Caption>№</Caption>\n\t\t</Header_Item>\n\t</Header_Items>\n</Scheme_Settings>\n')
    new = layouts.scheme_xml(xml, "Экспликация обмерный план", "ДСЛ-ЭП-1", "Экспликация помещений", 57)
    assert '<Scheme_Settings ID="ДСЛ-ЭП-1" Last_Modified="1" Name="Экспликация обмерный план" Scheme_Type="Element_List"' in new
    assert '<Hide_Main_Header value="false"/>' in new and "<Caption>Экспликация помещений</Caption>" in new and "<Caption>№</Caption>" in new
    assert "<Value>57</Value>" in new and "<Value>17</Value>" in new and "<Value>4</Value>" not in new
    # no layer of the zones in the project: the criterion goes, the rest stays
    new = layouts.scheme_xml(xml, "А", "Б", None, None)
    assert new.count("<Criterion>") == 1 and "Слой" not in new and '<Hide_Main_Header value="true"/>' in new
    assert new.count("</Complex_Criteria>") == 1 and "<Value>17</Value>" in new
    rows = '<Row Type="Header">\n<Row_Height value="15"/>\n</Row>\n<Row Type="Value">\n<Row_Height value="8"/>\n</Row>\n'
    new = layouts.scheme_xml(xml.replace("\t<View_Settings>", rows + "\t<View_Settings>"), "А", "Б", None, None, {"Header": 8, "Value": 6.5})
    assert '<Row Type="Header">\n<Row_Height value="8"/>' in new and '<Row Type="Value">\n<Row_Height value="6.5"/>' in new
    # Archicad 29 asked about a scheme «not from the current version» (Игорь, 14.11.2026): the running one's is put in
    assert 'Version="29.0.0">' in layouts.scheme_xml(xml, "А", "Б", version=29) and 'Version="27.0.0"' in new


def test_axonometry_spot():
    from deslab_bridge import layouts
    frame = {"xMin": 0.034, "xMax": 0.326, "yMin": 0.04, "yMax": 0.296}
    cfg = {"right": 0.3085, "bottom": 0.0494, "maxWidth": 0.119, "maxHeight": 0.130, "minWidth": 0.045, "gap": 0.005}
    # a wide plan over the whole width: the strip below it, 35.6 mm tall
    x, y, w, h = layouts.axonometry_spot({"xMin": 0.03, "xMax": 0.31, "yMin": 0.09, "yMax": 0.28}, frame, cfg, 2.0)
    assert abs(h - 0.0356) < 1e-9 and abs(w - 0.0712) < 1e-9 and abs(x + w - 0.3085) < 1e-9 and y == 0.0494
    # a narrow plan: the strip to its right, the template's size at most
    x, y, w, h = layouts.axonometry_spot({"xMin": 0.03, "xMax": 0.18, "yMin": 0.06, "yMax": 0.28}, frame, cfg, 1.0)
    assert abs(w - 0.119) < 1e-9 and abs(x + w - 0.326) < 1e-9
    # no room at all
    assert layouts.axonometry_spot({"xMin": 0.03, "xMax": 0.32, "yMin": 0.06, "yMax": 0.28}, frame, cfg, 2.0) is None


def test_narrow_hall_labels():
    """deslab: 14.11.2026, a hall 1.9 m wide (Nagornaya): its two inner chains 1 m apart, a vent duct in the corner by the
    entrance door. The door label and the doorway label to the bathroom found no place clear of the chains and were drawn
    across a dimension line; now a 45° leader takes them between the chains (or the label goes to the other room)."""
    from deslab_bridge import check as layout_check

    def wall(i, a, b, t, openings=()):
        return {"id": f"W{i}", "start": {"x": a[0], "y": a[1]}, "end": {"x": b[0], "y": b[1]}, "thickness": t, "height": 3.3,
                "kind": "interior", "openings": list(openings)}

    def room(i, x0, y0, x1, y1):
        return {"id": f"R{i}", "number": i, "name": f"Помещение {i}", "floor": 0, "ceiling": 3.3, "height": 3.3,
                "area": (x1 - x0) * (y1 - y0),
                "polygon": [{"x": x0, "y": y0}, {"x": x1, "y": y0}, {"x": x1, "y": y1}, {"x": x0, "y": y1}]}
    scene = {
        "format": "deslab.scan2bim/1", "levels": {"floor": 0.0, "ceiling": 3.3}, "slabs": [], "assumptions": [], "warnings": [],
        "walls": [
            wall(1, (-0.2, -0.16), (2.1, -0.16), 0.32,
                 [{"id": "W1.1", "kind": "door", "centerOffset": 1.33, "width": 0.94, "sill": 0, "height": 2.21}]),
            wall(2, (-0.2, 4.95), (2.1, 4.95), 0.3,
                 [{"id": "W2.1", "kind": "opening", "centerOffset": 1.41, "width": 0.79, "sill": 0, "height": 2.21}]),
            wall(3, (2.1, -0.32), (2.1, 8.0), 0.4), wall(4, (-0.2, 8.0), (-0.2, -0.32), 0.4), wall(5, (2.1, 8.0), (-0.2, 8.0), 0.4)],
        "rooms": [room(1, 0, 0, 1.9, 4.8), room(2, 0, 5.1, 1.9, 7.8)],
        "mep": [{"kind": "shaft", "duct": True, "id": "VD1", "label": "Воздуховод 260×150", "elevation": 0, "height": 3.3,
                 "polygon": [{"x": 0.21, "y": 0}, {"x": 0.47, "y": 0}, {"x": 0.47, "y": 0.15}, {"x": 0.21, "y": 0.15}]}],
    }
    assert not obmer.check_room_faces(scene)
    texts = obmer.plan_height_texts(scene, PROFILE)
    chains, labels, eng_labels, split = obmer.plan_annotations(scene, PROFILE, texts)
    room_chains = [c for c in chains if c.get("plan") != "eng"] if split else chains
    faults = [f for f in layout_check.check(scene, room_chains, labels) if "подпись" in f or "налезает" in f]
    assert not faults, (faults, [(lb["opening"], lb["midCoordinate"], lb["box"]) for lb in labels])
    # the left chain of the hall goes just past the duct, not a whole step further
    left = next(c for c in chains if c["room"] == "R1" and abs(c["direction"]["x"]) < 0.5 and c["referencePoint"]["x"] < 1.0)
    assert left["offset"] < 0.6, left
