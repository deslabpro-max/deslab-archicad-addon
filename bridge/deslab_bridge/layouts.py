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


def place_on_layouts(ac, bbox, profile, view_scale=45):
    """bbox = {"xMin", "xMax", "yMin", "yMax"} of the flat in model metres (dimensions and labels included).
    Returns one line per layout for the report."""
    cfg = profile.get("layouts") or {}
    margin = float(cfg.get("margin", 0.25))
    frame = cfg["frame"]  # {"xMin","xMax","yMin","yMax"} on paper, metres: the plan area of the template's layouts
    box = {"width": frame["xMax"] - frame["xMin"], "height": frame["yMax"] - frame["yMin"]}
    width = bbox["xMax"] - bbox["xMin"] + 2 * margin
    height = bbox["yMax"] - bbox["yMin"] + 2 * margin
    denominator = pick_scale(width, height, box, cfg.get("scales", SCALES))
    ratio = view_scale / denominator
    views = {i.get("prefix", "") + " " + i["name"]: i for i in _tree(ac, "ViewMap") if i.get("type") != "FolderItem"}
    layouts = {(i.get("prefix", "") + " " + i["name"]).strip(): i for i in _tree(ac, "LayoutBook") if i.get("type") == "LayoutItem"}
    cx, cy = (bbox["xMin"] + bbox["xMax"]) / 2, (bbox["yMin"] + bbox["yMax"]) / 2
    fx, fy = (frame["xMin"] + frame["xMax"]) / 2, (frame["yMin"] + frame["yMax"]) / 2
    out = []
    for entry in cfg["items"]:
        layout = next((l for k, l in layouts.items() if entry["layout"] in k), None)
        view = next((v for k, v in views.items() if k.strip() == entry["view"] or entry["view"] in k), None)
        if not layout or not view:
            out.append(f"{entry['layout']}: нет листа или вида в шаблоне")
            continue
        ac.tapir("ChangeWindow", {"navigatorItemId": layout["navigatorItemId"]})
        drawings = ac.tapir("GetElementsByType", {"elementType": "Drawing"}).get("elements", [])
        details = ac.tapir("GetDetailsOfElements", {"elements": drawings}).get("detailsOfElements", []) if drawings else []
        # the plan Drawing is the one at the plan scale (the explication is 1:1, the axonometry a different ratio)
        # (ratio * drawingScale between 1:100 and 1:16; this also finds the one placed by the previous run)
        old = [e for e, d in zip(drawings, details)
               if 0.009 <= d["details"].get("ratio", 1) * d["details"].get("drawingScale", 1) <= 0.06]
        # measured on this template (A27, drawing clipped with a polygon): the drawing's position is the paper point
        # of the model origin, not of the content corner (that is only so for an unclipped drawing), and a clip polygon
        # moves the drawing by -(clip corner) * ratio. So: put the flat's centre on the frame centre, then move back.
        s = 1 / denominator
        # "offset" (profile): what is still left after that, in model metres, found by looking at the layouts
        shift = cfg.get("offset", {"x": 0.0, "y": 0.0})
        position = {"x": fx - cx * s + frame["xMin"] * ratio + shift["x"] * s,
                    "y": fy - cy * s + frame["yMin"] * ratio + shift["y"] * s}
        clip = [{"x": frame["xMin"], "y": frame["yMin"]}, {"x": frame["xMax"], "y": frame["yMin"]},
                {"x": frame["xMax"], "y": frame["yMax"]}, {"x": frame["xMin"], "y": frame["yMax"]}]
        new = ac.tapir("CreateDrawings", {"drawingsData": [{
            "navigatorItemId": view["navigatorItemId"], "position": position, "scale": ratio,
            "drawingScale": 1 / view_scale, "modelOffset": {"x": 0, "y": 0}, "clipPolygon": clip,
            "nameType": "ViewOrSourceFileName"}]})["elements"][0]
        if "elementId" not in new:
            out.append(f"{entry['layout']}: чертёж не создан ({new})")
            continue
        if old:  # the template's own plan drawing is replaced, the rest of the layout stays
            ac.tapir("DeleteElements", {"elements": [{"elementId": e["elementId"]} for e in old]})
        out.append(f"{entry['layout']}: «{entry['view']}» 1:{denominator}")
    # deslab: leave a floor plan window in front, the next rebuild creates walls and cannot do it on a layout
    show_plan_view(ac, cfg["items"][0]["view"])
    return out
