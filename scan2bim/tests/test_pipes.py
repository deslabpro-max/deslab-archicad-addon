"""deslab: pipes in the cloud: a heating riser in the corner by the window, its run along the outer wall to the
radiator, a sewer riser. Run: python scan2bim/tests/test_pipes.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_synthetic import H, cloud, plane_x  # noqa: E402
from deslab_scan2bim.analyze import Options, analyze  # noqa: E402
from deslab_scan2bim.pipes import find_pipes  # noqa: E402
from deslab_scan2bim.views import model_points  # noqa: E402


def cylinder(c, axis, lo, hi, d, facing, step=0.004):
    """The half of a pipe that faces the room: axis 'z' (c = x, y) or 'y' (c = x, z); `facing` is the angle of the
    middle of the seen half."""
    angles = np.arange(facing - math.pi / 2, facing + math.pi / 2, step / (d / 2))
    along = np.arange(lo, hi, step)
    a, s = np.meshgrid(angles, along)
    u, v = c[0] + d / 2 * np.cos(a.ravel()), c[1] + d / 2 * np.sin(a.ravel())
    if axis == "z":
        return np.stack([u, v, s.ravel()], 1)
    return np.stack([u, s.ravel(), v], 1)


def veil(x0, x1, y, share, seed):
    """Stray points between a pipe and the wall behind it, at a share of the heights."""
    rng = np.random.default_rng(seed)
    zs = rng.choice(np.arange(0, H, 0.05), int(H / 0.05 * share), replace=False) + 0.02
    xs = np.arange(x0, x1, 0.01)
    z, x = np.meshgrid(zs, xs)
    return np.stack([x.ravel(), np.full(x.size, y), z.ravel()], 1)


def test_pipes():
    # room B: outer wall x 7.12. Two heating risers d 26 mm 12 cm off the wall, 11 cm apart (Nagornaya), each with a
    # veil of stray points to the wall; a run d 20 mm at z 0.40 from the first riser to a radiator (a plate 6 cm off
    # the wall, y 1.6..2.6, z 0.15..0.65); a sewer riser d 110 mm at (4.25, 4.85); a riser outside the flat
    extra = [cylinder((7.00, 0.30), "z", 0, H, 0.026, math.pi), cylinder((7.00, 0.41), "z", 0, H, 0.026, math.pi),
             veil(7.01, 7.12, 0.30, 0.35, 1), veil(7.01, 7.12, 0.41, 0.35, 2),
             cylinder((7.00, 0.40), "y", 0.30, 1.6, 0.020, math.pi), plane_x(7.00, 1.6, 2.6, 0.15, 0.65),
             cylinder((4.25, 4.85), "z", 0, H, 0.110, -math.pi / 4), cylinder((8.2, 4.0), "z", 0, H, 0.05, math.pi)]
    pts = cloud(extra)
    scene = analyze(pts, Options(step=1))
    model = model_points(pts, scene)
    pipes = find_pipes(model, scene)
    risers = [p for p in pipes if p["kind"] == "riser"]
    runs = [p for p in pipes if p["kind"] == "run"]
    assert len(risers) == 3, pipes
    risers.sort(key=lambda p: p["diameter"])
    thin, wide = risers[0], risers[2]
    assert all(0.015 <= r["diameter"] <= 0.04 for r in risers[:2]) and 0.09 <= wide["diameter"] <= 0.13, risers
    gap = math.dist((thin["path"][0]["x"], thin["path"][0]["y"]), (risers[1]["path"][0]["x"], risers[1]["path"][0]["y"]))
    assert abs(gap - 0.11) < 0.015, gap
    assert thin["path"][0]["z"] == 0.0 and abs(thin["path"][1]["z"] - H) < 0.06, thin
    assert wide["hint"] == "sewer riser?" and thin["hint"] == "heating or water riser?"
    assert len(runs) == 1, runs
    run = runs[0]
    assert abs(run["path"][0]["z"] - 0.40) < 0.015 and 0.012 <= run["diameter"] <= 0.035, run
    length = math.dist((run["path"][0]["x"], run["path"][0]["y"]), (run["path"][1]["x"], run["path"][1]["y"]))
    assert abs(length - 1.3) < 0.15, (length, run)
    # the run ends on a riser's axis
    assert any(e.get("joins") in (thin["id"], risers[1]["id"]) for e in run["path"]), run
    return pipes


if __name__ == "__main__":
    for p in test_pipes():
        print(p["id"], p["kind"], p["diameter"], p["path"], p["hint"])
    print("ok")
