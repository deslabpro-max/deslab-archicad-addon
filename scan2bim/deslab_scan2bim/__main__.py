"""Command line: deslab-scan2bim <cloud.e57> -o <scene.json> [--png preview.png]."""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import __version__
from .analyze import Options, analyze
from .e57 import read_points


def main(argv=None):
    ap = argparse.ArgumentParser(prog="deslab-scan2bim", description=__doc__)
    ap.add_argument("e57")
    ap.add_argument("-o", "--output", required=True, help="scene JSON to write")
    ap.add_argument("--png", help="optional plan preview image")
    ap.add_argument("--step", type=int, default=Options.step, help="keep every n-th point")
    ap.add_argument("--exterior-thickness", type=float, default=Options.exterior_thickness)
    ap.add_argument("--default-thickness", type=float, default=Options.default_thickness)
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)

    opt = Options(step=args.step, exterior_thickness=args.exterior_thickness,
                  default_thickness=args.default_thickness)
    t0 = time.time()
    try:
        pts = read_points(args.e57, step=opt.step)
        scene = analyze(pts, opt, source=args.e57)
    except Exception as exc:  # the add-on shows this text to the user
        json.dump({"format": "deslab.scan2bim/1", "error": str(exc)},
                  open(args.output, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"error: {exc}", file=sys.stderr)
        return 1
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
