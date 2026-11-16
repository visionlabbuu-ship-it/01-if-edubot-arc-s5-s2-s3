"""Canonical calibrated workspace and intent planning helpers.

This module is deliberately free of robot I/O.  It normalizes persisted
Calibration and Storage Registry data into the model used by ARC planning.
"""
from __future__ import annotations

import math
from copy import deepcopy

from .object_calibration import apply_robot_plane_correction
from .storage_service import GROUPS, GROUP_ARM_ROUTING, normalize as normalize_storage
from .workspace_calibration import (
    apply_workspace_transform,
    calibration_arms,
    point_in_polygon,
    reach_for_local_xy,
)


WORK_ZONE_ROLES = {"custom", "work", "work_zone", "inner", "inner_box"}
STORAGE_REGION_ROLES = {"storage", "storage_region", "storage_box", "drop"}


def _name(item, fallback=""):
    return str((item or {}).get("name") or (item or {}).get("label") or fallback).strip()


def _region_id(item, index):
    return str((item or {}).get("region_id") or _name(item) or f"storage-region-{index}")


def _group(item):
    value = str((item or {}).get("group") or (item or {}).get("storage_group") or "").strip().upper()
    return value if value in GROUPS else None


def _work_zone_payload(zone):
    result = deepcopy(zone)
    result["id"] = _name(zone)
    result["name"] = result["id"]
    result["label"] = str(zone.get("label") or result["id"])
    result["role"] = "work_zone"
    result["enabled"] = bool(zone.get("enabled", True))
    return result


def _storage_region_payload(region, index):
    result = deepcopy(region)
    result["storage_region_id"] = _region_id(region, index)
    result["region_id"] = result["storage_region_id"]
    result["group_id"] = _group(region)
    result["group"] = result["group_id"]
    result["role"] = "storage_region"
    result["region_role"] = "storage_region"
    result["label"] = str(region.get("label") or _name(region, result["storage_region_id"]))
    return result


def _calibrated_handoff(calibration):
    station = deepcopy((calibration or {}).get("handoff_station") or {})
    if station and station.get("enabled", True):
        station.setdefault("id", station.get("name") or "handoff")
        station.setdefault("role", "handoff_station")
        return station
    for zone in (calibration or {}).get("zones") or []:
        if str(zone.get("role") or "").strip().lower() in {"handoff", "handoff_station"}:
            result = deepcopy(zone)
            result.setdefault("id", _name(zone, "handoff"))
            result["role"] = "handoff_station"
            return result
    return None


def _transform_ready(profile, arm_id):
    transform = (profile or {}).get("workspace_to_robot_transform") or {}
    return bool(transform.get("enabled")) and str(transform.get("arm_id") or arm_id) == str(arm_id)


def _slot_arm_plane(slot, arm_id, profile):
    existing = deepcopy(((slot.get("per_arm_robot_plane") or {}).get(arm_id)))
    if existing is not None:
        return existing
    workspace_xy = slot.get("workspace_xy")
    if not workspace_xy or not _transform_ready(profile, arm_id):
        return None
    transform = profile.get("workspace_to_robot_transform") or {}
    plane = apply_workspace_transform(transform, workspace_xy)
    correction = transform.get("robot_plane_correction") or {}
    if correction.get("enabled"):
        plane = apply_robot_plane_correction(correction, plane)
        plane["calibration"] = "object_workspace_affine_corrected"
    reach = reach_for_local_xy(plane, profile)
    plane["reachable"] = bool(reach["reachable"])
    plane["reach_radius_mm"] = float(reach["r_mm"])
    return plane


def _enrich_slot(slot, arms):
    result = deepcopy(slot)
    result["role"] = "storage"
    result["storage_group"] = result.get("group")
    result["allowed_arms"] = list(GROUP_ARM_ROUTING.get(result.get("group"), ()))
    result.setdefault("storage_region_id", None)
    planes = deepcopy(result.get("per_arm_robot_plane") or {})
    status = {}
    reachable_by = []
    for arm_id, arm in arms.items():
        profile = arm["profile"]
        if result["allowed_arms"] and arm_id not in result["allowed_arms"]:
            status[arm_id] = "slot_unreachable"
            continue
        if not arm["transform_ready"]:
            status[arm_id] = "transform_not_ready"
            continue
        plane = _slot_arm_plane(result, arm_id, profile)
        if plane is None:
            status[arm_id] = "transform_not_ready"
            continue
        planes[arm_id] = plane
        if bool(plane.get("reachable")):
            reachable_by.append(arm_id)
            status[arm_id] = "reachable"
        else:
            status[arm_id] = "slot_unreachable"
    result["per_arm_robot_plane"] = planes
    result["reachable_by"] = reachable_by
    result["reachability_status"] = status
    return result


def build_calibrated_workspace(calibration, registry=None, active_arms=None, online_arms=None):
    calibration = deepcopy(calibration or {})
    registry = normalize_storage(registry)
    active = set(active_arms) if active_arms is not None else None
    online = set(online_arms) if online_arms is not None else None

    arms = {}
    for arm_id, profile in calibration_arms(calibration).items():
        transform_ready = _transform_ready(profile, arm_id)
        has_geometry = (
            bool(profile.get("edge"))
            and float(profile.get("robot_center_to_outer_center_mm") or 0.0) > 0.0
            and float(profile.get("min_reach_mm") or 0.0) >= 0.0
            and float(profile.get("max_reach_mm") or 0.0) > float(profile.get("min_reach_mm") or 0.0)
        )
        # An applied per-arm transform proves calibration even if an older UI
        # left the seeded configured flag false.
        configured = bool(profile.get("configured", True)) or transform_ready
        arms[arm_id] = {
            "arm_id": arm_id,
            "profile": deepcopy(profile),
            "active": configured if active is None else arm_id in active,
            "online": None if online is None else arm_id in online,
            "geometry_ready": configured and has_geometry,
            "transform_ready": transform_ready,
        }

    explicit_regions = calibration.get("storage_regions") or calibration.get("storage_boxes")
    work_zones = []
    fallback_regions = []
    for zone in calibration.get("zones") or []:
        role = str(zone.get("role") or "custom").strip().lower()
        if role == "ignore":
            continue
        if role in STORAGE_REGION_ROLES:
            fallback_regions.append(zone)
        elif role not in {"handoff", "handoff_station"}:
            work_zones.append(_work_zone_payload(zone))

    region_source = explicit_regions if explicit_regions is not None else fallback_regions
    storage_regions = [_storage_region_payload(region, index) for index, region in enumerate(region_source or [], start=1)]
    assigned_groups = [region["group_id"] for region in storage_regions if region.get("group_id")]
    duplicates = sorted({group for group in assigned_groups if assigned_groups.count(group) > 1})
    missing_groups = [group for group in GROUPS if group not in assigned_groups]

    slots = [_enrich_slot(slot, arms) for slot in registry.get("slots") or []]
    slots_by_name = {str(slot.get("name")): slot for slot in slots if slot.get("name")}
    work_zones_by_id = {zone["id"]: zone for zone in work_zones if zone.get("id")}
    regions_by_group = {region["group_id"]: region for region in storage_regions if region.get("group_id")}
    handoff_station = _calibrated_handoff(calibration)
    outer = deepcopy(calibration.get("outer_box") or {})

    readiness = {
        "workspace_ready": bool(outer and float(outer.get("width_mm") or 0.0) > 0.0 and float(outer.get("height_mm") or 0.0) > 0.0),
        "work_zones_ready": bool(work_zones),
        "storage_regions_ready": len(storage_regions) == 4 and not missing_groups and not duplicates,
        "storage_registry_ready": len(slots) == int(registry.get("expected_count") or 16),
        "handoff_ready": handoff_station is not None,
        "arms": {
            arm_id: {
                "active": arm["active"],
                "online": arm["online"],
                "geometry_ready": arm["geometry_ready"],
                "transform_ready": arm["transform_ready"],
            }
            for arm_id, arm in arms.items()
        },
    }
    errors = []
    if duplicates:
        errors.append({"error_code": "storage_group_duplicate", "groups": duplicates})
    if storage_regions and missing_groups:
        errors.append({"error_code": "storage_group_missing", "groups": missing_groups})

    return {
        "ok": readiness["workspace_ready"],
        "version": 1,
        "outer_box": outer,
        "work_zones": work_zones_by_id,
        "storage_regions": regions_by_group,
        "storage_groups": deepcopy(registry.get("groups") or {}),
        "storage_slots": slots,
        "storage_slots_by_name": slots_by_name,
        "arms": arms,
        "handoff_station": handoff_station,
        "readiness": readiness,
        "errors": errors,
        "compatibility_aliases": {
            "storage_zone": {"target_type": "storage", "requires_policy": True},
            "drop_zone": {"target_type": "storage", "requires_policy": True},
        },
    }


def resolve_work_zone(model, reference):
    key = str(reference or "").strip().lower()
    for zone in (model or {}).get("work_zones", {}).values():
        aliases = {str(zone.get(field) or "").strip().lower() for field in ("id", "name", "label")}
        if key and key in aliases:
            return {"ok": True, "zone": deepcopy(zone), "zone_id": zone.get("id")}
    return {"ok": False, "error_code": "zone_not_found", "reference": reference}


def containing_work_zones(model, workspace_xy):
    if not workspace_xy:
        return []
    return [
        zone_id
        for zone_id, zone in (model or {}).get("work_zones", {}).items()
        if zone.get("enabled", True) and point_in_polygon(workspace_xy, zone.get("polygon_mm") or [])
    ]


def enrich_object_zones(model, obj):
    result = deepcopy(obj)
    zone_ids = containing_work_zones(model, result.get("workspace_xy"))
    result["work_zone_ids"] = zone_ids
    result["source_zone_id"] = zone_ids[0] if len(zone_ids) == 1 else None
    result["zone_ambiguous"] = len(zone_ids) > 1
    return result


def filter_objects(model, objects, zone_reference=None, color="any"):
    zone_id = None
    if zone_reference:
        resolved = resolve_work_zone(model, zone_reference)
        if not resolved.get("ok"):
            return {**resolved, "objects": []}
        zone_id = resolved["zone_id"]
    selected = []
    for original in objects or []:
        obj = enrich_object_zones(model, original)
        if zone_id and zone_id not in obj["work_zone_ids"]:
            continue
        if str(color or "any").lower() != "any" and str(obj.get("color") or "").lower() != str(color).lower():
            continue
        selected.append(obj)
    return {"ok": True, "zone_id": zone_id, "objects": selected}


def _distance_for_arm(source, destination, arm_id):
    source_xy = ((source.get("per_arm_robot_plane") or {}).get(arm_id)) or {}
    destination_xy = ((destination.get("per_arm_robot_plane") or {}).get(arm_id)) or {}
    if not source_xy or not destination_xy:
        return float("inf")
    return math.hypot(float(destination_xy["x"]) - float(source_xy["x"]), float(destination_xy["y"]) - float(source_xy["y"]))


def _resolve_destination(model, reference):
    key = str(reference or "").strip().upper()
    if key in model.get("storage_slots_by_name", {}):
        slot = model["storage_slots_by_name"][key]
        if slot.get("occupancy") != "empty":
            code = "capacity_unknown" if slot.get("occupancy") == "unknown" else "slot_unavailable"
            return {"ok": False, "error_code": code, "slot": deepcopy(slot)}
        return {"ok": True, "target_type": "storage_slot", "target": deepcopy(slot)}
    group = key if key in GROUPS else None
    if group:
        slots = [
            slot for slot in model.get("storage_slots", [])
            if slot.get("group") == group and slot.get("occupancy") == "empty"
        ]
        if not slots:
            return {"ok": False, "error_code": "capacity_full", "group": group}
        slots.sort(key=lambda slot: (int(slot.get("index_in_cluster") or 0), str(slot.get("name") or "")))
        return {"ok": True, "target_type": "storage_slot", "target": deepcopy(slots[0])}
    for region in model.get("storage_regions", {}).values():
        aliases = {str(region.get(field) or "").strip().lower() for field in ("group_id", "label", "region_id", "storage_region_id")}
        if str(reference or "").strip().lower() in aliases and region.get("group_id"):
            return _resolve_destination(model, region["group_id"])

    zone = resolve_work_zone(model, reference)
    if zone.get("ok"):
        return {"ok": True, "target_type": "work_zone", "target": zone["zone"]}
    if str(reference or "").strip().lower() in {"storage_zone", "drop_zone"}:
        return {"ok": False, "error_code": "needs_storage_policy", "reference": reference}
    return {"ok": False, "error_code": "destination_not_found", "reference": reference}


def plan_calibrated_intent(
    model,
    objects,
    destination_reference,
    source_zone=None,
    color="any",
    robot_id=None,
    active_arms=None,
    online_arms=None,
):
    """Produce a deterministic direct or handoff dry-run plan without robot I/O."""
    filtered = filter_objects(model, objects, zone_reference=source_zone, color=color)
    if not filtered.get("ok"):
        return filtered
    if not filtered["objects"]:
        return {"ok": False, "error_code": "object_not_in_zone" if source_zone else "object_not_found", "source_zone": source_zone}
    source = filtered["objects"][0]
    destination = _resolve_destination(model, destination_reference)
    if not destination.get("ok"):
        return destination
    target = destination["target"]

    active = set(active_arms) if active_arms is not None else {
        arm_id for arm_id, arm in model.get("arms", {}).items() if arm.get("active")
    }
    online = set(online_arms) if online_arms is not None else {
        arm_id for arm_id, arm in model.get("arms", {}).items() if arm.get("online") is not False
    }
    if robot_id and robot_id not in active:
        return {"ok": False, "error_code": "arm_inactive", "arm_id": robot_id}
    if robot_id and robot_id not in online:
        return {"ok": False, "error_code": "arm_offline", "arm_id": robot_id}

    source_arms = set(source.get("reachable_by") or []) & active & online
    destination_arms = set(target.get("reachable_by") or []) & active & online
    if robot_id:
        source_arms &= {robot_id}
        destination_arms &= {robot_id}
    direct = source_arms & destination_arms
    if direct:
        ranked = sorted(((_distance_for_arm(source, target, arm_id), arm_id) for arm_id in direct), key=lambda item: (item[0], item[1]))
        _, selected = ranked[0]
        return {
            "ok": True,
            "plan_type": "direct",
            "selected_arm": selected,
            "requires_handoff": False,
            "source": source,
            "destination": target,
            "steps": [
                {"action": "pick", "arm_id": selected, "object_id": source.get("id")},
                {"action": "place", "arm_id": selected, "target": target.get("name") or target.get("id")},
                {"action": "verify", "target": target.get("name") or target.get("id")},
            ],
            "reasoning_summary": f"{selected} can reach both source and destination",
        }

    if robot_id:
        code = "transform_not_ready" if not model.get("arms", {}).get(robot_id, {}).get("transform_ready") else "slot_unreachable"
        return {"ok": False, "error_code": code, "arm_id": robot_id}
    if not source_arms:
        return {"ok": False, "error_code": "source_unreachable"}
    if not destination_arms:
        return {"ok": False, "error_code": "slot_unreachable" if destination["target_type"] == "storage_slot" else "destination_unreachable"}
    if not model.get("readiness", {}).get("handoff_ready"):
        return {"ok": False, "error_code": "handoff_unavailable", "source_arms": sorted(source_arms), "destination_arms": sorted(destination_arms)}

    source_arm = sorted(source_arms)[0]
    destination_arm = sorted(destination_arms)[0]
    return {
        "ok": True,
        "plan_type": "station_mediated_handoff",
        "selected_arm": destination_arm,
        "support_arm": source_arm,
        "requires_handoff": True,
        "source": source,
        "destination": target,
        "handoff_station": deepcopy(model.get("handoff_station")),
        "steps": [
            {"action": "pick", "arm_id": source_arm, "object_id": source.get("id")},
            {"action": "place_handoff", "arm_id": source_arm},
            {"action": "retreat", "arm_id": source_arm},
            {"action": "verify_shared_zone_clear"},
            {"action": "pick_handoff", "arm_id": destination_arm},
            {"action": "place", "arm_id": destination_arm, "target": target.get("name") or target.get("id")},
            {"action": "verify", "target": target.get("name") or target.get("id")},
        ],
        "reasoning_summary": f"{source_arm} reaches the source and {destination_arm} reaches the destination",
    }

