"""Tutor chat for UC4 on sim — คุยหา topic แล้ว generate ชุดโจทย์.

- Intent / capability map: rule-based (ชัวร์ offline)
- ตอบภาษา: template + optional LLM แต่งประโยค (พังแล้วใช้ template)
- Generate: เรียก generate.generate_set แล้วให้ engine โหลด session
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from .skeletons import (
    get_capability,
    list_capabilities,
    load_capabilities,
    match_capability_from_topic,
)

_GREET_RE = re.compile(
    r"^(สวัสดี|หวัดดี|hello|hi|hey|ดีครับ|ดีค่ะ|ทักทาย)\b",
    re.I,
)
_HELP_RE = re.compile(
    r"(ช่วย|ทำอะไรได้|มีบท|มีหัวข้อ|สอนอะไร|capability|เมนู|help|\?)\s*$",
    re.I,
)
_LIST_RE = re.compile(r"(มีหัวข้อ|หัวข้ออะไร|สอนอะไรได้|รายการ|list|เมนู)", re.I)
_GEN_RE = re.compile(
    r"(สร้าง|ออกโจทย์|เริ่มเรียน|จัดชุด|generate|อยากเรียน|สอน|เรียนเรื่อง|เรื่อง)",
    re.I,
)
_YES_RE = re.compile(r"^(ใช่|ครับ|ค่ะ|ได้|ok|yes|เอา|เริ่ม|เลย|จัดมา|เอาเลย)\b", re.I)
_NO_RE = re.compile(r"^(ไม่|no|ยัง|เปลี่ยน|อื่น)", re.I)
_CATALOG_RE = re.compile(r"(โจทย์เดิม|คลัง|catalog|รายการโจทย์คงที่)", re.I)


def _teacher_llm_enabled() -> bool:
    raw = os.getenv("TEACHER_LLM")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() not in ("0", "false", "no", "off")
    try:
        from arc_runtime import config as arc_config

        return bool(getattr(arc_config, "SIM_LLM_ENABLED", False))
    except Exception:
        return os.getenv("ARC_SIM_LLM", "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )


def _llm_polish(system: str, user: str) -> Optional[str]:
    """Optional short natural-language polish; None on failure."""
    if not _teacher_llm_enabled():
        return None
    try:
        import requests
        from arc_runtime import config as arc_config

        base = (getattr(arc_config, "SIM_LLM_BASE_URL", "") or "").rstrip("/")
        if not base:
            return None
        url = f"{base}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {getattr(arc_config, 'SIM_LLM_API_KEY', '') or 'none'}",
            "Content-Type": "application/json",
        }
        body = {
            "model": getattr(arc_config, "SIM_LLM_MODEL", None) or "Qwen3.6",
            "temperature": 0.4,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        resp = requests.post(url, headers=headers, json=body, timeout=45)
        if resp.status_code >= 400:
            return None
        data = resp.json()
        content = (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        ).strip()
        return content or None
    except Exception:
        return None


def _cap_menu(caps: dict) -> str:
    lines = []
    for c in list_capabilities(caps):
        sk = c.get("skeleton_count") or 0
        lines.append(
            f"• **{c.get('title_th') or c.get('id')}** (`{c.get('id')}`) — {sk} โจทย์ · sim"
        )
    return "\n".join(lines) if lines else "(ยังไม่มี capability)"


def _extract_count(text: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*ข้อ", text)
    if m:
        n = int(m.group(1))
        return max(1, min(6, n))
    m = re.search(r"\b([1-6])\b", text)
    if m and any(k in text for k in ("ข้อ", "ชุด", "โจทย์", "count")):
        return int(m.group(1))
    return None


def detect_intent(
    message: str,
    *,
    pending_capability: Optional[str] = None,
    caps: Optional[dict] = None,
) -> dict:
    """Rule-based intent for tutor chat."""
    caps = caps or load_capabilities()
    text = (message or "").strip()
    low = text.lower()

    if not text:
        return {"intent": "empty", "reply_key": "empty"}

    if _GREET_RE.search(text) and len(text) < 24:
        return {"intent": "greet", "reply_key": "greet"}

    if _CATALOG_RE.search(text):
        return {"intent": "catalog", "reply_key": "catalog"}

    if _LIST_RE.search(text) or _HELP_RE.search(text):
        return {"intent": "list", "reply_key": "list"}

    # confirm pending generate
    if pending_capability and (
        _YES_RE.search(text)
        or re.search(r"(สร้าง|ออกโจทย์|generate|จัดชุด|เอาชุด)", text, re.I)
    ):
        return {
            "intent": "generate",
            "capability_id": pending_capability,
            "count": _extract_count(text),
            "topic": text,
            "reply_key": "generate_confirm",
        }
    if pending_capability and _NO_RE.search(text):
        return {"intent": "cancel_pending", "reply_key": "cancel"}

    # explicit capability id
    for c in caps.get("capabilities") or []:
        cid = str(c.get("id") or "")
        if cid and (cid in low or cid.replace("_", " ") in low):
            return {
                "intent": "propose_or_generate",
                "capability_id": cid,
                "count": _extract_count(text),
                "topic": text,
                "reply_key": "matched",
            }

    matched = match_capability_from_topic(text, caps)
    wants_gen = bool(_GEN_RE.search(text)) or bool(matched)

    if matched and wants_gen:
        # อยากเรียน / สร้าง / สอน → ออกโจทย์เลยบน sim (UX หลัก)
        direct = bool(
            re.search(
                r"(สร้าง|ออกโจทย์|เริ่ม|จัดชุด|generate|เอาเลย|จัดมา|อยากเรียน|สอน|เรียน)",
                text,
                re.I,
            )
        )
        return {
            "intent": "generate" if direct else "propose",
            "capability_id": matched,
            "count": _extract_count(text),
            "topic": text,
            "reply_key": "matched",
        }

    if matched:
        return {
            "intent": "propose",
            "capability_id": matched,
            "count": _extract_count(text),
            "topic": text,
            "reply_key": "matched",
        }

    if wants_gen:
        return {
            "intent": "need_topic",
            "topic": text,
            "reply_key": "need_topic",
        }

    return {
        "intent": "chat",
        "topic": text,
        "reply_key": "fallback",
        "capability_id": match_capability_from_topic(text, caps),
    }


def build_reply(
    intent: dict,
    *,
    caps: Optional[dict] = None,
    generate_result: Optional[dict] = None,
) -> str:
    caps = caps or load_capabilities()
    key = intent.get("reply_key")
    cid = intent.get("capability_id")
    cap = get_capability(cid, caps) if cid else None
    title = (cap or {}).get("title_th") or cid or ""

    if key == "empty":
        return "พิมพ์ได้เลย เช่น «อยากเรียนลำดับหยิบของ» หรือ «มีหัวข้ออะไรบ้าง»"
    if key == "greet":
        return (
            "สวัสดีครับ ผมเป็นครูหุ่นยนต์บน **sim**\n"
            "บอกได้ว่าอยากเรียนเรื่องอะไร แล้วผมจะสร้างชุดข้อสอบ + ให้หุ่นสาธิตได้\n\n"
            f"หัวข้อที่พร้อมสอน:\n{_cap_menu(caps)}"
        )
    if key == "list":
        return (
            "หัวข้อที่สอนได้บน simulator ตอนนี้:\n"
            f"{_cap_menu(caps)}\n\n"
            "ตัวอย่าง: «อยากเรียน pick pipeline» หรือ «สร้างโจทย์เรื่องวางของ»"
        )
    if key == "catalog":
        return (
            "มีคลังโจทย์คงที่ 6 ข้อ (Q1–Q3) เปิดแท็บ **เรียน** เลือกได้เลย "
            "หรือคุยกับผมให้สร้างชุดใหม่จากหัวข้อด้านบน"
        )
    if key == "cancel":
        return "ได้ครับ ยกเลิกหัวข้อนั้นแล้ว — บอกหัวข้อใหม่ได้เลย"
    if key == "need_topic":
        return (
            "ยังจับหัวข้อไม่ชัดครับ เลือกอย่างใดอย่างหนึ่งได้:\n"
            f"{_cap_menu(caps)}\n\n"
            "หรือพิมพ์เช่น «อยากเรียนทำไมต้องสแกนก่อน»"
        )
    if key == "matched" and intent.get("intent") == "propose" and cap:
        n = len(cap.get("question_skeletons") or [])
        return (
            f"เจอหัวข้อ **{title}** (`{cid}`) — มีโครงโจทย์ {n} ข้อ "
            f"พร้อม demo บน sim\n"
            f"พิมพ์ **«สร้างโจทย์»** หรือ **«ใช่»** เพื่อออกชุดให้เรียนเลย"
        )
    if generate_result is not None:
        if generate_result.get("ok"):
            n = generate_result.get("generated") or 0
            ids = generate_result.get("load", {}).get("challenge_ids") or []
            src = "LLM+seed" if generate_result.get("teacher_llm") else "seed"
            return (
                f"สร้างชุดเรียนแล้ว **{n} ข้อ** จาก `{generate_result.get('capability_id')}` "
                f"({src})\n"
                f"เปิดแท็บ **เรียน** ได้เลย — โจทย์แรกพร้อมตอบ Choice แล้ว\n"
                f"รหัส: {', '.join(ids[:4])}{'…' if len(ids) > 4 else ''}"
            )
        err = generate_result.get("error") or "ไม่สำเร็จ"
        return f"สร้างโจทย์ไม่สำเร็จ: {err}\nลองพิมพ์หัวข้อใหม่ หรือ «มีหัวข้ออะไรบ้าง»"

    if key == "fallback":
        base = (
            "ผมช่วยได้แบบนี้บน sim:\n"
            "1) คุยเลือกหัวข้อที่สอนได้\n"
            "2) สร้างชุด MCQ (choice A–D)\n"
            "3) ตอบถูกแล้วหุ่น demo ให้ดู\n\n"
            f"{_cap_menu(caps)}"
        )
        polished = _llm_polish(
            "คุณเป็นครูหุ่นยนต์สั้นๆ ภาษาไทย ตอบไม่เกิน 6 บรรทัด ห้ามแต่ง capability ใหม่",
            f"นักเรียนพูดว่า: {intent.get('topic')}\nตอบโดยอิงเมนูนี้:\n{base}",
        )
        return polished or base

    return (
        f"รับทราบเรื่อง «{intent.get('topic') or ''}» ครับ\n"
        f"ลองพิมพ์ «สร้างโจทย์» หลังเลือกหัวข้อ หรือดูเมนู:\n{_cap_menu(caps)}"
    )


def handle_tutor_turn(
    engine,
    message: str,
    *,
    use_llm: Optional[bool] = None,
    count: Optional[int] = None,
    auto_generate: bool = True,
) -> dict:
    """One chat turn against TeacherEngine (mutates chat history + maybe session set)."""
    caps = engine.capabilities or load_capabilities()
    text = (message or "").strip()

    pending = (engine.session_meta or {}).get("pending_capability")
    intent = detect_intent(text, pending_capability=pending, caps=caps)

    # override count from message if present
    if intent.get("count") is None and count is not None:
        intent["count"] = count

    generate_result = None
    action = intent.get("intent")

    if action == "cancel_pending":
        engine.session_meta = {**(engine.session_meta or {}), "pending_capability": None}

    if action == "propose" and intent.get("capability_id"):
        engine.session_meta = {
            **(engine.session_meta or {}),
            "pending_capability": intent["capability_id"],
            "pending_topic": intent.get("topic"),
        }

    should_gen = auto_generate and action in ("generate", "propose_or_generate")
    if should_gen and intent.get("capability_id"):
        gen_count = intent.get("count")
        # default: offline seed ชัวร์; ถ้า use_llm ระบุค่อยใช้
        generate_result = engine.generate_set(
            capability_id=intent["capability_id"],
            topic=intent.get("topic") or text,
            count=gen_count,
            use_llm=use_llm if use_llm is not None else False,
            start_first=True,
        )
        engine.session_meta = {
            **(engine.session_meta or {}),
            "pending_capability": None,
            "last_generate_ok": bool(generate_result.get("ok")),
        }

    reply = build_reply(intent, caps=caps, generate_result=generate_result)

    # Optional polish for generate success
    if generate_result and generate_result.get("ok") and _teacher_llm_enabled():
        polished = _llm_polish(
            "ครูหุ่นยนต์ ภาษาไทย สั้น อบอุ่น ห้ามเปลี่ยนตัวเลขจำนวนข้อ",
            f"สรุปให้นักเรียน: {reply}",
        )
        if polished:
            reply = polished

    turn = {
        "role": "user",
        "content": text,
    }
    bot = {
        "role": "assistant",
        "content": reply,
        "intent": intent.get("intent"),
        "capability_id": intent.get("capability_id"),
        "generated": bool(generate_result and generate_result.get("ok")),
        "challenge_ids": (generate_result or {}).get("load", {}).get("challenge_ids")
        if generate_result
        else None,
    }

    history = list(getattr(engine, "chat_history", None) or [])
    history.append(turn)
    history.append(bot)
    # cap history
    engine.chat_history = history[-40:]

    return {
        "ok": True,
        "action": "chat",
        "reply": reply,
        "intent": intent,
        "generate": generate_result,
        "history": list(engine.chat_history),
        "status": engine.status(),
        "switched_to_lesson": bool(generate_result and generate_result.get("ok")),
    }
