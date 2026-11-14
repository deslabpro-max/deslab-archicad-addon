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


def test_two_ducts_side_by_side():
    # deslab: 13.11.2026, Nagornaya bathroom corner: a narrow and a wide rectangular vent duct stand side by side from
    # floor to ceiling, 27 cm out of the wall face (y 0.15), with a 3 cm groove between them: two candidates
    scene = {"transform": {"rotationDeg": 0, "offset": [0, 0, 0]}, "levels": {"ceiling": 2.7},
             "walls": [{"id": "W1", "start": {"x": 0, "y": 0}, "end": {"x": 4, "y": 0}, "thickness": 0.3, "openings": []}]}
    # the wall face behind the ducts is hidden from the scanner, the bottom of the groove is not
    face = [plane_y(0.15, 0, 1.0, 0, 2.7), plane_y(0.15, 1.65, 4, 0, 2.7), plane_y(0.15, 1.15, 1.18, 0, 2.7),
            plane_z(0, 0, 4, 0.15, 2), plane_z(2.7, 0, 4, 0.15, 2)]
    narrow = [plane_y(0.42, 1.0, 1.15, 0, 2.7), plane_x(1.0, 0.15, 0.42, 0, 2.7), plane_x(1.15, 0.15, 0.42, 0, 2.7)]
    wide = [plane_y(0.415, 1.18, 1.65, 0, 2.7), plane_x(1.18, 0.15, 0.415, 0, 2.7), plane_x(1.65, 0.15, 0.415, 0, 2.7)]
    pts = np.concatenate(face + narrow + wide)
    pts += np.random.default_rng(3).normal(0, 0.002, pts.shape)
    found = [c for c in find_candidates(pts, scene) if c["top"] - c["bottom"] > 2.0]
    spans = sorted((c["from"], c["to"]) for c in found)
    assert len(found) == 2, found
    assert abs(spans[0][0] - 1.0) <= 0.03 and abs(spans[0][1] - 1.15) <= 0.03, found
    assert abs(spans[1][0] - 1.18) <= 0.03 and abs(spans[1][1] - 1.65) <= 0.03, found
    assert all(abs(c["depth"] - 0.27) < 0.02 for c in found), found
    # deslab: 14.11.2026, a duct 15 cm wide and 27 cm deep is no round pipe (Nagornaya C4 was «riser?»)
    assert [c["hint"] for c in found] == ["duct or box?", "duct or box?"], found
    # pressed together, the narrow one 26 cm and the wide one 30 cm out (Nagornaya): a step, two things
    tall = lambda p: [c for c in find_candidates(p, scene) if c["top"] - c["bottom"] > 2.0]  # noqa: E731
    flat = face[:2] + face[3:]
    pts = np.concatenate(flat + [plane_y(0.41, 1.0, 1.16, 0, 2.7), plane_x(1.0, 0.15, 0.41, 0, 2.7), plane_x(1.16, 0.41, 0.45, 0, 2.7),
                                 plane_y(0.45, 1.16, 1.65, 0, 2.7), plane_x(1.65, 0.15, 0.45, 0, 2.7)])
    pts += np.random.default_rng(4).normal(0, 0.002, pts.shape)
    spans = sorted((c["from"], c["to"]) for c in tall(pts))
    assert len(spans) == 2 and abs(spans[0][1] - 1.16) <= 0.03 and abs(spans[1][0] - 1.16) <= 0.03, spans
    # one box whose face slopes 5 cm over its 65 cm, or of one depth: one thing
    ys, zs = np.mgrid[1.0:1.65:0.015, 0:2.7:0.015]
    slope = np.stack([ys.ravel(), 0.45 - (ys.ravel() - 1.0) / 0.65 * 0.05, zs.ravel()], 1)
    for front in (slope, plane_y(0.42, 1.0, 1.65, 0, 2.7)):
        pts = np.concatenate(flat + [front, plane_x(1.0, 0.15, 0.45, 0, 2.7), plane_x(1.65, 0.15, 0.40, 0, 2.7)])
        pts += np.random.default_rng(5).normal(0, 0.002, pts.shape)
        spans = [(c["from"], c["to"]) for c in tall(pts)]
        assert len(spans) == 1 and abs(spans[0][0] - 1.0) <= 0.03 and abs(spans[0][1] - 1.65) <= 0.03, spans
    return found


def _cable(x0, amp, out0, out1, z0, z1, k, phase):
    """a cable 12 mm thick hanging down the wall face (y 0.15) in loose waves, `out0`..`out1` out of it"""
    z = np.arange(z0, z1, 0.004)
    x = x0 + amp * np.sin(k * z + phase)
    y = 0.15 + out0 + (out1 - out0) * (0.5 + 0.5 * np.sin(0.7 * k * z + 2 * phase))
    return np.concatenate([np.stack([x + 0.006 * np.cos(r), y + 0.006 * np.sin(r), z], 1)
                           for r in np.linspace(0, 2 * np.pi, 8, endpoint=False)])


def test_wiring_is_no_duct():
    # deslab: 14.11.2026, Nagornaya hall: old wiring hanging down by the entrance door was built as a duct 260x150.
    # A duct 26x15 cm has a face at every height; five cables in loose waves over a band as wide have none
    scene = {"transform": {"rotationDeg": 0, "offset": [0, 0, 0]}, "levels": {"ceiling": 2.7},
             "walls": [{"id": "W1", "start": {"x": 0, "y": 0}, "end": {"x": 4, "y": 0}, "thickness": 0.3, "openings": []}]}
    face = [plane_y(0.15, 0, 4, 0, 2.7), plane_z(0, 0, 4, 0.15, 2), plane_z(2.7, 0, 4, 0.15, 2)]
    duct = [plane_y(0.30, 1.0, 1.26, 0, 2.7), plane_x(1.0, 0.15, 0.30, 0, 2.7), plane_x(1.26, 0.15, 0.30, 0, 2.7)]
    wires = [_cable(2.6 + 0.05 * i, 0.04 + 0.02 * i, 0.03 + 0.02 * i, 0.12 + 0.01 * i, 0.1 + 0.3 * (i % 2), 2.7, 2.5 + i, i)
             for i in range(5)]
    pts = np.concatenate(face + duct + wires)
    pts += np.random.default_rng(7).normal(0, 0.002, pts.shape)
    found = {round(c["from"]): c for c in find_candidates(pts, scene) if c["top"] - c["bottom"] > 2.0}
    assert sorted(found) == [1, 3], found
    duct, wires = found[1], found[3]
    assert duct["face"]["cover"] >= 0.9 and duct["face"]["flat"] <= 0.01, duct
    assert wires["face"]["cover"] <= 0.5, wires
    assert duct["hint"] == "duct or box?" and wires["hint"] == "wires or clutter?", (duct, wires)
    return [duct, wires]


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
    # the door shut: its leaf 10 cm back in the wall fills the opening, the threshold stands in front of it
    shut = np.concatenate([plane_y(0.15, 0, 1.6, 0, 2.7), plane_y(0.15, 2.4, 4, 0, 2.7), plane_y(0.05, 1.6, 2.4, 0.2, 2.4),
                           plane_y(0.15, 1.6, 2.4, 0, 0.2), plane_z(0.2, 1.6, 2.4, 0.05, 0.15), plane_z(0, 0, 4, 0.15, 3)])
    measure_thresholds(shut, scene)
    assert abs(scene["walls"][0]["openings"][0]["sill"] - 0.2) < 0.03, scene["walls"][0]["openings"][0]


def test_empty_doorway():
    # two doors in one wall: one is looked through (kind "opening"), the other has its leaf shut (stays a door)
    scene = {"rooms": [{"polygon": [{"x": 0, "y": 0.1}, {"x": 6, "y": 0.1}, {"x": 6, "y": 4}, {"x": 0, "y": 4}]},
                       {"polygon": [{"x": 0, "y": -0.1}, {"x": 3, "y": -0.1}, {"x": 3, "y": -4}, {"x": 0, "y": -4}]}],
             "walls": [{"id": "W1", "start": {"x": 0, "y": 0}, "end": {"x": 6, "y": 0}, "thickness": 0.2,
                        "openings": [{"id": "W1.1", "kind": "door", "centerOffset": 1.5, "width": 0.9, "sill": 0.0, "height": 2.1},
                                     {"id": "W1.2", "kind": "door", "centerOffset": 4.0, "width": 0.9, "sill": 0.0, "height": 2.1}]}]}
    pts = np.concatenate([plane_y(0.1, 0, 1.05, 0, 2.7), plane_y(0.1, 1.95, 3.55, 0, 2.7), plane_y(0.1, 4.45, 6, 0, 2.7),
                          plane_y(0.05, 3.55, 4.45, 0, 2.1)])
    notes = mark_empty_doorways(pts, scene)
    kinds = [o["kind"] for o in scene["walls"][0]["openings"]]
    assert kinds == ["opening", "door"], (kinds, notes)
    # an open entrance door (no room behind it) stays a door
    scene["walls"][0]["openings"][1]["centerOffset"] = 4.0
    scene["walls"][0]["openings"][0]["kind"] = "door"
    mark_empty_doorways(np.concatenate([plane_y(0.1, 0, 1.05, 0, 2.7), plane_y(0.1, 1.95, 3.55, 0, 2.7)]), scene)
    assert [o["kind"] for o in scene["walls"][0]["openings"]] == ["opening", "door"]


if __name__ == "__main__":
    for c in test_candidates_and_views():
        print(c["id"], c["wallId"], c["side"], c["from"], c["to"], c["bottom"], c["top"], c["depth"], c["hint"])
    for c in test_two_ducts_side_by_side():
        print(c["id"], c["from"], c["to"], c["depth"], c["hint"])
    for c in test_wiring_is_no_duct():
        print(c["id"], c["from"], c["to"], c["depth"], c["fill"], c["hint"], c["face"])
    test_threshold()
    test_empty_doorway()
    print("ok")
