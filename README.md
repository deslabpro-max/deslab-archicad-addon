# deslab Archicad add-on

Fork of [Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) (MIT license, see `LICENSE`)
with commands that deslab needs for drafting in Archicad 27 from Claude.

- Upstream snapshot: ENZYME-APD/tapir-archicad-automation @ `a88df4ce5c3d62b9038fba782b60308681d98a6a` (Tapir 1.7.4).
- It is a drop-in replacement for Tapir: same JSON command namespace, so the `archicad-mcp` server keeps working.
  Disable the original Tapir add-on in Archicad before loading this one, and do not accept Tapir's
  "update to the latest version" prompt, which would replace this build with upstream Tapir.
- The build runs in GitHub Actions (`.github/workflows/build_ac27_win.yml`) on every push.
  Each main build is attached to the `ac27-latest` release, builds of other branches to the `ac27-dev` pre-release;
  no Visual Studio is needed on the drafting PC.

## deslab changes

| Command | Change |
| --- | --- |
| `CreateWindows` | Optional `cornerWindow: true` places the window with the Corner Window tool variation (`APIVarId_CornerWindow`) and that tool's defaults. `favoriteName` / `libraryPartName` are applied to the Corner Window tool defaults too, with `ac_cw_function` on, so a corner pair can be placed as e.g. «Двустворчатое Окно 27». |
| `CreateWindows`, `CreateDoors` with `libraryPartName` | The tool defaults take the parameter block Archicad prepares for a new element of that part (`ACAPI_LibraryPart_OpenParameters`/`GetActParameters`) instead of the raw `ACAPI_LibraryPart_GetParams` list; with the raw list the GDL parameters of an «Окно 27» window could not be read or set afterwards. |
| Create and modify commands (`CreateElementsCommandBase` and the shared helpers in `ExtendedElementCommands.cpp`) | When Archicad refuses the undoable change (a modal dialog is open or another command runs), the command answers with an error instead of an empty element list. |
| `BuildModelFromScan` | New. Builds walls, doors, windows, floor slabs and zones from a point cloud: runs the `deslab-scan2bim` helper (`scan2bim/`, shipped as `deslab-scan2bim-win.zip` in the release, unzipped next to the add-on) on an E57, or takes its scene JSON, then creates the elements with the CreateWalls/CreateDoors/CreateWindows/CreateSlabs/CreateZones logic. Optional favorites, building materials (by name) and layers (by name). Defaults: walls and slab in reinforced concrete (not the brick of the default settings), windows with one sash up to 0.9 m, two up to 1.9 m, three above, opening into the room, set back from the facade (`windowSetback`), with handles and an outer sill, no inner board, frames and sashes in white plastic (`windowSurface`), a balcony door built as a window, one slab to the outer outline of the walls (scene `slabs`). Walls, openings, slabs and zones are created directly on their layers (the tool default layer is switched for the call and restored), so a hidden default layer cannot refuse them. Wall ends that meet (within 5 mm) are made to meet exactly, an L corner in one point and a T joint on the other wall's line, so Archicad joins them into one outline with one hatch. Walls are always square to the axes (a scanned wall that leans a little is placed at its average position, never drawn at an angle). The helper gives walls on one line, end to end, one common back face (no jog at the entrance door, no false step on the outer face). See `scan2bim/README.md`. |
| Analytical Model commands | Left out of the build (`DESLAB_NO_ANALYTICAL`): linking AnalyticalModelAPI.dll is suspected of making Archicad 27.0.0 R1 (5003) refuse the add-on. |

| `CreateAssociativeDimensions` | Changed. A witness point takes an optional `textPosition`: the text of the segment ending at that point is fixed at that offset from the segment middle on the dimension line, as if moved by hand (for short segments whose text Archicad pushes onto the wall; reliable on the last segment of a dimension). Or `textLeader` (beg, mid, end): the text goes on a leader line, as the office template does (set in a second undo step after the dimensions are created, when their witness points have settled, in absolute coordinates; a dimension whose leaders cannot be set, e.g. on a hidden layer, is reported as an error, underlined, text at 0° or 90°, leader in the text pen; the segment is found by position along the chain, because Archicad may reorder the witness points; a chain with leaders pointing left or down is sent reversed, since Archicad turned its texts by 180°; `textLeader` takes optional `pen` and `anchorAtTextEnd`, default true as in the template). The bridge sends leader pen 120 (profile `leaderPen`). A witness point may also take `textAt` (absolute): the text of the segment ending there slides along its line to that middle, set in the same second step, so no crossing line runs through it. `GetDimensionData` also returns each witness point's `note` (fixed position, angle, leader line). |
| `bridge/` (new) | deslab-mcp: a separate MCP server with a few large tools (`analyze_scan`, `build_measured_plan`, …). It drives the add-on from Python with the office rules in `bridge/office-profile.json`; no add-on change. |

Original Tapir documentation: `CLAUDE.tapir.md`, `archicad-addon/README.md`.
