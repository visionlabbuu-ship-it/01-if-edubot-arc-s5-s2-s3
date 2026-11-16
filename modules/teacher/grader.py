"""Grade student robot behavior from scene / pose — no LLM."""

from __future__ import annotations

import math
from typing import Any, Optional


def _dist_xy(a: dict, b: dict) -> float:
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))


def _named_target_local(controller, arm_id: str, named: str) -> Optional[dict]:
    if hasattr(controller, "_get_named_target"):
        t = controller._get_named_target(arm_id, named)
        if t:
            return {"x": float(t["x"]), "y": float(t["y"]), "z": float(t.get("hover_z", t.get("z", 0.0)))}
    return None


def _named_target_workspace(controller, arm_id: str, named: str) -> Optional[dict]:
    local = _named_target_local(controller, arm_id, named)
    if not local:
        return None
    try:
        from arc_runtime.sim_world import arm_local_to_workspace

        ws = arm_local_to_workspace(arm_id, local["x"], local["y"])
        return {"x": float(ws["x"]), "y": float(ws["y"])}
    except Exception:
        # fallback: treat local as workspace (real-mode single frame)
        return {"x": local["x"], "y": local["y"]}


def _find_object(scene: dict, object_id: str) -> Optional[dict]:
    for obj in scene.get("objects") or []:
        if obj.get("id") == object_id:
            return obj
    held = scene.get("held")
    if held and held.get("id") == object_id:
        return held
    return None


def _arm_pose_xy(controller, arm_id: str) -> Optional[dict]:
    payload = controller.get_arm_pose(arm_id)
    if not payload.get("ok"):
        return None
    pose = payload.get("pose") or {}
    return {"x": float(pose["x"]), "y": float(pose["y"]), "z": float(pose.get("z", 0.0))}


def grade_challenge(
    controller,
    challenge: dict,
    *,
    waypoint_log: Optional[list] = None,
    procedure_log: Optional[list] = None,
) -> dict:
    """Evaluate success conditions against live fleet/scene state (or procedure sequence)."""
    success = challenge.get("success") or {}
    scene = controller.scene_state() if hasattr(controller, "scene_state") else {}
    checks: list[dict] = []
    all_ok = True

    # held: null means nothing held
    if "held" in success:
        want = success["held"]
        held = scene.get("held")
        if want is None:
            ok = held is None
            checks.append({"rule": "held_none", "ok": ok, "actual": (held or {}).get("id") if held else None})
        else:
            ok = bool(held) and held.get("id") == want
            checks.append({"rule": "held_id", "ok": ok, "expected": want, "actual": (held or {}).get("id") if held else None})
        all_ok = all_ok and ok

    if "held_id" in success:
        want = success["held_id"]
        held = scene.get("held")
        ok = bool(held) and held.get("id") == want
        checks.append({"rule": "held_id", "ok": ok, "expected": want, "actual": (held or {}).get("id") if held else None})
        all_ok = all_ok and ok

    if "held_by" in success:
        want = success["held_by"]
        actual = scene.get("held_by")
        ok = actual == want
        checks.append({"rule": "held_by", "ok": ok, "expected": want, "actual": actual})
        all_ok = all_ok and ok

    if "pose_near" in success:
        rule = success["pose_near"]
        arm = rule.get("arm") or challenge.get("arm") or "Arm1"
        tol = float(rule.get("tol_mm", 25.0))
        pose = _arm_pose_xy(controller, arm)
        target = None
        if rule.get("named"):
            target = _named_target_local(controller, arm, rule["named"])
        elif "x" in rule and "y" in rule:
            target = {"x": float(rule["x"]), "y": float(rule["y"])}
        if pose is None or target is None:
            ok = False
            d = None
        else:
            d = _dist_xy(pose, target)
            ok = d <= tol
        checks.append(
            {
                "rule": "pose_near",
                "ok": ok,
                "arm": arm,
                "named": rule.get("named"),
                "distance_mm": d,
                "tol_mm": tol,
                "pose": pose,
                "target": target,
            }
        )
        all_ok = all_ok and ok

    if "object_near" in success:
        rule = success["object_near"]
        oid = rule.get("id")
        arm = rule.get("arm") or challenge.get("arm") or "Arm1"
        tol = float(rule.get("tol_mm", 40.0))
        obj = _find_object(scene, oid) if oid else None
        target = None
        if rule.get("named"):
            target = _named_target_workspace(controller, arm, rule["named"])
        elif "x" in rule and "y" in rule:
            target = {"x": float(rule["x"]), "y": float(rule["y"])}
        if not obj or not target:
            ok = False
            d = None
            rp = None
        else:
            rp = obj.get("robot_plane") or {}
            d = _dist_xy(rp, target)
            ok = d <= tol
        checks.append(
            {
                "rule": "object_near",
                "ok": ok,
                "object_id": oid,
                "named": rule.get("named"),
                "distance_mm": d,
                "tol_mm": tol,
                "robot_plane": rp,
                "target": target,
            }
        )
        all_ok = all_ok and ok

    if "waypoints_order" in success:
        rule = success["waypoints_order"]
        expected_wps = challenge.get("waypoints") or []
        tol = float(rule.get("tol_mm", 40.0))
        log = list(waypoint_log or [])
        ok = True
        detail = []
        if len(log) < len(expected_wps):
            ok = False
        for i, exp in enumerate(expected_wps):
            if i >= len(log):
                detail.append({"i": i, "ok": False, "missing": True})
                ok = False
                continue
            got = log[i]
            d = _dist_xy(got, exp)
            step_ok = d <= tol
            detail.append({"i": i, "ok": step_ok, "distance_mm": d, "expected": exp, "got": got})
            ok = ok and step_ok
        checks.append(
            {
                "rule": "waypoints_order",
                "ok": ok,
                "expected_count": len(expected_wps),
                "logged_count": len(log),
                "steps": detail,
            }
        )
        all_ok = all_ok and ok

    # โจทย์เรียงลำดับขั้นตอน (procedure) — เทียบ student sequence กับ correct_order
    if success.get("procedure_order"):
        expected = [str(x) for x in (challenge.get("correct_order") or [])]
        got = [str(x) for x in (procedure_log or [])]
        ok = got == expected
        detail = []
        for i, exp_id in enumerate(expected):
            g = got[i] if i < len(got) else None
            step_ok = g == exp_id
            detail.append({"i": i, "ok": step_ok, "expected": exp_id, "got": g})
        if len(got) != len(expected):
            ok = False
        checks.append(
            {
                "rule": "procedure_order",
                "ok": ok,
                "expected": expected,
                "got": got,
                "expected_count": len(expected),
                "got_count": len(got),
                "steps": detail,
                "prefix_match": got == expected[: len(got)] if got else len(expected) == 0,
            }
        )
        all_ok = all_ok and ok

    # MCQ — ตรวจจาก selected_choice ที่ engine เก็บไว้ (ผ่าน procedure_log[0] หรือ selected_choice kw)
    if success.get("mcq_correct"):
        want = str(challenge.get("correct_choice") or "").strip()
        # ใช้ procedure_log เป็น [selected_id] สำหรับ mcq ด้วย (engine ส่งเข้า)
        got = ""
        if procedure_log:
            got = str(procedure_log[-1]).strip()
        ok = bool(want) and got == want
        checks.append(
            {
                "rule": "mcq_correct",
                "ok": ok,
                "expected": want,
                "got": got or None,
            }
        )
        all_ok = all_ok and ok

    hint = None if all_ok else (challenge.get("hint_fail") or "ยังไม่ตรงเงื่อนไข — ลองดูตัวอย่างหรือทำซ้ำ")
    return {
        "ok": True,
        "passed": bool(all_ok),
        "score": 1.0 if all_ok else 0.0,
        "checks": checks,
        "hint": hint,
        "challenge_id": challenge.get("id"),
    }
