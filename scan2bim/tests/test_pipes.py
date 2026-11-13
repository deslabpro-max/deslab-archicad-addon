"""deslab: pipes in the cloud: a heating riser in the corner by the window, its run along the outer wall to the
radiator, a sewer riser. Run: python scan2bim/tests/test_pipes.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_synthetic import H, cloud  # noqa: E402
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


def test_pipes():
    # room B: outer wall x 7.12. A heating riser d 26 mm at (7.07, 0.30), a run d 20 mm along the outer wall at z 0.40
    # from the riser to y 1.6, a sewer riser d 110 mm at (4.25, 4.85)
    extra = [cylinder((7.07, 0.30), "z", 0, H, 0.026, math.pi), cylinder((7.08, 0.40), "y", 0.30, 1.6, 0.020, math.pi),
             cylinder((4.25, 4.85), "z", 0, H, 0.110, -math.pi / 4)]
    pts = cloud(extra)
    scene = analyze(pts, Options(step=1))
    model = model_points(pts, scene)
    pipes = find_pipes(model, scene)
    risers = [p for p in pipes if p["kind"] == "riser"]
    runs = [p for p in pipes if p["kind"] == "run"]
    assert len(risers) == 2, pipes
    thin = min(risers, key=lambda p: p["diameter"])
    wide = max(risers, key=lambda p: p["diameter"])
    assert 0.015 <= thin["diameter"] <= 0.04 and 0.09 <= wide["diameter"] <= 0.13, (thin, wide)
    assert thin["path"][0]["z"] == 0.0 and abs(thin["path"][1]["z"] - H) < 0.06, thin
    assert wide["hint"] == "sewer riser?" and thin["hint"] == "heating or water riser?"
    assert len(runs) == 1, runs
    run = runs[0]
    assert abs(run["path"][0]["z"] - 0.40) < 0.015 and 0.012 <= run["diameter"] <= 0.035, run
    length = math.dist((run["path"][0]["x"], run["path"][0]["y"]), (run["path"][1]["x"], run["path"][1]["y"]))
    assert abs(length - 1.3) < 0.12, (length, run)
    # the run ends on the riser's axis
    assert any(e.get("joins") == thin["id"] for e in run["path"]), run
    return pipes


if __name__ == "__main__":
    for p in test_pipes():
        print(p["id"], p["kind"], p["diameter"], p["path"], p["hint"])
    print("ok")
