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
