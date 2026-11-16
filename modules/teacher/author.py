"""Assemble + validate MCQ challenges from skeletons (deterministic gate).

Rules (sim author):
- demo_steps / setup / arm / success always come from capability skeleton
- LLM (or seed) may only supply text fields + correct_choice
- reject unknown ops / bad choice shape before teach
"""

from __future__ import annotations

import copy
import re
import time
import uuid
from typing import Any, Optional

from .skeletons import get_skeleton, load_capabilities

_CHOICE_IDS = ("A", "B", "C", "D")
_TEXT_FIELDS = (
    "title_th",
    "intro",
    "question_th",
    "explanation_th",
    "choices",
    "correct_choice",
    "wrong_hints",
)


def _allowed_ops(caps: dict) -> set[str]:
    return {str(o) for o in (caps.get("allowed_demo_ops") or [])}


def _allowed_dest(caps: dict) -> set[str]:
    return {str(d) for d in (caps.get("allowed_named_destinations") or [])}


def validate_demo_steps(steps: list, caps: Optional[dict] = None) -> dict:
    caps = caps or load_capabilities()
    ops = _allowed_ops(caps)
    dests = _allowed_dest(caps)
    errors: list[str] = []
    for i, step in enumerate(steps or []):
        if not isinstance(step, dict):
            errors.append(f"step[{i}] must be object")
            continue
        op = step.get("op")
        if op not in ops:
            errors.append(f"step[{i}] op '{op}' not in allow-list")
            continue
        if op in ("move_named", "place_named"):
            dest = step.get("destination") or "home"
            if dest not in dests:
                errors.append(f"step[{i}] destination '{dest}' not allowed")
    return {"ok": not errors, "errors": errors}


def validate_mcq_text(payload: dict) -> dict:
    """Validate LLM/seed text payload (no demo)."""
    errors: list[str] = []
    warnings: list[str] = []

    for key in ("title_th", "question_th", "explanation_th"):
        val = payload.get(key)
        if not val or not str(val).strip():
            errors.append(f"missing {key}")

    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 4:
        errors.append("choices must be list of 4")
        choices = choices or []
    else:
        ids = []
        for i, c in enumerate(choices):
            if not isinstance(c, dict):
                errors.append(f"choices[{i}] must be object")
                continue
            cid = str(c.get("id") or "").strip().upper()
            label = str(c.get("label") or "").strip()
            if cid not in _CHOICE_IDS:
                errors.append(f"choices[{i}].id must be A-D")
            if not label:
                errors.append(f"choices[{i}].label empty")
            ids.append(cid)
        if len(set(ids)) != 4:
            errors.append("choices ids must be unique A,B,C,D")

    correct = str(payload.get("correct_choice") or "").strip().upper()
    if correct not in _CHOICE_IDS:
        errors.append("correct_choice must be A-D")
    else:
        choice_ids = {
            str(c.get("id") or "").strip().upper()
            for c in (choices or [])
            if isinstance(c, dict)
        }
        if choice_ids and correct not in choice_ids:
            errors.append("correct_choice not in choices")

    hints = payload.get("wrong_hints") or {}
    if not isinstance(hints, dict):
        errors.append("wrong_hints must be object")
    else:
        for cid in _CHOICE_IDS:
            if cid == correct:
                continue
            if cid not in hints and cid.lower() not in hints:
                warnings.append(f"missing wrong_hints for {cid}")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def validate_challenge(challenge: dict, caps: Optional[dict] = None) -> dict:
    caps = caps or load_capabilities()
    errors: list[str] = []
    warnings: list[str] = []

    if not challenge.get("id"):
        errors.append("missing id")
    if (challenge.get("mode") or "mcq") != "mcq":
        errors.append("sim author only supports mode=mcq")

    text_v = validate_mcq_text(challenge)
    errors.extend(text_v["errors"])
    warnings.extend(text_v.get("warnings") or [])

    demo_v = validate_demo_steps(challenge.get("demo_steps") or [], caps)
    errors.extend(demo_v["errors"])
    if not (challenge.get("demo_steps") or []):
        warnings.append("no demo_steps — teach without robot demo")

    success = challenge.get("success") or {}
    if not success.get("mcq_correct"):
        warnings.append("success.mcq_correct not set — will default true on assemble")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "challenge_id": challenge.get("id"),
    }


def _slug(s: str, max_len: int = 24) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", (s or "").strip())
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return (s or "gen")[:max_len]


def new_challenge_id(skeleton_id: str, capability_id: str) -> str:
    short = uuid.uuid4().hex[:8]
    return f"gen_{_slug(capability_id, 12)}_{_slug(skeleton_id, 16)}_{short}"


def assemble_challenge(
    *,
    capability_id: str,
    skeleton_id: str,
    text: dict,
    caps: Optional[dict] = None,
    challenge_id: Optional[str] = None,
    source: str = "seed",
) -> dict:
    """Merge text payload with locked demo/setup from capability skeleton."""
    caps = caps or load_capabilities()
    pack = get_skeleton(skeleton_id, capability_id=capability_id, caps=caps)
    if not pack:
        raise ValueError(f"unknown skeleton {skeleton_id} under {capability_id}")

    cap = pack["capability"]
    sk = pack["skeleton"]
    text = copy.deepcopy(text or {})

    # normalize choices ids
    choices = []
    for c in text.get("choices") or []:
        if not isinstance(c, dict):
            continue
        choices.append(
            {
                "id": str(c.get("id") or "").strip().upper(),
                "label": str(c.get("label") or "").strip(),
            }
        )

    correct = str(text.get("correct_choice") or "").strip().upper()
    wrong_hints = {}
    raw_hints = text.get("wrong_hints") or {}
    if isinstance(raw_hints, dict):
        for k, v in raw_hints.items():
            wrong_hints[str(k).strip().upper()] = str(v)

    cid = challenge_id or new_challenge_id(skeleton_id, capability_id)
    challenge = {
        "id": cid,
        "level": int(sk.get("level") or cap.get("level_min") or 1),
        "mode": "mcq",
        "title_th": str(text.get("title_th") or sk.get("teaching_goal_th") or cid),
        "intro": str(text.get("intro") or "เลือกคำตอบที่ถูก"),
        "question_th": str(text.get("question_th") or ""),
        "choices": choices,
        "correct_choice": correct,
        "explanation_th": str(text.get("explanation_th") or ""),
        "wrong_hints": wrong_hints,
        "arm": cap.get("arm") or caps.get("default_arm") or "Arm1",
        "setup": copy.deepcopy(cap.get("setup") or {"reset_scene": True, "park_others": True}),
        "demo_on_correct": True,
        # LOCKED — never take demo from LLM text
        "demo_steps": copy.deepcopy(cap.get("demo_skeleton") or []),
        "success": {"mcq_correct": True},
        "meta": {
            "source": source,
            "capability_id": capability_id,
            "skeleton_id": skeleton_id,
            "skill": sk.get("skill"),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "sim_proven_capability": bool(cap.get("sim_proven")),
        },
    }

    gate = validate_challenge(challenge, caps)
    return {"ok": gate["ok"], "challenge": challenge, "validation": gate}


def assemble_from_seed(
    skeleton_id: str,
    *,
    capability_id: Optional[str] = None,
    caps: Optional[dict] = None,
    challenge_id: Optional[str] = None,
    paraphrase_tag: Optional[str] = None,
) -> dict:
    """Build challenge from skeleton.seed (offline, no LLM)."""
    caps = caps or load_capabilities()
    pack = get_skeleton(skeleton_id, capability_id=capability_id, caps=caps)
    if not pack:
        return {"ok": False, "error": f"skeleton not found: {skeleton_id}"}
    cap = pack["capability"]
    sk = pack["skeleton"]
    seed = copy.deepcopy(sk.get("seed") or {})
    if not seed:
        return {"ok": False, "error": f"skeleton {skeleton_id} has no seed"}

    if paraphrase_tag:
        # light offline variant marker (not full paraphrase)
        q = seed.get("question_th") or ""
        seed["question_th"] = q
        seed["intro"] = (seed.get("intro") or "") + f" · ชุด {paraphrase_tag}"

    return assemble_challenge(
        capability_id=cap["id"],
        skeleton_id=sk["id"],
        text=seed,
        caps=caps,
        challenge_id=challenge_id,
        source="seed" if not paraphrase_tag else f"seed:{paraphrase_tag}",
    )


def extract_text_fields(challenge: dict) -> dict:
    """Fields an LLM is allowed to edit."""
    return {k: copy.deepcopy(challenge.get(k)) for k in _TEXT_FIELDS if k in challenge}
