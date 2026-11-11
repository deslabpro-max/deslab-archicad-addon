"""deslab: put the built flat on the template's layouts (Layout Book), as large as the frame allows.

Игорь 06.11.2026: «в книге макетов, в чертеже обмерный план, должен размещаться наш чертеж, максимального размера».
The template already has the layouts «04 ОБМЕРНЫЙ ПЛАН» (view «Обмерный план») and «05 ИНЖЕНЕРНЫЕ УЗЛЫ»
(view «Инженерные узлы»). The Tapir API cannot edit a placed Drawing (SetDetailsOfElements has no drawing fields),
so each plan Drawing is re-created with CreateDrawings: same source view, own scale, own frame (clip polygon)
and a position that puts the flat in the middle of the frame. Everything else on the layout (stamp, explication,
axonometry, notes) is not touched. Layout and view names come from office-profile.json, "layouts"."""
from .archicad import ArchicadError

SCALES = (20, 25, 30, 40, 45, 50, 60, 75, 100)


def _items(item):
    item = item.get("navigatorItem", item)
    yield item
    for child in item.get("children", []):
        yield from _items(child)


def _tree(ac, kind):
    return list(_items(ac.api("API.GetNavigatorItemTree", {"navigatorTreeId": {"type": kind}})["navigatorTree"]["rootItem"]))


def show_plan_view(ac, view_name):
    """deslab: model queries (bounding boxes, creating walls) need a floor plan window, not a layout."""
    view = next((v for v in _tree(ac, "ViewMap") if v["name"] == view_name or view_name in v["name"]), None)
    if view:
        ac.tapir("ChangeWindow", {"navigatorItemId": view["navigatorItemId"]})


def pick_scale(width, height, frame, scales=SCALES):
    """The largest standard scale (the smallest denominator) at which width x height (m) fits the frame (m on paper)."""
    for denominator in sorted(scales):
        if width / denominator <= frame["width"] and height / denominator <= frame["height"]:
            return denominator
    return max(scales)


def choose_scale(width, height, avail, scales, prefer, overflow):
    """deslab: the template's scale (1:45) whenever the plan fits the free area from the anchor point or sticks out by
    at most `overflow` m on paper, else the largest standard scale that fits (never larger than the preferred one)."""
    fit = pick_scale(width, height, avail, scales)
    # deslab: Игорь 08.11.2026, «масштаб должен каждый раз подгоняться под формат А3, чтобы он заполнял максимальное
    # пространство листа»: with no preferred scale (profile "preferScale": null) the largest scale that fits is taken
    if not prefer:
        return fit
    if fit <= prefer:
        return prefer
    if width / prefer - avail["width"] <= overflow and height / prefer - avail["height"] <= overflow:
        return prefer
    return fit


def missing_template_items(ac, profile):
    """deslab: the layouts and views of the office template that the open file lacks (Игорь 07.11.2026: a palette build in
    «Тест студия», a file not made from the template, failed only at the layouts step)."""
    cfg = profile.get("layouts") or {}
    views = [(i.get("prefix", "") + " " + i["name"]).strip() for i in _tree(ac, "ViewMap") if i.get("type") != "FolderItem"]
    layouts = [(i.get("prefix", "") + " " + i["name"]).strip() for i in _tree(ac, "LayoutBook") if i.get("type") == "LayoutItem"]
    out = []
    for entry in cfg.get("items", []):
        if not any(entry["layout"] in k for k in layouts):
            out.append("лист «" + entry["layout"] + "»")
        if not any(k == entry["view"] or entry["view"] in k for k in views):
            out.append("вид «" + entry["view"] + "»")
    return out


def place_on_layouts(ac, bbox, profile, view_scale=45):
    """bbox = {"xMin", "xMax", "yMin", "yMax"} of the flat in model metres (dimensions and labels included).
    Returns one line per layout for the report."""
    cfg = profile.get("layouts") or {}
    margin = float(cfg.get("margin", 0.25))
    frame = cfg["frame"]  # {"xMin","xMax","yMin","yMax"} on paper, metres: the plan area of the template's layouts
    inset = float(cfg.get("inset", 0.015))  # deslab: the drawing always starts 15 mm right of and below the area's top-left corner
    prefer = int(cfg.get("preferScale") or 0)
    tolerance_paper = float(cfg.get("overflow", 0.01))  # m on paper that the plan may stick out at the preferred scale
    width = bbox["xMax"] - bbox["xMin"] + 2 * margin
    height = bbox["yMax"] - bbox["yMin"] + 2 * margin
    views = {i.get("prefix", "") + " " + i["name"]: i for i in _tree(ac, "ViewMap") if i.get("type") != "FolderItem"}
    layouts = {(i.get("prefix", "") + " " + i["name"]).strip(): i for i in _tree(ac, "LayoutBook") if i.get("type") == "LayoutItem"}
    out = []
    for entry in cfg["items"]:
        layout = next((l for k, l in layouts.items() if entry["layout"] in k), None)
        view = next((v for k, v in views.items() if k.strip() == entry["view"] or entry["view"] in k), None)
        if not layout or not view:
            out.append(f"{entry['layout']}: нет листа или вида в шаблоне")
            continue
        # the corner of the layout's drawing area (page minus margins, Layout Settings), not a guessed number
        page = ac.tapir("GetLayoutSettings", {"layoutDatabaseIds": [{"navigatorItemId": layout["navigatorItemId"]}]})["layoutSettings"][0]
        tx = page["leftMargin"] / 1000 + inset
        ty = (page["verticalSize"] - page["topMargin"]) / 1000 - inset
        avail = {"width": frame["xMax"] - tx, "height": ty - frame["yMin"]}  # frame: right and bottom limits (stamp, legend)
        denominator = choose_scale(width, height, avail, cfg.get("scales", SCALES), prefer, tolerance_paper)
        ratio = view_scale / denominator
        ac.tapir("ChangeWindow", {"navigatorItemId": layout["navigatorItemId"]})
        drawings = ac.tapir("GetElementsByType", {"elementType": "Drawing"}).get("elements", [])
        details = ac.tapir("GetDetailsOfElements", {"elements": drawings}).get("detailsOfElements", []) if drawings else []
        # the plan Drawing is the one at the plan scale (the explication is 1:1, the axonometry a different ratio)
        # (ratio * drawingScale between 1:100 and 1:16; this also finds the one placed by the previous run)
        old = [e for e, d in zip(drawings, details)
               if 0.009 <= d["details"].get("ratio", 1) * d["details"].get("drawingScale", 1) <= 0.06]
        source = {"navigatorItemId": view["navigatorItemId"], "drawingScale": 1 / view_scale,
                  "modelOffset": {"x": 0, "y": 0}, "nameType": "ViewOrSourceFileName"}
        # Игорь 06.11.2026: the frame must be «Рамка по размеру Чертежа», so the drawing is created without a clip
        # polygon (the add-on then clears the crop and the frame follows the drawing). That shows the WHOLE view, so
        # a probe tells whether the view holds only the flat (plus dimensions); otherwise the crop is the only way
        # to keep the template's own flat and old leftovers off the sheet, and the frame stays manual.
        s = 1 / denominator
        # deslab: 08.11.2026, in a project set up by ApplyOfficeStandard the view keeps the zoom it was made with (77 x 38 m)
        # and Archicad sizes a «по размеру чертежа» frame by it: the frame came out 1715 x 850 mm, off the sheet. The
        # view's saved zoom is set to the flat (with the margin) first.
        try:
            ac.tapir("SetViewSettings", {"navigatorItemIdsWithViewSettings": [{"navigatorItemId": view["navigatorItemId"], "viewSettings": {
                "zoom": {"xMin": bbox["xMin"] - margin, "yMin": bbox["yMin"] - margin, "xMax": bbox["xMax"] + margin, "yMax": bbox["yMax"] + margin}}}]})
        except ArchicadError:  # an older add-on without zoom in SetViewSettings: the frame is checked below anyway
            pass
        probe = ac.tapir("CreateDrawings", {"drawingsData": [dict(source, position={"x": tx, "y": ty}, scale=1)]})["elements"][0]
        content = ac.tapir("GetDrawingContentBoxes", {"elements": [{"elementId": probe["elementId"]}]})["contentBoxes"][0]["contentBox"]
        # deslab: the view's REAL scale is what the probe came out with (Archicad takes the drawing's scale from the view; the
        # «Квартира 94 м2» copy has the view at 1:60 and the fixed 45 gave a 1:60 drawing reported as 1:45)
        real = ac.tapir("GetDetailsOfElements", {"elements": [{"elementId": probe["elementId"]}]})["detailsOfElements"][0]["details"]
        view_scale = round(1 / real["drawingScale"]) if real.get("drawingScale") else view_scale
        source["drawingScale"] = 1 / view_scale
        ratio = view_scale / denominator
        ac.tapir("DeleteElements", {"elements": [{"elementId": probe["elementId"]}]})
        tolerance = float(cfg.get("onlyFlatTolerance", 1.0))
        only_flat = (content["xMin"] >= bbox["xMin"] - tolerance and content["xMax"] <= bbox["xMax"] + tolerance and
                     content["yMin"] >= bbox["yMin"] - tolerance and content["yMax"] <= bbox["yMax"] + tolerance)
        if only_flat:
            # the content box is the frame: scale to it, then an unclipped drawing sits with its corner at the position
            width, height = content["xMax"] - content["xMin"] + 2 * margin, content["yMax"] - content["yMin"] + 2 * margin
            denominator = choose_scale(width, height, avail, cfg.get("scales", SCALES), prefer, tolerance_paper)
            ratio, s = view_scale / denominator, 1 / denominator
            # top-left corner of the content (plus margin) on the anchor point
            position = {"x": tx + margin * s, "y": ty - margin * s - (content["yMax"] - content["yMin"]) * s}
            new = ac.tapir("CreateDrawings", {"drawingsData": [dict(source, position=position, scale=ratio)]})["elements"][0]
            note = "рамка по размеру чертежа"
            if "elementId" in new:
                # measure the real frame on the layout and move the drawing onto the anchor point
                got = ac.tapir("GetDetailsOfElements", {"elements": [{"elementId": new["elementId"]}]})["detailsOfElements"][0]["details"]["bounds"]
                dx, dy = tx - got["xMin"], ty - got["yMax"]
                if abs(dx) > 0.0002 or abs(dy) > 0.0002:
                    ac.tapir("MoveElements", {"elementsWithMoveVectors": [{"elementId": new["elementId"], "moveVector": {"x": dx, "y": dy, "z": 0}, "copy": False}]})
                    got = ac.tapir("GetDetailsOfElements", {"elements": [{"elementId": new["elementId"]}]})["detailsOfElements"][0]["details"]["bounds"]
                note += f", левый верхний угол в {1000 * (got['xMin'] - page['leftMargin'] / 1000):.1f} мм справа и {1000 * ((page['verticalSize'] - page['topMargin']) / 1000 - got['yMax']):.1f} мм ниже угла области"
        else:
            # measured on this template (A27, drawing clipped with a polygon): the drawing's position is the paper point
            # of the model origin, not of the content corner (that is only so for an unclipped drawing), and a clip polygon
            # moves the drawing by -(clip corner) * ratio. So: put the flat's centre on the frame centre, then move back.
            # "offset" (profile): what is still left after that, in model metres, found by looking at the layouts
            shift = cfg.get("offset", {"x": 0.0, "y": 0.0})
            position = {"x": tx + margin * s - bbox["xMin"] * s + frame["xMin"] * ratio + shift["x"] * s,
                        "y": ty - margin * s - bbox["yMax"] * s + frame["yMin"] * ratio + shift["y"] * s}
            clip = [{"x": frame["xMin"], "y": frame["yMin"]}, {"x": frame["xMax"], "y": frame["yMin"]},
                    {"x": frame["xMax"], "y": frame["yMax"]}, {"x": frame["xMin"], "y": frame["yMax"]}]
            new = ac.tapir("CreateDrawings", {"drawingsData": [dict(source, position=position, scale=ratio, clipPolygon=clip)]})["elements"][0]
            note = "рамка обрезает вид: в виде есть лишнее вне плана (проект не только кв. 94), «по размеру чертежа» невозможна"
        if "elementId" not in new:
            out.append(f"{entry['layout']}: чертёж не создан ({new})")
            continue
        # deslab: CreateDrawings gives the Drawing tool's default title; Игорь wants none, and the add-on accepts the
        # title part index in SetDetailsOfElements (0 = no title)
        done = ac.tapir("SetDetailsOfElements", {"elementsWithDetails": [{"elementId": new["elementId"], "details": {
            "typeSpecificDetails": {"titleLibraryPartIndex": int(cfg.get("titleLibraryPartIndex", 0))}}}]})
        if not all(r.get("success") for r in done.get("executionResults", [])):
            note += f"; заголовок не убран ({done})"
        if old:  # the template's own plan drawing is replaced, the rest of the layout stays
            ac.tapir("DeleteElements", {"elements": [{"elementId": e["elementId"]} for e in old]})
        out.append(f"{entry['layout']}: «{entry['view']}» 1:{denominator}, {note}")
    # deslab: leave a floor plan window in front, the next rebuild creates walls and cannot do it on a layout
    show_plan_view(ac, cfg["items"][0]["view"])
    return out


def _area(value):
    return f"{value:.2f}".replace(".", ",")


def note_text(cfg, source, total):
    """deslab: Игорь 11.11.2026, the note on the layout says what the drawing was really made from (a 3D scan, a drawing,
    a manual survey) and carries the measured area of this very plan, not the template's sample."""
    source = source or {}
    kind = source.get("kind")
    line = cfg.get("sources", {}).get(kind) or cfg.get("sources", {}).get("default", "все чертежи выполнены на основе обмеров существующих перегородок")
    line = line.format(name=source.get("name", ""), date=source.get("date", "")).replace(" от )", ")").strip()
    rows = [line] + list(cfg.get("lines", [])) + [cfg.get("area", "фактическая площадь при замерах {area} м2").format(area=_area(total))]
    return "\n".join([cfg.get("title", "ПРИМЕЧАНИЕ")] + ["- " + r for r in rows])


def _layout(ac, name):
    layouts = {(i.get("prefix", "") + " " + i["name"]).strip(): i for i in _tree(ac, "LayoutBook") if i.get("type") == "LayoutItem"}
    return next((l for k, l in layouts.items() if name in k), None)


def place_note(ac, profile, source, total):
    """Rewrites the template's note text on the layouts (found by its first word)."""
    cfg = (profile.get("layouts") or {}).get("note")
    if not cfg:
        return []
    text, title, done = note_text(cfg, source, total), cfg.get("title", "ПРИМЕЧАНИЕ"), 0
    for name in cfg.get("layouts", []):
        layout = _layout(ac, name)
        if not layout:
            continue
        ac.tapir("ChangeWindow", {"navigatorItemId": layout["navigatorItemId"]})
        texts = ac.tapir("GetElementsByType", {"elementType": "Text"}).get("elements", [])
        details = ac.tapir("GetDetailsOfElements", {"elements": texts}).get("detailsOfElements", []) if texts else []
        for element, d in zip(texts, details):
            old = str((d.get("details") or {}).get("typeSpecificDetails", d.get("details") or {}).get("text", ""))
            # the API gives Cyrillic of this font as cp1251 read as Latin-1
            try:
                old = old if title in old else old.encode("latin-1").decode("cp1251")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            if old.lstrip().startswith(title):
                ac.tapir("ModifyTexts", {"textsWithDetails": [{"elementId": element["elementId"], "text": text}]})
                done += 1
    return [f"Примечание: обновлено на {done} листах, площадь {_area(total)} м²" if done else "Примечание: текст на листах не найден"]


def place_explication(ac, profile):
    """deslab: Игорь 11.11.2026: the explication is the interactive schedule of the zones (as in the template), placed on
    the layout as a Drawing, so the rooms, their names and the total follow the zones. The API cannot create a schedule
    scheme, so the first one found by the profile's names is used: the office scheme when the file has it, else the
    stock zone schedule of the Archicad template."""
    cfg = (profile.get("layouts") or {}).get("explication")
    if not cfg:
        return []
    layout = _layout(ac, cfg["layout"])
    if not layout:
        return [f"Экспликация: нет листа «{cfg['layout']}»"]
    items = [i for kind in ("ViewMap", "ProjectMap") for i in _tree(ac, kind) if i.get("type") == "ScheduleItem"]
    schedule = next((i for name in cfg["schedules"] for i in items if i["name"] == name), None)
    if not schedule:
        return ["Экспликация: в файле нет ведомости зон (" + ", ".join(cfg["schedules"]) + ")"]
    ac.tapir("ChangeWindow", {"navigatorItemId": layout["navigatorItemId"]})
    drawings = ac.tapir("GetElementsByType", {"elementType": "Drawing"}).get("elements", [])
    details = ac.tapir("GetDetailsOfElements", {"elements": drawings}).get("detailsOfElements", []) if drawings else []
    title = cfg.get("title", "Экспликация помещений")
    old = [e for e, d in zip(drawings, details) if (d.get("details") or {}).get("typeSpecificDetails", d.get("details") or {}).get("name") == title]
    right, top = cfg["right"], cfg["top"]
    new = ac.tapir("CreateDrawings", {"drawingsData": [{"navigatorItemId": schedule["navigatorItemId"], "name": title,
                                                         "position": {"x": right - 0.04, "y": top - 0.02}, "scale": 1}]})["elements"][0]
    if "elementId" not in new:
        return [f"Экспликация: чертёж ведомости не создан ({new})"]
    ac.tapir("SetDetailsOfElements", {"elementsWithDetails": [{"elementId": new["elementId"], "details": {
        "typeSpecificDetails": {"titleLibraryPartIndex": int(cfg.get("titleLibraryPartIndex", 0))}}}]})
    # the schedule's size is known only once it is placed: its top right corner goes to the template's place
    got = ac.tapir("GetDetailsOfElements", {"elements": [{"elementId": new["elementId"]}]})["detailsOfElements"][0]["details"]["bounds"]
    dx, dy = right - got["xMax"], top - got["yMax"]
    if abs(dx) > 0.0002 or abs(dy) > 0.0002:
        ac.tapir("MoveElements", {"elementsWithMoveVectors": [{"elementId": new["elementId"], "moveVector": {"x": dx, "y": dy, "z": 0}, "copy": False}]})
    if old:
        ac.tapir("DeleteElements", {"elements": [{"elementId": e["elementId"]} for e in old]})
    size = f"{1000 * (got['xMax'] - got['xMin']):.0f} x {1000 * (got['yMax'] - got['yMin']):.0f} мм"
    return [f"Экспликация: ведомость «{schedule['name']}» на листе, {size}"]
