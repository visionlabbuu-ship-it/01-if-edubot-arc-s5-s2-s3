"""Compact, read-only context for ARC tool planning.

This module deliberately reads calibrated runtime state without issuing motion or
camera commands. Fresh object and Slot scans remain separate tools.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import ARM_PICK_CONFIG, active_robot_ids
from .storage_service import GROUP_ARM_ROUTING, load as load_storage
from .workspace_model import get_calibrated_workspace_model


def _transform_ready(transform: Any) -> bool:
    return bool(isinstance(transform, dict) and transform.get("enabled") is True)


def _iso_age_seconds(value: Any) -> float | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())
    except (TypeError, ValueError):
        return None


def build_tool_context(online_arms: list[str] | None = None) -> dict[str, Any]:
    """Return compact planning facts; never connects to or moves a robot."""
    active = active_robot_ids()
    online = set(online_arms) if online_arms is not None else None
    model = get_calibrated_workspace_model(active_arms=active, online_arms=online_arms)
    registry = load_storage()
    readiness = model.get("readiness") or {}
    arms: dict[str, Any] = {}
    model_arms = model.get("arms") or {}
    for arm_id in active:
        arm = model_arms.get(arm_id) or {}
        profile = arm.get("profile") or {}
        transform = profile.get("workspace_to_robot_transform")
        pick = (ARM_PICK_CONFIG or {}).get(arm_id) or {}
        arms[arm_id] = {
            "active": True,
            "online": (arm_id in online) if online is not None else None,
            "geometry_ready": bool(arm.get("geometry_ready")),
            "transform_ready": _transform_ready(transform),
            "pick_z": pick.get("pick_z"),
            "hover_z": pick.get("hover_z"),
            "min_reach_mm": profile.get("min_reach_mm"),
            "max_reach_mm": profile.get("max_reach_mm"),
        }
    summary = registry.get("summary") or {}
    slots = registry.get("slots") or []
    groups = {}
    for group, data in (summary.get("by_group") or {}).items():
        groups[group] = {
            "total": data.get("total", 0),
            "empty": data.get("empty", 0),
            "occupied": data.get("occupied", 0),
            "unknown": data.get("unknown", 0),
            "reserved": data.get("reserved", 0),
            "verification_pending": data.get("verification_pending", 0),
            "occupied_by_color": data.get("occupied_by_color", {}),
        }
    return {
        "ok": bool(readiness.get("workspace_ready", readiness.get("outer_box_ready", False))),
        "source_of_truth": {
            "workspace": "calibrated_workspace",
            "motion_xy": "per_arm_robot_plane[arm_id]",
            "storage": "runtime_state/capacity_slots.json",
        },
        "scan_contract": {
            "camera_scan": "objects and per-arm robot coordinates",
            "scan_storage_slots": "fresh compact Slot states plus color_locations mapping colors to occupied slots/groups",
        },
        "active_arm_ids": active,
        "arms": arms,
        "workspace": {
            "ready": bool(readiness.get("workspace_ready", readiness.get("outer_box_ready", False))),
            "storage_regions_ready": bool(readiness.get("storage_regions_ready", False)),
            "errors": model.get("errors", []),
        },
        "storage": {
            "expected_count": registry.get("expected_count", 16),
            "slot_count": len(slots),
            "summary": summary,
            "groups": groups,
            "updated_at": registry.get("updated_at"),
            "age_seconds": _iso_age_seconds(registry.get("updated_at")),
            "group_arm_routing": {arm_id: [group for group, arms in GROUP_ARM_ROUTING.items() if arm_id in arms] for arm_id in active},
        },
        "policy": {
            "sequential_motion_default": True,
            "verified_slot_state_is_authoritative": True,
            "legacy_zone_aliases": ["storage_zone", "drop_zone"],
        },
    }
