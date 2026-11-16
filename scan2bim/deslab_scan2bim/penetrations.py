"""deslab: 14.11.2026 (Игорь on Nagornaya v21: «У вентиляции есть отводы, в том числе и через стену. То есть нужно рисовать
проходку и делать обязательно вывод. Вентиляция – это очень важно»): channels through the walls and what stands out of
the wall's faces at their ends, measured in the cloud.

A wall's body is solid: the scanner sees into it only where a channel goes through it (or a niche goes into it). The
points inside the body (3 cm and more off both faces, away from the doors and windows) that gather into one spot of the
wall's elevation, 6-50 cm each way, are a penetration; it goes through when its points spread over half the wall's
thickness or more (the far end of the channel is seen as well). At each end, what stands out of the face up to 20 cm
around the channel's spot (a grille, the duct it comes from) is measured too.

Scene "penetrations": [{"id": "PN1", "wallId", "along", "width", "bottom", "top", "through", "at": {x, y} on the
wall's axis, "ends": [{"side": "left"|"right", "from", "to", "bottom", "top", "depth"}]}] ("left" is the left of the
wall's start -> end, as the pipes' runs)."""

from __future__ import annotations

import numpy as np

from .grid import label
from .views import _frame, _wall_coords

CELL = 0.02   # cell of the wall's elevation, m


def _grow(mask):
    out = mask.copy()
    out[1:, :] |= mask[:-1, :]
    out[:-1, :] |= mask[1:, :]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    return out


def find_penetrations(pts, scene, inset=0.03, least=40, size=(0.06, 0.5), through=0.5, reach=0.2, around=0.15):
    height = float(scene["levels"]["ceiling"])
    found = []
    for wall in scene.get("walls", []):
        a, u, n, length = _frame(wall)
        half = float(wall["thickness"]) / 2
        if 2 * half - 2 * inset < 0.04 or length < 0.2:
            continue
        t, d, _ = _wall_coords(pts, wall)
        z = pts[:, 2]
        body = (t > 0.05) & (t < length - 0.05) & (np.abs(d) < half - inset) & (z > 0.05) & (z < height - 0.02)
        for o in wall.get("openings", []):
            lo = float(o["centerOffset"]) - float(o["width"]) / 2 - 0.05
            body &= ~((t > lo) & (t < lo + float(o["width"]) + 0.1))
        if body.sum() < least:
            continue
        tb, db, zb = t[body], d[body], z[body]
        it = np.clip((tb / CELL).astype(np.int32), 0, int(length / CELL))
        iz = np.clip((zb / CELL).astype(np.int32), 0, int(height / CELL))
        count = np.zeros((int(length / CELL) + 1, int(height / CELL) + 1), dtype=np.int32)
        np.add.at(count, (it, iz), 1)
        labels, k = label(_grow(count >= 2))
        own = labels[it, iz]
        for c in range(1, k + 1):
            mine = own == c
            if mine.sum() < least:
                continue
            tt, zz, dd = tb[mine], zb[mine], db[mine]
            t0, t1 = (float(v) for v in np.percentile(tt, [2, 98]))
            z0, z1 = (float(v) for v in np.percentile(zz, [2, 98]))
            if not (size[0] <= t1 - t0 <= size[1] and size[0] <= z1 - z0 <= size[1]):
                continue
            spread = float(np.percentile(dd, 98) - np.percentile(dd, 2))
            ends = []
            for side, name in ((1, "left"), (-1, "right")):
                depth = side * d - half
                # deslab: not the ceiling (Nagornaya v21: a 34 cm «grille» 20 cm deep on both faces of a seam under it)
                m = (t > t0 - around) & (t < t1 + around) & (z > z0 - 0.25) & (z < min(z1 + around, height - 0.03)) & \
                    (depth > 0.03) & (depth < reach)
                if m.sum() < least:
                    continue
                ends.append({"side": name, "from": round(float(np.percentile(t[m], 2)), 3), "to": round(float(np.percentile(t[m], 98)), 3),
                             "bottom": round(float(np.percentile(z[m], 2)), 3), "top": round(float(np.percentile(z[m], 98)), 3),
                             "depth": round(float(np.percentile(depth[m], 98)), 3)})
            mid = a + u * (t0 + t1) / 2
            found.append({"wallId": wall["id"], "along": round((t0 + t1) / 2, 3), "width": round(t1 - t0, 3),
                          "bottom": round(z0, 3), "top": round(z1, 3),
                          "through": bool(spread >= through * (2 * half - 2 * inset)),
                          "at": {"x": round(float(mid[0]), 4), "y": round(float(mid[1]), 4)}, "ends": ends,
                          "points": int(mine.sum())})
    found.sort(key=lambda p: (p["wallId"], p["along"]))
    for k, p in enumerate(found, 1):
        p["id"] = f"PN{k}"
    return found
