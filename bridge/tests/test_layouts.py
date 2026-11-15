"""deslab: schedule Drawing links and refreshed anchoring, without an Archicad process."""
import sys
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
