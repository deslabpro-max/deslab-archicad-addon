"""Command line: deslab-scan2bim <cloud.e57> -o <scene.json> [--png preview.png]."""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import __version__
from .analyze import Options, analyze
from .e57 import read_points


def _dump(pts, prefix, part=2_000_000):
    import numpy as np
    pts = np.asarray(pts, dtype=np.float64)
    origin = np.floor(pts.min(axis=0)) if len(pts) else np.zeros(3)
    cm = np.clip(np.round((pts - origin) * 100.0), -32768, 32767).astype(np.int16)
    for i in range(0, max(len(cm), 1), part):
        np.savez_compressed(f"{prefix}.{i // part}.npz", xyz=cm[i:i + part], origin=origin)


def _voxels(pts, path, size=0.01):
    """deslab: the cloud thinned to one point per `size` cube, as int16 steps of `size` from `origin`: a file of a few MB
    that shows pipes and joints, for debugging the helper away from the scan."""
    import numpy as np
    pts = np.asarray(pts, dtype=np.float64)
    origin = np.floor(pts.min(axis=0))
    cells = np.unique(np.round((pts - origin) / size).astype(np.int16), axis=0)
    np.savez_compressed(path, xyz=cells, origin=origin, size=size)
    return len(cells)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="deslab-scan2bim", description=__doc__)
    ap.add_argument("e57")
    ap.add_argument("-o", "--output", required=True, help="scene JSON to write")
    ap.add_argument("--png", help="optional plan preview image")
    ap.add_argument("--step", type=int, default=Options.step, help="keep every n-th point")
    ap.add_argument("--exterior-thickness", type=float, default=Options.exterior_thickness)
    ap.add_argument("--default-thickness", type=float, default=Options.default_thickness)
    ap.add_argument("--dump", help="also write the read points as <DUMP>.N.npz parts "
                    "(centimetres, int16, up to 2M points each) for offline debugging")
    ap.add_argument("--voxels", help="deslab: write the whole cloud thinned to 1 cm cubes into this .npz and stop")
    ap.add_argument("--views", help="deslab: folder for the pictures of the cloud for the reviewing model "
                    "(wall elevations, plan cuts) and views.json")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)

    opt = Options(step=args.step, exterior_thickness=args.exterior_thickness,
                  default_thickness=args.default_thickness)
    t0 = time.time()
    if args.voxels:
        print(_voxels(read_points(args.e57, step=1), args.voxels), "voxels")
        return 0
    try:
        pts = read_points(args.e57, step=opt.step)
        if args.dump:
            _dump(pts, args.dump)
        scene = analyze(pts, opt, source=args.e57)
    except Exception as exc:  # the add-on shows this text to the user
        json.dump({"format": "deslab.scan2bim/1", "error": str(exc)},
                  open(args.output, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # deslab: 13.11.2026, what the walls alone do not tell: the threshold of a balcony door and the things that stand at
    # the walls (candidates for radiators, risers, boxes), measured in the cloud; the pictures are for the model
    try:
        from .views import find_candidates, mark_empty_doorways, measure_thresholds, model_points, write_views
        model = model_points(pts, scene)
        scene["assumptions"] += measure_thresholds(model, scene)
        scene["assumptions"] += mark_empty_doorways(model, scene)
        scene["candidates"] = find_candidates(model, scene)
        if args.views:
            scene["views"] = [v["file"] for v in write_views(pts, scene, args.views)]
    except Exception as exc:  # the walls are still good without it
        scene["warnings"].append(f"cloud views and candidates failed: {exc}")
    scene["source"]["seconds"] = round(time.time() - t0, 1)
    scene["source"]["version"] = __version__
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(scene, f, ensure_ascii=False, indent=1)
    if args.png:
        from .render import render
        render(scene, args.png)
    print(f"{len(scene['walls'])} walls, "
          f"{sum(len(w['openings']) for w in scene['walls'])} openings, "
          f"{len(scene['rooms'])} rooms in {scene['source']['seconds']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
