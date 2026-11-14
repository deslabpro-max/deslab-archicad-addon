"""deslab: a vent channel through a wall near the ceiling and the grille at its far end (Nagornaya bathroom, 14.11.2026:
a channel 15 x 13 cm through the 42 cm wall at 2.90 m from the duct, a grille 20 x 30 cm on the kitchen side).
Run: python scan2bim/tests/test_penetrations.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_synthetic import cloud, plane_x, plane_y, plane_z  # noqa: E402
from deslab_scan2bim.analyze import Options, analyze  # noqa: E402
from deslab_scan2bim.penetrations import find_penetrations  # noqa: E402
from deslab_scan2bim.views import model_points  # noqa: E402


def test_vent_channel():
    # the partition x 4.00-4.12 between rooms A and B: a channel y 4.00-4.15, z 2.60-2.75 through it, a grille
    # 8 cm out of it on the room B side (x 4.12-4.20, y 3.95-4.20, z 2.55-2.80)
    extra = [plane_z(2.60, 4.0, 4.12, 4.0, 4.15), plane_z(2.75, 4.0, 4.12, 4.0, 4.15),
             plane_y(4.0, 4.0, 4.12, 2.60, 2.75), plane_y(4.15, 4.0, 4.12, 2.60, 2.75),
             plane_x(4.20, 3.95, 4.20, 2.55, 2.80), plane_y(3.95, 4.12, 4.20, 2.55, 2.80), plane_y(4.20, 4.12, 4.20, 2.55, 2.80),
             plane_z(2.55, 4.12, 4.20, 3.95, 4.20)]
    pts = cloud(extra)
    scene = analyze(pts, Options(step=1))
    model = model_points(pts, scene)
    found = find_penetrations(model, scene)
    assert len(found) == 1, found
    pen = found[0]
    assert pen["through"] and abs(pen["width"] - 0.15) < 0.03 and abs(pen["bottom"] - 2.60) < 0.03 and abs(pen["top"] - 2.75) < 0.03, pen
    ends = [e for e in pen["ends"] if abs(e["depth"] - 0.08) < 0.02]
    assert len(ends) == 1 and abs((ends[0]["to"] - ends[0]["from"]) - 0.25) < 0.04 and abs(ends[0]["bottom"] - 2.55) < 0.03, pen
    # the flat without it: no penetration (the window's reveals and the door's are no channels)
    plain = cloud()
    scene = analyze(plain, Options(step=1))
    assert not find_penetrations(model_points(plain, scene), scene)
    return found


if __name__ == "__main__":
    for p in test_vent_channel():
        print(p)
    print("ok")
