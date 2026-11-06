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

from . import check as layout_check
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
    # deslab: API.GetAllElements has no 2D elements (dimensions, labels, texts, polylines), so they were never
    # in the state file and piled up with every rebuild. Add them by type.
    found = {e["elementId"]["guid"] for e in ac.api("API.GetAllElements").get("elements", []) if "elementId" in e}
    for kind in ("Dimension", "Label", "Text", "PolyLine", "Line", "Arc", "Hatch"):
        try:
            found |= {e["elementId"]["guid"] for e in ac.tapir("GetElementsByType", {"elementType": kind}).get("elements", [])}
        except ArchicadError:
            pass
    return found


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


def snapshots(ac, guids, out_dir, name, views=(("view1", "Обмерный план"), ("view2", "Инженерные узлы"))):
    """deslab: a picture of each view, fitted to the new walls (one call each, no Claude needed)."""
    tree = ac.api("API.GetNavigatorItemTree", {"navigatorTreeId": {"type": "ViewMap"}})

    def walk(i):
        i = i.get("navigatorItem", i)
        yield i
        for c in i.get("children", []):
            yield from walk(c)
    items = list(walk(tree["navigatorTree"]["rootItem"]))
    walls = {e["elementId"]["guid"] for e in ac.api("API.GetElementsByType", {"elementType": "Wall"}).get("elements", [])}
    fit = [{"elementId": {"guid": g}} for g in guids if g in walls]
    files = []
    for tag, view in views:
        hit = [i for i in items if view in (i.get("name") or "")]
        if not hit or not fit:
            continue
        try:
            ac.tapir("ChangeWindow", {"navigatorItemId": hit[0]["navigatorItemId"]})
            ac.tapir("FitInWindow", {"elements": fit})
            path = str(out_dir / f"rebuild-{name}-{tag}.png")
            ac.tapir("SaveCurrentWindowAsImage", {"imageFilePath": path})
            files.append(path)
        except ArchicadError:
            pass
    return files


def rebuild(name, root=None, keep=False, pictures=True):
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
    # deslab: the planned texts and lines of each plan, for the check below
    planned = []
    place = obmer.place_dimension_texts

    def capture(chains, labels, profile, marks=(), inside=None):
        out = place(chains, labels, profile, marks, inside)
        planned.append((chains, labels))
        return out
    obmer.place_dimension_texts = capture
    try:
        report = obmer.build_measured_plan(ac, scene, rooms=preset.get("rooms"), origin=preset.get("origin"))
    finally:
        obmer.place_dimension_texts = place
        new = sorted(_all_guids(ac) - before)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"preset": name, "guids": new}, f)
    report["deleted"] = deleted
    report["created"] = len(new)
    shifted = obmer.shift_scene(json.loads(json.dumps(scene)), preset.get("origin"))
    report["check"] = {f"план {i + 1}": layout_check.check(shifted, c, l) for i, (c, l) in enumerate(planned)}
    # deslab: --no-pictures skips ChangeWindow/SaveCurrentWindowAsImage (a run must not touch the open view)
    report["pictures"] = snapshots(ac, new, out_dir, name) if pictures else []
    with open(out_dir / f"rebuild-{name}-report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description="Пересобрать тестовый обмерный план одной командой.")
    p.add_argument("name", help="пресет из rebuild-presets.json, например kv94 или jk")
    p.add_argument("--root", help="папка «Архикад» (по умолчанию Desktop\\Архикад)")
    p.add_argument("--no-pictures", action="store_true", help="не снимать картинки видов")
    p.add_argument("--keep", action="store_true", help="не удалять прошлую сборку")
    a = p.parse_args(argv)
    try:
        r = rebuild(a.name, a.root, a.keep, not a.no_pictures)
    except (ArchicadError, ValueError) as error:
        print(f"ОШИБКА: {error}")
        return 1
    print(f"Готово за {r.get('seconds')} с: удалено {r['deleted']}, создано {r['created']} элементов.")
    errors = r.get("errors") or []
    print("Ошибок нет." if not errors else "Замечания моста:\n- " + "\n- ".join(errors))
    for plan, items in r.get("check", {}).items():
        print(f"Проверка наложений, {plan}: " + ("чисто." if not items else f"{len(items)}\n- " + "\n- ".join(items)))
    for path in r.get("pictures", []):
        print(f"Снимок: {path}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
