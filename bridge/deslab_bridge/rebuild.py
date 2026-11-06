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
from . import layouts
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
    for kind in ("Dimension", "Label", "Text", "PolyLine", "Line", "Arc", "Hatch", "Hotspot"):
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
    if preset.get("aiReview"):
        # deslab: the scene WITHOUT its hand-made mep plus the saved AI review applied through the checks (no new paid call)
        from . import ai
        review_path = root / preset["aiReview"]
        scene = {k: v for k, v in scene.items() if k not in ("mep", "tracing")}
        scene, report = ai.apply_review(scene, _load(review_path)["review"])
        scene["aiReport"] = report
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


# deslab: Archicad takes the next free JSON API port when 19723 is busy (19724 after a restart,
# 06.11.2026); the office template must never be built into
PORTS = range(19723, 19731)
PROTECTED = ("Антон Крючков",)


def connect():
    for port in PORTS:
        ac = Archicad(port=port)
        if ac.alive():
            break
    else:
        raise SystemExit("Archicad не отвечает: откройте тестовый файл и закройте диалоги.")
    try:
        info = ac.tapir("GetProjectInfo")
    except ArchicadError:
        info = {}
    project = info.get("projectName") or info.get("projectLocation") or "?"
    print(f"Проект: {project} (порт {port})")
    if any(n in str(info.get("projectName", "")) + str(info.get("projectLocation", "")) for n in PROTECTED):
        raise SystemExit("Открыт шаблон «Антон Крючков.pln»: в него не строю. Откройте тестовый файл.")
    return ac


def flat_bbox(ac, guids):
    """The bounding box (model metres) of the built elements, 2D dimensions and labels included."""
    boxes = ac.tapir("Get3DBoundingBoxes", {"elements": [{"elementId": {"guid": g}} for g in guids]})["boundingBoxes3D"]
    boxes = [b.get("boundingBox3D", b) for b in boxes]
    return {k: (min if k.endswith("Min") else max)(b[k] for b in boxes) for k in ("xMin", "xMax", "yMin", "yMax")}


def place(ac, guids):
    # deslab: the built flat goes to the template layouts (layouts.py)
    profile = _load(Path(__file__).resolve().parent.parent / "office-profile.json")
    layouts.show_plan_view(ac, profile["layouts"]["items"][0]["view"])
    return layouts.place_on_layouts(ac, flat_bbox(ac, guids), profile)


def rebuild(name, root=None, keep=False, pictures=True, on_layouts=True, preset=None):
    root = _root(root)
    if preset is None:
        presets = _load(PRESETS)
        if name not in presets:
            raise SystemExit(f"Нет пресета «{name}». Есть: {', '.join(presets)}")
        preset = presets[name]
    out_dir = root / "captures"
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / f"rebuild-{name}.json"

    ac = connect()
    # deslab: walls were refused ("Failed to create new Wall") on a run right after a layouts step; build in the floor plan window
    layouts.show_plan_view(ac, _load(Path(__file__).resolve().parent.parent / "office-profile.json")["layouts"]["items"][0]["view"])
    if preset.get("e57"):
        # deslab: a new scan (Игорь 06.11.2026: «обмер из скана.bat», drop an E57 on it): the helper
        # turns the cloud into a scene first, then the scene is built like a preset
        print("Разбираю облако точек, это несколько минут…")
        preset = dict(preset, scene=obmer.analyze_scan(ac, str(preset["e57"])))
        print(f"Задание: {preset['scene']}")
        found = _load(Path(preset["scene"]))
        if "error" in found:
            raise SystemExit(f"Облако не разобрано: {found['error']}")
    scene = load_scene(preset, root)
    deleted = 0 if keep else delete_previous(ac, state_path)
    for other in preset.get("replaces", []):  # deslab: «kv94ai» takes the place of «kv94» at the same origin
        deleted += 0 if keep else delete_previous(ac, out_dir / f"rebuild-{other}.json")
    before = _all_guids(ac)
    # deslab: the planned texts and lines of each plan, for the check below
    planned = []
    # deslab: not "place": that name is the layouts step below (it was shadowed here)
    place_texts = obmer.place_dimension_texts

    def capture(chains, labels, profile, marks=(), inside=None):
        out = place_texts(chains, labels, profile, marks, inside)
        planned.append((chains, labels))
        return out
    obmer.place_dimension_texts = capture
    try:
        report = obmer.build_measured_plan(ac, scene, rooms=preset.get("rooms"), origin=preset.get("origin"))
    finally:
        obmer.place_dimension_texts = place_texts
        new = sorted(_all_guids(ac) - before)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"preset": name, "guids": new}, f)
    report["deleted"] = deleted
    report["created"] = len(new)
    shifted = obmer.shift_scene(json.loads(json.dumps(scene)), preset.get("origin"))
    report["check"] = {f"план {i + 1}": layout_check.check(shifted, c, l) for i, (c, l) in enumerate(planned)}
    # deslab: --no-pictures skips ChangeWindow/SaveCurrentWindowAsImage (a run must not touch the open view)
    report["pictures"] = snapshots(ac, new, out_dir, name) if pictures else []
    report["layouts"] = place(ac, new) if on_layouts else []
    with open(out_dir / f"rebuild-{name}-report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description="Пересобрать тестовый обмерный план одной командой.")
    p.add_argument("name", help="пресет из rebuild-presets.json (kv94, jk) или путь к файлу облака .e57")
    p.add_argument("--root", help="папка «Архикад» (по умолчанию Desktop\\Архикад)")
    p.add_argument("--no-pictures", action="store_true", help="не снимать картинки видов")
    p.add_argument("--no-layouts", action="store_true", help="не ставить чертёж на листы")
    p.add_argument("--layouts-only", action="store_true", help="только поставить уже собранный план на листы")
    p.add_argument("--keep", action="store_true", help="не удалять прошлую сборку")
    a = p.parse_args(argv)
    try:
        if a.layouts_only:
            state = _load(_root(a.root) / "captures" / f"rebuild-{a.name}.json")
            for line in place(connect(), state["guids"]):
                print(line)
            return 0
        if a.name.lower().endswith(".e57"):
            # a scan: built at the origin of the open file (one flat per file, from the template)
            e57 = Path(a.name).resolve()
            r = rebuild(e57.stem.split(".")[0], a.root, a.keep, not a.no_pictures, not a.no_layouts,
                        preset={"e57": str(e57), "origin": {"x": 0, "y": 0}})
        else:
            r = rebuild(a.name, a.root, a.keep, not a.no_pictures, not a.no_layouts)
    except (ArchicadError, ValueError) as error:
        print(f"ОШИБКА: {error}")
        return 1
    print(f"Готово за {r.get('seconds')} с: удалено {r['deleted']}, создано {r['created']} элементов.")
    for line in r.get("layouts", []):
        print("Лист:", line)
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
