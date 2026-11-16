"""Chat router — SIM / Scene Editor natural-language entrypoint.

Default path (ARC_SIM_LLM=1): Jetson LiteLLM (`local-llama`) + tools.
Keyword matching is only a fallback when LLM is disabled or unreachable.

Returns always:
  {"ok": bool, "reply": str, "action": str, "data": {...}, "refresh": bool}
"""

from __future__ import annotations

import re

from . import config

_ARMS = ("arm1", "arm2")
_COLORS = ("green", "red", "blue")
_NAMED_TARGETS = (
    "home",
    "storage_zone",
    "handoff_zone",
    "drop_zone",
    "storage",
    "handoff",
    "drop",
)

# Thai / casual aliases — used only by keyword fallback path
_THAI_REPLACEMENTS = (
    (r"แขน\s*1|arm\s*1|แขนหนึ่ง", "arm1"),
    (r"แขน\s*2|arm\s*2|แขนสอง", "arm2"),
    (r"กลับบ้าน|กลับฐาน|กลับที่เดิม|\bhome\b", "home"),
    (r"ไป\s*park|\bparking\b|\bpark\b", "park"),
    (r"โซนเก็บ|ที่เก็บ|\bstorage_zone\b|\bstorage\b", "storage_zone"),
    (r"โซนส่ง|\bhandoff_zone\b|\bhandoff\b", "handoff_zone"),
    (r"โซนทิ้ง|โซนดรอป|\bdrop_zone\b|\bdrop\b", "drop_zone"),
    (
        r"ทดสอบ(?:การ)?เชื่อมต่อ(?:แขนกล|แขน|หุ่น)?|"
        r"เชื่อมต่อ(?:แขนกล|แขน|หุ่น)?|"
        r"เช็คการเชื่อมต่อ|ตรวจสอบการเชื่อมต่อ|"
        r"\bconnect(?:ion)?\s*(?:test|arms?|robots?)?\b|"
        r"\btest\s+connect(?:ion)?\b",
        "connect",
    ),
    (r"สแกน|\bscan\b", "scan"),
    (r"หยิบ|\bpick\b", "pick"),
    (r"วาง|\bplace\b", "place"),
    (r"ย้าย|ไปที่|\bmove\b|go\s+to", "move"),
    (r"เพิ่ม|สร้าง|\bspawn\b|\badd\b", "add"),
    (r"ลบ|\bremove\b|\bdelete\b", "remove"),
    (r"รีเซ็ต|\breset\b", "reset"),
    (r"รายการวัตถุ|รายการฉาก|\blist\s+objects\b|\bscene\b", "list"),
    (r"เขียว|\bgreen\b", "green"),
    (r"แดง|\bred\b", "red"),
    (r"น้ำเงิน|\bblue\b", "blue"),
)


def _reply(ok, reply, action, data=None, refresh=False):
    return {
        "ok": bool(ok),
        "reply": reply,
        "action": action,
        "data": data or {},
        "refresh": refresh,
    }


def _normalize(message: str) -> str:
    text = message.strip().lower()
    for pattern, replacement in _THAI_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _find_arm(text: str, default="Arm1") -> str:
    for arm in _ARMS:
        if arm in text:
            return "Arm1" if arm == "arm1" else "Arm2"
    if re.search(r"(?<!\d)1(?!\d)", text) and not re.search(r"(?<!\d)2(?!\d)", text):
        return "Arm1"
    if re.search(r"(?<!\d)2(?!\d)", text):
        return "Arm2"
    return default


def _find_color(text: str):
    for c in _COLORS:
        if c in text:
            return c
    return None


def _find_named_target(text: str):
    for t in ("storage_zone", "handoff_zone", "drop_zone", "home"):
        if t in text:
            return t
    if "storage" in text:
        return "storage_zone"
    if "handoff" in text:
        return "handoff_zone"
    if "drop" in text:
        return "drop_zone"
    if "park" in text or "parking" in text:
        return "park"
    return None


def _find_xy(text: str):
    xm = re.search(r"x\s*=\s*(-?\d+(?:\.\d+)?)", text)
    ym = re.search(r"y\s*=\s*(-?\d+(?:\.\d+)?)", text)
    if xm and ym:
        return float(xm.group(1)), float(ym.group(1))
    return None


def _find_object_id(text: str, controller):
    m = re.search(r"obj-\d+", text)
    if m:
        return m.group(0)
    return None


def _scene_objects(controller):
    state = controller.scene_state()
    if not state.get("ok"):
        return None, state
    return state.get("objects", []), state


def _format_arm_connection(controller) -> dict:
    try:
        controller.connect_all()
    except Exception as exc:  # noqa: BLE001
        return _reply(False, f"เชื่อมต่อแขนไม่สำเร็จ: {exc}", "connect")

    payload = controller.list_arms()
    arms = payload.get("arms") or []
    lines = []
    for arm in arms:
        arm_id = arm.get("robot_id") or arm.get("id") or "?"
        connected = bool(arm.get("connected"))
        mode = arm.get("robot_mode_name") or arm.get("mode") or "-"
        mark = "connected" if connected else "disconnected"
        lines.append(f"- {arm_id}: {mark} (mode={mode})")

    total = payload.get("total_arms", len(arms))
    connected_n = payload.get(
        "connected_arms", sum(1 for a in arms if a.get("connected"))
    )
    mode_label = "SIM" if getattr(controller, "world", None) is not None else "REAL"
    reply = (
        f"[{mode_label}] ทดสอบการเชื่อมต่อแขนกล — "
        f"{connected_n}/{total} แขนพร้อม\n"
        + ("\n".join(lines) if lines else "(ไม่มีแขนใน fleet)")
    )
    if mode_label == "SIM":
        reply += "\n(โหมดจำลอง — ไม่ได้ต่อ MG400 จริง)"
    ok = connected_n > 0 or total == 0
    return _reply(ok, reply, "connect", payload)


def _handle_keyword(controller, message: str) -> dict:
    """Legacy keyword matcher — used only when SIM LLM is off or as last resort."""
    text = _normalize(message)
    if not text:
        return _reply(
            False, "Say something like 'scan' or 'pick Arm1 at obj-1'.", "noop"
        )

    if (
        text.startswith("connect")
        or "connect" in text
        or text in ("status", "arms", "list arms", "arm status")
        or text.startswith("status")
        or (re.search(r"\barms?\b", text) and re.search(r"\b(list|status|test)\b", text))
    ):
        return _format_arm_connection(controller)

    if text.startswith("reset") or text == "reset":
        result = controller.reset_scene()
        if result.get("ok"):
            return _reply(True, "Scene reset to defaults.", "reset", result, refresh=True)
        return _reply(False, result.get("error", "Reset failed."), "reset", result)

    if text in ("list", "state", "scene") or text.startswith("list") or text.startswith(
        "show"
    ):
        objs, state = _scene_objects(controller)
        if objs is None:
            return _reply(False, state.get("error", "Scene unavailable."), "list", state)
        held = state.get("held")
        lines = [
            f"{o['id']} ({o['color']}) at x={o['robot_plane']['x']:.1f}, y={o['robot_plane']['y']:.1f}"
            for o in objs
        ]
        reply = (
            f"{len(objs)} object(s):\n" + "\n".join(lines) if lines else "Scene is empty."
        )
        if held:
            reply += f"\nHeld: {held['id']} ({held['color']})"
        return _reply(True, reply, "list", state)

    if text.startswith("add") or text.startswith("spawn"):
        color = _find_color(text) or "green"
        xy = _find_xy(text)
        kwargs = {"color": color}
        if xy:
            kwargs["x"], kwargs["y"] = xy
        result = controller.spawn_object(**kwargs)
        if result.get("ok"):
            obj = result["object"]
            return _reply(
                True, f"Spawned {obj['id']} ({obj['color']}).", "spawn", result, refresh=True
            )
        return _reply(False, result.get("error", "Spawn failed."), "spawn", result)

    if text.startswith("remove") or text.startswith("delete"):
        obj_id = _find_object_id(text, controller)
        if not obj_id:
            return _reply(False, "Which object? e.g. 'remove obj-3'.", "remove")
        result = controller.remove_object(obj_id)
        if result.get("ok"):
            return _reply(True, f"Removed {obj_id}.", "remove", result, refresh=True)
        return _reply(False, result.get("error", "Remove failed."), "remove", result)

    if "scan" in text:
        color = _find_color(text) or "any"
        try:
            payload = controller.camera.scan_workspace(camera_id="Cam1", color=color)
        except Exception as exc:  # noqa: BLE001
            return _reply(False, f"Scan error: {exc}", "scan")
        count = payload.get("object_count", 0)
        label = "" if color == "any" else f" {color}"
        return _reply(
            payload.get("ok", False), f"Scan found {count}{label} object(s).", "scan", payload
        )

    if text.startswith("pick") or re.search(r"\bpick\b", text):
        arm = _find_arm(text)
        xy = _find_xy(text)
        if not xy:
            obj_id = _find_object_id(text, controller)
            if obj_id:
                obj = None
                objs, _ = _scene_objects(controller)
                for o in objs or []:
                    if o["id"] == obj_id:
                        obj = o
                        break
                if obj is None:
                    return _reply(False, f"{obj_id} not in scene.", "pick")
                xy = (obj["robot_plane"]["x"], obj["robot_plane"]["y"])
        if not xy:
            return _reply(
                False,
                "Pick where? e.g. 'pick Arm1 at obj-1' or 'pick Arm1 at x=158, y=-20'.",
                "pick",
            )
        result = controller.pick_at(arm, xy[0], xy[1])
        ok = result.get("ok", False)
        return _reply(
            ok,
            f"{arm} pick at ({xy[0]:.0f}, {xy[1]:.0f}): {'done' if ok else result.get('error', 'failed')}.",
            "pick",
            result,
            refresh=True,
        )

    if text.startswith("place") or re.search(r"\bplace\b", text):
        arm = _find_arm(text)
        xy = _find_xy(text)
        if xy:
            result = controller.place_at(arm, xy[0], xy[1])
            ok = result.get("ok", False)
            return _reply(
                ok,
                f"{arm} place at ({xy[0]:.0f}, {xy[1]:.0f}): {'done' if ok else result.get('error', 'failed')}.",
                "place",
                result,
                refresh=True,
            )
        target = _find_named_target(text)
        if target and target != "park":
            result = controller.place_to_named_target(arm, target)
            ok = result.get("ok", False)
            return _reply(
                ok,
                f"{arm} place at {target}: {'done' if ok else result.get('error', 'failed')}.",
                "place",
                result,
                refresh=True,
            )
        return _reply(False, "Place where? e.g. 'place Arm1 at storage_zone'.", "place")

    if text.startswith("park") or re.search(r"\bpark(?:ing)?\b", text):
        arm = _find_arm(text)
        park_target = "storage_zone" if arm == "Arm1" else "drop_zone"
        result = controller.move_to_named_pose(arm, park_target, "hover")
        ok = result.get("ok", False)
        pose = (result.get("target_pose") or {}) if isinstance(result, dict) else {}
        if ok:
            reply = (
                f"{arm} park สำเร็จ — ย้ายไป {park_target} "
                f"ที่ ({pose.get('x', '?')}, {pose.get('y', '?')}, {pose.get('z', '?')})"
            )
        else:
            reply = (
                f"{arm} park ไม่ได้ — "
                f"{result.get('error', 'failed') if isinstance(result, dict) else result}"
            )
        return _reply(ok, reply, "park", result, refresh=True)

    if text.startswith("home") or re.search(r"\bhome\b", text):
        arm = _find_arm(text)
        result = controller.home_arm(arm)
        ok = result.get("ok", False)
        if ok:
            reply = f"{arm} ไป home สำเร็จ"
        else:
            err = result.get("error", "failed") if isinstance(result, dict) else str(result)
            details = result.get("details") if isinstance(result, dict) else None
            if isinstance(details, dict) and details.get("reason"):
                err = details.get("reason")
            elif isinstance(result, dict) and result.get("reason"):
                err = result.get("reason")
            reply = f"{arm} ไป home ไม่ได้ — {err}"
        return _reply(ok, reply, "home", result, refresh=True)

    if text.startswith("move") or text.startswith("go") or re.search(r"\bmove\b", text):
        arm = _find_arm(text)
        target = _find_named_target(text)
        if not target or target == "park":
            if target == "park":
                park_target = "storage_zone" if arm == "Arm1" else "drop_zone"
                result = controller.move_to_named_pose(arm, park_target, "hover")
                ok = result.get("ok", False)
                return _reply(
                    ok,
                    f"{arm} move to {park_target}: {'done' if ok else result.get('error', 'failed')}.",
                    "move",
                    result,
                    refresh=True,
                )
            return _reply(
                False,
                "Move where? e.g. 'move Arm1 to home' หรือ 'เอาแขน 1 ไป park'.",
                "move",
            )
        phase = "surface" if "surface" in text else "hover"
        result = controller.move_to_named_pose(arm, target, phase)
        ok = result.get("ok", False)
        return _reply(
            ok,
            f"{arm} move to {target} ({phase}): {'done' if ok else result.get('error', 'failed')}.",
            "move",
            result,
            refresh=True,
        )

    return _reply(
        False,
        "Unknown command (keyword fallback). ตั้ง ARC_SIM_LLM=1 เพื่อใช้ LLM "
        f"ที่ {config.SIM_LLM_BASE_URL} หรือลอง: scan, home Arm1, เอาแขน 1 ไป park.",
        "unknown",
    )


def _is_deterministic_primitive(message: str) -> bool:
    """คำสั่ง pick/place/reset ชัดๆ — รัน keyword ตรงๆ ไม่ผ่าน LLM

    กัน LLM แค่ move ไปปลายทางแล้วไม่เรียก place → วัตถุยังติดปลาย / หลุดจุดวาง
    """
    text = _normalize(message)
    if not text:
        return False
    # place / วาง + ปลายทาง (named หรือ x,y)
    if text.startswith("place") or re.search(r"\bplace\b", text):
        if _find_xy(text) or (_find_named_target(text) not in (None, "park")):
            return True
    # pick / หยิบ + เป้า (obj-id หรือ x,y)
    if text.startswith("pick") or re.search(r"\bpick\b", text):
        if _find_xy(text) or re.search(r"obj-\d+", message, re.I):
            return True
    if text.startswith("reset") or text == "reset":
        return True
    return False


def handle_chat(controller, message: str) -> dict:
    """Primary entry: LLM-first when enabled; keyword only as fallback.

    ยกเว้น pick/place/reset ที่ระบุเป้าชัด — ใช้ keyword primitive ตรงๆ
    เพื่อให้ place ปล่อยวัตถุตรงปลายแขนเสมอ (ไม่ให้ LLM แค่ move)
    """
    text = (message or "").strip()
    if not text:
        return _reply(False, "ข้อความว่าง", "noop")

    # Primitive ชัดเจน: อย่าให้ LLM ข้าม place
    if _is_deterministic_primitive(text):
        result = _handle_keyword(controller, text)
        if result.get("action") != "unknown":
            result["data"] = {**(result.get("data") or {}), "via": "keyword_primitive"}
            return result

    # ---- LLM is the main path for SIM chat ----
    if config.SIM_LLM_ENABLED:
        try:
            from .sim_llm_chat import handle_llm_chat

            result = handle_llm_chat(controller, text)
            # If gateway hard-fails with empty/unknown, still surface the error
            # rather than silently keyword-matching (that felt like "จับคำ").
            return result
        except Exception as exc:  # noqa: BLE001
            # Network/import crash only → optional keyword rescue
            fallback = _handle_keyword(controller, text)
            if fallback.get("action") != "unknown":
                fallback["reply"] = (
                    f"(LLM ล้มเหลว: {exc} — ใช้ keyword fallback)\n{fallback['reply']}"
                )
                fallback["data"] = {
                    **(fallback.get("data") or {}),
                    "llm_error": str(exc),
                    "via": "keyword_fallback",
                }
                return fallback
            return _reply(
                False,
                f"SIM LLM ล้มเหลว: {exc}. ตรวจ gateway {config.SIM_LLM_BASE_URL}",
                "llm_error",
                {"error": str(exc)},
            )

    return _handle_keyword(controller, text)
