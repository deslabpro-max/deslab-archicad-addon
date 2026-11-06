# deslab-scan2bim

Point cloud (E57) of an apartment → scene JSON with walls, openings and rooms, for the
`BuildModelFromScan` command of the deslab add-on. Deterministic, numpy only, no AI model:
the scene is the interface a model can review or correct later (e.g. "this wall is a 80 mm
partition", "room 3 is a bathroom") before the add-on builds it.

```
deslab-scan2bim cloud.e57 -o cloud.e57.scene.json [--png plan.png] [--step 4] [--dump points]
               [--exterior-thickness 0.64] [--default-thickness 0.38]
```

The Windows build (`deslab-scan2bim.exe`, PyInstaller, no Python needed) comes out of
`.github/workflows/build_ac27_win.yml` as `deslab-scan2bim-win.zip` in the release. Unzip it next
to the add-on file so that the add-on finds `deslab-scan2bim\deslab-scan2bim.exe`.

Tests: `python scan2bim/tests/test_fit.py` (JK bottom wall), `python scan2bim/tests/test_clutter.py` (a stack and a board are no wall, tracing found), `python scan2bim/tests/test_synthetic.py` (two rooms, a partition with a door, a window
in a niche, rotated 7°).

## How it works

1. `e57.py` reads the points of every scan (bitpack codec, cartesian or spherical, scan pose
   applied). 22 million points read in about 5 s; every 4th point is kept by default.
2. Floor and ceiling: the lowest and highest dense horizontal surfaces (Z histogram).
3. Orientation: the rotation that makes the wall points sharpest along X and Y. The scene is
   written in that frame, so the walls run along the axes; a single wall's small deviation is
   averaged out (step 4), never drawn.
4. Wall faces: 2 cm cells scanned over at least 55 % of the room height; runs of such cells along
   X and Y, placed at the average position of the raw points (least-squares at the middle). A face
   is always straight along its axis: we never draw at an angle, every corner is 90° (Игорь, 04.11.2026); so is a room outline: an edge within 15° of an axis becomes a small step plus a square run. The room side of a face is the side
   with floor hits right next to it.
5. Walls: faces that look at each other 5–900 mm apart are one wall of the measured thickness.
   A face seen from one side only becomes a wall of default thickness behind it (640 mm when it
   has a window, else 380 mm, or the measured thickness of the same wall where part of it was seen
   from both sides). Door and window reveals and frames inside a niche are not walls, nor are
   thin plates seen from both sides (an open door leaf, a board). A facade face that stops at an
   opening next to a corner runs on to the corner wall when a lintel spans the gap.
6. Openings: a gap in a face with a lintel above it. Along the gap, in 5 cm steps, the lowest
   material behind the face decides: the floor (doorway, door leaf, balcony door) or a sill board
   (window, sill height). A balcony block comes out as a door and a window. A glass plane at
   least 0.2 m deep in the opening down to the floor (French window, glazed facade) makes it a
   `balconyDoor`: a window without sill boards. A recess up to the ceiling (lintel under
   0.3 m) with the other face of the wall whole behind it is a niche, not a doorway: no
   opening, and the wall stays closed there.
7. No jogs: a wall continuing a longer parallel wall end to end, off its line by less than half
   the thickness, goes onto that line, unless their scanned room faces are 0.1 m or more apart (a real
   step: the JK shaft box at the entrance door went onto the corridor wall's line and into the doorway) (a one-sided wall takes the measured thickness). A step of
   0.3 m or more on the room side (a shaft box, a pier) stays: the one-sided piece keeps its face
   and takes the back face of the measured wall, so the back stays straight. Short
   connectors (under 0.25 m) are dropped. A one-sided wall lying on another wall is dropped and
   its openings move to that wall.
7a. One straight back face: two walls on one line, end to end (also across a crossing wall),
   keep their scanned room faces, small real steps included, but share one back face. A one-sided
   wall takes the back face of a measured wall, else of the longer one, with its slope (a slightly
   skewed scan wall becomes parallel to it), and its thickness follows;
   the joints are then redone (no jog at the entrance door, no step on the outer face).
8. Walls are always closed: wall ends are moved onto the centre lines of the walls they meet.
   An L corner is closed when both ends are within 1.3 m of it, a free end runs on to a crossing
   wall up to 0.8 m away (T joint), collinear pieces up to 0.8 m apart are joined, and a piece
   shorter than 0.6 m that joins nothing is dropped as noise.
9. Rooms: scanned floor/ceiling area grown by 0.4 m and cut by the walls (openings closed), holes
   filled, 2 cm raster, outline traced, simplified, squared and snapped to the wall faces, so the
   zone follows the walls and not the furniture shadows. Each room has its own floor level and height.
   A space whose floor is 8 cm or more off the main room's floor (a finished common corridor or
   landing seen through the entrance door) is outside the flat: it is left out with the walls
   that bound only it, and the scene says so in `assumptions`. A flat wall that runs on along
   that space is cut back to the last wall it meets, so it leaves no free tail.
10. Floor slab: one slab under the rooms and the walls, to the outer wall faces (mitred corners, never
    past a wall), top at the main floor.
11. Interior doors get one height (median of the measured 1.95–2.45 m heads); a door in a wall with
    a window is a balcony door (`balconyDoor`) and keeps its own head.
12. Room outlines and walls agree (`fit.py`, Игорь 04.11.2026, run JK): an outline edge off its axis
    by less than 12 mm is made square; where the outline steps between two wall faces, the walls
    end exactly on the step (a wall that runs on outside the room to a T joint stays); a small
    corner lump (up to 0.2 m) that no wall face carries is filled and noted in `assumptions`; a wall
    that runs on inside a parallel wall at least as thick stops at that wall's end.

13. Clutter is no wall (Игорь 05.11.2026, run JK: a stack of tiles 0.65 m high with a board leaning
    on it made a wall into the entrance door): a wall-face cell must be scanned over 40 % of the
    band from 1.5 m to 0.3 m under the ceiling as well, and a wall whose faces have points low
    down but nothing above 1.2 m over most of their length is dropped (noted in `assumptions`).
14. Tracing (the first row of blocks of new walls): 2 cm cells with points 0.04–0.6 m over the floor
    and nothing at 0.65–2.0 m, away from the walls; runs 6–26 cm across that line up over at least
    0.3 m, standing on the floor (a radiator hangs above it), 0.12–0.6 m high, up to 0.2 m wide; a strip
    runs on along its own cells where clutter beside it makes the rows too wide. Width and position
    from the points. An end runs on to a wall face up to 0.25 m off and onto the axis of a square
    strip it meets (L and T joints). Written as `tracing` (the bridge builds low walls).

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
    "openings": [{"id": "W1.1", "kind": "door | window | balconyDoor", "centerOffset": 1.2,
                  "width": 0.8, "sill": 0.0, "height": 2.1}]
  }],
  "tracing": [{"id": "T1", "start": {"x": 4.67, "y": 5.2}, "end": {"x": 4.67, "y": 9.34},
               "thickness": 0.08, "height": 0.25}],
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
