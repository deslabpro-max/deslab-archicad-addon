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
    # from the segment middle on the dimension line square into the room, then a shelf back
    # over the chain; it sits on the segment's end witness point
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
    assert lead[1]["end"]["y"] > 0.125  # the shelf runs back over the chain
    # the south chain's text stands at y 0.25..0.45 next to the corner: the leader text goes past it
    assert lead[1]["end"]["y"] > 0.45, lead
    assert not east[0].get("leaderConflict")


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

    def alive(self):
        return True

    def _new(self, n):
        return [{"elementId": {"guid": f"G{next(self.ids)}"}} for _ in range(n)]

    def tapir(self, command, params=None, timeout=None):
        self.calls.append((command, params))
        self.log.append((command, 0))
        if command == "GetAttributesByType" and params["attributeType"] == "Surface":
            return {"attributes": [{"name": "Бетон - Гладкий", "attributeId": {"guid": "S0"}}, {"name": "Краска - Белая", "attributeId": {"guid": "S1"}}]}
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
               "CreateAssociativeDimensions": "dimensionsData", "CreateHatches": "hatchesData"}.get(command)
        if key:
            return {"elements": self._new(len(params[key]))}
        if command == "GetElementsByType":
            return {"elements": [{"elementId": {"guid": "D1"}}]}
        return {}


def test_build_calls():
    ac = FakeArchicad()
    report = obmer.build_measured_plan(ac, SCENE, PROFILE, rooms={"R1": {"name": "Кухня", "wet": "kitchen"}})
    names = [c for c, _ in ac.calls]
    # a layer hidden in the open view is shown for the build and hidden again at the end
    assert names[:6] == ["GetAttributesByType", "GetLayers", "CreateLayers", "ImportFavorites", "GetAttributesByType", "BuildModelFromScan"], names
    assert names[-1] == "CreateLayers" and ac.calls[-1][1]["layerDataArray"][0]["isHidden"] is True
    assert ac.calls[2][1]["layerDataArray"] == [{"attributeId": {"guid": "L0"}, "name": "слой", "isHidden": False, "isLocked": False}]
    assert len(names) < 30, names  # a whole sheet in a couple dozen fast calls, not hundreds of Claude turns
    build = ac.calls[5][1]
    assert build["scene"]["rooms"][0]["name"] == "Кухня"
    assert build["favorites"]["zone"] == "ОБМЕР шаблон Зона"
    labels = next(p for c, p in ac.calls if c == "CreateLabels")["labelsData"]
    assert labels[0]["favoriteName"] == "ОБМЕР шаблон Выноска" and "parentElementId" not in labels[0]
    walls = next(p for c, p in ac.calls if c == "ModifyWalls")["wallsWithDetails"]
    assert all(w["sideMaterial"]["attributeId"] == {"guid": "S1"} for w in walls)
    boards = next(p for c, p in ac.calls if c == "SetGDLParametersOfElements")["elementsWithGDLParameters"]
    assert boards[0]["gdlParameters"] == [{"name": "gs_board_inside", "value": False}]
    assert "UpdateDrawings" in names and not report["errors"], report


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
