"""deslab: one-command rebuild of a test measured plan, without Claude (python -m deslab_bridge.rebuild kv94).

Игорь 06.11.2026: each fix cost a long Claude session on the PC. Now the PC only runs this:
the previous build of the preset is deleted (its guids are kept in a state file), the scene is
built again at the same place, and a short report is printed and saved. Presets live in
rebuild-presets.json next to office-profile.json; paths in them are relative to the «Архикад» folder."""
import argparse
import json
import os
import sys
from pathlib import Path

from . import obmer
from .archicad import Archicad, ArchicadError

PRESETS = Path(__file__).resolve().parent.parent / "rebuild-presets.json"


def _root(arg=None):
    if arg:
        return Path(arg)
    return Path(os.environ.get("DESLAB_ROOT") or Path.home() / "Desktop" / "Архикад")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _all_guids(ac):
    return {e["elementId"]["guid"] for e in ac.api("API.GetAllElements").get("elements", []) if "elementId" in e}


def load_scene(preset, root):
    scene = _load(root / preset["scene"])
    if preset.get("mep"):
        mep_path = Path(preset["mep"])
        if not mep_path.is_absolute():
            # a repo-relative path (bridge/tests/…) or one under the «Архикад» folder
            repo = Path(__file__).resolve().parent.parent.parent
            mep_path = repo / mep_path if (repo / mep_path).exists() else root / mep_path
        scene["mep"] = _load(mep_path)
    return scene


def delete_previous(ac, state_path):
    if not state_path.exists():
        return 0
    guids = _load(state_path).get("guids", [])
    if not guids:
        return 0
    live = _all_guids(ac)
    guids = [g for g in guids if g in live]
    if guids:
        # deleting on a hidden or locked layer is refused: open all layers for the delete
        closed = obmer.open_layers(ac, None, obmer.Report())
        try:
            ac.tapir("DeleteElements", {"elements": [{"elementId": {"guid": g}} for g in guids]})
        finally:
            obmer.restore_layers(ac, closed)
    return len(guids)


def rebuild(name, root=None, keep=False):
    root = _root(root)
    presets = _load(PRESETS)
    if name not in presets:
        raise SystemExit(f"Нет пресета «{name}». Есть: {', '.join(presets)}")
    preset = presets[name]
    out_dir = root / "captures"
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / f"rebuild-{name}.json"

    ac = Archicad()
    if not ac.alive():
        raise SystemExit("Archicad не отвечает: откройте тест-угол.pln и закройте диалоги.")
    scene = load_scene(preset, root)
    deleted = 0 if keep else delete_previous(ac, state_path)
    before = _all_guids(ac)
    try:
        report = obmer.build_measured_plan(ac, scene, rooms=preset.get("rooms"), origin=preset.get("origin"))
    finally:
        new = sorted(_all_guids(ac) - before)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"preset": name, "guids": new}, f)
    report["deleted"] = deleted
    report["created"] = len(new)
    with open(out_dir / f"rebuild-{name}-report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description="Пересобрать тестовый обмерный план одной командой.")
    p.add_argument("name", help="пресет из rebuild-presets.json, например kv94 или jk")
    p.add_argument("--root", help="папка «Архикад» (по умолчанию Desktop\\Архикад)")
    p.add_argument("--keep", action="store_true", help="не удалять прошлую сборку")
    a = p.parse_args(argv)
    try:
        r = rebuild(a.name, a.root, a.keep)
    except (ArchicadError, ValueError) as error:
        print(f"ОШИБКА: {error}")
        return 1
    print(f"Готово за {r.get('seconds')} с: удалено {r['deleted']}, создано {r['created']} элементов.")
    errors = r.get("errors") or []
    print("Ошибок нет." if not errors else "Замечания моста:\n- " + "\n- ".join(errors))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
