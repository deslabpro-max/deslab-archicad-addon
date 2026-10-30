"""Two rooms with a partition, a door and a window, rotated 7 degrees: the scene must match.

Run: python scan2bim/tests/test_synthetic.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from deslab_scan2bim import grid  # noqa: E402
from deslab_scan2bim.analyze import Options, Wall, analyze, join_walls  # noqa: E402

D = 0.015
H = 3.0


def plane_x(x, y0, y1, z0, z1):
    ys, zs = np.mgrid[y0:y1:D, z0:z1:D]
    return np.stack([np.full(ys.size, x), ys.ravel(), zs.ravel()], 1)


def plane_y(y, x0, x1, z0, z1):
    xs, zs = np.mgrid[x0:x1:D, z0:z1:D]
    return np.stack([xs.ravel(), np.full(xs.size, y), zs.ravel()], 1)


def plane_z(z, x0, x1, y0, y1):
    xs, ys = np.mgrid[x0:x1:D, y0:y1:D]
    return np.stack([xs.ravel(), ys.ravel(), np.full(xs.size, z)], 1)


def cloud():
    p = []
    # room A x 0..4, room B x 4.12..7.12, both y 0..5; partition 120 mm with a door y 2..2.9, head 2.1
    for x0, x1 in [(0, 4), (4.12, 7.12)]:
        p += [plane_z(0, x0, x1, 0, 5), plane_z(H, x0, x1, 0, 5), plane_y(0, x0, x1, 0, H), plane_y(5, x0, x1, 0, H)]
    p.append(plane_x(0, 0, 5, 0, H))
    # outer wall x 7.12 with a window y 1.5..3.0, sill 0.9, head 2.4, niche 0.45
    p += [plane_x(7.12, 0, 1.5, 0, H), plane_x(7.12, 3.0, 5, 0, H),
          plane_x(7.12, 1.5, 3.0, 0, 0.9), plane_x(7.12, 1.5, 3.0, 2.4, H),
          plane_z(0.9, 7.12, 7.57, 1.5, 3.0), plane_z(2.4, 7.12, 7.57, 1.5, 3.0),
          plane_y(1.5, 7.12, 7.57, 0.9, 2.4), plane_y(3.0, 7.12, 7.57, 0.9, 2.4)]
    for x in (4.0, 4.12):
        p += [plane_x(x, 0, 2, 0, H), plane_x(x, 2.9, 5, 0, H), plane_x(x, 2, 2.9, 2.1, H)]
    p += [plane_z(0, 4, 4.12, 2, 2.9), plane_z(2.1, 4, 4.12, 2, 2.9)]
    pts = np.concatenate(p)
    pts += np.random.default_rng(1).normal(0, 0.002, pts.shape)
    a = math.radians(7)
    rot = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    return (pts @ rot.T + [10, 20, -1.3]).astype(np.float32)


def near(a, b, tol):
    assert abs(a - b) <= tol, f"{a} != {b} (+-{tol})"


def main():
    scene = analyze(cloud(), Options())
    near(abs(scene["transform"]["rotationDeg"]), 7.0, 0.1)
    near(scene["levels"]["ceiling"], 3.0, 0.01)
    rooms = sorted(r["area"] for r in scene["rooms"])
    assert len(rooms) == 2, rooms
    near(rooms[0], 15.0, 0.2)
    near(rooms[1], 20.0, 0.2)
    partition = [w for w in scene["walls"] if w["measuredThickness"]]
    assert len(partition) == 1, partition
    near(partition[0]["thickness"], 0.12, 0.01)
    door = partition[0]["openings"]
    assert [o["kind"] for o in door] == ["door"], door
    near(door[0]["width"], 0.9, 0.02)
    near(door[0]["height"], 2.1, 0.02)
    windows = [o for w in scene["walls"] for o in w["openings"] if o["kind"] == "window"]
    assert len(windows) == 1, windows
    near(windows[0]["width"], 1.5, 0.02)
    near(windows[0]["sill"], 0.9, 0.02)
    near(windows[0]["height"], 1.5, 0.02)
    # one slab under the rooms and the walls, to the outer wall faces
    slabs = scene["slabs"]
    assert len(slabs) == 1, slabs
    poly = [(p["x"], p["y"]) for p in slabs[0]["polygon"]]
    assert grid.is_simple(poly), poly
    xs = [w[k]["x"] + s * w["thickness"] / 2 for w in scene["walls"] for k in ("start", "end") for s in (-1, 1)]
    near(min(x for x, _ in poly), min(xs), 0.02)
    near(max(x for x, _ in poly), max(xs), 0.02)
    check_closed(scene["walls"])
    check_join()
    print("ok:", len(scene["walls"]), "walls,", len(scene["rooms"]), "rooms")


def check_closed(walls):
    """Every wall end is another wall's end or lies on another wall's centre line."""
    for w in walls:
        for k in ("start", "end"):
            e = np.array([w[k]["x"], w[k]["y"]])
            ok = False
            for o in walls:
                if o is w:
                    continue
                p, q = np.array([o["start"]["x"], o["start"]["y"]]), np.array([o["end"]["x"], o["end"]["y"]])
                d = q - p
                t = np.clip(np.dot(e - p, d) / np.dot(d, d), 0, 1)
                ok |= np.linalg.norm(p + t * d - e) < 0.005
            assert ok, f"{w['id']} {k} is open"


def check_join():
    """An L corner with a 1 m gap and a T joint with a 0.5 m gap are closed."""
    a = Wall(axis=1, c=0.0, a=0.0, b=4.0, thickness=0.2, room_side=0, measured=True)   # along x at y=0
    b = Wall(axis=0, c=5.0, a=1.0, b=4.0, thickness=0.2, room_side=0, measured=True)   # along y at x=5
    t = Wall(axis=0, c=2.0, a=0.5, b=3.0, thickness=0.1, room_side=0, measured=True)   # stops 0.5 short of a
    walls = [a, b, t]
    join_walls(walls)
    assert np.allclose(a.end, (5.0, 0.0)) and np.allclose(b.beg, (5.0, 0.0)), (a.end, b.beg)
    assert np.allclose(t.beg, (2.0, 0.0)), t.beg


if __name__ == "__main__":
    main()
