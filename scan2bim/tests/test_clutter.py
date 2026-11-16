"""Clutter is no wall, and the first row of blocks (tracing) is found (Игорь 05.11.2026, JK: a stack
of tiles with a board leaning on it at the entrance door came out as a wall).

Run: python scan2bim/tests/test_clutter.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_synthetic import box, cloud, near, plane_x, plane_y, plane_z  # noqa: E402
from deslab_scan2bim.analyze import Options, analyze  # noqa: E402


def main():
    extra = []
    # room A (x 0..4): a stack 0.67 x 0.08 x 0.65 m by the south wall, a board leaning on it with a
    # gap (0.7-1.5 and 1.7-1.9 m), and a pile of material 1.0 x 0.8 x 0.35 m
    extra += box(1.0, 1.67, 0.05, 0.13, 0.65)
    extra += [plane_y(0.16, 1.0, 1.4, 0.7, 1.5), plane_y(0.16, 1.0, 1.4, 1.7, 1.9)]
    extra += box(2.5, 3.5, 3.5, 4.3, 0.35)
    # room B (x 4.12..7.12): tracing, 80 mm blocks 0.25 m high, an L from the south wall
    extra += box(5.5, 5.58, 0.0, 4.0, 0.25)
    extra += box(5.58, 6.8, 3.92, 4.0, 0.25)
    # a radiator on the partition, 0.12-0.62 m over the floor: no tracing
    extra += [plane_x(4.17, 1.0, 2.0, 0.12, 0.62), plane_x(4.25, 1.0, 2.0, 0.12, 0.62),
              plane_z(0.62, 4.17, 4.25, 1.0, 2.0), plane_z(0.12, 4.17, 4.25, 1.0, 2.0)]
    scene = analyze(cloud(extra), Options())
    assert len(scene["walls"]) == 5, [(w["id"], w["start"], w["end"], w["thickness"]) for w in scene["walls"]]
    rooms = sorted(r["area"] for r in scene["rooms"])
    near(rooms[0], 15.0, 0.2)
    near(rooms[1], 20.0, 0.2)
    tr = scene.get("tracing", [])
    assert len(tr) == 2, tr
    lens = sorted(abs(t["end"]["x"] - t["start"]["x"]) + abs(t["end"]["y"] - t["start"]["y"]) for t in tr)
    near(lens[0], 1.26, 0.06)   # the leg along x runs on to the other leg's axis
    near(lens[1], 4.0, 0.06)    # from the wall face
    for t in tr:
        near(t["thickness"], 0.08, 0.02)
        near(t["height"], 0.25, 0.03)
    print("ok: clutter left out,", len(tr), "tracing strips")


if __name__ == "__main__":
    main()
