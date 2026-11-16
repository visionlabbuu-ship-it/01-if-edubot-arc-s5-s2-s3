"""Offline tests: skeleton → seed assemble → validate (no live LLM)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.teacher.author import (  # noqa: E402
    assemble_from_seed,
    validate_challenge,
    validate_demo_steps,
)
from modules.teacher.generate import generate_set  # noqa: E402
from modules.teacher.skeletons import (  # noqa: E402
    list_capabilities,
    list_skeletons,
    load_capabilities,
    match_capability_from_topic,
)


def test_capabilities_have_skeletons():
    caps = load_capabilities()
    assert caps.get("version")
    clist = list_capabilities(caps)
    assert len(clist) >= 2
    sk = list_skeletons(caps=caps)
    assert len(sk) >= 6
    for row in sk:
        assert row["id"]
        assert row["capability_id"]
        assert row["demo_skeleton"]


def test_topic_match():
    assert match_capability_from_topic("อยากเรียนลำดับหยิบของ") == "pick_pipeline"
    assert match_capability_from_topic("pick and place storage") == "pick_then_place"


def test_seed_assemble_and_validate():
    out = assemble_from_seed("qs_why_scan", capability_id="pick_pipeline")
    assert out["ok"], out
    ch = out["challenge"]
    assert ch["mode"] == "mcq"
    assert ch["correct_choice"] == "B"
    assert ch["demo_steps"][0]["op"] == "reset_scene"
    assert ch["demo_steps"][1]["op"] == "pick_object"
    # demo must not come from text — locked skeleton
    assert (ch.get("meta") or {}).get("skeleton_id") == "qs_why_scan"
    gate = validate_challenge(ch)
    assert gate["ok"], gate


def test_demo_allowlist_rejects_unknown_op():
    v = validate_demo_steps([{"op": "fly_away"}])
    assert not v["ok"]
    assert v["errors"]


def test_generate_set_offline_seed():
    result = generate_set(
        capability_id="pick_pipeline",
        use_llm=False,
        count=2,
    )
    assert result["ok"], result
    assert result["generated"] == 2
    assert len(result["challenges"]) == 2
    for ch in result["challenges"]:
        assert ch["success"]["mcq_correct"] is True
        assert all(s.get("op") for s in ch["demo_steps"])


def test_generate_from_topic_offline():
    result = generate_set(topic="อยากเรียนเรื่องวางของ storage", use_llm=False)
    assert result["ok"], result
    assert result["capability_id"] == "pick_then_place"
    assert result["generated"] >= 1


if __name__ == "__main__":
    test_capabilities_have_skeletons()
    test_topic_match()
    test_seed_assemble_and_validate()
    test_demo_allowlist_rejects_unknown_op()
    test_generate_set_offline_seed()
    test_generate_from_topic_offline()
    print("OK all author/generate tests")
