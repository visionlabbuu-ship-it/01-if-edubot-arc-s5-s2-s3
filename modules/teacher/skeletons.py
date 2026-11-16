"""Capability + question skeleton registry (sim author path).

LLM เติมได้เฉพาะข้อความ — demo_steps / setup / arm มาจาก capability เท่านั้น.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Optional

_TEACHER_DIR = Path(__file__).resolve().parent
_CAPABILITIES_PATH = _TEACHER_DIR / "capabilities.json"


def load_capabilities(path: Optional[Path] = None) -> dict:
    p = path or _CAPABILITIES_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def list_capabilities(caps: Optional[dict] = None) -> list[dict]:
    caps = caps or load_capabilities()
    out = []
    for c in caps.get("capabilities") or []:
        qsk = c.get("question_skeletons") or []
        out.append(
            {
                "id": c.get("id"),
                "title_th": c.get("title_th"),
                "level_min": c.get("level_min"),
                "level_max": c.get("level_max"),
                "sim_proven": bool(c.get("sim_proven")),
                "keywords": list(c.get("keywords") or []),
                "skeleton_count": len(qsk),
                "skeleton_ids": [s.get("id") for s in qsk],
                "demo_skeleton": copy.deepcopy(c.get("demo_skeleton") or []),
                "facts_th": list(c.get("facts_th") or []),
            }
        )
    return out


def get_capability(capability_id: str, caps: Optional[dict] = None) -> Optional[dict]:
    caps = caps or load_capabilities()
    for c in caps.get("capabilities") or []:
        if c.get("id") == capability_id:
            return copy.deepcopy(c)
    return None


def list_skeletons(
    *,
    capability_id: Optional[str] = None,
    level: Optional[int] = None,
    caps: Optional[dict] = None,
) -> list[dict]:
    """Flat list of question skeletons with parent capability id."""
    caps = caps or load_capabilities()
    rows: list[dict] = []
    for c in caps.get("capabilities") or []:
        cid = c.get("id")
        if capability_id and cid != capability_id:
            continue
        for sk in c.get("question_skeletons") or []:
            if level is not None and int(sk.get("level", 1)) != int(level):
                continue
            rows.append(
                {
                    "id": sk.get("id"),
                    "capability_id": cid,
                    "skill": sk.get("skill"),
                    "level": sk.get("level"),
                    "mode": sk.get("mode") or "mcq",
                    "teaching_goal_th": sk.get("teaching_goal_th"),
                    "must_cover": list(sk.get("must_cover") or []),
                    "correct_key_points": list(sk.get("correct_key_points") or []),
                    "wrong_patterns": list(sk.get("wrong_patterns") or []),
                    "prompt_hint_th": sk.get("prompt_hint_th"),
                    "has_seed": bool(sk.get("seed")),
                    "demo_skeleton": copy.deepcopy(c.get("demo_skeleton") or []),
                }
            )
    return rows


def get_skeleton(
    skeleton_id: str,
    *,
    capability_id: Optional[str] = None,
    caps: Optional[dict] = None,
) -> Optional[dict]:
    """Return {capability, skeleton} or None."""
    caps = caps or load_capabilities()
    for c in caps.get("capabilities") or []:
        if capability_id and c.get("id") != capability_id:
            continue
        for sk in c.get("question_skeletons") or []:
            if sk.get("id") == skeleton_id:
                return {
                    "capability": copy.deepcopy(c),
                    "skeleton": copy.deepcopy(sk),
                }
    return None


def match_capability_from_topic(topic: str, caps: Optional[dict] = None) -> Optional[str]:
    """Map free-text topic → capability id via keywords (no LLM)."""
    text = (topic or "").strip().lower()
    if not text:
        return None
    caps = caps or load_capabilities()
    best_id = None
    best_score = 0
    for c in caps.get("capabilities") or []:
        score = 0
        for kw in c.get("keywords") or []:
            k = str(kw).lower()
            if k and k in text:
                score += 1
        # id / title partial
        cid = str(c.get("id") or "").lower()
        if cid and cid in text.replace(" ", "_"):
            score += 2
        title = str(c.get("title_th") or "").lower()
        if title and any(part in text for part in title.split() if len(part) > 2):
            score += 1
        if score > best_score:
            best_score = score
            best_id = c.get("id")
    return best_id if best_score > 0 else None


def author_meta(caps: Optional[dict] = None) -> dict:
    caps = caps or load_capabilities()
    return {
        "version": caps.get("version"),
        "runtime": caps.get("runtime"),
        "author": caps.get("author") or {},
        "allowed_demo_ops": list(caps.get("allowed_demo_ops") or []),
        "allowed_named_destinations": list(caps.get("allowed_named_destinations") or []),
        "capability_count": len(caps.get("capabilities") or []),
        "skeleton_count": sum(
            len(c.get("question_skeletons") or []) for c in (caps.get("capabilities") or [])
        ),
    }
