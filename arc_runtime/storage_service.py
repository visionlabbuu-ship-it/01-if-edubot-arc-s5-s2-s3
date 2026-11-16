"""Storage capacity registry and routing helpers for ARC."""
from __future__ import annotations
import json
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from .state_paths import runtime_state_path

SLOTS_PATH = runtime_state_path("capacity_slots.json")
_REGISTRY_LOCK = threading.RLock()
GROUPS = ("LT", "LB", "RT", "RB")
# Baseline physical ownership; calibrated reach remains the second-stage check.
GROUP_ARM_ROUTING = {"LT": ("Arm2",), "RT": ("Arm2",), "LB": ("Arm1",), "RB": ("Arm1",)}
GROUP_META = {
    "LT": {"label": "Storage Left Top", "side": "left", "cluster": "top"},
    "LB": {"label": "Storage Left Bottom", "side": "left", "cluster": "bottom"},
    "RT": {"label": "Storage Right Top", "side": "right", "cluster": "top"},
    "RB": {"label": "Storage Right Bottom", "side": "right", "cluster": "bottom"},
}

def now():
    return datetime.now(timezone.utc).isoformat()

def default_policy():
    return {"color_groups": {}, "default_group": None, "selection_order": "index_ascending"}

def empty_registry():
    return {"ok": True, "version": 2, "purpose": "named_storage_slots", "created_at": now(), "updated_at": now(), "source_scan": None, "expected_count": 16, "slot_count": 0, "groups": {name: {"name": name, "role": "storage", **meta} for name, meta in GROUP_META.items()}, "slot_policy": default_policy(), "slots": [], "summary": {"empty": 0, "occupied": 0, "unknown": 0, "by_group": {}}}

def normalize_group(value):
    key = re.sub(r"[_-]+", " ", str(value or "").strip().lower())
    key = " ".join(key.split())
    aliases = {"left top": "LT", "lefttop": "LT", "lt": "LT", "left bottom": "LB", "leftbottom": "LB", "lb": "LB", "right top": "RT", "righttop": "RT", "rt": "RT", "right bottom": "RB", "rightbottom": "RB", "rb": "RB"}
    return aliases.get(key, key.upper() if key.upper() in GROUPS else None)

def parse_reference(value):
    raw = str(value or "").strip()
    compact = raw.lower().replace(" ", "").replace("-", "")
    match = re.fullmatch(r"(lt|lb|rt|rb)(\d+)", compact)
    if match:
        group, index = match.group(1).upper(), int(match.group(2))
        return f"{group}{index}", group, index
    pattern = r"(?:(?:slot|number)\s*(?:no\.?\s*)?|\u0e0a\u0e48\u0e2d\u0e07\u0e17\u0e35\u0e48\s*)(\d+)\s*(?:(?:of|in|\u0e02\u0e2d\u0e07|\u0e43\u0e19)\s*)?(lt|lb|rt|rb)"
    match = re.search(pattern, raw.lower())
    if match:
        index, group = int(match.group(1)), match.group(2).upper()
        return f"{group}{index}", group, index
    fallback = re.search(r"(\d+)\D{0,20}(lt|lb|rt|rb)\b", raw.lower())
    if fallback:
        index, group = int(fallback.group(1)), fallback.group(2).upper()
        return f"{group}{index}", group, index
    return None, normalize_group(raw), None
def _slot_color(slot):
    for key in ("occupancy_color", "object_color", "color"):
        value = slot.get(key)
        if value:
            return str(value).strip().lower()
    held_object = slot.get("held_object")
    if isinstance(held_object, dict) and held_object.get("color"):
        return str(held_object["color"]).strip().lower()
    return None


def summarize(slots):
    result = {
        "empty": 0,
        "occupied": 0,
        "unknown": 0,
        "reserved": 0,
        "verification_pending": 0,
        "by_group": {},
        "occupied_by_color": {},
    }
    for slot in slots or []:
        status = str(slot.get("occupancy") or "unknown").lower()
        if status not in {"empty", "occupied", "unknown", "reserved", "verification_pending"}:
            status = "unknown"
        result[status] += 1
        group = str(slot.get("group") or "unassigned")
        bucket = result["by_group"].setdefault(
            group,
            {
                "total": 0,
                "empty": 0,
                "occupied": 0,
                "unknown": 0,
                "reserved": 0,
                "verification_pending": 0,
                "occupied_by_color": {},
            },
        )
        bucket["total"] += 1
        bucket[status] += 1
        color = _slot_color(slot)
        if status == "occupied" and color:
            result["occupied_by_color"][color] = result["occupied_by_color"].get(color, 0) + 1
            bucket["occupied_by_color"][color] = bucket["occupied_by_color"].get(color, 0) + 1
    return result


def compact_storage_view(slots):
    """Return the lean Slot facts an orchestrator needs for one-step decisions."""
    ordered = sorted(
        slots or [],
        key=lambda slot: (
            str(slot.get("group") or ""),
            int(slot.get("index_in_cluster") or 0),
            str(slot.get("name") or ""),
        ),
    )
    slot_states = []
    color_locations = {}
    empty_slots_by_group = {group: [] for group in GROUPS}
    for slot in ordered:
        group = str(slot.get("group") or "")
        status = str(slot.get("occupancy") or "unknown").lower()
        color = _slot_color(slot) if status == "occupied" else None
        state = {
            "name": slot.get("name") or slot.get("label"),
            "group": group,
            "occupancy": status,
            "color": color,
            "allowed_arms": list(GROUP_ARM_ROUTING.get(group, ())),
        }
        if isinstance(slot.get("reachable_by"), list):
            state["reachable_by"] = list(slot["reachable_by"])
        if slot.get("preferred_arm"):
            state["preferred_arm"] = slot["preferred_arm"]
        slot_states.append(state)
        if status == "empty":
            empty_slots_by_group.setdefault(group, []).append(state["name"])
        if status == "occupied" and color:
            location = color_locations.setdefault(color, {"count": 0, "slots": [], "groups": []})
            location["count"] += 1
            location["slots"].append(state["name"])
            if group and group not in location["groups"]:
                location["groups"].append(group)
    for location in color_locations.values():
        location["groups"].sort()
    return {
        "slot_count": len(slot_states),
        "summary": summarize(ordered),
        "slots": slot_states,
        "color_locations": color_locations,
        "empty_slots_by_group": empty_slots_by_group,
    }

def normalize(registry):
    payload = deepcopy(registry or empty_registry())
    payload.setdefault("slots", [])
    policy = default_policy()
    policy.update(payload.get("slot_policy") or {})
    for slot in payload["slots"]:
        name = str(slot.get("name") or slot.get("label") or "")
        canonical, name_group, index = parse_reference(name)
        group = normalize_group(slot.get("group")) or name_group
        if not group:
            group = "LT" if str(slot.get("side") or "left").lower() == "left" else "RT"
        slot["group"] = group
        slot["storage_group"] = group
        slot["role"] = "storage"
        slot.setdefault("occupancy", "unknown")
        slot.setdefault("occupancy_source", "legacy_registry")
        slot.setdefault("held_object", None)
        if slot.get("occupancy") not in {"reserved", "verification_pending"}:
            slot.pop("reservation", None)
        if canonical and not slot.get("name"):
            slot["name"] = canonical
        if index is not None:
            slot.setdefault("index_in_cluster", index)
    colors = {}
    for color, groups in (policy.get("color_groups") or {}).items():
        values = groups if isinstance(groups, list) else [groups]
        colors[str(color).strip().lower()] = [group for group in (normalize_group(value) for value in values) if group]
    policy["color_groups"] = colors
    policy["default_group"] = normalize_group(policy.get("default_group"))
    payload["slot_policy"] = policy
    payload["groups"] = {name: {"name": name, "role": "storage", "allowed_arms": list(GROUP_ARM_ROUTING[name]), **meta} for name, meta in GROUP_META.items()}
    payload["version"] = 2
    payload["purpose"] = "named_storage_slots"
    payload["slot_count"] = len(payload["slots"])
    payload["summary"] = summarize(payload["slots"])
    return payload

def load(path=SLOTS_PATH):
    path = Path(path)
    if not path.exists():
        return empty_registry()
    return normalize(json.loads(path.read_text(encoding="utf-8")))

def save(registry, path=SLOTS_PATH):
    payload = normalize(registry)
    payload["updated_at"] = now()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload

def list_slots(registry=None, group=None, occupancy=None):
    payload = normalize(registry or load())
    group = normalize_group(group) if group else None
    slots = [slot for slot in payload["slots"] if not group or slot.get("group") == group]
    if occupancy:
        slots = [slot for slot in slots if slot.get("occupancy") == str(occupancy).lower()]
    return sorted(slots, key=lambda slot: (slot.get("group", ""), int(slot.get("index_in_cluster") or 0), slot.get("name", "")))

def find_slot(registry, reference):
    payload = normalize(registry)
    canonical, _, _ = parse_reference(reference)
    key = (canonical or str(reference or "").strip()).upper()
    return next((slot for slot in payload["slots"] if str(slot.get("name") or slot.get("label") or "").upper() == key), None)

def set_policy(policy, path=SLOTS_PATH):
    payload = load(path)
    merged = {**default_policy(), **(policy or {})}
    colors = {}
    for color, values in (merged.get("color_groups") or {}).items():
        values = values if isinstance(values, list) else [values]
        colors[str(color).strip().lower()] = [group for group in (normalize_group(value) for value in values) if group]
    merged["color_groups"] = colors
    merged["default_group"] = normalize_group(merged.get("default_group"))
    payload["slot_policy"] = merged
    return save(payload, path)
def policy(path=SLOTS_PATH):
    return load(path).get("slot_policy", default_policy())

def resolve(reference=None, color=None, arm_id=None, require_empty=True, path=SLOTS_PATH, active_arms=None, online_arms=None):
    payload = load(path)
    canonical, group, _ = parse_reference(reference)
    storage_policy = payload["slot_policy"]
    if not canonical and not group and color:
        values = storage_policy.get("color_groups", {}).get(str(color).strip().lower(), [])
        group = values[0] if values else storage_policy.get("default_group")
    if not canonical and not group:
        return {"ok": False, "error_code": "needs_storage_policy", "error": "Storage group or policy is required", "policy": storage_policy}
    allowed_arms = list(GROUP_ARM_ROUTING.get(group, ()))
    if arm_id and allowed_arms and arm_id not in allowed_arms:
        return {"ok": False, "error_code": "slot_unreachable", "group": group, "arm_id": arm_id, "allowed_arms": allowed_arms, "reason": "group_arm_routing_policy"}
    candidates = [slot for slot in payload["slots"] if (not group or slot.get("group") == group) and (not canonical or str(slot.get("name")).upper() == canonical.upper())]
    if arm_id:
        if active_arms is not None and arm_id not in set(active_arms):
            return {"ok": False, "error_code": "arm_inactive", "arm_id": arm_id}
        if online_arms is not None and arm_id not in set(online_arms):
            return {"ok": False, "error_code": "arm_offline", "arm_id": arm_id}
        if isinstance(payload.get("inactive_arms"), list) and arm_id not in payload.get("active_arms", []):
            return {"ok": False, "error_code": "arm_inactive", "arm_id": arm_id}
        if isinstance(payload.get("offline_arms"), list) and arm_id in payload.get("offline_arms", []):
            return {"ok": False, "error_code": "arm_offline", "arm_id": arm_id}
    if require_empty:
        empty = [slot for slot in candidates if slot.get("occupancy") == "empty"]
        if canonical and not empty:
            slot = candidates[0] if candidates else None
            code = "capacity_full" if slot and slot.get("occupancy") == "occupied" else "capacity_unknown" if slot and slot.get("occupancy") == "unknown" else "slot_unavailable"
            return {"ok": False, "error_code": code, "slot_name": canonical, "slot": slot}
        candidates = empty
    if arm_id:
        reach_candidates = list(candidates)
        arm_plane = [(slot.get("per_arm_robot_plane") or {}).get(arm_id) for slot in candidates]
        if candidates and all(plane is None for plane in arm_plane):
            first = sorted(reach_candidates, key=lambda item: int(item.get("index_in_cluster") or 0))[0] if reach_candidates else None
            return {"ok": False, "error_code": "transform_not_ready", "group": group, "slot_name": first.get("name") if first else canonical, "slot": deepcopy(first) if first else None, "available_slots": [slot.get("name") for slot in reach_candidates], "arm_id": arm_id}
        candidates = [slot for slot in candidates if arm_id in (slot.get("reachable_by") or []) or (slot.get("per_arm_robot_plane") or {}).get(arm_id, {}).get("reachable") is True]
        if not candidates:
            first = sorted(reach_candidates, key=lambda item: int(item.get("index_in_cluster") or 0))[0] if reach_candidates else None
            return {"ok": False, "error_code": "slot_unreachable", "group": group, "slot_name": first.get("name") if first else canonical, "slot": deepcopy(first) if first else None, "available_slots": [slot.get("name") for slot in reach_candidates], "arm_id": arm_id}
    if not candidates:
        return {"ok": False, "error_code": "capacity_full", "group": group, "summary": payload["summary"]["by_group"].get(group, {}), "available_slots": []}
    slot = sorted(candidates, key=lambda item: int(item.get("index_in_cluster") or 0))[0]
    return {"ok": True, "slot": deepcopy(slot), "slot_name": slot.get("name"), "group": slot.get("group"), "arm_id": arm_id, "allowed_arms": list(GROUP_ARM_ROUTING.get(slot.get("group"), ())), "recommended_arm": (GROUP_ARM_ROUTING.get(slot.get("group"), ())[0] if GROUP_ARM_ROUTING.get(slot.get("group")) else None)}

def update_occupancy(slot_name, occupancy, held_object=None, source="runtime", verification=None, path=SLOTS_PATH):
    payload = load(path)
    canonical, _, _ = parse_reference(slot_name)
    key = (canonical or str(slot_name or "").strip()).upper()
    slot = next((item for item in payload.get("slots", []) if str(item.get("name") or item.get("label") or "").upper() == key), None)
    if not slot:
        raise KeyError(f"Storage slot not found: {slot_name}")
    status = str(occupancy).lower()
    if status not in {"empty", "occupied", "unknown", "reserved", "verification_pending"}:
        raise ValueError("invalid occupancy")
    slot["occupancy"] = status
    slot["occupancy_source"] = source
    if held_object is not None or status in {"empty", "unknown"}:
        slot["held_object"] = deepcopy(held_object)
    if status not in {"reserved", "verification_pending"}:
        slot.pop("reservation", None)
    if verification is not None:
        slot["last_verification"] = deepcopy(verification)
    return save(payload, path)
def migrate_file(path=SLOTS_PATH):
    return save(load(path), path)

def scan(path=SLOTS_PATH, image_path=None, expected_count=16):
    from .capacity_slots import scan_and_build_slots
    result = scan_and_build_slots(image_path=image_path, expected_count=expected_count, slots_path=path, rescan=True)
    if result.get("ok"):
        registry = migrate_file(path)
        # Return the normalized registry as the authoritative scan snapshot.
        result["registry"] = registry
        result["slots"] = deepcopy(registry.get("slots") or [])
        result["groups"] = deepcopy(registry.get("groups") or {})
        result["summary"] = deepcopy(registry.get("summary") or {})
        result["compact"] = compact_storage_view(registry.get("slots") or [])
    return result



def reserve_slot(slot_name, robot_id=None, task_id=None, path=SLOTS_PATH):
    key = (parse_reference(slot_name)[0] or str(slot_name or "").strip()).upper()
    with _REGISTRY_LOCK:
        payload = load(path)
        slot = next((item for item in payload.get("slots", []) if str(item.get("name") or item.get("label") or "").upper() == key), None)
        if not slot:
            raise KeyError(f"Storage slot not found: {slot_name}")
        status = str(slot.get("occupancy") or "unknown").lower()
        if status != "empty":
            code = "capacity_full" if status == "occupied" else "capacity_unknown" if status == "unknown" else "slot_unavailable"
            return {"ok": False, "error_code": code, "slot_name": slot.get("name"), "slot": deepcopy(slot)}
        slot["occupancy"] = "reserved"
        slot["reservation"] = {"robot_id": robot_id, "task_id": task_id, "reserved_at": now()}
        save(payload, path)
        return {"ok": True, "slot": deepcopy(slot), "slot_name": slot.get("name")}

def release_reservation(slot_name, path=SLOTS_PATH, reason="released"):
    with _REGISTRY_LOCK:
        payload = load(path)
        key = (parse_reference(slot_name)[0] or str(slot_name or "").strip()).upper()
        slot = next((item for item in payload.get("slots", []) if str(item.get("name") or "").upper() == key), None)
        if not slot:
            raise KeyError(f"Storage slot not found: {slot_name}")
        if slot.get("occupancy") == "reserved":
            slot["occupancy"] = "empty"
            slot["occupancy_source"] = reason
            slot.pop("reservation", None)
        return save(payload, path)

def set_verification_pending(slot_name, verification=None, path=SLOTS_PATH):
    with _REGISTRY_LOCK:
        payload = load(path)
        key = (parse_reference(slot_name)[0] or str(slot_name or "").strip()).upper()
        slot = next((item for item in payload.get("slots", []) if str(item.get("name") or "").upper() == key), None)
        if not slot:
            raise KeyError(f"Storage slot not found: {slot_name}")
        slot["occupancy"] = "verification_pending"
        if verification is not None:
            slot["last_verification"] = deepcopy(verification)
        return save(payload, path)




