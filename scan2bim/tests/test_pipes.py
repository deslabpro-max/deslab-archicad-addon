"""deslab: pipes in the cloud: a heating riser in the corner by the window, its run along the outer wall to the
radiator, a sewer riser. Run: python scan2bim/tests/test_pipes.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_synthetic import H, cloud, plane_x, plane_y  # noqa: E402
from deslab_scan2bim.analyze import Options, analyze  # noqa: E402
from deslab_scan2bim.pipes import CELL, _merge_arcs, find_pipes, find_runs  # noqa: E402
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


def arc_pieces(c, d, step=0.004, apart=False):
    """deslab: a riser seen over 140 degrees in three pieces with gaps of 30 degrees (2.8 cm) between them."""
    out = []
    for a0, a1 in ((-70, -45), (-15, 15), (45, 70)):
        angles = np.radians(np.arange(a0, a1, math.degrees(step / (d / 2)))) + math.pi
        along = np.arange(0, H, step)
        a, z = np.meshgrid(angles, along)
        out.append(np.stack([c[0] + d / 2 * np.cos(a.ravel()), c[1] + d / 2 * np.sin(a.ravel()), z.ravel()], 1))
    return out if apart else np.concatenate(out)


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
             cylinder((4.25, 4.85), "z", 0, H, 0.110, -math.pi / 4), cylinder((8.2, 4.0), "z", 0, H, 0.05, math.pi),
             # deslab: the edge-on face of a duct: a flat strip 16 cm wide, floor to ceiling, is no riser
             plane_y(3.5, 5.0, 5.16, 0, H),
             # deslab: a sewer riser 108 mm seen over a narrow arc only, scanned in three pieces with gaps
             arc_pieces((5.6, 1.0), 0.108)]
    pts = cloud(extra)
    scene = analyze(pts, Options(step=1))
    model = model_points(pts, scene)
    pipes = find_pipes(model, scene)
    risers = [p for p in pipes if p["kind"] == "riser"]
    runs = [p for p in pipes if p["kind"] == "run"]
    # the model frame is the flat's, shifted: the first heating riser stands at (7.00, 0.30)
    first = min(risers, key=lambda p: (p["diameter"] > 0.04, p["path"][0]["y"]))
    dx, dy = first["path"][0]["x"] - 7.00, first["path"][0]["y"] - 0.30
    big = [p for p in risers if math.dist((p["path"][0]["x"] - dx, p["path"][0]["y"] - dy), (5.6, 1.0)) < 0.1]
    assert len(big) == 1 and abs(big[0]["diameter"] - 0.108) < 0.012, (big, risers)
    risers = [p for p in risers if p not in big]
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


def tube(a, b, d, step=0.004):
    """A whole pipe from point a to point b (3-D), scanned all round."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    axis = b - a
    length = float(np.linalg.norm(axis))
    axis /= length
    u = np.cross(axis, [0, 0, 1] if abs(axis[2]) < 0.9 else [1, 0, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    ang, along = np.meshgrid(np.arange(0, 2 * math.pi, step / (d / 2)), np.arange(0, length, step))
    ang, along = ang.ravel()[:, None], along.ravel()[:, None]
    return a + along * axis + d / 2 * (np.cos(ang) * u + np.sin(ang) * v)


def test_branches():
    # deslab: 14.11.2026, Игорь: «там ещё есть отводы… было бы здорово фиксировать». A water riser d 32 mm 15 cm off the
    # north wall of room A (y 5) with two branches d 20 mm: 30 cm along the wall at z 1.0, 45 cm into the room at z 1.4;
    # a shelf 2 cm thick at z 1.7 beside the riser and a hose 10 mm are no branches
    c = (1.0, 4.85)
    extra = [tube((c[0], c[1], 0.0), (c[0], c[1], H), 0.032),
             tube((c[0] + 0.016, c[1], 1.0), (c[0] + 0.33, c[1], 1.0), 0.02),
             tube((c[0], c[1] - 0.016, 1.4), (c[0], c[1] - 0.47, 1.4), 0.02)]
    shelf = [np.stack([xs.ravel(), ys.ravel(), np.full(xs.size, z)], 1)
             for z in (1.7, 1.72) for xs, ys in [np.mgrid[0.6:0.98:0.008, 4.6:5.0:0.008]]]
    hose = tube((c[0] - 0.016, c[1], 0.6), (c[0] - 0.4, c[1], 0.6), 0.01)
    pts = cloud(extra + shelf + [hose])
    scene = analyze(pts, Options(step=1))
    pipes = find_pipes(model_points(pts, scene), scene)
    riser = [p for p in pipes if p["kind"] == "riser"]
    assert len(riser) == 1 and abs(riser[0]["diameter"] - 0.032) < 0.008, riser
    branches = sorted((p for p in pipes if p.get("branch")), key=lambda p: p["path"][0]["z"])
    zs = [round(p["path"][0]["z"], 2) for p in branches]
    lengths = [round(math.dist((p["path"][0]["x"], p["path"][0]["y"]), (p["path"][1]["x"], p["path"][1]["y"])), 2) for p in branches]
    # the hose is found too (the helper measures, the bridge drops what is thinner than any pipe by GOST)
    assert [z for z, p in zip(zs, branches) if p["diameter"] >= 0.016] == [1.0, 1.4], (zs, branches)
    big = [(l, p) for l, p in zip(lengths, branches) if p["diameter"] >= 0.016]
    assert abs(big[0][0] - 0.33) < 0.04 and abs(big[1][0] - 0.47) < 0.04, big
    assert all(0.014 <= p["diameter"] <= 0.03 and p["path"][0]["joins"] == riser[0]["id"] for _, p in big), big
    # the branch into the room leaves the riser at a right angle to the one along the wall
    (_, a), (_, b) = big
    va = (a["path"][1]["x"] - a["path"][0]["x"], a["path"][1]["y"] - a["path"][0]["y"])
    vb = (b["path"][1]["x"] - b["path"][0]["x"], b["path"][1]["y"] - b["path"][0]["y"])
    cos = (va[0] * vb[0] + va[1] * vb[1]) / (math.hypot(*va) * math.hypot(*vb))
    assert abs(cos) < 0.1, cos
    return branches


def test_runs_on_cubes():
    # deslab: 14.11.2026, Nagornaya v17 on: on the 1 cm cubes of every point the wall's skin (0-2.5 cm off its face) is
    # filled at every height and the stray points under each pipe tie the pipe to it: both pipes from the second radiator
    # to the corner were lost. Two pipes 30 mm, 12 cm off the wall at z 0.33 and 0.57, 1.1 m long
    rng = np.random.default_rng(5)
    scene = {"levels": {"ceiling": 2.7}, "rooms": [],
             "walls": [{"id": "W1", "start": {"x": 0, "y": 0}, "end": {"x": 0, "y": 3}, "thickness": 0.2, "openings": []}]}

    def at(depth, y, z):   # the left face of W1 is x -0.1, the room lies at x < -0.1
        return np.stack([-(0.1 + depth), y, z], 1)
    n = 400000
    out = [at(rng.uniform(-0.005, 0.025, n), rng.uniform(0, 3, n), rng.uniform(0, 2.7, n))]
    for zc in (0.33, 0.57):
        a = rng.uniform(math.pi / 2, 3 * math.pi / 2, 60000)
        out.append(at(0.12 - 0.015 * np.cos(a), rng.uniform(1.0, 2.1, 60000), zc + 0.015 * np.sin(a)))
        out.append(at(rng.uniform(0.0, 0.12, 30000), rng.uniform(1.0, 2.1, 30000), zc - 0.015 - rng.uniform(0.0, 0.012, 30000)))
    cubes = np.unique(np.round(np.concatenate(out) / 0.01), axis=0) * 0.01
    runs = sorted(find_runs(cubes, scene), key=lambda r: r["path"][0]["z"])
    assert len(runs) == 2 and all(abs(r["path"][0]["z"] - z) <= 0.015 for r, z in zip(runs, (0.33, 0.57))), runs
    for r in runs:
        # the axis 12 cm off the face (x -0.22), not at the stray points behind the pipe
        assert abs(r["path"][0]["x"] + 0.22) <= 0.015, r
        ys = sorted(e["y"] for e in r["path"])
        assert abs(ys[0] - 1.0) < 0.05 and abs(ys[1] - 2.1) < 0.05 and 0.016 <= r["diameter"] <= 0.05, r
    return runs


def test_runs_from_radiator():
    # deslab: 14.11.2026, Nagornaya v20: the wall's skin goes on 1 cm past the cut (at 2-3 cm off the face) over the
    # stretch between the second radiator and the corner, above and below its pipes: it read as the radiator and cut
    # the pipes to 40 cm of their 1.1 m. A panel radiator at y 0.4-1.0 (front 16 cm off the wall, z 0.2-0.7), its two
    # pipes 20 mm 12 cm off the wall at z 0.33 and 0.57 run on from under it to y 2.1; the skin's tail at 2.5-3.5 cm
    # covers two thirds of the heights over y 0-2.8 (on past the pipes), none further
    rng = np.random.default_rng(7)
    scene = {"levels": {"ceiling": 2.7}, "rooms": [],
             "walls": [{"id": "W1", "start": {"x": 0, "y": 0}, "end": {"x": 0, "y": 3}, "thickness": 0.2, "openings": []}]}

    def at(depth, y, z):
        return np.stack([-(0.1 + depth), y, z], 1)
    n = 400000
    out = [at(rng.uniform(-0.005, 0.025, n), rng.uniform(0, 3, n), rng.uniform(0, 2.7, n))]
    tail = rng.uniform(0, 2.8, n // 2), rng.uniform(0, 2.7, n // 2)
    keep = (tail[1] % 0.3) < 0.2
    out.append(at(rng.uniform(0.025, 0.035, keep.sum()), tail[0][keep], tail[1][keep]))
    # patches of the skin 4 cm high above and below the pipes every 10 cm over y 1.4-2.0, past its tail (4.5-5.5 cm off
    # the face, too little of the height to be the skin; Nagornaya v21: at 2-4 cm)
    for z0 in (0.38, 0.48):
        y = 1.4 + 0.1 * rng.integers(0, 6, 30000) + rng.uniform(0.005, 0.04, 30000)
        out.append(at(rng.uniform(0.045, 0.055, 30000), y, rng.uniform(z0, z0 + 0.04, 30000)))
    m = 80000
    out.append(at(np.full(m, 0.16), rng.uniform(0.4, 1.0, m), rng.uniform(0.2, 0.7, m)))       # the front
    out.append(at(rng.uniform(0.06, 0.16, m // 4), rng.uniform(0.4, 1.0, m // 4), np.full(m // 4, 0.7)))   # the top
    for zc in (0.33, 0.57):
        a = rng.uniform(math.pi / 2, 3 * math.pi / 2, 40000)
        out.append(at(0.12 - 0.01 * np.cos(a), rng.uniform(1.0, 2.1, 40000), zc + 0.01 * np.sin(a)))
        out.append(at(rng.uniform(0.0, 0.12, 20000), rng.uniform(1.0, 2.1, 20000), zc - 0.01 - rng.uniform(0.0, 0.012, 20000)))
    cubes = np.unique(np.round(np.concatenate(out) / 0.01), axis=0) * 0.01
    runs = sorted(find_runs(cubes, scene), key=lambda r: r["path"][0]["z"])
    assert len(runs) == 2 and all(abs(r["path"][0]["z"] - z) <= 0.015 for r, z in zip(runs, (0.33, 0.57))), runs
    for r in runs:
        ys = sorted(e["y"] for e in r["path"])
        # from the radiator's side (y 1.0) to the end, not into the radiator and not on along the skin
        assert abs(ys[0] - 1.0) < 0.08 and abs(ys[1] - 2.1) < 0.05, r
    return runs


def test_merge_arcs():
    # deslab: three pieces of one riser 108 mm are one spot; two thin pipes 3 cm apart stay two
    def spots_of(pieces, p):
        x0, y0 = p[:, 0].min(), p[:, 1].min()
        out = []
        for q in pieces:
            cells = np.unique(np.stack([((q[:, 1] - y0) / CELL).astype(int), ((q[:, 0] - x0) / CELL).astype(int)], 1), axis=0)
            out.append((cells[:, 0], cells[:, 1]))
        return out, x0, y0
    pieces = arc_pieces((0.0, 0.0), 0.108, apart=True)
    p = np.concatenate(pieces)
    spots, x0, y0 = spots_of(pieces, p)
    assert len(_merge_arcs(spots, p, x0, y0, 0.22)) == 1
    pair = [cylinder((0.0, 0.0), "z", 0, H, 0.02, math.pi), cylinder((0.0, 0.05), "z", 0, H, 0.02, math.pi)]
    p = np.concatenate(pair)
    spots, x0, y0 = spots_of(pair, p)
    assert len(_merge_arcs(spots, p, x0, y0, 0.22)) == 2


if __name__ == "__main__":
    test_merge_arcs()
    for p in test_pipes():
        print(p["id"], p["kind"], p["diameter"], p["path"], p["hint"])
    for p in test_runs_on_cubes():
        print(p["diameter"], p["path"])
    for p in test_runs_from_radiator():
        print(p["diameter"], p["path"])
    for p in test_branches():
        print(p["id"], p["diameter"], p["path"], p["hint"])
    print("ok")
