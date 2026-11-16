import math

from .calibrated_workspace import build_calibrated_workspace, plan_calibrated_intent

from .config import (
    ARM_FRAME_TRANSFORMS,
    ARM_NAMED_TARGETS,
    ARM_PICK_CONFIG,
    ROBOT_SAFETY_CONFIG,
    active_config,
    active_robot_ids,
)
from .robot_guardrails import RobotSafetyGuardrail
from .surface_model import phase_z_from_pick_config, surface_z_for_named_target, surface_z_for_pose
from .storage_service import load as load_storage_registry
from .workspace_calibration import (
    DEFAULT_CALIBRATION_PATH,
    DEFAULT_DETECTION_REPORT_PATH,
    resolve_workspace_calibration,
    workspace_resolution_status,
)


STANDARD_PARK_TARGETS = {
    "Arm1": "home",
    "Arm2": "home",
}

STANDARD_DESTINATIONS = {
    "Arm1": "storage_zone",
    "Arm2": "drop_zone",
}

WORKSPACE_BLOCKS = {
    "Block 1,1": {"x_range_mm": [0.0, 200.0], "y_range_mm": [200.0, 400.0], "center_mm": [100.0, 300.0]},
    "Block 1,2": {"x_range_mm": [200.0, 400.0], "y_range_mm": [200.0, 400.0], "center_mm": [300.0, 300.0]},
    "Block 1,3": {"x_range_mm": [400.0, 600.0], "y_range_mm": [200.0, 400.0], "center_mm": [500.0, 300.0]},
    "Block 2,1": {"x_range_mm": [0.0, 200.0], "y_range_mm": [0.0, 200.0], "center_mm": [100.0, 100.0]},
    "Block 2,2": {"x_range_mm": [200.0, 400.0], "y_range_mm": [0.0, 200.0], "center_mm": [300.0, 100.0]},
    "Block 2,3": {"x_range_mm": [400.0, 600.0], "y_range_mm": [0.0, 200.0], "center_mm": [500.0, 100.0]},
}

ZONE_BOUNDS = {
    "storage_zone": {"x_range_mm": [0.0, 200.0], "y_range_mm": [-200.0, 0.0]},
    "drop_zone": {"x_range_mm": [400.0, 600.0], "y_range_mm": [-200.0, 0.0]},
    "handoff_zone": {"x_range_mm": [350.0, 440.0], "y_range_mm": [-80.0, 80.0]},
}

ZONE_ALLOWED_ARMS = {
    "storage_zone": ["Arm1"],
    "drop_zone": ["Arm2"],
    "handoff_zone": ["Arm1", "Arm2"],
}


def _guardrails():
    return RobotSafetyGuardrail(
        ROBOT_SAFETY_CONFIG,
        active_config(ARM_FRAME_TRANSFORMS),
        active_arm_ids=active_robot_ids(ARM_FRAME_TRANSFORMS),
    )


def _active_arms():
    return active_robot_ids(ARM_NAMED_TARGETS)


def _active_dict(mapping):
    active = set(_active_arms())
    return {
        key: value
        for key, value in mapping.items()
        if key in active
    }


def _active_zone_arms(zone_name):
    active = set(_active_arms())
    return [
        arm_id
        for arm_id in ZONE_ALLOWED_ARMS.get(zone_name, [])
        if arm_id in active
    ]


def normalize_arm_id(arm_id):
    value = str(arm_id or "").strip().lower().replace(" ", "")
    if value in {"arm1", "1", "left", "first"}:
        normalized = "Arm1"
    elif value in {"arm2", "2", "right", "second"}:
        normalized = "Arm2"
    elif arm_id in ARM_NAMED_TARGETS:
        normalized = arm_id
    else:
        normalized = None
    if normalized and normalized in _active_arms():
        return normalized
    return None


def other_arm_id(arm_id):
    arm = normalize_arm_id(arm_id)
    if len(_active_arms()) < 2:
        return None
    if arm == "Arm1" and "Arm2" in _active_arms():
        return "Arm2"
    if arm == "Arm2" and "Arm1" in _active_arms():
        return "Arm1"
    return None


def resolve_destination_for_arm(arm_id, destination=None):
    arm = normalize_arm_id(arm_id)
    if not arm:
        return {
            "ok": False,
            "destination": destination,
            "requested_destination": destination,
            "error": f"Unknown arm: {arm_id}",
        }

    requested = str(destination or STANDARD_DESTINATIONS[arm]).strip()
    target = ARM_NAMED_TARGETS.get(arm, {}).get(requested)
    if target and not target.get("deprecated_alias_of"):
        return {
            "ok": True,
            "arm_id": arm,
            "destination": requested,
            "requested_destination": requested,
            "rewritten": False,
        }

    if len(_active_arms()) == 1 and arm == _active_arms()[0]:
        fallback = (target or {}).get("deprecated_alias_of") or STANDARD_DESTINATIONS[arm]
        return {
            "ok": True,
            "arm_id": arm,
            "destination": fallback,
            "requested_destination": requested,
            "rewritten": fallback != requested,
            "reason": "single_arm_destination_fallback",
        }

    return {
        "ok": False,
        "arm_id": arm,
        "destination": requested,
        "requested_destination": requested,
        "error": f"Destination '{requested}' is not configured for {arm}",
        "recommended_destination": STANDARD_DESTINATIONS[arm],
    }


def _target_pose(target, phase):
    phase_key = str(phase or "").strip().lower()
    if phase_key == "hover":
        z = target.get("hover_z")
    elif phase_key == "surface":
        z = surface_z_for_named_target(target)
    else:
        z = None
    if z is None:
        return None
    return {
        "x": float(target["x"]),
        "y": float(target["y"]),
        "z": float(z),
        "r": float(target.get("r", 0.0)),
    }


def _named_target_payload(guardrails, arm_id, name, target):
    phases = {}
    for phase in ("hover", "surface"):
        pose = _target_pose(target, phase)
        if not pose:
            continue
        global_x, global_y = guardrails.local_xy_to_global_xy(arm_id, pose["x"], pose["y"])
        phases[phase] = {
            "local_pose": pose,
            "global_xy_mm": {"x": global_x, "y": global_y},
            "in_shared_zone": guardrails.global_x_in_shared_zone(global_x),
        }
    local_xy = {"x": float(target["x"]), "y": float(target["y"])}
    global_x, global_y = guardrails.local_xy_to_global_xy(arm_id, local_xy["x"], local_xy["y"])
    return {
        "name": name,
        "local_xy_mm": local_xy,
        "global_xy_mm": {"x": global_x, "y": global_y},
        "in_shared_zone": guardrails.global_x_in_shared_zone(global_x),
        "role": target.get("role"),
        "deprecated_alias_of": target.get("deprecated_alias_of"),
        "verified": target.get("verified", True),
        "phases": phases,
    }


def _bounds_polygon(bounds):
    x0, x1 = bounds["x_range_mm"]
    y0, y1 = bounds["y_range_mm"]
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _default_heights_for_zone(zone_name):
    heights = {}
    for arm_id in _active_zone_arms(zone_name):
        target = ARM_NAMED_TARGETS.get(arm_id, {}).get(zone_name)
        if target:
            heights[arm_id] = {
                "hover_z": float(target["hover_z"]),
                "surface_z": surface_z_for_named_target(target),
            }
            continue
        cfg = ARM_PICK_CONFIG.get(arm_id, ARM_PICK_CONFIG["Arm1"])
        heights[arm_id] = {
            "hover_z": float(cfg["hover_z"]),
            "surface_z": surface_z_for_pose(arm_id, 0.0, 0.0),
            "surface_z_model": "fixed",
        }
    return heights


def _zone_layout_payload(zone_name):
    bounds = ZONE_BOUNDS[zone_name]
    allowed_arms = _active_zone_arms(zone_name)
    return {
        "name": zone_name,
        "semantics": "region",
        "bounds": bounds,
        "polygon_mm": _bounds_polygon(bounds),
        "allowed_arms": allowed_arms,
        "default_heights_by_arm": _default_heights_for_zone(zone_name),
        "placement_slots": [
            {
                "slot_id": f"{zone_name}:auto",
                "selection": "runtime_or_planner_selects_safe_point",
                "occupied": "unknown_until_scan_or_runtime_assignment",
            }
        ],
        "safe_approach_policy": {
            "approach_phase": "hover",
            "contact_phase": "surface",
            "retreat_phase": "hover",
            "release_allowed_phase": "surface",
            "planner_selects_point_in_region": True,
        },
    }


def _runtime_workspace_calibration():
    return _runtime_workspace_resolution().get("calibration")


def _runtime_workspace_resolution():
    return resolve_workspace_calibration(
        DEFAULT_CALIBRATION_PATH,
        DEFAULT_DETECTION_REPORT_PATH,
        safety_config=ROBOT_SAFETY_CONFIG,
    )


def get_calibrated_workspace_model(active_arms=None, online_arms=None):
    """Return the canonical read-only model used by ARC planners and tools."""
    resolution = _runtime_workspace_resolution()
    return build_calibrated_workspace(
        resolution.get("calibration") or {},
        registry=load_storage_registry(),
        active_arms=active_arms if active_arms is not None else _active_arms(),
        online_arms=online_arms,
    )


def list_workspace_zones():
    model = get_calibrated_workspace_model()
    return {
        "ok": model.get("ok", False),
        "outer_box": model.get("outer_box"),
        "work_zones": list(model.get("work_zones", {}).values()),
        "readiness": model.get("readiness"),
        "errors": model.get("errors", []),
    }


def list_storage_groups():
    model = get_calibrated_workspace_model()
    return {
        "ok": model.get("readiness", {}).get("storage_regions_ready", False),
        "groups": model.get("storage_groups", {}),
        "regions": model.get("storage_regions", {}),
        "readiness": model.get("readiness"),
        "errors": model.get("errors", []),
    }


def plan_workspace_intent(
    objects,
    destination_reference,
    source_zone=None,
    color="any",
    robot_id=None,
    active_arms=None,
    online_arms=None,
):
    model = get_calibrated_workspace_model(active_arms=active_arms, online_arms=online_arms)
    return plan_calibrated_intent(
        model,
        objects,
        destination_reference,
        source_zone=source_zone,
        color=color,
        robot_id=robot_id,
        active_arms=active_arms,
        online_arms=online_arms,
    )


def _calibrated_zone_payload(zone):
    return {
        "name": zone.get("name"),
        "semantics": "region",
        "role": zone.get("role", "custom"),
        "bounds": zone.get("bounds_mm"),
        "polygon_mm": zone.get("polygon_mm"),
        "center_mm": zone.get("center_mm"),
        "configured_size_mm": {
            "width": float(zone.get("width_mm", 0.0)),
            "height": float(zone.get("height_mm", 0.0)),
        },
        "robot_local_center_mm": zone.get("robot_local_center_mm"),
        "reach_preview": zone.get("reach_preview"),
        "allowed_arms": [
            zone.get("robot_id")
        ] if zone.get("robot_id") else [],
        "placement_slots": [
            {
                "slot_id": f"{zone.get('name')}:auto",
                "selection": "runtime_or_planner_selects_safe_point",
                "occupied": "unknown_until_scan_or_runtime_assignment",
            }
        ],
    }


def build_workspace_layout():
    guardrails = _guardrails()
    workspace_resolution = _runtime_workspace_resolution()
    calibration = workspace_resolution.get("calibration")
    calibration_status = workspace_resolution_status(workspace_resolution)
    canonical = build_calibrated_workspace(
        calibration or {},
        registry=load_storage_registry(),
        active_arms=_active_arms(),
    )
    named_targets = {}
    for arm_id, targets in _active_dict(ARM_NAMED_TARGETS).items():
        named_targets[arm_id] = {
            name: _named_target_payload(guardrails, arm_id, name, target)
            for name, target in targets.items()
        }
    active_arms = _active_arms()
    arm_payloads = {
        "Arm1": {
            "side": "left",
            "faces": "Arm2" if "Arm2" in active_arms else None,
            "aliases": ["Arm1", "arm 1", "left arm"],
            "standard_park_target": STANDARD_PARK_TARGETS["Arm1"],
            "standard_destination": STANDARD_DESTINATIONS["Arm1"],
        },
        "Arm2": {
            "side": "right",
            "faces": "Arm1" if "Arm1" in active_arms else None,
            "aliases": ["Arm2", "arm 2", "right arm"],
            "standard_park_target": STANDARD_PARK_TARGETS["Arm2"],
            "standard_destination": STANDARD_DESTINATIONS["Arm2"],
        },
    }

    calibrated_workspace = None
    if calibration:
        outer = calibration["outer_box"]
        robot = calibration["robot"]
        robot_id = str(robot.get("robot_id", "Arm1"))
        zones = canonical.get("work_zones", {})
        calibrated_workspace = {
            "calibration_path": workspace_resolution.get("calibration_path") or str(DEFAULT_CALIBRATION_PATH),
            "source": workspace_resolution.get("source"),
            "saved": bool(workspace_resolution.get("saved")),
            "stale_saved": bool(workspace_resolution.get("stale_saved")),
            "report_timestamp": workspace_resolution.get("report_timestamp"),
            "saved_report_timestamp": workspace_resolution.get("saved_report_timestamp"),
            "main_workspace_mm": {
                "x_range": [0.0, float(outer["width_mm"])],
                "y_range": [0.0, float(outer["height_mm"])],
            },
            "outer_box": {
                "width_mm": float(outer["width_mm"]),
                "height_mm": float(outer["height_mm"]),
                "center_mm": outer.get("center_mm"),
                "corners_px": outer.get("corners_px"),
            },
            "robot": {
                "robot_id": robot_id,
                "edge": robot.get("edge"),
                "robot_center_to_outer_center_mm": robot.get("robot_center_to_outer_center_mm"),
                "robot_center_workspace_xy_mm": robot.get("robot_center_workspace_xy_mm"),
                "min_reach_mm": robot.get("min_reach_mm"),
                "max_reach_mm": robot.get("max_reach_mm"),
            },
            "zones": zones,
            "storage_regions": canonical.get("storage_regions", {}),
            "storage_slots": canonical.get("storage_slots", []),
            "readiness": canonical.get("readiness", {}),
        }

    return {
        "ok": True,
        "workspace_layout": True,
        "source": {
            "runtime_geometry": (
                workspace_resolution.get("calibration_path")
                if calibration
                else "config.py ARM_FRAME_TRANSFORMS"
            ),
            "workspace_calibration": calibration_status,
            "layout_reference": "workspace_layout_reference.md",
            "policy": "Camera scan may use the latest valid detection draft; pick execution requires saved current workspace calibration.",
        },
        "coordinate_frames": {
            "motion_commands": "arm-local robot plane",
            "camera_objects": "camera_scan per_arm_robot_plane[arm_id]",
            "working_space": "outer_box pixel frame mapped to millimeters" if calibration else "legacy fixed workspace millimeters",
            "shared_zone": "global X derived from ARM_FRAME_TRANSFORMS",
        },
        "arms": {
            arm_id: arm_payloads[arm_id]
            for arm_id in active_arms
            if arm_id in arm_payloads
        },
        "workspace": {
            "main_workspace_mm": (
                calibrated_workspace["main_workspace_mm"]
                if calibrated_workspace
                else {"x_range": [0.0, 600.0], "y_range": [0.0, 400.0]}
            ),
            "blocks": canonical.get("work_zones", {}) if calibration else WORKSPACE_BLOCKS,
            "zones": canonical.get("work_zones", {}) if calibration else {
                zone_name: _zone_layout_payload(zone_name)
                for zone_name in ZONE_BOUNDS.keys()
            },
            "storage_regions": canonical.get("storage_regions", {}),
            "storage_groups": canonical.get("storage_groups", {}),
            "storage_slots": canonical.get("storage_slots", []),
            "readiness": canonical.get("readiness", {}),
            "calibrated": calibrated_workspace,
            "external_zones_reference": {
                name: ZONE_BOUNDS[name]
                for name in ("storage_zone", "drop_zone")
            },
        },
        "safety_geometry": guardrails.safety_geometry(),
        "named_targets": named_targets,
        "standard_parking": {
            "clear_workspace": _active_dict(STANDARD_PARK_TARGETS),
            "home_is_clear_workspace": True,
            "reason": "Configured home poses are outside the runtime shared zone.",
        },
        "handoff_contract": {
            "station": canonical.get("handoff_station"),
            "mode": "station_mediated",
            "enabled": len(active_arms) >= 2 and canonical.get("readiness", {}).get("handoff_ready", False),
            "error_code": None if canonical.get("readiness", {}).get("handoff_ready", False) else "handoff_unavailable",
            "direct_gripper_to_gripper_allowed": False,
            "planner_tool": "plan_handoff",
            "execution_tools": ["move_to_work_pose", "move_to_named_pose", "set_tool_state", "get_workspace_state"],
            "forbidden_tools_during_handoff": [
                "pick_detected_place",
                "pick_all_detected_place",
                "pick_at_pose",
                "place_at_named",
                "place_at_pose",
            ],
            "rules": [
                "The requested arm remains the intent arm and owns the final goal.",
                "The support arm may only pick, place at handoff_zone, release, and retreat.",
                "The receiver picks from handoff_zone, never from the original object pose.",
                "The support arm must retreat to its standard park target before the receiver enters handoff_zone.",
                "Stop after any failed, aborted, or guardrail-blocked step.",
            ],
        },
    }


def _xy_distance(a, b):
    dx = float(a["x"]) - float(b["x"])
    dy = float(a["y"]) - float(b["y"])
    return math.sqrt(dx * dx + dy * dy)


def _nearest_named_target(arm_id, pose, xy_tolerance_mm=35.0, z_tolerance_mm=35.0):
    candidates = []
    for name, target in ARM_NAMED_TARGETS.get(arm_id, {}).items():
        for phase in ("hover", "surface"):
            target_pose = _target_pose(target, phase)
            if not target_pose:
                continue
            xy_distance = _xy_distance(pose, target_pose)
            z_distance = abs(float(pose.get("z", 0.0)) - target_pose["z"])
            candidates.append(
                {
                    "name": name,
                    "phase": phase,
                    "xy_distance_mm": xy_distance,
                    "z_distance_mm": z_distance,
                    "at_target": xy_distance <= xy_tolerance_mm and z_distance <= z_tolerance_mm,
                }
            )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (not item["at_target"], item["xy_distance_mm"] + item["z_distance_mm"]))
    return candidates[0]


def build_workspace_state(pose_provider):
    guardrails = _guardrails()
    active_arms = _active_arms()
    canonical = get_calibrated_workspace_model(active_arms=active_arms)
    arms = {}
    occupied_by = []
    zone_occupancy = {
        zone_name: {
            "occupied_by_arms": [],
            "placement_slots": [
                {
                    "slot_id": f"{zone_name}:auto",
                    "occupied": "unknown",
                    "occupancy_source": "camera_scan_or_runtime_assignment_required",
                }
            ],
        }
        for zone_name in ZONE_BOUNDS.keys()
    }

    for arm_id in sorted(_active_arms()):
        pose_result = pose_provider(arm_id)
        if not pose_result.get("ok"):
            arms[arm_id] = {
                "ok": False,
                "robot_id": arm_id,
                "error": pose_result.get("error", "Pose unavailable"),
                "standard_park_target": STANDARD_PARK_TARGETS.get(arm_id),
                "holding": "unknown",
            }
            continue

        pose = pose_result["pose"]
        global_x, global_y = guardrails.local_xy_to_global_xy(arm_id, pose["x"], pose.get("y", 0.0))
        in_shared_zone = guardrails.global_x_in_shared_zone(global_x)
        if in_shared_zone:
            occupied_by.append(arm_id)
        nearest_target = _nearest_named_target(arm_id, pose)
        if nearest_target and nearest_target["at_target"] and nearest_target["name"] in zone_occupancy:
            zone_occupancy[nearest_target["name"]]["occupied_by_arms"].append(arm_id)
        park_target = STANDARD_PARK_TARGETS.get(arm_id)
        arms[arm_id] = {
            "ok": True,
            "robot_id": arm_id,
            "local_pose": pose,
            "global_xy_mm": {"x": global_x, "y": global_y},
            "in_shared_zone": in_shared_zone,
            "nearest_named_target": nearest_target,
            "standard_park_target": park_target,
            "is_at_standard_park": bool(nearest_target and nearest_target["name"] == park_target and nearest_target["at_target"]),
            "holding": "unknown",
            "holding_contract": "ARC cannot infer a held object from pose alone; only successful tool-state and verification steps may support that claim.",
        }

    return {
        "ok": True,
        "workspace_state": True,
        "safety_geometry": guardrails.safety_geometry(),
        "arms": arms,
        "shared_zone_occupancy": {
            "occupied_by": occupied_by,
            "is_clear": len(occupied_by) == 0,
            "requires_retreat_before_other_enters": len(active_arms) > 1 and len(occupied_by) > 0,
        },
        "zones": {
            zone_id: {**zone, "occupancy": {"occupied_by_arms": [], "is_clear_of_arms": True}}
            for zone_id, zone in canonical.get("work_zones", {}).items()
        },
        "storage": {
            "regions": canonical.get("storage_regions", {}),
            "groups": canonical.get("storage_groups", {}),
            "slots": canonical.get("storage_slots", []),
        },
        "readiness": canonical.get("readiness", {}),
        "legacy_named_zone_occupancy": zone_occupancy,
        "standard_parking": {
            "clear_workspace": _active_dict(STANDARD_PARK_TARGETS),
            "home_is_clear_workspace": True,
        },
    }


def _tool_step(step_id, tool, params, purpose, success_contract=None):
    return {
        "step_id": step_id,
        "tool": tool,
        "params": params,
        "purpose": purpose,
        "success_contract": success_contract or "Continue only if the tool result reports success and is not aborted, faulted, or guardrail-blocked.",
    }


def build_handoff_plan(intent_arm, object_x, object_y, destination=None, support_arm=None, object_id=None, color=None, camera_id="Cam1"):
    if len(_active_arms()) < 2:
        return {
            "ok": False,
            "plan_type": "station_mediated_handoff",
            "error": "Handoff planning requires two active arms; current ARC runtime is in single-arm mode.",
            "active_arm_ids": _active_arms(),
            "recommended_action": "use build_pick_place_plan with the active arm or enable two-arm mode",
        }

    intent = normalize_arm_id(intent_arm)
    if not intent:
        return {"ok": False, "error": f"Unknown intent arm: {intent_arm}"}

    support = normalize_arm_id(support_arm) if support_arm else other_arm_id(intent)
    if not support:
        return {"ok": False, "error": f"Unknown support arm: {support_arm}"}
    if support == intent:
        return {"ok": False, "error": "support_arm must be different from intent_arm"}

    destination_name = str(destination or STANDARD_DESTINATIONS[intent]).strip()
    if destination_name not in ARM_NAMED_TARGETS.get(intent, {}):
        return {
            "ok": False,
            "error": f"Destination '{destination_name}' is not configured for {intent}",
            "recommended_destination": STANDARD_DESTINATIONS[intent],
        }

    required_targets = [
        (support, "handoff_zone"),
        (support, STANDARD_PARK_TARGETS[support]),
        (intent, "handoff_zone"),
        (intent, STANDARD_PARK_TARGETS[intent]),
        (intent, destination_name),
    ]
    for arm_id, target_name in required_targets:
        if target_name not in ARM_NAMED_TARGETS.get(arm_id, {}):
            return {"ok": False, "error": f"Named target '{target_name}' is not configured for {arm_id}"}

    guardrails = _guardrails()
    support_pick_z = surface_z_for_pose(support, object_x, object_y)
    support_pick_pose = {"x": float(object_x), "y": float(object_y), "z": support_pick_z, "r": 0.0}
    checks = {
        "support_pick_surface": guardrails.check_pose(
            support,
            support_pick_pose,
            other_poses={},
            operation="plan_handoff:support_pick_surface",
            check_collision=False,
        ),
        "support_handoff_surface": guardrails.check_pose(
            support,
            _target_pose(ARM_NAMED_TARGETS[support]["handoff_zone"], "surface"),
            other_poses={},
            operation="plan_handoff:support_handoff_surface",
            check_collision=False,
        ),
        "intent_handoff_surface": guardrails.check_pose(
            intent,
            _target_pose(ARM_NAMED_TARGETS[intent]["handoff_zone"], "surface"),
            other_poses={},
            operation="plan_handoff:intent_handoff_surface",
            check_collision=False,
        ),
        "intent_destination_surface": guardrails.check_pose(
            intent,
            _target_pose(ARM_NAMED_TARGETS[intent][destination_name], "surface"),
            other_poses={},
            operation="plan_handoff:intent_destination_surface",
            check_collision=False,
        ),
    }
    failed_checks = {name: result for name, result in checks.items() if not result.get("ok")}
    if failed_checks:
        return {
            "ok": False,
            "plan_type": "station_mediated_handoff",
            "intent_arm": intent,
            "support_arm": support,
            "object": {"id": object_id, "color": color, "support_arm_local_xy": {"x": float(object_x), "y": float(object_y)}},
            "destination": destination_name,
            "static_checks": checks,
            "error": "Handoff plan failed static reach checks.",
            "recommended_action": "rescan_choose_different_handoff_station_or_replan",
        }

    steps = [
        _tool_step(
            "park_intent_arm",
            "move_to_named_pose",
            {"arm_id": intent, "destination": STANDARD_PARK_TARGETS[intent], "phase": "hover"},
            "Park the requested receiver arm outside the shared zone before the support arm enters.",
        ),
        _tool_step(
            "inspect_workspace_before_support_enters",
            "get_workspace_state",
            {"must_be_clear_of": [intent]},
            "Confirm the shared zone is not occupied by the receiver arm before continuing.",
        ),
        _tool_step(
            "support_hover_object",
            "move_to_work_pose",
            {"arm_id": support, "x": float(object_x), "y": float(object_y), "phase": "hover", "r": 0.0},
            "Move the support arm above the source object using support-arm local coordinates from camera_scan.",
        ),
        _tool_step(
            "support_descend_object",
            "move_to_work_pose",
            {"arm_id": support, "x": float(object_x), "y": float(object_y), "phase": "surface", "r": 0.0},
            "Descend the support arm to the object surface.",
        ),
        _tool_step(
            "support_tool_on",
            "set_tool_state",
            {"arm_id": support, "enable": True},
            "Turn on suction for the support arm.",
        ),
        _tool_step(
            "support_lift_object",
            "move_to_work_pose",
            {"arm_id": support, "x": float(object_x), "y": float(object_y), "phase": "hover", "r": 0.0},
            "Lift the object from the source area.",
        ),
        _tool_step(
            "support_handoff_hover",
            "move_to_named_pose",
            {"arm_id": support, "destination": "handoff_zone", "phase": "hover"},
            "Move the support arm to the handoff station hover pose.",
        ),
        _tool_step(
            "support_handoff_surface",
            "move_to_named_pose",
            {"arm_id": support, "destination": "handoff_zone", "phase": "surface"},
            "Lower the object onto the handoff station.",
        ),
        _tool_step(
            "support_tool_off",
            "set_tool_state",
            {"arm_id": support, "enable": False},
            "Release the object at handoff_zone.",
        ),
        _tool_step(
            "support_handoff_hover_after_release",
            "move_to_named_pose",
            {"arm_id": support, "destination": "handoff_zone", "phase": "hover"},
            "Lift the support arm away from the released object.",
        ),
        _tool_step(
            "support_retreat",
            "move_to_named_pose",
            {"arm_id": support, "destination": STANDARD_PARK_TARGETS[support], "phase": "hover"},
            "Retreat the support arm to its standard park target before the receiver enters.",
        ),
        _tool_step(
            "inspect_workspace_before_receiver_enters",
            "get_workspace_state",
            {"must_be_clear_of": [support]},
            "Confirm the support arm has left the shared zone before the receiver enters handoff_zone.",
            "Continue only if the support arm is not in the shared zone and the previous release succeeded.",
        ),
        _tool_step(
            "receiver_handoff_hover",
            "move_to_named_pose",
            {"arm_id": intent, "destination": "handoff_zone", "phase": "hover"},
            "Move the requested intent arm to the handoff station hover pose.",
        ),
        _tool_step(
            "receiver_handoff_surface",
            "move_to_named_pose",
            {"arm_id": intent, "destination": "handoff_zone", "phase": "surface"},
            "Lower the requested intent arm to pick from handoff_zone.",
        ),
        _tool_step(
            "receiver_tool_on",
            "set_tool_state",
            {"arm_id": intent, "enable": True},
            "Turn on suction for the requested intent arm.",
        ),
        _tool_step(
            "receiver_lift_from_handoff",
            "move_to_named_pose",
            {"arm_id": intent, "destination": "handoff_zone", "phase": "hover"},
            "Lift the object from handoff_zone.",
        ),
        _tool_step(
            "receiver_destination_hover",
            "move_to_named_pose",
            {"arm_id": intent, "destination": destination_name, "phase": "hover"},
            "Move the requested intent arm to the final destination hover pose.",
        ),
        _tool_step(
            "receiver_destination_surface",
            "move_to_named_pose",
            {"arm_id": intent, "destination": destination_name, "phase": "surface"},
            "Lower to place the object at the final destination.",
        ),
        _tool_step(
            "receiver_tool_off",
            "set_tool_state",
            {"arm_id": intent, "enable": False},
            "Release the object at the final destination.",
        ),
        _tool_step(
            "receiver_destination_hover_after_release",
            "move_to_named_pose",
            {"arm_id": intent, "destination": destination_name, "phase": "hover"},
            "Lift away from the placed object.",
        ),
    ]

    return {
        "ok": True,
        "plan_type": "station_mediated_handoff",
        "intent_arm": intent,
        "support_arm": support,
        "destination": destination_name,
        "camera_id": camera_id,
        "object": {
            "id": object_id,
            "color": color,
            "coordinate_contract": f"object_x/object_y are {support} local coordinates from camera_scan per_arm_robot_plane[{support}]",
            "support_arm_local_xy": {"x": float(object_x), "y": float(object_y)},
        },
        "static_checks": checks,
        "shared_zone": guardrails.shared_zone(),
        "standard_parking": _active_dict(STANDARD_PARK_TARGETS),
        "execution_policy": {
            "use_exact_step_order": True,
            "use_atomic_tools_only": True,
            "composite_pick_place_allowed": False,
            "receiver_picks_from": "handoff_zone",
            "stop_on_failure": True,
            "do_not_claim_holding_without_successful_tool_steps": True,
        },
        "steps": steps,
    }


def build_pick_place_plan(arm_id, object_x, object_y, destination=None, object_id=None, color=None, camera_id="Cam1"):
    arm = normalize_arm_id(arm_id)
    if not arm:
        return {"ok": False, "error": f"Unknown arm: {arm_id}"}

    destination_resolution = resolve_destination_for_arm(arm, destination)
    if not destination_resolution.get("ok"):
        return destination_resolution
    destination_name = destination_resolution["destination"]
    destination_target = ARM_NAMED_TARGETS.get(arm, {}).get(destination_name)
    if not destination_target:
        return {
            "ok": False,
            "error": f"Destination '{destination_name}' is not configured for {arm}",
            "recommended_destination": STANDARD_DESTINATIONS[arm],
        }
    if destination_target.get("deprecated_alias_of"):
        return {
            "ok": False,
            "error": f"Destination '{destination_name}' is a deprecated alias for {arm}; use '{destination_target['deprecated_alias_of']}' instead.",
            "recommended_destination": destination_target["deprecated_alias_of"],
        }

    guardrails = _guardrails()
    pick_z = surface_z_for_pose(arm, object_x, object_y)
    source_surface_pose = {"x": float(object_x), "y": float(object_y), "z": pick_z, "r": 0.0}
    destination_surface_pose = _target_pose(destination_target, "surface")

    checks = {
        "source_surface": guardrails.check_pose(
            arm,
            source_surface_pose,
            other_poses={},
            operation="plan_pick_place:source_surface",
            check_collision=False,
        ),
        "destination_surface": guardrails.check_pose(
            arm,
            destination_surface_pose,
            other_poses={},
            operation="plan_pick_place:destination_surface",
            check_collision=False,
        ),
    }
    failed_checks = {name: result for name, result in checks.items() if not result.get("ok")}
    if failed_checks:
        return {
            "ok": False,
            "plan_type": "atomic_pick_place",
            "arm_id": arm,
            "object": {"id": object_id, "color": color, "arm_local_xy": {"x": float(object_x), "y": float(object_y)}},
            "destination": destination_name,
            "static_checks": checks,
            "error": "Pick/place plan failed static reach checks.",
            "recommended_action": "choose_different_arm_or_handoff",
        }

    source_global_x, _ = guardrails.local_xy_to_global_xy(arm, object_x, object_y)
    destination_global_x, _ = guardrails.local_xy_to_global_xy(
        arm,
        destination_target["x"],
        destination_target["y"],
    )
    source_in_shared_zone = guardrails.global_x_in_shared_zone(source_global_x)
    destination_in_shared_zone = guardrails.global_x_in_shared_zone(destination_global_x)
    other_arm = other_arm_id(arm)

    steps = []
    if other_arm and (source_in_shared_zone or destination_in_shared_zone):
        steps.extend(
            [
                _tool_step(
                    "park_other_arm",
                    "move_to_named_pose",
                    {"arm_id": other_arm, "destination": STANDARD_PARK_TARGETS[other_arm], "phase": "hover"},
                    "Park the other arm outside the shared zone before this arm enters a shared source or destination.",
                ),
                _tool_step(
                    "inspect_workspace_before_pick",
                    "get_workspace_state",
                    {},
                    "Confirm the shared zone is clear before entering for direct pick/place.",
                ),
            ]
        )

    steps.extend(
        [
            _tool_step(
                "arm_hover_object",
                "move_to_work_pose",
                {"arm_id": arm, "x": float(object_x), "y": float(object_y), "phase": "hover", "r": 0.0},
                "Move above the selected object using this arm's camera_scan per_arm_robot_plane coordinates.",
            ),
            _tool_step(
                "arm_descend_object",
                "move_to_work_pose",
                {"arm_id": arm, "x": float(object_x), "y": float(object_y), "phase": "surface", "r": 0.0},
                "Descend to the object surface.",
            ),
            _tool_step(
                "arm_tool_on",
                "set_tool_state",
                {"arm_id": arm, "enable": True},
                "Turn on suction for the selected object.",
            ),
            _tool_step(
                "arm_lift_object",
                "move_to_work_pose",
                {"arm_id": arm, "x": float(object_x), "y": float(object_y), "phase": "hover", "r": 0.0},
                "Lift the selected object.",
            ),
            _tool_step(
                "arm_destination_hover",
                "move_to_named_pose",
                {"arm_id": arm, "destination": destination_name, "phase": "hover"},
                "Move to the final destination hover pose.",
            ),
            _tool_step(
                "arm_destination_surface",
                "move_to_named_pose",
                {"arm_id": arm, "destination": destination_name, "phase": "surface"},
                "Lower to place the selected object.",
            ),
            _tool_step(
                "arm_tool_off",
                "set_tool_state",
                {"arm_id": arm, "enable": False},
                "Release the selected object.",
            ),
            _tool_step(
                "arm_destination_hover_after_release",
                "move_to_named_pose",
                {"arm_id": arm, "destination": destination_name, "phase": "hover"},
                "Lift away from the placed object.",
            ),
        ]
    )

    return {
        "ok": True,
        "plan_type": "atomic_pick_place",
        "arm_id": arm,
        "destination": destination_name,
        "requested_destination": destination_resolution.get("requested_destination"),
        "destination_resolution": destination_resolution,
        "camera_id": camera_id,
        "object": {
            "id": object_id,
            "color": color,
            "coordinate_contract": f"object_x/object_y are {arm} local coordinates from camera_scan per_arm_robot_plane[{arm}]",
            "arm_local_xy": {"x": float(object_x), "y": float(object_y)},
        },
        "static_checks": checks,
        "shared_zone": guardrails.shared_zone(),
        "source_in_shared_zone": source_in_shared_zone,
        "destination_in_shared_zone": destination_in_shared_zone,
        "execution_policy": {
            "use_exact_step_order": True,
            "use_atomic_tools_only": True,
            "composite_pick_place_allowed": False,
            "stop_on_failure": True,
            "do_not_claim_holding_without_successful_tool_steps": True,
        },
        "steps": steps,
    }


