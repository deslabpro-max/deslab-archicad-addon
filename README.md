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
| `BuildModelFromScan` | New. Builds walls, doors, windows, floor slabs and zones from a point cloud: runs the `deslab-scan2bim` helper (`scan2bim/`, shipped as `deslab-scan2bim-win.zip` in the release, unzipped next to the add-on) on an E57, or takes its scene JSON, then creates the elements with the CreateWalls/CreateDoors/CreateWindows/CreateSlabs/CreateZones logic. Optional favorites, building materials (by name) and layers (by name). Defaults: walls and slab in reinforced concrete (not the brick of the default settings), windows with one sash up to 0.9 m, two up to 1.9 m, three above, opening into the room, set back from the facade (`windowSetback`), with handles, outer sill and inner board, frames and sashes in white plastic (`windowSurface`), a balcony door built as a window, one slab to the outer outline of the walls (scene `slabs`). See `scan2bim/README.md`. |
| Analytical Model commands | Left out of the build (`DESLAB_NO_ANALYTICAL`): linking AnalyticalModelAPI.dll is suspected of making Archicad 27.0.0 R1 (5003) refuse the add-on. |

| `bridge/` (new) | deslab-mcp: a separate MCP server with a few large tools (`analyze_scan`, `build_measured_plan`, …). It drives the add-on from Python with the office rules in `bridge/office-profile.json`; no add-on change. |

Original Tapir documentation: `CLAUDE.tapir.md`, `archicad-addon/README.md`.
