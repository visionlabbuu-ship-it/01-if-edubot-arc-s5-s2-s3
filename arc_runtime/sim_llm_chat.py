"""Sim chat via OpenAI-compatible LLM (Jetson LiteLLM).

Sim web UI posts to /api/sim/chat. When ARC_SIM_LLM is on (default), unknown
or free-form messages go through this module:

  Web → sim_server → sim_llm_chat → Jetson:4000 LiteLLM
                                 → tool calls on FleetController (SimRobot)
  markers → Jetson:9100 sidecar (collection identity; not chat headers)

Keyword routing in chat_router remains a fast path / fallback.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

import requests

from . import config
from .jetson_sidecar import CollectionRun, current_run

_SYSTEM = """คุณคือผู้ช่วยควบคุมระบบหุ่นยนต์ Dobot MG400 สองแขนในโหมด SIMULATION (SimRobot)

กฎสำคัญ:
- ไม่มีแขนจริง — ทุกคำสั่งรันบน WorldModel / SimRobot เท่านั้น
- ตอบภาษาไทย ชัดเจน อธิบายเหตุผลสั้นๆ เมื่อมี guardrail / collision
- ใช้ tools เมื่อต้องขยับแขน / สแกน / ดูสถานะ / จัดการฉาก — อย่าเดาผลลัพธ์
- ชื่อแขน: Arm1 (ซ้าย), Arm2 (ขวา) — ทั้งคู่ใช้ได้ใน SIM เสมอ ห้ามบอกว่า Arm2 ไม่มี/ไม่ได้เปิดโดยไม่เรียก list_arms ก่อน
- วัตถุ: obj-1, obj-2, … — ดู list_scene/scan ก่อนหยิบ อย่าเดา block/ระยะ
- ถ้าผู้ใช้ระบุแขน (เช่น Arm2 หยิบเขียวใน 2,2) ให้ใช้แขนนั้น + pick_object ตาม id ที่ match สี/block — อย่าเปลี่ยนเป็น Arm1 เอง
- ถ้า pick ล้มเหลวเพราะ max_safe_r / reach ให้รายงาน error จาก tool จริง อย่าสมมติ
- จุดชื่อ (named targets):
  • home = ท่า home ของแขน (มักอยู่ใน shared zone — อาจชนกันถ้าอีกแขนยังอยู่)
  • storage_zone = โซนจอด/park ของ Arm1 (อยู่นอก shared)
  • drop_zone = โซนจอด/park ของ Arm2
  • handoff_zone = โซนส่งของ
- "park" ≠ "home":
  • ผู้ใช้พูด park / จอด / หลบ → ใช้ park_arm (Arm1→storage_zone, Arm2→drop_zone)
  • ผู้ใช้พูด home / กลับบ้าน / กลับฐาน → ใช้ home_arm
- ก่อนสั่ง home ถ้าอีกแขนอาจอยู่ shared zone ให้ park แขนนั้นก่อน หรืออธิบายว่าทำไมบล็อก
- ถ้าแค่ทักทาย/ถามทั่วไป ตอบได้โดยไม่ต้องเรียก tool
"""

_MAX_TOOL_ROUNDS = 6
_TIMEOUT_S = 120


def _tools_schema() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "list_arms",
                "description": "เชื่อมต่อและสรุปสถานะแขน (sim connect test)",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_scene",
                "description": "สรุปวัตถุในฉากจำลอง (objects, held, zones)",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "scan",
                "description": "สแกน workspace จาก world model (sim camera)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "color": {
                            "type": "string",
                            "description": "any | green | red | blue",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "home_arm",
                "description": "พาแขนกลับ home pose (ไม่ใช่ park). อาจถูก guardrail บล็อกถ้าอีกแขนอยู่ใน shared zone",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_id": {
                            "type": "string",
                            "description": "Arm1 หรือ Arm2",
                        },
                    },
                    "required": ["robot_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "park_arm",
                "description": (
                    "จอด/หลบแขนออกจาก shared zone: "
                    "Arm1 → storage_zone, Arm2 → drop_zone. "
                    "ใช้เมื่อผู้ใช้พูด park / จอด / หลบ / ไปที่เก็บ"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_id": {
                            "type": "string",
                            "description": "Arm1 หรือ Arm2",
                        },
                    },
                    "required": ["robot_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "move_to_named",
                "description": "ย้ายแขนไป named target (home/storage_zone/drop_zone/handoff_zone)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_id": {"type": "string"},
                        "destination": {"type": "string"},
                        "phase": {
                            "type": "string",
                            "description": "hover หรือ surface",
                        },
                    },
                    "required": ["robot_id", "destination"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "pick_at",
                "description": "หยิบที่พิกัด x,y (mm) บน robot plane",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_id": {"type": "string"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    },
                    "required": ["robot_id", "x", "y"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "pick_object",
                "description": "หยิบวัตถุตาม id ในฉาก เช่น obj-1",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_id": {"type": "string"},
                        "object_id": {"type": "string"},
                    },
                    "required": ["robot_id", "object_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "place_at",
                "description": "วางที่พิกัด x,y",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_id": {"type": "string"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    },
                    "required": ["robot_id", "x", "y"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "place_named",
                "description": "วางที่ named destination",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "robot_id": {"type": "string"},
                        "destination": {"type": "string"},
                    },
                    "required": ["robot_id", "destination"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "spawn_object",
                "description": "เพิ่มวัตถุสีในฉาก sim",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "color": {"type": "string"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "remove_object",
                "description": "ลบวัตถุตาม id",
                "parameters": {
                    "type": "object",
                    "properties": {"object_id": {"type": "string"}},
                    "required": ["object_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "reset_scene",
                "description": "รีเซ็ตฉากกลับค่าเริ่มต้น",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _run_tool(controller, name: str, args: dict) -> tuple[str, bool]:
    """Execute one tool. Returns (result_text, refresh_scene)."""
    args = args or {}
    refresh = False
    try:
        if name == "list_arms":
            try:
                controller.connect_all()
            except Exception:
                pass
            return _dump(controller.list_arms()), False

        if name == "list_scene":
            return _dump(controller.scene_state()), False

        if name == "scan":
            color = args.get("color") or "any"
            payload = controller.camera.scan_workspace(
                camera_id="Cam1", color=color
            )
            return _dump(payload), False

        if name == "home_arm":
            return _dump(controller.home_arm(args.get("robot_id", "Arm1"))), True

        if name == "park_arm":
            robot_id = str(args.get("robot_id") or "Arm1")
            rid = robot_id.strip().lower().replace(" ", "")
            dest = "drop_zone" if rid in ("arm2", "2") or rid.endswith("2") else "storage_zone"
            return _dump(controller.move_to_named_pose(robot_id, dest, "hover")), True

        if name == "move_to_named":
            phase = args.get("phase") or "hover"
            return _dump(
                controller.move_to_named_pose(
                    args.get("robot_id", "Arm1"),
                    args.get("destination", "home"),
                    phase,
                )
            ), True

        if name == "pick_at":
            return _dump(
                controller.pick_at(
                    args.get("robot_id", "Arm1"),
                    float(args["x"]),
                    float(args["y"]),
                )
            ), True

        if name == "pick_object":
            obj_id = args.get("object_id")
            robot_id = str(args.get("robot_id") or "Arm1").strip()
            # normalize arm id
            rid = robot_id.lower().replace(" ", "")
            if rid in ("arm2", "2", "right"):
                robot_id = "Arm2"
            elif rid in ("arm1", "1", "left"):
                robot_id = "Arm1"
            if robot_id not in getattr(controller, "robots", {}):
                return _dump({
                    "ok": False,
                    "error": f"Robot '{robot_id}' not active. Active: {list(getattr(controller, 'robots', {}))}",
                }), False
            state = controller.scene_state()
            obj = None
            for item in state.get("objects") or []:
                if item.get("id") == obj_id:
                    obj = item
                    break
            if obj is None:
                return _dump({"ok": False, "error": f"{obj_id} not in scene"}), False
            # ใช้ arm-local ของแขนนั้น (ไม่ส่ง workspace ดิบ)
            per = (obj.get("per_arm_robot_plane") or {}).get(robot_id)
            if isinstance(per, dict) and "x" in per and "y" in per:
                xy = per
            else:
                # fallback: workspace → resolve ใน pick_at
                xy = obj.get("robot_plane") or {}
            return _dump(
                controller.pick_at(
                    robot_id,
                    float(xy["x"]),
                    float(xy["y"]),
                )
            ), True

        if name == "place_at":
            return _dump(
                controller.place_at(
                    args.get("robot_id", "Arm1"),
                    float(args["x"]),
                    float(args["y"]),
                )
            ), True

        if name == "place_named":
            return _dump(
                controller.place_to_named_target(
                    args.get("robot_id", "Arm1"),
                    args.get("destination", "storage_zone"),
                )
            ), True

        if name == "spawn_object":
            kwargs = {"color": args.get("color") or "green"}
            if "x" in args:
                kwargs["x"] = float(args["x"])
            if "y" in args:
                kwargs["y"] = float(args["y"])
            return _dump(controller.spawn_object(**kwargs)), True

        if name == "remove_object":
            return _dump(controller.remove_object(args.get("object_id"))), True

        if name == "reset_scene":
            return _dump(controller.reset_scene()), True

        return _dump({"ok": False, "error": f"Unknown tool: {name}"}), False
    except Exception as exc:  # noqa: BLE001
        return _dump({"ok": False, "error": str(exc)}), refresh


def _parse_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def assemble_chat_completion_from_sse(lines: Iterable[str]) -> dict[str, Any]:
    """Fold OpenAI-style SSE chunks into one chat.completion object."""
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason = None
    completion: dict[str, Any] = {
        "id": None,
        "model": None,
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": ""},
                "finish_reason": None,
            }
        ],
        "usage": {},
    }
    for raw in lines:
        line = (raw or "").strip()
        if not line:
            continue
        if line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue
        if chunk.get("id"):
            completion["id"] = chunk["id"]
        if chunk.get("model"):
            completion["model"] = chunk["model"]
        if chunk.get("usage"):
            completion["usage"] = chunk["usage"]
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0] or {}
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]
        delta = choice.get("delta") or {}
        piece = delta.get("content")
        if piece:
            content_parts.append(piece)
        for tc in delta.get("tool_calls") or []:
            idx = int(tc.get("index") or 0)
            slot = tool_calls.setdefault(
                idx,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            if tc.get("id"):
                slot["id"] = tc["id"]
            if tc.get("type"):
                slot["type"] = tc["type"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] = fn["name"]
            if fn.get("arguments"):
                slot["function"]["arguments"] += fn["arguments"]
        message = choice.get("message")
        if isinstance(message, dict):
            if message.get("content"):
                content_parts.append(message["content"])
            for tc in message.get("tool_calls") or []:
                idx = len(tool_calls)
                tool_calls[idx] = tc
    message_out: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts) or None,
    }
    if tool_calls:
        message_out["tool_calls"] = [tool_calls[idx] for idx in sorted(tool_calls)]
    completion["choices"][0]["message"] = message_out
    completion["choices"][0]["finish_reason"] = finish_reason
    return completion


def _chat_completion(
    messages: list[dict],
    use_tools: bool = True,
    max_tokens: int = 1200,
) -> dict:
    headers = {
        "Authorization": f"Bearer {config.SIM_LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": config.SIM_LLM_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if use_tools:
        payload["tools"] = _tools_schema()
        payload["tool_choice"] = "auto"

    errors: list[str] = []
    run = current_run()
    request_cm = run.request() if run is not None else None
    if request_cm is None:
        return _post_chat_with_fallback(headers, payload, errors)
    with request_cm:
        return _post_chat_with_fallback(headers, payload, errors)


def _post_chat_with_fallback(
    headers: dict[str, str],
    payload: dict[str, Any],
    errors: list[str],
) -> dict:
    for base in config.llm_base_candidates():
        url = f"{base.rstrip('/')}/v1/chat/completions"
        try:
            with requests.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=_TIMEOUT_S,
            ) as resp:
                resp.raise_for_status()
                assembled = assemble_chat_completion_from_sse(
                    resp.iter_lines(decode_unicode=True)
                )
                message = (
                    (assembled.get("choices") or [{}])[0].get("message") or {}
                )
                if assembled.get("id") or message.get("content") or message.get("tool_calls"):
                    return assembled
                errors.append(f"{url}: empty stream")
                continue
        except requests.HTTPError:
            raise
        except (requests.RequestException, json.JSONDecodeError, UnicodeDecodeError) as exc:
            errors.append(f"{url}: {exc}")
            continue
    raise requests.RequestException("; ".join(errors) or "chat unreachable")


def _run_llm_rounds(controller, messages: list[dict], tool_trace: list[dict]) -> dict[str, Any]:
    refresh = False
    final_text = ""
    for _ in range(_MAX_TOOL_ROUNDS):
        data = _chat_completion(messages, use_tools=True)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = (msg.get("content") or "").strip()
        tool_calls = msg.get("tool_calls") or []

        if content:
            final_text = content

        if not tool_calls:
            break

        messages.append(
            {
                "role": "assistant",
                "content": content or None,
                "tool_calls": tool_calls,
            }
        )

        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args = _parse_args(fn.get("arguments"))
            result_text, need_refresh = _run_tool(controller, name, args)
            refresh = refresh or need_refresh
            tool_trace.append(
                {"name": name, "args": args, "result": result_text[:2000]}
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id") or name,
                    "name": name,
                    "content": result_text,
                }
            )
    else:
        messages.append(
            {
                "role": "user",
                "content": "สรุปผลสั้นๆ จาก tool ที่ทำไปแล้ว (ไม่ต้องเรียก tool เพิ่ม)",
            }
        )
        data = _chat_completion(messages, use_tools=False)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        final_text = (msg.get("content") or final_text or "").strip()
    return {"final_text": final_text, "refresh": refresh}


def handle_llm_chat(controller, message: str) -> dict:
    """Run one user message through the Jetson LLM with sim tools.

    Returns chat_router-compatible dict:
      {ok, reply, action, data, refresh}
    """
    if not str(message).strip():
        return {
            "ok": False,
            "reply": "Empty message",
            "action": "noop",
            "data": {},
            "refresh": False,
        }

    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": message.strip()},
    ]
    tool_trace: list[dict] = []
    refresh = False
    final_text = ""
    jetson_info: dict[str, Any] = {}

    try:
        with CollectionRun(task_id="sim-chat") as collection:
            result = _run_llm_rounds(controller, messages, tool_trace)
            refresh = result["refresh"]
            final_text = result["final_text"]
        jetson_info = collection.summary()
    except requests.HTTPError as exc:
        body = ""
        try:
            body = exc.response.text[:400] if exc.response is not None else ""
        except Exception:
            pass
        return {
            "ok": False,
            "reply": f"SIM LLM HTTP error: {exc} {body}".strip(),
            "action": "llm_error",
            "data": {
                "base_url": config.SIM_LLM_BASE_URL,
                "model": config.SIM_LLM_MODEL,
            },
            "refresh": False,
            "error": str(exc),
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "reply": f"SIM LLM เชื่อมต่อไม่ได้ ({config.SIM_LLM_BASE_URL}): {exc}",
            "action": "llm_error",
            "data": {
                "base_url": config.SIM_LLM_BASE_URL,
                "model": config.SIM_LLM_MODEL,
            },
            "refresh": False,
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "reply": f"SIM LLM error: {exc}",
            "action": "llm_error",
            "data": {},
            "refresh": False,
            "error": str(exc),
        }

    if not final_text and tool_trace:
        names = ", ".join(t["name"] for t in tool_trace)
        final_text = f"รัน tools แล้ว: {names}"
    if not final_text:
        final_text = "(ไม่มีข้อความตอบกลับจากโมเดล)"

    final_text = re.sub(
        r"<think>[\s\S]*?</think>", "", final_text, flags=re.I
    ).strip() or final_text

    return {
        "ok": True,
        "reply": final_text,
        "action": "llm" if not tool_trace else "llm_tools",
        "data": {
            "model": config.SIM_LLM_MODEL,
            "base_url": config.SIM_LLM_BASE_URL,
            "tools": tool_trace,
            "jetson": jetson_info,
        },
        "refresh": refresh,
    }
