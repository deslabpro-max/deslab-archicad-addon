# deslab Archicad add-on

Fork of [Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) (MIT license, see `LICENSE`)
with commands that deslab needs for drafting in Archicad 27 from Claude.

- Upstream snapshot: ENZYME-APD/tapir-archicad-automation @ `a88df4ce5c3d62b9038fba782b60308681d98a6a` (Tapir 1.7.4).
- It is a drop-in replacement for Tapir: same JSON command namespace, so the `archicad-mcp` server keeps working.
  Disable the original Tapir add-on in Archicad before loading this one, and do not accept Tapir's
  "update to the latest version" prompt, which would replace this build with upstream Tapir.
- The build runs in GitHub Actions (`.github/workflows/build_ac27_win.yml`) on every push.
  Each main build is attached to the `ac27-latest` release; no Visual Studio is needed on the drafting PC.

## deslab changes

| Command | Change |
| --- | --- |
| `CreateWindows` | Optional `cornerWindow: true` places the window with the Corner Window tool variation (`APIVarId_CornerWindow`) and that tool's defaults. |
| Analytical Model commands | Left out of the build (`DESLAB_NO_ANALYTICAL`): linking AnalyticalModelAPI.dll is suspected of making Archicad 27.0.0 R1 (5003) refuse the add-on. |

Original Tapir documentation: `CLAUDE.tapir.md`, `archicad-addon/README.md`.
