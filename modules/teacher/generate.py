"""Generate MCQ sets from skeletons — LLM fills text only; demo locked.

Fallback: seed from skeleton when LLM off/unreachable.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import requests

from .author import assemble_challenge, assemble_from_seed, validate_mcq_text
from .skeletons import (
    get_capability,
    get_skeleton,
    list_skeletons,
    load_capabilities,
    match_capability_from_topic,
)

_TIMEOUT_S = 90


def _llm_config() -> dict:
    try:
        from arc_runtime import config as arc_config

        return {
            "enabled": bool(getattr(arc_config, "SIM_LLM_ENABLED", False)),
            "base_url": (getattr(arc_config, "SIM_LLM_BASE_URL", "") or "").rstrip("/"),
            "api_key": getattr(arc_config, "SIM_LLM_API_KEY", "") or "",
            "model": getattr(arc_config, "SIM_LLM_MODEL", "") or "Qwen3.6",
        }
    except Exception:
        return {
            "enabled": os.getenv("ARC_SIM_LLM", "1").strip().lower()
            not in ("0", "false", "no", "off"),
            "base_url": (
                os.getenv("ARC_SIM_LLM_BASE_URL")
                or os.getenv("OPENAI_BASE_URL")
                or ""
            ).rstrip("/"),
            "api_key": os.getenv("ARC_SIM_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "",
            "model": os.getenv("ARC_SIM_LLM_MODEL") or os.getenv("OPENAI_MODEL") or "Qwen3.6",
        }


def _teacher_llm_enabled() -> bool:
    """TEACHER_LLM overrides; default follows SIM LLM when unset."""
    raw = os.getenv("TEACHER_LLM")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() not in ("0", "false", "no", "off")
    return bool(_llm_config().get("enabled"))


def _extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    # strip markdown fence
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # find first { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _system_prompt() -> str:
    return """คุณเป็นผู้เขียนข้อสอบ MCQ สำหรับห้องเรียนหุ่นยนต์ simulator (UC4 Teacher)

กฎเข้มงวด:
1. ตอบเป็น JSON object อย่างเดียว ไม่มี markdown อธิบายนอก JSON
2. เขียนภาษาไทย ชัดเจน ระดับนักเรียนมัธยม–มหาวิทยาลัยต้น
3. ต้องมี choices พอดี 4 ข้อ id เป็น A,B,C,D
4. correct_choice ต้องตรงความรู้ที่ให้ใน facts / correct_key_points
5. wrong_hints ต้องมีสำหรับทุกข้อที่ผิด (ไม่เฉลยตรงๆ ว่าข้อถูกคืออะไร)
6. ห้ามคิดขั้นตอน motion ใหม่ — ห้ามใส่ demo_steps / setup / arm
7. อย่าแต่งความรู้ที่ขัด facts_th

รูปแบบ JSON:
{
  "title_th": "...",
  "intro": "...",
  "question_th": "...",
  "choices": [
    {"id":"A","label":"..."},
    {"id":"B","label":"..."},
    {"id":"C","label":"..."},
    {"id":"D","label":"..."}
  ],
  "correct_choice": "A|B|C|D",
  "explanation_th": "...",
  "wrong_hints": {"A":"...","C":"...","D":"..."}
}
"""


def _user_prompt(capability: dict, skeleton: dict, *, topic: Optional[str] = None) -> str:
    seed = skeleton.get("seed") or {}
    payload = {
        "topic_request": topic,
        "capability_id": capability.get("id"),
        "capability_title": capability.get("title_th"),
        "facts_th": capability.get("facts_th"),
        "skeleton_id": skeleton.get("id"),
        "skill": skeleton.get("skill"),
        "level": skeleton.get("level"),
        "teaching_goal_th": skeleton.get("teaching_goal_th"),
        "must_cover": skeleton.get("must_cover"),
        "correct_key_points": skeleton.get("correct_key_points"),
        "wrong_patterns": skeleton.get("wrong_patterns"),
        "prompt_hint_th": skeleton.get("prompt_hint_th"),
        "seed_example": {
            "title_th": seed.get("title_th"),
            "question_th": seed.get("question_th"),
            "correct_choice": seed.get("correct_choice"),
        },
        "instruction": (
            "เขียนข้อสอบใหม่ในหัวข้อ skill นี้ "
            "อาจถอดความจาก seed ได้ แต่ควรเปลี่ยนคำถาม/ตัวลวงบ้าง "
            "correct_choice ต้องสอดคล้อง correct_key_points"
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _call_llm(system: str, user: str) -> dict:
    cfg = _llm_config()
    if not cfg.get("base_url"):
        return {"ok": False, "error": "SIM LLM base_url not configured"}
    url = f"{cfg['base_url']}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.get('api_key') or 'none'}",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg.get("model") or "Qwen3.6",
        "temperature": 0.5,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=_TIMEOUT_S)
    except requests.RequestException as exc:
        return {"ok": False, "error": f"LLM request failed: {exc}"}
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": f"LLM HTTP {resp.status_code}: {(resp.text or '')[:300]}",
        }
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return {"ok": False, "error": "LLM response not JSON"}
    content = (
        ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
        or ""
    )
    parsed = _extract_json_object(content)
    if not parsed:
        return {
            "ok": False,
            "error": "LLM did not return parseable JSON object",
            "raw": content[:500],
        }
    return {"ok": True, "text": parsed, "raw": content}


def generate_one(
    skeleton_id: str,
    *,
    capability_id: Optional[str] = None,
    topic: Optional[str] = None,
    use_llm: Optional[bool] = None,
    caps: Optional[dict] = None,
) -> dict:
    """Generate one challenge for a skeleton."""
    caps = caps or load_capabilities()
    pack = get_skeleton(skeleton_id, capability_id=capability_id, caps=caps)
    if not pack:
        return {"ok": False, "error": f"skeleton not found: {skeleton_id}"}

    cap = pack["capability"]
    sk = pack["skeleton"]
    want_llm = _teacher_llm_enabled() if use_llm is None else bool(use_llm)

    llm_meta: dict[str, Any] = {"attempted": False}
    text = None
    source = "seed"

    if want_llm:
        llm_meta["attempted"] = True
        llm_result = _call_llm(_system_prompt(), _user_prompt(cap, sk, topic=topic))
        llm_meta["ok"] = llm_result.get("ok")
        if llm_result.get("ok"):
            text = llm_result["text"]
            v = validate_mcq_text(text)
            if v["ok"]:
                source = "llm"
                llm_meta["validation"] = v
            else:
                llm_meta["validation"] = v
                llm_meta["fallback"] = "seed_after_invalid_llm"
                text = None
        else:
            llm_meta["error"] = llm_result.get("error")
            llm_meta["fallback"] = "seed_after_llm_error"

    if text is None:
        assembled = assemble_from_seed(
            sk["id"],
            capability_id=cap["id"],
            caps=caps,
            paraphrase_tag="offline" if llm_meta.get("attempted") else None,
        )
        if not assembled.get("ok"):
            return {
                "ok": False,
                "error": assembled.get("error") or assembled.get("validation"),
                "llm": llm_meta,
            }
        assembled["source"] = assembled["challenge"].get("meta", {}).get("source")
        assembled["llm"] = llm_meta
        return assembled

    assembled = assemble_challenge(
        capability_id=cap["id"],
        skeleton_id=sk["id"],
        text=text,
        caps=caps,
        source=source,
    )
    assembled["llm"] = llm_meta
    return assembled


def generate_set(
    *,
    capability_id: Optional[str] = None,
    topic: Optional[str] = None,
    skeleton_ids: Optional[list[str]] = None,
    count: Optional[int] = None,
    use_llm: Optional[bool] = None,
    caps: Optional[dict] = None,
) -> dict:
    """Generate a set of challenges for a capability / topic."""
    caps = caps or load_capabilities()

    resolved_cap = capability_id
    if not resolved_cap and topic:
        resolved_cap = match_capability_from_topic(topic, caps)
    if not resolved_cap and skeleton_ids:
        # infer from first skeleton
        pack = get_skeleton(skeleton_ids[0], caps=caps)
        if pack:
            resolved_cap = pack["capability"]["id"]
    if not resolved_cap:
        # default first capability
        clist = caps.get("capabilities") or []
        if not clist:
            return {"ok": False, "error": "no capabilities configured"}
        resolved_cap = clist[0]["id"]

    cap = get_capability(resolved_cap, caps)
    if not cap:
        return {"ok": False, "error": f"unknown capability: {resolved_cap}"}

    if skeleton_ids:
        targets = list(skeleton_ids)
    else:
        targets = [s["id"] for s in (cap.get("question_skeletons") or [])]

    if count is not None:
        targets = targets[: max(0, int(count))]

    challenges = []
    results = []
    for sid in targets:
        one = generate_one(
            sid,
            capability_id=resolved_cap,
            topic=topic,
            use_llm=use_llm,
            caps=caps,
        )
        results.append(
            {
                "skeleton_id": sid,
                "ok": bool(one.get("ok")),
                "error": one.get("error"),
                "source": (one.get("challenge") or {}).get("meta", {}).get("source")
                or one.get("source"),
                "llm": one.get("llm"),
                "validation": one.get("validation"),
                "challenge_id": (one.get("challenge") or {}).get("id"),
            }
        )
        if one.get("ok") and one.get("challenge"):
            challenges.append(one["challenge"])

    return {
        "ok": bool(challenges),
        "capability_id": resolved_cap,
        "topic": topic,
        "requested": len(targets),
        "generated": len(challenges),
        "challenges": challenges,
        "results": results,
        "teacher_llm": _teacher_llm_enabled() if use_llm is None else bool(use_llm),
        "message_th": (
            f"สร้าง {len(challenges)}/{len(targets)} ข้อ จาก capability «{resolved_cap}»"
            + (f" (topic: {topic})" if topic else "")
        ),
    }
