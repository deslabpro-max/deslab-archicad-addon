"""deslab: schedule Drawing links and refreshed anchoring, without an Archicad process."""
import sys
import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deslab_bridge import layouts


PROFILE = {"layouts": {"explication": {"layout": "Обмерный план", "title": "Экспликация помещений",
            "right": 0.415, "bottom": 0.1043, "scheme": {"name": "Экспликация обмерный план"}}}}
SCHEDULE = {"name": "Экспликация обмерный план", "navigatorItemId": {"guid": "new-source"}}


class ScheduleDrawing:
    def __init__(self, source):
        self.source, self.calls, self.updated = source, [], False
        self.existing = {"elementId": {"guid": "existing-table"}}
        self.unrelated = {"elementId": {"guid": "plan-drawing"}}

    def tapir(self, command, params):
        self.calls.append((command, params))
        if command == "ChangeWindow":
            return {"success": True}
        if command == "GetElementsByType":
            return {"elements": [self.existing, self.unrelated]}
        if command == "GetDetailsOfElements":
            if params["elements"] == [self.existing, self.unrelated]:
                return {"detailsOfElements": [
                    {"details": {"customName": "Экспликация помещений", "navigatorItemId": {"guid": self.source}}},
                    {"details": {"customName": "План", "navigatorItemId": {"guid": "plan-source"}}}]}
            assert self.updated, "Measure the table after the schedule content was refreshed"
            return {"detailsOfElements": [{"details": {"bounds": {"xMin": 0.3, "xMax": 0.4, "yMin": 0.1, "yMax": 0.17}}}]}
        if command == "CreateDrawings":
            assert params["drawingsData"][0]["navigatorItemId"] == SCHEDULE["navigatorItemId"]
            return {"elements": [{"elementId": {"guid": "replacement-table"}}]}
        if command == "UpdateDrawings":
            self.updated = True
        if command in ("UpdateDrawings", "SetDetailsOfElements", "MoveElements", "DeleteElements"):
            return {"success": True}
        raise AssertionError(command)


def place(ac):
    with patch.object(layouts, "_layout", return_value={"navigatorItemId": {"guid": "layout"}}), \
         patch.object(layouts, "_schedules", return_value=[SCHEDULE]), \
         patch.object(layouts, "import_scheme", return_value=None):
        return layouts.place_explication(ac, PROFILE)


def test_stale_schedule_link_is_replaced():
    ac = ScheduleDrawing("old-source")
    report = place(ac)
    names = [c for c, _ in ac.calls]
    assert names.count("CreateDrawings") == 1
    assert next(p for c, p in ac.calls if c == "UpdateDrawings")["elements"] == [{"elementId": {"guid": "replacement-table"}}]
    assert next(p for c, p in ac.calls if c == "DeleteElements")["elements"] == [ac.existing]
    assert names.index("UpdateDrawings") < names.index("DeleteElements")
    assert "на листе" in report[-1]


def test_current_schedule_refreshes_and_reanchors_without_duplicate():
    ac = ScheduleDrawing("NEW-SOURCE")
    place(ac)
    names = [c for c, _ in ac.calls]
    assert "CreateDrawings" not in names and "DeleteElements" not in names
    assert next(p for c, p in ac.calls if c == "UpdateDrawings")["elements"] == [ac.existing]
    move = next(p for c, p in ac.calls if c == "MoveElements")["elementsWithMoveVectors"][0]
    assert move["elementId"] == ac.existing["elementId"]
    assert abs(move["moveVector"]["x"] - 0.015) < 1e-9
    assert abs(move["moveVector"]["y"] - 0.0043) < 1e-9
    detail = next(p for c, p in ac.calls if c == "SetDetailsOfElements")["elementsWithDetails"][0]["details"]["typeSpecificDetails"]
    assert detail == {"titleLibraryPartIndex": 0, "includeInScaleAutoTexts": False}


class Legend:
    def __init__(self):
        self.calls, self.elements = [], []
        self.attrs = {
            "Layer": [{"name": "Layout", "index": 7, "attributeId": {"guid": "layer-target"}}],
            "Fill": [{"name": "Solid", "index": 20, "attributeId": {"guid": "fill-target"}}],
            "Line": [{"name": "Continuous", "index": 3, "attributeId": {"guid": "line-target"}}],
            "BuildingMaterial": []}

    def tapir(self, command, params):
        self.calls.append((command, params))
        if command == "GetAttributesByType":
            return {"attributes": self.attrs[params["attributeType"]]}
        if command == "ChangeWindow":
            return {"success": True}
        if command == "GetElementsByType":
            return {"elements": [{"elementId": e["elementId"]} for e in self.elements if e["type"] == params["elementType"]]}
        if command == "GetDetailsOfElements":
            return {"detailsOfElements": [e for request in params["elements"] for e in self.elements if e["elementId"] == request["elementId"]]}
        if command in ("CreateHatches", "CreatePolylines"):
            hatch = command == "CreateHatches"
            data = copy.deepcopy(params["hatchesData" if hatch else "polylinesData"][0])
            if hatch:
                data["coordinates"].append(data["coordinates"][0])  # native response closes a Hatch contour
            else:
                data.pop("lineTypeIndex")
                data["lineTypeId"] = {"guid": "LINE-TARGET"}
            element = {"type": "Hatch" if hatch else "PolyLine", "elementId": {"guid": str(len(self.elements))},
                       "layerIndex": data.pop("layerIndex"), "details": data}
            self.elements.append(element)
            return {"elements": [{"elementId": element["elementId"]}]}
        raise AssertionError(command)


def legend_package():
    return {"format": "deslab.reference-legends/1", "layouts": [{"name": "Layout", "elements": [
        {"type": "Hatch", "layer": "Layout", "fillName": "Solid", "fillPenIndex": 1, "fillBackgroundPenIndex": 0,
         "contourPenIndex": 1, "showArea": False,
         "coordinates": [{"x": 0.3, "y": 0.04}, {"x": 0.33, "y": 0.04}, {"x": 0.33, "y": 0.05}]},
        {"type": "PolyLine", "layer": "Layout", "lineTypeName": "Continuous", "linePenIndex": 8,
         "coordinates": [{"x": 0.3, "y": 0.04}, {"x": 0.33, "y": 0.04}]}]}]}


def test_reference_legend_resolves_target_attributes_and_is_idempotent():
    ac = Legend()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "legend.json"
        path.write_text(json.dumps(legend_package()), encoding="utf-8")
        profile = {"layouts": {"legendContent": str(path)}}
        with patch.object(layouts, "_layout", return_value={"navigatorItemId": {"guid": "layout"}}):
            first = layouts.place_reference_legend(ac, profile)
            second = layouts.place_reference_legend(ac, profile)
    assert "добавлено 2" in first[-1] and "добавлено 0, уже есть 2" in second[-1]
    assert len(ac.elements) == 2
    hatch = next(p for c, p in ac.calls if c == "CreateHatches")["hatchesData"][0]
    line = next(p for c, p in ac.calls if c == "CreatePolylines")["polylinesData"][0]
    assert hatch["fillId"] == {"guid": "fill-target"} and hatch["layerIndex"] == 7
    assert line["lineTypeIndex"] == 3
    assert not any(c == "DeleteElements" for c, _ in ac.calls)


def test_reference_legend_reports_missing_attribute_without_partial_creation():
    ac = Legend()
    package = legend_package()
    package["layouts"][0]["elements"][0]["fillName"] = "Missing reference fill"
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "legend.json"
        path.write_text(json.dumps(package), encoding="utf-8")
        with patch.object(layouts, "_layout", return_value={"navigatorItemId": {"guid": "layout"}}):
            report = layouts.place_reference_legend(ac, {"layouts": {"legendContent": str(path)}})
    assert "Missing reference fill" in report[0]
    assert not ac.elements and not any(c.startswith("Create") for c, _ in ac.calls)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
