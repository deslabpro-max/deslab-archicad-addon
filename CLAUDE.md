# CLAUDE.md

Fork of Tapir for deslab (see README.md). Upstream guidance is in CLAUDE.tapir.md; its CI, release and bot
workflows were not copied. Only `.github/workflows/build_ac27_win.yml` builds the add-on (Archicad 27, Windows).

- Keep the Tapir command namespace and existing command behaviour, so the archicad-mcp server stays compatible.
- Mark deslab changes in code with a `deslab:` comment and list each one in README.md.
