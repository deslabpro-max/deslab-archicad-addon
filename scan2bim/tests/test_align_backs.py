"""align_backs: walls end to end keep their room faces and share one back face (python scan2bim/tests/test_align_backs.py)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deslab_scan2bim.analyze import Wall, align_backs  # noqa: E402


def wall(axis, c, a, b, t, side, measured):
    w = Wall(axis=axis, c=c, a=a, b=b, thickness=t, room_side=side, measured=measured)
    w.beg, w.end = np.array(w.point(a), float), np.array(w.point(b), float)
    return w


def back(w):
    return w.c - w.room_side * w.thickness / 2 if w.room_side else None


# Nagornaya 7: a measured wall at the entrance door and a one-sided one after it (jog of 4 cm),
# and two one-sided walls of the left facade with a real 10 cm step of the room faces.
w8 = wall(1, 1.075, 1.078, 3.337, 0.287, 0, True)
w9 = wall(1, 1.032, 3.337, 8.538, 0.38, 1, False)
w4 = wall(0, 1.07, 1.063, 6.146, 0.38, 1, False)
w5 = wall(0, 1.175, 6.146, 9.185, 0.38, 1, False)
assert align_backs([w8, w9, w4, w5])
assert abs(back(w9) - (1.075 - 0.287 / 2)) < 1e-6          # takes the measured back face
assert abs((w9.c + w9.thickness / 2) - 1.222) < 1e-6        # keeps its scanned room face
assert abs(back(w5) - back(w4)) < 1e-6                       # one straight outer face
assert abs((w5.c + w5.thickness / 2) - 1.365) < 1e-6        # the real step stays on the room side
assert w8.thickness == 0.287 and w4.thickness == 0.38        # leaders unchanged
assert abs(w9.beg[1] - w9.c) < 1e-9 and abs(w5.end[0] - w5.c) < 1e-9
assert not align_backs([w8, w9, w4, w5])                     # nothing left to do

# run 6: the one-sided wall after the door is skewed (1.06 -> 1.12 over 5.2 m); it becomes
# parallel to the measured wall, so the outer face is one straight line without a wedge
w8 = wall(1, 1.075, 1.078, 3.337, 0.287, 0, True)
w9 = wall(1, 1.09, 3.337, 8.538, 0.324, 1, False)
w9.slope = 0.06 / 5.2
w9.beg, w9.end = np.array(w9.point(w9.a), float), np.array(w9.point(w9.b), float)
assert align_backs([w8, w9])
assert w9.slope == 0.0
assert abs(w9.beg[1] - w9.end[1]) < 1e-9
assert abs(back(w9) - (1.075 - 0.287 / 2)) < 1e-6
assert abs((w9.c + w9.thickness / 2) - (1.09 + 0.162)) < 1e-6  # mean room face kept
print("ok align_backs")
