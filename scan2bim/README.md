# deslab-scan2bim

Point cloud (E57) of an apartment → scene JSON with walls, openings and rooms, for the
`BuildModelFromScan` command of the deslab add-on. Deterministic, numpy only, no AI model:
the scene is the interface a model can review or correct later (e.g. "this wall is a 80 mm
partition", "room 3 is a bathroom") before the add-on builds it.

```
deslab-scan2bim cloud.e57 -o cloud.e57.scene.json [--png plan.png] [--step 4]
               [--exterior-thickness 0.64] [--default-thickness 0.38]
```

The Windows build (`deslab-scan2bim.exe`, PyInstaller, no Python needed) comes out of
`.github/workflows/build_ac27_win.yml` as `deslab-scan2bim-win.zip` in the release. Unzip it next
to the add-on file so that the add-on finds `deslab-scan2bim\deslab-scan2bim.exe`.

Tests: `python scan2bim/tests/test_synthetic.py` (two rooms, a partition with a door, a window
in a niche, rotated 7°).

## How it works

1. `e57.py` reads the points of every scan (bitpack codec, cartesian or spherical, scan pose
   applied). 22 million points read in about 5 s; every 4th point is kept by default.
2. Floor and ceiling: the lowest and highest dense horizontal surfaces (Z histogram).
3. Orientation: the rotation that makes the wall points sharpest along X and Y. The scene is
   written in that frame, so the main walls run along the axes; small deviations of single walls
   (0.2–0.5° are normal) are kept.
4. Wall faces: 2 cm cells scanned over at least 55 % of the room height; runs of such cells along
   X and Y, refined to a least-squares line on the raw points. The room side of a face is the side
   with floor hits right next to it.
5. Walls: faces that look at each other 5–900 mm apart are one wall of the measured thickness.
   A face seen from one side only becomes a wall of default thickness behind it (640 mm when it
   has a window, else 380 mm, or the measured thickness of the same wall where part of it was seen
   from both sides). Door and window reveals and frames inside a niche are not walls.
6. Openings: a gap in a face with a lintel above it. Along the gap, in 5 cm steps, the lowest
   material behind the face decides: the floor (doorway, door leaf, balcony door) or a sill board
   (window, sill height). A balcony block comes out as a door and a window.
7. Walls are always closed: wall ends are moved onto the centre lines of the walls they meet.
   An L corner is closed when both ends are within 1.3 m of it, a free end runs on to a crossing
   wall up to 0.8 m away (T joint), collinear pieces up to 0.8 m apart are joined, and a piece
   shorter than 0.6 m that joins nothing is dropped as noise.
8. Rooms: scanned floor/ceiling area cut by the walls (openings closed), 2 cm raster, outline
   traced, simplified and snapped to the wall faces. Each room has its own floor level and height.
9. Floor slab: one slab under the rooms and the walls, to the outer wall faces, top at the main floor.
10. Interior doors get one height (median of the measured 1.95–2.45 m heads); a door in a wall with
    a window (balcony block) keeps its own head.

## Scene format `deslab.scan2bim/1`

Metres, model frame (see `transform`). Floor of the largest room at z = 0.

```json
{
  "format": "deslab.scan2bim/1",
  "source": {"file": "...", "points": 5567611, "step": 4, "seconds": 9.3, "version": "0.1.0"},
  "transform": {"rotationDeg": 0.45, "offset": [-5.9, 0.4, -1.446]},
  "levels": {"floor": 0.0, "ceiling": 3.319},
  "walls": [{
    "id": "W1", "start": {"x": 1.16, "y": 7.85}, "end": {"x": 3.43, "y": 7.85},
    "thickness": 0.281, "height": 3.319, "bottom": 0.0,
    "measuredThickness": true, "kind": "interior | exterior | unknown",
    "openings": [{"id": "W1.1", "kind": "door | window", "centerOffset": 1.2,
                  "width": 0.8, "sill": 0.0, "height": 2.1}]
  }],
  "rooms": [{"id": "R1", "number": 1, "name": "Помещение 1",
             "polygon": [{"x": 0, "y": 0}], "floor": 0.0, "ceiling": 3.31,
             "height": 3.31, "area": 36.18}],
  "slabs": [{"id": "S1", "polygon": [{"x": 0, "y": 0}], "floor": 0.0}],
  "assumptions": ["W5: only one side scanned, thickness 380 mm by default"],
  "warnings": []
}
```

- `start` → `end`: for a wall seen from one side, the scanned room is on the left.
- `centerOffset`: distance of the opening centre from `start` (Archicad's window/door `objLoc`).
- `sill`: above the room floor; `height`: opening height.
- On failure the file holds `{"format": ..., "error": "message"}`.

Known limits of this first version: walls along two main directions only, no curved walls,
door leaves are not told apart from leafless openings, rooms the scanner saw only partly get a
partial outline, furniture tall enough to look like a wall is not filtered.
