"""fit_walls_to_rooms: the room outline and the wall ends agree (python -I scan2bim/tests/test_fit.py).
Data: the bottom wall of the JK_Will_Towers_463 scan, 05.11.2026."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deslab_scan2bim.fit import fit_walls_to_rooms  # noqa: E402

POLY = [(1.2648, 1.1866), (4.1882, 1.1866), (4.1882, 1.3014), (4.3293, 1.3014), (4.3293, 1.5014), (5.0935, 1.5014),
        (5.0935, 1.1866), (6.1482, 1.1866), (6.1482, 1.3844), (8.8682, 1.3844), (8.8682, 1.713), (10.0682, 1.7214),
        (10.1282, 1.713), (10.1282, 1.1765), (11.167, 1.1765), (11.167, 9.425), (1.2648, 9.425)]
WALLS = [("W1", (10.0682, 1.0646), (11.357, 1.0646), 0.224), ("W2", (6.1944, 1.1685), (9.0637, 1.1685), 0.432),
         ("W3", (9.0637, 1.3328), (10.0682, 1.3328), 0.76), ("W4", (11.357, 1.0646), (11.357, 9.745), 0.38),
         ("W5", (4.7114, 1.5014), (4.7114, 1.0696), 0.764), ("W6", (1.0748, 9.745), (1.0748, 1.0696), 0.38),
         ("W7", (11.357, 9.745), (1.0748, 9.745), 0.64), ("W8", (1.0748, 1.0696), (2.1482, 1.0696), 0.234),
         ("W9", (2.1282, 1.0696), (6.8482, 1.0696), 0.234)]
SCENE = {"walls": [{"id": i, "start": {"x": a[0], "y": a[1]}, "end": {"x": b[0], "y": b[1]}, "thickness": t, "openings": []}
                   for i, a, b, t in WALLS],
         "rooms": [{"id": "R1", "polygon": [{"x": x, "y": y} for x, y in POLY]}]}


def test_jk_bottom_wall():
    scene = copy.deepcopy(SCENE)
    notes = fit_walls_to_rooms(scene)
    w = {x["id"]: x for x in scene["walls"]}
    # wall ends on the outline's steps
    assert w["W2"]["start"]["x"] == 6.1482 and w["W2"]["end"]["x"] == 8.8682, w["W2"]
    assert w["W3"]["start"]["x"] == 8.8682 and w["W3"]["end"]["x"] == 10.1282, w["W3"]
    assert w["W1"]["start"]["x"] == 10.1282
    # W5 runs on past the room to its T joint with W9
    assert w["W5"]["end"]["y"] == 1.0696
    poly = [(p["x"], p["y"]) for p in scene["rooms"][0]["polygon"]]
    # the 8 mm slant is gone, the lump at W5 filled, every edge square
    assert all(abs(a[0] - b[0]) < 1e-9 or abs(a[1] - b[1]) < 1e-9 for a, b in zip(poly, poly[1:] + poly[:1])), poly
    assert (4.3293, 1.1866) in poly and (4.1882, 1.3014) not in poly, poly
    assert any("выступ 141 x 115" in n for n in notes), notes


if __name__ == "__main__":
    test_jk_bottom_wall()
    print("ok fit")
