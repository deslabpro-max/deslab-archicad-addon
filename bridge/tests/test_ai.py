"""Tests of the AI review step without a network (python bridge/tests/test_ai.py)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deslab_bridge import ai  # noqa: E402

SCENE = {
    "walls": [
        {"id": "W1", "start": {"x": 0, "y": 0}, "end": {"x": 6, "y": 0}, "thickness": 0.2, "kind": "exterior",
         "openings": [{"id": "W1.1", "kind": "window", "centerOffset": 2.0, "width": 1.2, "sill": 0.55, "height": 2.0},
                      {"id": "W1.2", "kind": "window", "centerOffset": 4.5, "width": 0.8, "sill": 0.55, "height": 2.0}]},
        {"id": "W2", "start": {"x": 6, "y": 0}, "end": {"x": 6, "y": 4}, "thickness": 0.2, "kind": "interior",
         "openings": [{"id": "W2.1", "kind": "door", "centerOffset": 2.0, "width": 0.9, "sill": 0, "height": 2.1}]},
        {"id": "W9", "start": {"x": 3, "y": 3}, "end": {"x": 3.3, "y": 3}, "thickness": 0.1, "kind": "interior", "openings": []},
    ],
    "rooms": [{"id": "R1", "name": "Помещение 1", "polygon": [{"x": 0, "y": 0}, {"x": 6, "y": 0}, {"x": 6, "y": 4}, {"x": 0, "y": 4}]}],
    "slabs": [], "mep": [{"kind": "shaft", "id": "SH1", "polygon": [{"x": 0, "y": 0.5}, {"x": 0.6, "y": 0.5}, {"x": 0.6, "y": 1.2}, {"x": 0, "y": 1.2}]}],
}

REVIEW = {
    "openings": [
        {"id": "W2.1", "kind": "door", "reflected": True, "oSide": False, "confidence": 0.9},   # applied
        {"id": "W1.2", "kind": "window", "sashes": 2, "confidence": 0.9},                         # rejected: 0.8 m, one sash only
        {"id": "W1.1", "kind": "door", "confidence": 0.9},                                        # rejected: window cannot become a door
        {"id": "NOPE", "kind": "window", "confidence": 0.9},                                      # rejected: unknown
        {"id": "W1.1", "sashes": 2, "confidence": 0.3},                                           # rejected: low confidence
    ],
    "newOpenings": [
        {"wallId": "W2", "id": "W2.2", "kind": "window", "centerOffset": 3.5, "width": 0.4, "sill": 0.9, "height": 1.0, "confidence": 0.8},  # applied
        {"wallId": "W2", "id": "W2.3", "kind": "window", "centerOffset": 2.2, "width": 1.0, "confidence": 0.8},                              # overlaps W2.1
        {"wallId": "W1", "id": "W1.9", "kind": "window", "centerOffset": 5.9, "width": 0.6, "confidence": 0.8},                              # off the wall
    ],
    "mep": [
        {"kind": "sewerRiser", "id": "K1", "at": {"x": 0.2, "y": 0.8}, "diameter": 0.11, "confidence": 0.9},   # applied: inside SH1
        {"kind": "sewerRiser", "id": "K2", "at": {"x": 3.0, "y": 2.0}, "diameter": 0.11, "confidence": 0.9},   # applied: a riser may stand outside a shaft (kv94 K3)
        {"kind": "radiator", "id": "RD1", "start": {"x": 1, "y": 3.9}, "end": {"x": 2, "y": 3.9}, "depth": 0.07, "confidence": 0.9},  # applied
        {"kind": "radiator", "id": "RD2", "start": {"x": 9, "y": 9}, "end": {"x": 10, "y": 9}, "depth": 0.07, "confidence": 0.9},      # rejected: outside the flat
        {"kind": "boiler", "id": "B1", "at": {"x": 1, "y": 1}, "confidence": 0.9},                                                       # rejected: kind
        {"kind": "shaft", "id": "SH1", "polygon": [], "confidence": 0.9},                                                                # rejected: repeated id
    ],
    "clutter": [{"wallId": "W9", "reason": "стопка плитки"}, {"wallId": "W1", "reason": "?"}],   # W9 dropped, W1 rejected (long)
    "rooms": [{"id": "R1", "name": "Гостиная"}, {"id": "R7", "name": "х"}],
    "wetZones": [{"kind": "bathroom", "polygon": [{"x": 4, "y": 0.5}, {"x": 5.5, "y": 0.5}, {"x": 5.5, "y": 2}]},
                 {"kind": "bathroom", "polygon": [{"x": 8, "y": 8}, {"x": 9, "y": 8}, {"x": 9, "y": 9}]}],
    "doubts": ["Входная дверь открывается внутрь?"],
}


def test_apply_review():
    before = json.dumps(SCENE, sort_keys=True)
    new, report = ai.apply_review(SCENE, REVIEW)
    assert json.dumps(SCENE, sort_keys=True) == before, "the input scene must stay untouched"
    door = new["walls"][1]["openings"][0]
    assert door["reflected"] is True and door["oSide"] is False
    assert "sashes" not in new["walls"][0]["openings"][1]
    assert new["walls"][0]["openings"][0]["kind"] == "window"
    assert [o["id"] for o in new["walls"][1]["openings"]] == ["W2.1", "W2.2"]
    assert {m["id"] for m in new["mep"]} == {"SH1", "K1", "K2", "RD1"}
    assert [w["id"] for w in new["walls"]] == ["W1", "W2"]
    assert new["rooms"][0]["name"] == "Гостиная"
    assert len(new["wetZones"]) == 1
    assert report["doubts"] == ["Входная дверь открывается внутрь?"]
    text = "\n".join(report["rejected"])
    for part in ("NOPE", "W1.2", "W1.1", "W2.3", "W1.9", "RD2", "B1", "SH1", "W1:", "R7"):
        assert part in text or part.rstrip(":") in text, (part, text)
    assert len(report["rejected"]) == 12, len(report["rejected"])


def test_parse_json_and_prompt():
    assert ai.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    try:
        ai.parse_json("no json here")
        raise AssertionError("expected AIError")
    except ai.AIError:
        pass
    prompt = ai.build_system_prompt({}, rules_text="ПРАВИЛО-ТЕСТ")
    assert "ПРАВИЛО-ТЕСТ" in prompt and "doubts" in prompt


def test_ai_review_with_fake_model():
    seen = {}

    def fake(messages, profile, model):
        seen["model"] = model
        seen["user"] = messages[1]["content"][0]["text"]
        return '{"doubts": ["?"], "openings": []}', {"prompt_tokens": 1, "completion_tokens": 1}

    review, usage, _ = ai.ai_review(SCENE, profile={"ai": {"model": "m1", "hardModel": "m2"}}, call=fake)
    assert review["doubts"] == ["?"] and seen["model"] == "m1" and "W1.1" in seen["user"]
    ai.ai_review(SCENE, profile={"ai": {"model": "m1", "hardModel": "m2"}}, hard=True, call=fake)
    assert seen["model"] == "m2"


def test_candidates_from_the_cloud():
    # deslab: 13.11.2026, the helper measures, the model only names the numbers
    scene = json.loads(json.dumps(SCENE))
    scene["mep"] = []
    scene["candidates"] = [
        {"id": "C1", "wallId": "W1", "side": "left", "from": 1.5, "to": 2.5, "bottom": 0.12, "top": 0.62, "depth": 0.1,
         "at": {"x": 2.0, "y": 0.15}, "start": {"x": 1.5, "y": 0.1}, "end": {"x": 2.5, "y": 0.1}, "hint": "radiator?"},
        {"id": "C2", "wallId": "W1", "side": "left", "from": 2.7, "to": 2.74, "bottom": 0.0, "top": 2.7, "depth": 0.08,
         "at": {"x": 2.72, "y": 0.14}, "start": {"x": 2.7, "y": 0.1}, "end": {"x": 2.74, "y": 0.1}, "hint": "riser?"},
        {"id": "C3", "wallId": "W2", "side": "left", "from": 3.0, "to": 3.6, "bottom": 0.0, "top": 2.7, "depth": 0.3,
         "at": {"x": 5.75, "y": 3.3}, "start": {"x": 5.9, "y": 3.0}, "end": {"x": 5.9, "y": 3.6}, "hint": "box or shaft?"},
        {"id": "C4", "wallId": "W1", "side": "left", "from": 4.0, "to": 5.0, "bottom": 0.4, "top": 1.0, "depth": 0.4,
         "at": {"x": 4.5, "y": 0.3}, "start": {"x": 4.0, "y": 0.1}, "end": {"x": 5.0, "y": 0.1}, "hint": ""},
        {"id": "C5", "wallId": "W1", "side": "left", "from": 5.2, "to": 5.3, "bottom": 0.0, "top": 2.7, "depth": 0.1,
         "at": {"x": 5.25, "y": 0.15}, "start": {"x": 5.2, "y": 0.1}, "end": {"x": 5.3, "y": 0.1}, "hint": "riser?"},
    ]
    review = {"candidates": [{"id": "C1", "kind": "radiator", "confidence": 0.9}, {"id": "C2", "kind": "heatingRiser", "confidence": 0.8},
                             {"id": "C3", "kind": "shaft", "confidence": 0.7}, {"id": "C4", "kind": "furniture", "confidence": 0.9},
                             {"id": "C9", "kind": "radiator", "confidence": 0.9}],
              "vents": [{"wallId": "W2", "side": "left", "along": 3.3, "height": 2.4, "confidence": 0.8}],
              "openings": [{"id": "W2.1", "kind": "opening", "confidence": 0.9}]}
    new, report = ai.apply_review(scene, review)
    items = {m["id"]: m for m in new["mep"]}
    assert set(items) == {"RD1", "T2", "SH3", "VO1"}, (set(items), report)
    assert items["RD1"]["start"] == {"x": 1.5, "y": 0.1} and items["RD1"]["height"] == 0.5 and items["RD1"]["elevation"] == 0.12
    assert items["T2"]["label"] == "Стояк отопления" and 0.02 <= items["T2"]["diameter"] <= 0.06
    assert items["SH3"]["polygon"][2] == {"x": 5.6, "y": 3.6}, items["SH3"]
    assert items["VO1"]["at"] == {"x": 5.9, "y": 3.3} and items["VO1"]["label"] == "Вент. отв"
    assert new["walls"][1]["openings"][0]["kind"] == "opening"
    text = "\n".join(report["rejected"])
    assert "C9" in text and "C5" in text and "C4" not in text, text
    assert "candidates" in ai.scene_for_model(scene, with_mep=False)


def test_pipes_from_the_cloud():
    # deslab: 13.11.2026, the helper finds the pipes, the model names their systems; a box candidate on the risers is not drawn
    from deslab_bridge.mep import pipe_label_items, pipe_objects
    scene = json.loads(json.dumps(SCENE))
    scene["mep"] = []
    scene["candidates"] = [
        {"id": "C1", "wallId": "W1", "side": "left", "from": 2.6, "to": 2.9, "bottom": 0.0, "top": 2.7, "depth": 0.15,
         "at": {"x": 2.75, "y": 0.2}, "start": {"x": 2.6, "y": 0.1}, "end": {"x": 2.9, "y": 0.1}, "hint": "box or shaft?"}]
    scene["pipes"] = [
        {"id": "P1", "kind": "riser", "diameter": 0.03, "path": [{"x": 2.7, "y": 0.2, "z": 0.0}, {"x": 2.7, "y": 0.2, "z": 2.7}]},
        {"id": "P2", "kind": "run", "diameter": 0.02, "path": [{"x": 2.7, "y": 0.2, "z": 0.35, "joins": "P1"}, {"x": 1.5, "y": 0.2, "z": 0.35}]},
        {"id": "P3", "kind": "run", "diameter": 0.012, "path": [{"x": 4.0, "y": 0.12, "z": 2.0}, {"x": 4.6, "y": 0.12, "z": 2.0}]},
        {"id": "P4", "kind": "riser", "diameter": 0.11, "path": [{"x": 5.0, "y": 3.0, "z": 0.0}, {"x": 5.0, "y": 3.0, "z": 2.7}]}]
    review = {"candidates": [{"id": "C1", "kind": "box", "confidence": 0.8}],
              "pipes": [{"id": "P1", "system": "heating", "confidence": 0.8}, {"id": "P2", "system": "heating", "confidence": 0.7},
                        {"id": "P3", "system": "clutter", "confidence": 0.9}]}
    new, report = ai.apply_review(scene, review)
    systems = {p["id"]: p.get("system") for p in new["pipes"]}
    assert systems == {"P1": "heating", "P2": "heating", "P3": None, "P4": None}, systems
    assert new["mep"] == [], new["mep"]
    assert "P4" in "\n".join(report["rejected"])
    assert len(pipe_objects(new, {})) == 2
    assert [(i, t) for i, t, _ in pipe_label_items(new)] == [("P1", "Стояк отопления")]
    assert "pipes" in ai.scene_for_model(scene, with_mep=False)
    # a box 0.65 m long next to one riser is a box of its own (Nagornaya bathroom corner)
    scene["candidates"][0].update({"from": 2.3, "to": 2.95, "start": {"x": 2.3, "y": 0.1}, "end": {"x": 2.95, "y": 0.1}})
    new, report = ai.apply_review(scene, review)
    assert [m["candidate"] for m in new["mep"]] == ["C1"], new["mep"]
    # deslab: 14.11.2026, a box 10 cm beside the riser (not standing on it) stays
    scene["candidates"][0].update({"from": 2.8, "to": 3.1, "start": {"x": 2.8, "y": 0.1}, "end": {"x": 3.1, "y": 0.1},
                                   "at": {"x": 2.95, "y": 0.175}})
    new, report = ai.apply_review(scene, review)
    assert [m["candidate"] for m in new["mep"]] == ["C1"], (new["mep"], report["rejected"])


def test_two_vent_ducts():
    # deslab: 13.11.2026, Nagornaya bathroom corner (Игорь: «две квадратных трубы, одна широкая, другая узкая»): two
    # candidates named duct are two solid morphs on the plan with their labels, no shaft walls, kept next to a riser
    from deslab_bridge.mep import duct_morphs, shaft_walls
    scene = json.loads(json.dumps(SCENE))
    scene["mep"] = []
    scene["candidates"] = [
        {"id": "C1", "wallId": "W1", "side": "left", "from": 2.6, "to": 2.75, "bottom": 0.0, "top": 2.7, "depth": 0.275,
         "at": {"x": 2.675, "y": 0.2375}, "start": {"x": 2.6, "y": 0.1}, "end": {"x": 2.75, "y": 0.1}, "hint": "riser?"},
        {"id": "C2", "wallId": "W1", "side": "left", "from": 2.78, "to": 3.25, "bottom": 0.0, "top": 2.7, "depth": 0.265,
         "at": {"x": 3.015, "y": 0.2325}, "start": {"x": 2.78, "y": 0.1}, "end": {"x": 3.25, "y": 0.1}, "hint": "box or shaft?"}]
    scene["pipes"] = [{"id": "P1", "kind": "riser", "diameter": 0.04, "path": [{"x": 2.7, "y": 0.42, "z": 0.0}, {"x": 2.7, "y": 0.42, "z": 2.7}]}]
    review = {"candidates": [{"id": "C1", "kind": "duct", "confidence": 0.8}, {"id": "C2", "kind": "duct", "confidence": 0.8}],
              "pipes": [{"id": "P1", "system": "water", "confidence": 0.7}]}
    new, report = ai.apply_review(scene, review)
    ducts = [m for m in new["mep"] if m.get("duct")]
    assert [m["label"] for m in ducts] == ["Воздуховод 150×275", "Воздуховод 470×265"], (ducts, report)
    assert shaft_walls(new) == []
    morphs = duct_morphs(new)
    assert [round(d["size"]["x"], 3) for _, d in morphs] == [0.15, 0.47] and all(d["size"]["z"] == 2.7 for _, d in morphs), morphs
    assert abs(morphs[0][1]["size"]["y"] - 0.275) < 1e-6 and morphs[0][1]["basePoint"]["z"] == 0.0
    # one candidate with a faint seam that the model sees as two ducts is cut there
    scene["candidates"] = [dict(scene["candidates"][0], id="C1", to=3.25, end={"x": 3.25, "y": 0.1}, at={"x": 2.925, "y": 0.2375}, seam=2.75,
                                seamStrength=0.025, seamDepths=[0.275, 0.265])]
    new, report = ai.apply_review(scene, {"candidates": [{"id": "C1", "kind": "duct", "confidence": 0.8, "parts": 2}]})
    ducts = [m for m in new["mep"] if m.get("duct")]
    assert [(m["id"], m["label"]) for m in ducts] == [("VD1a", "Воздуховод 150×275"), ("VD1b", "Воздуховод 500×265")], (ducts, report)
    assert abs(ducts[1]["polygon"][0]["x"] - 2.75) < 1e-6 and abs(ducts[1]["polygon"][2]["y"] - 0.365) < 1e-6, ducts[1]
    # parts 2 without a seam: built whole, and said so
    del scene["candidates"][0]["seam"]
    new, report = ai.apply_review(scene, {"candidates": [{"id": "C1", "kind": "duct", "confidence": 0.8, "parts": 2}]})
    assert [m["id"] for m in new["mep"] if m.get("duct")] == ["VD1"] and "без стыка" in "\n".join(report["rejected"])
    # a duct along a skew wall is a prism over its polygon
    skew = {"mep": [{"kind": "shaft", "duct": True, "id": "VD1", "elevation": 0.0, "height": 2.5,
                     "polygon": [{"x": 0, "y": 0}, {"x": 0.3, "y": 0.1}, {"x": 0.25, "y": 0.25}, {"x": -0.05, "y": 0.15}]}]}
    (_, data), = duct_morphs(skew)
    body = data["body"]
    assert "size" not in data and len(body["vertices"]) == 8 and len(body["polygons"]) == 6, data
    assert body["vertices"][4]["z"] == 2.5 and body["polygons"][1]["vertexIds"] == [4, 5, 6, 7]
    # deslab: 14.11.2026, Nagornaya: the narrow duct named a sewer riser became a pipe of 160 mm; a rectangular candidate is
    # no pipe, it is left out with a question for Игорь
    scene["candidates"] = [dict(scene["candidates"][0], to=2.76, end={"x": 2.76, "y": 0.1}, depth=0.3)]
    new, report = ai.apply_review(scene, {"candidates": [{"id": "C1", "kind": "sewerRiser", "confidence": 0.8}]})
    assert not [m for m in new["mep"] if m.get("candidate") == "C1"], new["mep"]
    assert "это не труба" in "\n".join(report["rejected"]) and "160×300" in report["doubts"][-1], report
    # a round one (11 cm wide, 15 cm out of the wall) still is
    scene["candidates"] = [dict(scene["candidates"][0], to=2.71, end={"x": 2.71, "y": 0.1}, depth=0.15)]
    new, report = ai.apply_review(scene, {"candidates": [{"id": "C1", "kind": "sewerRiser", "confidence": 0.8}]})
    assert [m["id"] for m in new["mep"] if m.get("candidate") == "C1"] == ["K1"], (new["mep"], report)
    # deslab: 14.11.2026, a narrow «duct» over two heating risers one behind the other is those risers; a thin pipe at the
    # seam of a wide duct leaves the duct
    scene["candidates"] = [
        {"id": "C1", "wallId": "W1", "side": "left", "from": 2.6, "to": 2.74, "bottom": 0.0, "top": 2.7, "depth": 0.378,
         "at": {"x": 2.67, "y": 0.289}, "start": {"x": 2.6, "y": 0.1}, "end": {"x": 2.74, "y": 0.1}, "hint": "duct or box?"},
        {"id": "C2", "wallId": "W1", "side": "left", "from": 3.0, "to": 3.45, "bottom": 0.0, "top": 2.7, "depth": 0.3,
         "at": {"x": 3.225, "y": 0.25}, "start": {"x": 3.0, "y": 0.1}, "end": {"x": 3.45, "y": 0.1}, "hint": "duct or box?"}]
    scene["pipes"] = [{"id": f"P{i}", "kind": "riser", "diameter": d, "path": [{"x": x, "y": y, "z": 0.0}, {"x": x, "y": y, "z": 2.7}]}
                      for i, (x, y, d) in enumerate(((2.67, 0.36, 0.02), (2.67, 0.26, 0.026), (3.42, 0.12, 0.019)), 1)]
    new, report = ai.apply_review(scene, {"candidates": [{"id": "C1", "kind": "duct", "confidence": 0.7}, {"id": "C2", "kind": "duct", "confidence": 0.7}],
                                          "pipes": [{"id": "P1", "system": "heating", "confidence": 0.7}, {"id": "P2", "system": "heating", "confidence": 0.7},
                                                    {"id": "P3", "system": "water", "confidence": 0.6}]})
    assert [m["id"] for m in new["mep"] if m.get("duct")] == ["VD2"], (new["mep"], report)
    # the thin pipe in the duct that stays is its seam: not built, no label
    p3 = next(p for p in new["pipes"] if p["id"] == "P3")
    assert not p3.get("system") and not p3.get("label") and "P3" in "\n".join(report["rejected"]), (p3, report)
    assert all(next(p for p in new["pipes"] if p["id"] == i).get("system") == "heating" for i in ("P1", "P2"))


def test_old_wiring():
    # deslab: 14.11.2026, Nagornaya hall (Игорь: «маленький щиток, и остатки старой проводки висят. Это не надо учитывать»):
    # a tall candidate named a duct without a face by height is not built; 12 mm «pipes» away from wet rooms, radiators and
    # heating risers are cables
    scene = json.loads(json.dumps(SCENE))
    scene["mep"] = []
    tall = {"wallId": "W1", "side": "left", "bottom": 0.0, "top": 2.7, "depth": 0.15, "hint": "duct or box?"}
    scene["candidates"] = [
        dict(tall, id="C1", **{"from": 0.4, "to": 0.66}, at={"x": 0.53, "y": 0.175}, start={"x": 0.4, "y": 0.1}, end={"x": 0.66, "y": 0.1},
             face={"flat": 0.017, "cover": 0.33}),
        dict(tall, id="C2", **{"from": 3.0, "to": 3.26}, at={"x": 3.13, "y": 0.175}, start={"x": 3.0, "y": 0.1}, end={"x": 3.26, "y": 0.1},
             face={"flat": 0.004, "cover": 0.97}),
        {"id": "C3", "wallId": "W1", "side": "left", "from": 1.0, "to": 2.0, "bottom": 0.2, "top": 0.7, "depth": 0.1, "hint": "radiator?",
         "at": {"x": 1.5, "y": 0.15}, "start": {"x": 1.0, "y": 0.1}, "end": {"x": 2.0, "y": 0.1}}]
    riser = lambda i, x, y, d: {"id": i, "kind": "riser", "diameter": d, "path": [{"x": x, "y": y, "z": 0.0}, {"x": x, "y": y, "z": 2.7}]}  # noqa: E731
    run = lambda i, a, b, d: {"id": i, "kind": "run", "diameter": d, "path": [{"x": a[0], "y": a[1], "z": 1.6}, {"x": b[0], "y": b[1], "z": 1.6}]}  # noqa: E731
    scene["pipes"] = [riser("P1", 5.5, 3.8, 0.012), run("P2", (5.5, 3.8), (5.9, 3.0), 0.012), riser("P3", 4.5, 1.0, 0.012),
                      run("P4", (1.2, 0.12), (1.2, 0.9), 0.012), riser("P5", 3.0, 3.8, 0.02), run("P6", (3.1, 3.8), (3.5, 3.8), 0.012),
                      run("P7", (0.5, 3.0), (0.5, 3.5), 0.05), run("P8", (0.5, 2.0), (0.5, 2.5), 0.05), riser("P9", 0.3, 3.8, 0.11),
                      run("P10", (4.6, 1.5), (3.6, 1.5), 0.05)]
    review = {"candidates": [{"id": "C1", "kind": "duct", "confidence": 0.6}, {"id": "C2", "kind": "duct", "confidence": 0.6},
                             {"id": "C3", "kind": "radiator", "confidence": 0.8}],
              "pipes": [{"id": i, "system": sy, "confidence": 0.7} for i, sy in
                        (("P1", "heating"), ("P2", "heating"), ("P3", "water"), ("P4", "heating"), ("P5", "heating"), ("P6", "heating"),
                         ("P7", "heating"), ("P8", "sewer"), ("P9", "sewer"), ("P10", "sewer"))],
              "wetZones": [{"kind": "bathroom", "polygon": [{"x": 4, "y": 0.5}, {"x": 5.5, "y": 0.5}, {"x": 5.5, "y": 2}, {"x": 4, "y": 2}]}]}
    new, report = ai.apply_review(scene, review)
    assert [m["candidate"] for m in new["mep"] if m.get("duct")] == ["C2"], (new["mep"], report)
    assert any("провода" in d and "C1" in d for d in report["doubts"]), report["doubts"]
    systems = {p["id"]: p.get("system") for p in new["pipes"]}
    # P7, a heating pipe with no radiator or heating riser by it, and P8, a sewer pipe away from wet rooms and risers, are
    # no pipes either; the sewer riser P9 stands where it stands, the pipe P10 from the bathroom is a pipe
    assert systems == {"P1": None, "P2": None, "P3": "water", "P4": "heating", "P5": "heating", "P6": "heating",
                       "P7": None, "P8": None, "P9": "sewer", "P10": "sewer"}, (systems, report)
    assert not next(p for p in new["pipes"] if p["id"] == "P1").get("label")
    assert sum("кабель" in r for r in report["rejected"]) == 2, report["rejected"]
    assert "P7 (heating, 50 мм)" in report["doubts"][-1] and "P8 (sewer, 50 мм)" in report["doubts"][-1], report["doubts"]
    # the wiring by the Nagornaya entrance: about as wide as it should be, but its front goes in and out by height
    scene["candidates"][1]["face"] = {"flat": 0.064, "cover": 0.78}
    new, _ = ai.apply_review(scene, review)
    assert not [m for m in new["mep"] if m.get("duct")], new["mep"]
    scene["candidates"][1]["face"] = {"flat": 0.021, "cover": 1.0}   # the wide bathroom duct
    # the small old panel the wiring hangs from has no flat front either, a flat panel box is built
    small = {"wallId": "W2", "side": "right", "from": 1.0, "to": 1.26, "bottom": 1.64, "top": 1.92, "depth": 0.18, "hint": "",
             "at": {"x": 5.81, "y": 1.13}, "start": {"x": 5.9, "y": 1.0}, "end": {"x": 5.9, "y": 1.26}}
    scene["candidates"] += [dict(small, id="C4", face={"flat": 0.012, "cover": 0.0}),
                            dict(small, id="C5", **{"from": 2.0, "to": 2.3}, start={"x": 5.9, "y": 2.0}, end={"x": 5.9, "y": 2.3},
                                 face={"flat": 0.002, "cover": 1.0})]
    new, report = ai.apply_review(scene, dict(review, candidates=review["candidates"] + [{"id": "C4", "kind": "panel", "confidence": 0.6},
                                                                                       {"id": "C5", "kind": "panel", "confidence": 0.6}]))
    assert [m["candidate"] for m in new["mep"] if m["kind"] == "panel"] == ["C5"], (new["mep"], report)
    scene["candidates"] = scene["candidates"][:3]
    # a room the model names a kitchen keeps its thin water pipe
    review["rooms"] = [{"id": "R1", "name": "Кухня-гостиная"}]
    review["pipes"][0]["system"] = "water"
    new, report = ai.apply_review(scene, review)
    assert next(p for p in new["pipes"] if p["id"] == "P1").get("system") == "water", report


def test_balcony_door_swing_in_a_thick_wall():
    # Nagornaya: the slab runs to the outer outline, the wall is 0.64 m thick; the rooms tell the flat's side
    scene = {"walls": [{"id": "W3", "start": {"x": 0, "y": 0}, "end": {"x": 6, "y": 0}, "thickness": 0.64, "kind": "exterior",
                        "openings": [{"id": "W3.2", "kind": "balconyDoor", "centerOffset": 5.0, "width": 0.8, "sill": 0.1, "height": 2.6}]}],
             "rooms": [{"id": "R1", "polygon": [{"x": 0, "y": 0.32}, {"x": 6, "y": 0.32}, {"x": 6, "y": 4}, {"x": 0, "y": 4}]}],
             "slabs": [{"polygon": [{"x": -0.3, "y": -0.32}, {"x": 6.3, "y": -0.32}, {"x": 6.3, "y": 4.3}, {"x": -0.3, "y": 4.3}]}]}
    new, report = ai.apply_review(scene, {"openings": [{"id": "W3.2", "swing": {"hinge": "end", "opens": "into"}, "confidence": 0.8}]})
    door = new["walls"][0]["openings"][0]
    assert door.get("hinge") == "end", (door, report)


KV94_DOOR = {
    "walls": [{"id": "W3", "start": {"x": -0.1, "y": 10.0745}, "end": {"x": -0.1, "y": -0.1}, "thickness": 0.2, "kind": "interior",
               "openings": [{"id": "W3.1", "kind": "door", "width": 1.2, "sill": 0.0, "height": 2.1, "centerOffset": 8.7835}]}],
    "rooms": [{"id": "R1", "name": "Помещение 1",
               "polygon": [{"x": 0, "y": 0}, {"x": 11.752, "y": 0}, {"x": 11.752, "y": 9.854}, {"x": 0, "y": 9.854}]}],
    "slabs": [],
}


def test_door_swing_w31():
    # the confirmed kv94 door: hinge at the wall's end, opens out -> reflected True, oSide False
    new, report = ai.apply_review(KV94_DOOR, {"openings": [{"id": "W3.1", "swing": {"hinge": "end", "opens": "out"}, "confidence": 0.8}]})
    door = new["walls"][0]["openings"][0]
    assert (door["reflected"], door["oSide"]) == (True, False), (door, report)
    # the other three combinations flip exactly one flag each
    for hinge, opens, expected in (("start", "out", (False, False)), ("end", "into", (True, True)), ("start", "into", (False, True))):
        new, _ = ai.apply_review(KV94_DOOR, {"openings": [{"id": "W3.1", "swing": {"hinge": hinge, "opens": opens}, "confidence": 0.8}]})
        door = new["walls"][0]["openings"][0]
        assert (door["reflected"], door["oSide"]) == expected, (hinge, opens, door)
    # a wall that is not next to the flat: the side cannot be told, nothing is applied
    far = json.loads(json.dumps(KV94_DOOR))
    far["walls"][0]["start"], far["walls"][0]["end"] = {"x": 50, "y": 10}, {"x": 50, "y": 0}
    new, report = ai.apply_review(far, {"openings": [{"id": "W3.1", "swing": {"hinge": "end", "opens": "out"}, "confidence": 0.8}]})
    assert "reflected" not in new["walls"][0]["openings"][0] and report["rejected"]


def test_risers_in_shaft_need_less_confidence():
    scene = {"walls": [], "rooms": [{"id": "R1", "polygon": [{"x": 0, "y": 0}, {"x": 6, "y": 0}, {"x": 6, "y": 4}, {"x": 0, "y": 4}]}],
             "slabs": [], "mep": [{"kind": "shaft", "id": "SH1", "polygon": [{"x": 0, "y": 0.5}, {"x": 0.6, "y": 0.5}, {"x": 0.6, "y": 1.2}, {"x": 0, "y": 1.2}]}]}
    review = {"mep": [{"kind": "waterRiser", "id": "B1", "at": {"x": 0.3, "y": 0.8}, "confidence": 0.35},
                      {"kind": "radiator", "id": "RD1", "start": {"x": 1, "y": 3.9}, "end": {"x": 2, "y": 3.9}, "confidence": 0.35}]}
    new, report = ai.apply_review(scene, review)
    ids = {m["id"] for m in new["mep"]}
    assert "B1" in ids and "RD1" not in ids, (ids, report)
    assert next(m for m in new["mep"] if m["id"] == "B1")["diameter"] == 0.05


def test_key_never_in_errors():
    text = ai._clean("failed with Bearer SECRET123 at host", "SECRET123")
    assert "SECRET123" not in text and "***" in text


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
