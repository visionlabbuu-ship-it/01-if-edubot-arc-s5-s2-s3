"""HTTP helpers for /api/teacher/* — catalog + author/generate (sim)."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from .engine import get_engine


def handle_teacher_get(controller, route: str, query: Optional[dict] = None) -> Tuple[dict, int]:
    query = query or {}
    student_id = str(query.get("student_id") or "default")
    eng = get_engine(controller, student_id=student_id)

    if route == "/api/teacher/lock":
        return eng.lock_info(), 200

    if route == "/api/teacher/capabilities":
        from .skeletons import author_meta, list_capabilities

        caps = eng.capabilities
        return {
            "ok": True,
            "version": (caps or {}).get("version"),
            "runtime": (caps or {}).get("runtime"),
            "author": author_meta(caps).get("author"),
            "capabilities": list_capabilities(caps),
            "allowed_demo_ops": list((caps or {}).get("allowed_demo_ops") or []),
            "out_of_scope": list((caps or {}).get("out_of_scope_sim_v1") or []),
        }, 200

    if route == "/api/teacher/skeletons":
        from .skeletons import list_skeletons

        capability_id = query.get("capability_id") or query.get("capability")
        level = query.get("level")
        level_i = int(level) if level not in (None, "") else None
        return {
            "ok": True,
            "skeletons": list_skeletons(
                capability_id=capability_id or None,
                level=level_i,
                caps=eng.capabilities,
            ),
        }, 200

    if route == "/api/teacher/catalog":
        level = query.get("level")
        level_i = int(level) if level not in (None, "") else None
        lock = eng.lock_info()
        return {
            "ok": True,
            "module": "UC4_robot_teacher",
            "version": lock.get("version"),
            "runtime": lock.get("runtime"),
            "llm": False,
            "challenges": eng.list_challenges(level=level_i),
        }, 200

    if route == "/api/teacher/status":
        return eng.status(), 200

    if route == "/api/teacher/actions":
        return eng.student_actions(), 200

    return {"ok": False, "error": f"Unknown teacher route {route}"}, 404


def handle_teacher_post(controller, route: str, body: Optional[dict] = None) -> Tuple[dict, int]:
    body = body or {}
    student_id = str(body.get("student_id") or "default")
    eng = get_engine(controller, student_id=student_id)

    if route == "/api/teacher/start":
        return eng.start(body.get("challenge_id")), 200

    if route == "/api/teacher/demo":
        return eng.demo(), 200

    if route == "/api/teacher/check":
        return eng.check(), 200

    if route == "/api/teacher/next":
        return eng.next_challenge(), 200

    if route == "/api/teacher/reset":
        return eng.reset_session(), 200

    if route == "/api/teacher/act":
        action_id = body.get("action_id") or body.get("action")
        if not action_id:
            return {"ok": False, "error": "Require action_id"}, 400
        return eng.student_act(str(action_id)), 200

    if route == "/api/teacher/waypoint":
        arm = body.get("arm") or "Arm1"
        if "x" not in body or "y" not in body:
            return {"ok": False, "error": "Require x, y"}, 400
        return eng.record_waypoint(
            arm, float(body["x"]), float(body["y"]), float(body.get("z", 0.0))
        ), 200

    # ---- tutor chat (คุย → ออกโจทย์) ----
    if route == "/api/teacher/chat":
        message = body.get("message") or body.get("text") or body.get("content")
        if not message or not str(message).strip():
            return {"ok": False, "error": "Require message"}, 400
        use_llm = body.get("use_llm")
        if use_llm is not None:
            use_llm = bool(use_llm)
        count = body.get("count")
        if count is not None:
            count = int(count)
        return eng.chat(str(message), use_llm=use_llm, count=count), 200

    # ---- author / generate ----
    if route == "/api/teacher/generate":
        # use_llm: null → env TEACHER_LLM / SIM LLM; false → seed only
        use_llm = body.get("use_llm")
        if use_llm is not None:
            use_llm = bool(use_llm)
        skeleton_ids = body.get("skeleton_ids") or body.get("skeletons")
        if isinstance(skeleton_ids, str):
            skeleton_ids = [skeleton_ids]
        count = body.get("count")
        if count is not None:
            count = int(count)
        start_first = body.get("start_first")
        if start_first is None:
            start_first = True
        return eng.generate_set(
            capability_id=body.get("capability_id") or body.get("capability"),
            topic=body.get("topic"),
            skeleton_ids=skeleton_ids,
            count=count,
            use_llm=use_llm,
            start_first=bool(start_first),
        ), 200

    if route == "/api/teacher/load_set":
        challenges = body.get("challenges")
        if not isinstance(challenges, list) or not challenges:
            return {"ok": False, "error": "Require challenges: [...]"}, 400
        return eng.load_challenge_set(
            challenges,
            meta=body.get("meta"),
            start_first=bool(body.get("start_first", False)),
        ), 200

    if route == "/api/teacher/validate":
        from .author import validate_challenge

        ch = body.get("challenge") or body
        if not isinstance(ch, dict) or not ch.get("id") and not ch.get("question_th"):
            # allow bare text validation
            from .author import validate_mcq_text

            return validate_mcq_text(ch if isinstance(ch, dict) else {}), 200
        return validate_challenge(ch, eng.capabilities), 200

    return {"ok": False, "error": f"Unknown teacher route {route}"}, 404
