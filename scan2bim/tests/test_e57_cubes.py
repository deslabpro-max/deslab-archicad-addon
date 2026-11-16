"""deslab: 14.11.2026, the E57 reader's 1 cm cubes of every point next to the every-n-th points (python scan2bim/tests/test_e57_cubes.py)."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deslab_scan2bim import e57  # noqa: E402
from deslab_scan2bim.__main__ import _cells  # noqa: E402


def test_cubes_next_to_points():
    rng = np.random.default_rng(3)
    scans = []
    for n in (1003, 998):  # scan sizes that are no multiple of the step
        xyz = rng.random((n, 3)) * [2.0, 3.0, 2.7] + [5.0, -1.0, 0.0]
        bad = (rng.random(n) < 0.1).astype(np.int8)
        scans.append((xyz, bad))

    def fake_scans():
        out = []
        for i in range(len(scans)):
            scan = ET.Element("scan", {"i": str(i)})
            points = ET.SubElement(scan, e57.NS + "points")
            ET.SubElement(points, e57.NS + "prototype")
            out.append(scan)
        return out

    def fake_read(_file, points_node, _fields, wanted, step):
        i = int(next(s for s in fake_scans_list if s.find(e57.NS + "points") is points_node).get("i"))
        xyz, bad = scans[i]
        cols = {"cartesianX": xyz[::step, 0], "cartesianY": xyz[::step, 1], "cartesianZ": xyz[::step, 2],
                "cartesianInvalidState": bad[::step]}
        return {k: v for k, v in cols.items() if k in wanted}

    fake_scans_list = fake_scans()
    saved = (e57.E57File, e57._fields, e57._read_compressed_vector)
    e57.E57File = lambda path: SimpleNamespace(scans=lambda: fake_scans_list, close=lambda: None)
    e57._fields = lambda proto: [SimpleNamespace(name=k) for k in ("cartesianX", "cartesianY", "cartesianZ", "cartesianInvalidState")]
    e57._read_compressed_vector = fake_read
    try:
        plain = e57.read_points("x.e57", step=4)
        pts, cubes = e57.read_points("x.e57", step=4, cube=0.01)
    finally:
        e57.E57File, e57._fields, e57._read_compressed_vector = saved
    # the points are the ones an every-4th read gives
    assert pts.shape == plain.shape and np.array_equal(pts, plain), (pts.shape, plain.shape)
    # the cubes are those of every valid point, as the --voxels file makes them
    every = np.concatenate([xyz[bad == 0] for xyz, bad in scans]).astype(np.float32)
    cells, origin = _cells(every, 0.01)
    expect = cells.astype(np.float64) * 0.01 + origin
    got = cubes[np.lexsort(cubes.T[::-1])]
    expect = expect[np.lexsort(expect.T[::-1])]
    assert got.shape == expect.shape and np.allclose(got, expect, atol=1e-6), (got.shape, expect.shape)
    assert len(cubes) > 4 * len(pts) * 0.9


if __name__ == "__main__":
    test_cubes_next_to_points()
    print("ok")
