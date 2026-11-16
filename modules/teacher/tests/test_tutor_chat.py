"""Tutor chat intent + offline generate path (no live server)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.teacher.engine import TeacherEngine  # noqa: E402
from modules.teacher.tutor import detect_intent  # noqa: E402


def test_detect_generate_from_want_learn():
    intent = detect_intent("อยากเรียนลำดับหยิบของ")
    assert intent["intent"] == "generate"
    assert intent["capability_id"] == "pick_pipeline"


def test_detect_list():
    intent = detect_intent("มีหัวข้ออะไรบ้าง")
    assert intent["intent"] == "list"


def test_engine_chat_generates_offline():
    ctrl = MagicMock()
    ctrl.scene_state.return_value = {"objects": [], "held": None}
    ctrl.reset_scene.return_value = {"ok": True}
    ctrl.get_robot.return_value = None
    eng = TeacherEngine(ctrl, student_id="test_tutor")
    res = eng.chat("อยากเรียนลำดับหยิบของ", use_llm=False, count=2)
    assert res["ok"]
    assert res.get("switched_to_lesson") is True
    assert res.get("generate", {}).get("ok")
    assert res["status"]["session_challenge_count"] == 2
    assert res["status"]["challenge_id"]
    assert "สร้าง" in (res.get("reply") or "") or res.get("generate")


if __name__ == "__main__":
    test_detect_generate_from_want_learn()
    test_detect_list()
    test_engine_chat_generates_offline()
    print("OK tutor chat tests")
