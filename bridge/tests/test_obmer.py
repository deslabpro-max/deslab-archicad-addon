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
    labels = {l["opening"]: l for l in obmer.plan_opening_labels(SCENE, PROFILE)}
    text = "".join(r["text"] for r in labels["W1.1"]["runs"])
    assert text == "hокна 1950\nhподокон 690", text
    assert any(r.get("effectSubscript") for r in labels["W1.1"]["runs"])
    assert labels["W1.1"]["midCoordinate"]["y"] > 0  # into the room, not outside
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
        if command == "GetAttributesByType":
            return {"attributes": [{"name": n, "index": i + 10} for i, n in enumerate(set(PROFILE["layers"].values()))]}
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
    assert names[0] == "GetAttributesByType" and names[1] == "BuildModelFromScan"
    assert len(names) < 25, names  # a whole sheet in a couple dozen fast calls, not hundreds of Claude turns
    build = ac.calls[1][1]
    assert build["scene"]["rooms"][0]["name"] == "Кухня"
    assert build["favorites"]["zone"] == "ОБМЕР шаблон Зона"
    labels = next(p for c, p in ac.calls if c == "CreateLabels")["labelsData"]
    assert labels[0]["favoriteName"] == "ОБМЕР шаблон Выноска" and labels[0]["parentElementId"] == {"guid": "OW"}
    assert "UpdateDrawings" in names and not report["errors"], report


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
