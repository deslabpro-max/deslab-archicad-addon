"""deslab: candidates at the walls (a radiator under the window, a riser), the threshold of a balcony door and the
pictures for the reviewing model, on the synthetic flat.

Run: python scan2bim/tests/test_views.py
"""

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_synthetic import H, box, cloud, plane_x, plane_y, plane_z  # noqa: E402
from deslab_scan2bim.analyze import Options, analyze  # noqa: E402
from deslab_scan2bim.views import find_candidates, mark_empty_doorways, measure_thresholds, model_points, write_views  # noqa: E402


def test_candidates_and_views():
    # a radiator under the window of the outer wall (x 7.12): 100 mm out, y 1.6..2.9, z 0.15..0.75; a riser 50 mm at y 4.5
    radiator = [plane_x(7.02, 1.6, 2.9, 0.15, 0.75), plane_z(0.75, 7.02, 7.12, 1.6, 2.9), plane_z(0.15, 7.02, 7.12, 1.6, 2.9),
                plane_y(1.6, 7.02, 7.12, 0.15, 0.75), plane_y(2.9, 7.02, 7.12, 0.15, 0.75)]
    riser = [plane_x(7.02, 4.5, 4.55, 0, H), plane_y(4.5, 7.02, 7.07, 0, H), plane_y(4.55, 7.02, 7.07, 0, H)]
    pts = cloud(radiator + riser)
    scene = analyze(pts, Options(step=1))
    model = model_points(pts, scene)
    found = find_candidates(model, scene)
    hints = [c["hint"] for c in found]
    assert "radiator?" in hints, found
    assert "riser?" in hints, found
    rad = next(c for c in found if c["hint"] == "radiator?")
    assert abs((rad["to"] - rad["from"]) - 1.3) < 0.08, rad
    assert abs(rad["bottom"] - 0.15) < 0.05 and abs(rad["top"] - 0.75) < 0.05, rad
    assert abs(rad["depth"] - 0.10) < 0.03, rad
    scene["candidates"] = found
    with tempfile.TemporaryDirectory() as folder:
        views = write_views(pts, scene, folder)
        kinds = [v["kind"] for v in views]
        assert kinds.count("plan") == 3 and kinds.count("elevation") >= 4, kinds
        assert all(os.path.getsize(v["file"]) > 2000 for v in views)
    return found


def test_threshold():
    # a wall along x with a balcony door 0.8 wide at 2.0, a threshold of 180 mm under it
    scene = {"transform": {"rotationDeg": 0, "offset": [0, 0, 0]}, "levels": {"ceiling": 2.7},
             "walls": [{"id": "W1", "start": {"x": 0, "y": 0}, "end": {"x": 4, "y": 0}, "thickness": 0.3,
                        "openings": [{"id": "W1.1", "kind": "balconyDoor", "centerOffset": 2.0, "width": 0.8, "sill": 0.0, "height": 2.4},
                                     {"id": "W1.2", "kind": "window", "centerOffset": 3.2, "width": 0.8, "sill": 0.9, "height": 1.5}]}]}
    pts = np.concatenate([plane_y(0.15, 0, 1.6, 0, 2.7), plane_y(0.15, 2.4, 4, 0, 2.7), plane_y(0.15, 1.6, 2.4, 2.4, 2.7),
                          plane_y(0.15, 1.6, 2.4, 0, 0.18), plane_z(0.18, 1.6, 2.4, -0.15, 0.15), plane_z(0, 0, 4, 0.15, 3)])
    notes = measure_thresholds(pts, scene)
    door = scene["walls"][0]["openings"][0]
    assert abs(door["sill"] - 0.18) < 0.025, (door, notes)
    assert abs(door["sill"] + door["height"] - 2.4) < 1e-6, door
    assert scene["walls"][0]["openings"][1]["sill"] == 0.9
    # no threshold: the door stays at the floor
    scene["walls"][0]["openings"][0].update(sill=0.0, height=2.4)
    flat = np.concatenate([plane_y(0.15, 0, 1.6, 0, 2.7), plane_y(0.15, 2.4, 4, 0, 2.7), plane_z(0, 0, 4, -0.15, 3)])
    assert measure_thresholds(flat, scene) == [] and scene["walls"][0]["openings"][0]["sill"] == 0.0


def test_empty_doorway():
    # two doors in one wall: one is looked through (kind "opening"), the other has its leaf shut (stays a door)
    scene = {"walls": [{"id": "W1", "start": {"x": 0, "y": 0}, "end": {"x": 6, "y": 0}, "thickness": 0.2,
                        "openings": [{"id": "W1.1", "kind": "door", "centerOffset": 1.5, "width": 0.9, "sill": 0.0, "height": 2.1},
                                     {"id": "W1.2", "kind": "door", "centerOffset": 4.0, "width": 0.9, "sill": 0.0, "height": 2.1}]}]}
    pts = np.concatenate([plane_y(0.1, 0, 1.05, 0, 2.7), plane_y(0.1, 1.95, 3.55, 0, 2.7), plane_y(0.1, 4.45, 6, 0, 2.7),
                          plane_y(0.05, 3.55, 4.45, 0, 2.1)])
    notes = mark_empty_doorways(pts, scene)
    kinds = [o["kind"] for o in scene["walls"][0]["openings"]]
    assert kinds == ["opening", "door"], (kinds, notes)


if __name__ == "__main__":
    for c in test_candidates_and_views():
        print(c["id"], c["wallId"], c["side"], c["from"], c["to"], c["bottom"], c["top"], c["depth"], c["hint"])
    test_threshold()
    test_empty_doorway()
    print("ok")
