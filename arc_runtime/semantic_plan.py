import copy
import json
import math
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from .config import (
    ARM_FRAME_TRANSFORMS,
    ARM_NAMED_TARGETS,
    ARM_PICK_CONFIG,
    ROBOT_FLEET_CONFIG,
    ROBOT_SAFETY_CONFIG,
    active_config,
    active_robot_ids,
)
from .camera_controller import CameraController
from .fleet_controller import FleetController
from .robot_guardrails import RobotSafetyGuardrail
from .storage_service import (
    GROUPS as STORAGE_GROUPS,
    GROUP_ARM_ROUTING,
    parse_reference as parse_storage_reference,
    resolve as resolve_storage,
    scan as scan_storage,
)
from .surface_model import phase_z_from_pick_config, surface_z_for_named_target, surface_z_for_pose
from .workspace_model import (
    STANDARD_PARK_TARGETS,
    ZONE_ALLOWED_ARMS,
    build_handoff_plan,
    resolve_destination_for_arm,
    build_workspace_state,
    normalize_arm_id,
)
from .state_paths import arc_py_path, project_path, runtime_state_path


PLAN_STATE_DIR = os.fspath(runtime_state_path("plans"))
SEMANTIC_ACTIONS = {
    "move", "grip", "pick", "drop", "handoff", "scan",
    "scan_storage_slots", "place_to_storage_slot", "get_state",
}
MOVE_PHASES = {"hover", "surface", "park", "home"}
GRIP_STATES = {"on", "off"}
SCAN_COLORS = {"any", "red", "green", "blue", "unknown"}
STORAGE_COMPATIBILITY_ALIASES = {"storage_zone", "drop_zone"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def ensure_plan_state_dir():
    os.makedirs(PLAN_STATE_DIR, exist_ok=True)


def plan_state_path(plan_id: str):
    ensure_plan_state_dir()
    return os.path.join(PLAN_STATE_DIR, f"{plan_id}.json")


def _is_record(value):
    return isinstance(value, dict)


def _error(path, code, message, repair_hint=None, details=None):
    payload = {
        "path": path,
        "code": code,
        "message": message,
    }
    if repair_hint:
        payload["repair_hint"] = repair_hint
    if details is not None:
        payload["details"] = details
    return payload


def _coerce_json_document(value, label):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} must be valid JSON: {exc}") from exc
    return value


def coerce_plan_document(value):
    plan_doc = _coerce_json_document(value, "plan")
    if isinstance(plan_doc, list):
        return {"name": "semantic-plan", "actions": plan_doc}
    if not isinstance(plan_doc, dict):
        raise ValueError("Plan must be a JSON object or an action array")
    if "actions" not in plan_doc and "plan" in plan_doc:
        nested = plan_doc["plan"]
        if isinstance(nested, dict):
            plan_doc = {**nested, **{k: v for k, v in plan_doc.items() if k != "plan"}}
    return plan_doc


def load_semantic_plan_document(file_path=None, json_text=None):
    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            return coerce_plan_document(json.load(f))
    if json_text:
        return coerce_plan_document(json_text)
    raise ValueError("Expected either file_path or json_text")


def _action_type(action):
    if not _is_record(action):
        return None
    return action.get("action") or action.get("type")


def _target_from_action(action):
    target = copy.deepcopy(action.get("target")) if _is_record(action.get("target")) else {}
    target_type = action.get("target_type") or target.get("type")

    if target_type in {"object", "object_pose"}:
        target_type = "pose"
    if target_type == "zone":
        target_type = "named"

    if not target_type:
        if "destination" in action or "name" in target:
            target_type = "named"
        elif "x" in action or "x" in target:
            target_type = "pose"
        elif action.get("phase") == "park":
            target_type = "park"
        elif action.get("phase") == "home":
            target_type = "home"

    target["type"] = target_type
    if "destination" in action and "name" not in target:
        target["name"] = action["destination"]
    if "zone" in action and "name" not in target:
        target["name"] = action["zone"]
    for key in ("x", "y", "z", "r", "object_id", "color"):
        if key in action and key not in target:
            target[key] = action[key]
    return target


def _move_action(arm_id, target, phase, purpose=None):
    action = {
        "action": "move",
        "arm_id": arm_id,
        "phase": phase,
        "target": target,
    }
    if purpose:
        action["purpose"] = purpose
    return action


def _grip_action(arm_id, state, purpose=None):
    action = {"action": "grip", "arm_id": arm_id, "state": state}
    if purpose:
        action["purpose"] = purpose
    return action


def _scan_action(camera_id="Cam1", color="any", selection="first", purpose=None, verification=None):
    action = {
        "action": "scan",
        "camera_id": camera_id or "Cam1",
        "color": str(color or "any").strip().lower(),
        "selection": selection or "first",
    }
    if purpose:
        action["purpose"] = purpose
    if verification:
        action["verification"] = verification
    return action


def _scan_storage_slots_action(camera_id="Cam1", purpose=None):
    action = {"action": "scan_storage_slots", "camera_id": camera_id or "Cam1"}
    if purpose:
        action["purpose"] = purpose
    return action


def _place_to_storage_slot_action(robot_id, reference=None, color=None, object_id=None, purpose=None):
    action = {
        "action": "place_to_storage_slot",
        "robot_id": normalize_arm_id(robot_id) or robot_id,
        "reference": reference,
        "color": color,
    }
    if object_id is not None:
        action["object_id"] = object_id
    if purpose:
        action["purpose"] = purpose
    return action


def _storage_reference(value):
    raw = str(value or "").strip()
    if raw.lower() in STORAGE_COMPATIBILITY_ALIASES:
        return True
    canonical, group, _ = parse_storage_reference(raw)
    return bool(canonical or group)


def _storage_resolve_reference(value):
    raw = str(value or "").strip()
    return None if raw.lower() in STORAGE_COMPATIBILITY_ALIASES else (raw or None)


def _normalize_move(action):
    arm = normalize_arm_id(action.get("arm_id"))
    phase = str(action.get("phase") or "").strip().lower()
    target = _target_from_action(action)

    if target.get("type") == "park":
        phase = "park"
    if target.get("type") == "home":
        phase = "home"
    if phase == "park" and "name" not in target and arm:
        target = {"type": "named", "name": STANDARD_PARK_TARGETS.get(arm)}
    if phase == "home":
        target = {"type": "home"}

    return _move_action(arm or action.get("arm_id"), target, phase, action.get("purpose"))


def _normalize_grip(action):
    state = action.get("state")
    if isinstance(state, bool):
        state = "on" if state else "off"
    if state is None and "enable" in action:
        state = "on" if action.get("enable") else "off"
    state = str(state or "").strip().lower()
    arm = normalize_arm_id(action.get("arm_id"))
    return _grip_action(arm or action.get("arm_id"), state, action.get("purpose"))


def _expand_pick(action):
    arm = normalize_arm_id(action.get("arm_id"))
    target = _target_from_action(action)
    return [
        _move_action(arm or action.get("arm_id"), target, "hover", "Approach above object"),
        _move_action(arm or action.get("arm_id"), target, "surface", "Descend to object surface"),
        _grip_action(arm or action.get("arm_id"), "on", "Turn suction on"),
        _move_action(arm or action.get("arm_id"), target, "hover", "Lift object"),
    ]


def _expand_drop(action):
    arm = normalize_arm_id(action.get("arm_id"))
    target = _target_from_action(action)
    return [
        _move_action(arm or action.get("arm_id"), target, "hover", "Approach above placement target"),
        _move_action(arm or action.get("arm_id"), target, "surface", "Descend to placement surface"),
        _grip_action(arm or action.get("arm_id"), "off", "Release object at surface"),
        _move_action(arm or action.get("arm_id"), target, "hover", "Retreat after release"),
    ]


def _translate_handoff_step(step):
    tool = step.get("tool")
    params = step.get("params") or {}
    purpose = step.get("purpose")
    if tool == "move_to_work_pose":
        return _move_action(
            params.get("arm_id"),
            {
                "type": "pose",
                "x": params.get("x"),
                "y": params.get("y"),
                "r": params.get("r", 0.0),
            },
            params.get("phase"),
            purpose,
        )
    if tool == "move_to_named_pose":
        return _move_action(
            params.get("arm_id"),
            {"type": "named", "name": params.get("destination")},
            params.get("phase"),
            purpose,
        )
    if tool == "set_tool_state":
        return _grip_action(params.get("arm_id"), "on" if params.get("enable") else "off", purpose)
    if tool == "get_workspace_state":
        action = {"action": "get_state", "purpose": purpose}
        required_clear_arms = params.get("must_be_clear_of")
        if required_clear_arms:
            action["must_be_clear_of"] = required_clear_arms
        return action
    raise ValueError(f"Unsupported handoff planner step tool: {tool}")


def _expand_handoff(action):
    support = normalize_arm_id(action.get("support_arm"))
    requested_destination = action.get("destination")
    storage_handoff = requested_destination is not None and _storage_reference(requested_destination)
    storage_reference = _storage_resolve_reference(requested_destination) if storage_handoff else None
    plan = build_handoff_plan(
        intent_arm=action.get("intent_arm") or action.get("arm_id"),
        support_arm=action.get("support_arm"),
        object_x=action.get("object_x"),
        object_y=action.get("object_y"),
        destination=None if storage_handoff else requested_destination,
        object_id=action.get("object_id"),
        color=action.get("color"),
        camera_id=action.get("camera_id", "Cam1"),
    )
    if not plan.get("ok"):
        raise ValueError(json.dumps(plan, ensure_ascii=False))

    intent = normalize_arm_id(action.get("intent_arm") or action.get("arm_id")) or plan.get("intent_arm")
    translated = [_translate_handoff_step(step) for step in plan.get("steps", [])]
    if not support:
        support = plan.get("support_arm")

    if storage_handoff:
        legacy_destination = plan.get("destination")
        final_move_index = next(
            (
                index
                for index, candidate in enumerate(translated)
                if candidate.get("action") == "move"
                and candidate.get("arm_id") == intent
                and candidate.get("phase") == "hover"
                and _target_name(candidate) == legacy_destination
            ),
            None,
        )
        if final_move_index is None:
            raise ValueError("Handoff planner did not produce a final destination transition to replace with Storage placement")
        translated = translated[:final_move_index]

    expanded = []
    if storage_handoff:
        expanded.append(
            _scan_storage_slots_action(
                camera_id=action.get("camera_id", "Cam1"),
                purpose="Refresh Storage occupancy before the handoff begins.",
            )
        )

    released_to_handoff = False
    for translated_action in translated:
        expanded.append(translated_action)
        if (
            translated_action.get("action") == "grip"
            and translated_action.get("arm_id") == support
            and translated_action.get("state") == "off"
            and len(expanded) >= 2
            and expanded[-2].get("action") == "move"
            and _is_handoff_zone(expanded[-2])
        ):
            released_to_handoff = True
            continue
        if released_to_handoff and _is_standard_park_move(translated_action):
            expanded.append(
                _scan_action(
                    camera_id=action.get("camera_id", "Cam1"),
                    color=action.get("color") or "any",
                    purpose="Verify the handoff station after the support arm retreated.",
                )
            )
            released_to_handoff = False

    if storage_handoff:
        expanded.append(
            _place_to_storage_slot_action(
                intent,
                reference=storage_reference if storage_reference is not None else requested_destination,
                color=action.get("color"),
                object_id=action.get("object_id"),
                purpose="Transactional verified Storage placement after the handoff.",
            )
        )
    return expanded
def expand_semantic_actions(actions):
    expanded = []
    errors = []
    for index, action in enumerate(actions):
        path = f"actions[{index}]"
        if not _is_record(action):
            errors.append(_error(path, "invalid_action", "Action must be an object"))
            continue

        action_name = _action_type(action)
        try:
            if action_name == "pick":
                expanded.extend(_expand_pick(action))
            elif action_name in {"drop", "place"}:
                expanded.extend(_expand_drop(action))
            elif action_name == "handoff":
                expanded.extend(_expand_handoff(action))
            elif action_name == "scan":
                expanded.append(
                    _scan_action(
                        camera_id=action.get("camera_id", "Cam1"),
                        color=action.get("color", "any"),
                        selection=action.get("selection", "first"),
                        purpose=action.get("purpose"),
                        verification=action.get("verification"),
                    )
                )
            elif action_name == "scan_storage_slots":
                expanded.append(
                    _scan_storage_slots_action(
                        camera_id=action.get("camera_id", "Cam1"),
                        purpose=action.get("purpose"),
                    )
                )
            elif action_name == "place_to_storage_slot":
                expanded.append(
                    _place_to_storage_slot_action(
                        robot_id=action.get("robot_id") or action.get("arm_id"),
                        reference=action.get("reference") or action.get("destination"),
                        color=action.get("color"),
                        object_id=action.get("object_id"),
                        purpose=action.get("purpose"),
                    )
                )
            elif action_name == "move":
                expanded.append(_normalize_move(action))
            elif action_name == "grip":
                expanded.append(_normalize_grip(action))
            elif action_name == "get_state":
                expanded.append({"action": "get_state", "purpose": action.get("purpose")})
            else:
                errors.append(
                    _error(
                        path,
                        "unknown_action",
                        f"Unsupported semantic action '{action_name}'",
                        "Use one of: move, grip, pick, drop, handoff, scan, scan_storage_slots, place_to_storage_slot, get_state.",
                    )
                )
        except (TypeError, ValueError) as exc:
            errors.append(
                _error(
                    path,
                    "expansion_failed",
                    str(exc),
                    "Fix the semantic action parameters and run validate_plan again.",
                )
            )
    return expanded, errors


def _guardrails():
    return RobotSafetyGuardrail(
        ROBOT_SAFETY_CONFIG,
        active_config(ARM_FRAME_TRANSFORMS),
        active_arm_ids=active_robot_ids(ARM_FRAME_TRANSFORMS),
    )


def _phase_pose_for_target(arm_id, target, phase):
    target_type = target.get("type")
    if phase == "home":
        home = ARM_NAMED_TARGETS.get(arm_id, {}).get("home")
        if not home:
            return None
        return {
            "x": float(home["x"]),
            "y": float(home["y"]),
            "z": float(home["hover_z"]),
            "r": float(home.get("r", 0.0)),
        }

    if target_type == "named":
        name = target.get("name")
        named_target = ARM_NAMED_TARGETS.get(arm_id, {}).get(name)
        if not named_target:
            return None
        z = float(named_target["hover_z"]) if phase in {"hover", "park"} else surface_z_for_named_target(named_target)
        return {
            "x": float(named_target["x"]),
            "y": float(named_target["y"]),
            "z": z,
            "r": float(named_target.get("r", 0.0)),
        }

    if target_type == "pose":
        if target.get("z") is not None:
            z = float(target["z"])
        elif phase == "hover":
            z = phase_z_from_pick_config(arm_id, phase)
        elif phase == "surface":
            z = phase_z_from_pick_config(arm_id, phase, target["x"], target["y"])
        else:
            return None
        return {
            "x": float(target["x"]),
            "y": float(target["y"]),
            "z": z,
            "r": float(target.get("r", 0.0)),
        }

    return None


def _target_name(action):
    target = action.get("target") or {}
    if target.get("type") == "named":
        return target.get("name")
    return None


def _is_handoff_zone(action):
    return _target_name(action) == "handoff_zone"


def _is_standard_park_move(action):
    arm = action.get("arm_id")
    return (
        action.get("action") == "move"
        and action.get("phase") in {"hover", "park"}
        and _target_name(action) == STANDARD_PARK_TARGETS.get(arm)
    )


def _is_handoff_surface_move(action):
    return action.get("action") == "move" and action.get("phase") == "surface" and _is_handoff_zone(action)


def _is_handoff_release(expanded_actions, index):
    action = expanded_actions[index]
    previous_action = expanded_actions[index - 1] if index > 0 else None
    return (
        action.get("action") == "grip"
        and action.get("state") == "off"
        and previous_action
        and previous_action.get("arm_id") == action.get("arm_id")
        and _is_handoff_surface_move(previous_action)
    )


def _is_handoff_pick(expanded_actions, index):
    action = expanded_actions[index]
    previous_action = expanded_actions[index - 1] if index > 0 else None
    return (
        action.get("action") == "grip"
        and action.get("state") == "on"
        and previous_action
        and previous_action.get("arm_id") == action.get("arm_id")
        and _is_handoff_surface_move(previous_action)
    )


def _validate_move(action, index, errors):
    path = f"expanded_actions[{index}]"
    arm = normalize_arm_id(action.get("arm_id"))
    if not arm:
        errors.append(_error(path, "unknown_arm", f"Unknown arm_id: {action.get('arm_id')}"))
        return
    action["arm_id"] = arm

    phase = str(action.get("phase") or "").strip().lower()
    if phase not in MOVE_PHASES:
        errors.append(
            _error(
                path,
                "invalid_phase",
                "move.phase must be hover, surface, park, or home",
                "Use hover for approach/lift, surface for pick/drop contact, park for standard safe park, or home for home pose.",
            )
        )
        return
    action["phase"] = phase

    target = action.get("target") or {}
    if phase == "park" and (not target or target.get("type") in {None, "park"}):
        target = {"type": "named", "name": STANDARD_PARK_TARGETS.get(arm)}
        action["target"] = target
    if phase == "home":
        action["target"] = {"type": "home"}
        return

    target_type = target.get("type")
    if target_type == "named":
        name = target.get("name")
        named_target = ARM_NAMED_TARGETS.get(arm, {}).get(name)
        if not named_target:
            errors.append(
                _error(
                    path,
                    "target_not_configured_for_arm",
                    f"Target '{name}' is not configured for {arm}",
                    "Choose a named zone allowed for that arm or use a handoff plan.",
                )
            )
            return
        if named_target.get("deprecated_alias_of"):
            errors.append(
                _error(
                    path,
                    "deprecated_zone_for_arm",
                    f"Target '{name}' is deprecated for {arm}; use '{named_target['deprecated_alias_of']}' or plan a handoff.",
                    "Do not send Arm1 directly to drop_zone; use storage_zone or a handoff to Arm2.",
                )
            )
            return
    elif target_type == "pose":
        if not isinstance(target.get("x"), (int, float)) or not isinstance(target.get("y"), (int, float)):
            errors.append(
                _error(
                    path,
                    "pose_xy_required",
                    "Pose targets require numeric x and y",
                    "Use x/y from camera_scan per_arm_robot_plane[arm_id] or a planner-selected placement point.",
                )
            )
            return
    else:
        errors.append(
            _error(
                path,
                "invalid_target",
                "move.target.type must be pose, named, park, or home",
                "For zones use target={type:'named', name:'drop_zone'} with phase hover or surface.",
            )
        )
        return

    if phase in {"hover", "surface", "park"}:
        pose = _phase_pose_for_target(arm, target, phase)
        if pose:
            result = _guardrails().check_pose(
                arm,
                pose,
                other_poses={},
                operation=f"validate_plan:{path}",
                check_collision=False,
            )
            if not result.get("ok"):
                errors.append(
                    _error(
                        path,
                        "target_arm_unreachable",
                        f"{arm} cannot reach the target at phase {phase}",
                        "Choose a reachable arm, adjust the target, or plan a handoff.",
                        result,
                    )
                )


def _validate_grip(action, index, expanded_actions, errors):
    path = f"expanded_actions[{index}]"
    arm = normalize_arm_id(action.get("arm_id"))
    if not arm:
        errors.append(_error(path, "unknown_arm", f"Unknown arm_id: {action.get('arm_id')}"))
        return
    action["arm_id"] = arm

    state = str(action.get("state") or "").strip().lower()
    if state not in GRIP_STATES:
        errors.append(_error(path, "invalid_grip_state", "grip.state must be on or off"))
        return
    action["state"] = state

    previous_action = expanded_actions[index - 1] if index > 0 else None
    if not previous_action or previous_action.get("action") != "move" or previous_action.get("arm_id") != arm:
        errors.append(
            _error(
                path,
                "missing_surface_before_grip",
                "grip requires a preceding move by the same arm to phase=surface",
                "Use move hover -> move surface -> grip on/off -> move hover.",
            )
        )
        return
    if previous_action.get("phase") != "surface":
        code = "release_at_hover" if state == "off" and previous_action.get("phase") == "hover" else "missing_surface_before_grip"
        errors.append(
            _error(
                path,
                code,
                "grip off/on cannot happen at hover; preceding move must be phase=surface",
                "For drop use move hover -> move surface -> grip off -> move hover.",
            )
        )
    else:
        approach_action = expanded_actions[index - 2] if index > 1 else None
        if (
            not approach_action
            or approach_action.get("action") != "move"
            or approach_action.get("arm_id") != arm
            or approach_action.get("phase") != "hover"
        ):
            errors.append(
                _error(
                    path,
                    "missing_hover_before_surface",
                    "The surface move before grip must be preceded by a same-arm move to phase=hover",
                    "Use move hover -> move surface -> grip on/off -> move hover.",
                )
            )

    next_action = expanded_actions[index + 1] if index + 1 < len(expanded_actions) else None
    if not next_action or next_action.get("action") != "move" or next_action.get("arm_id") != arm or next_action.get("phase") != "hover":
        errors.append(
            _error(
                path,
                "missing_hover_after_grip",
                "grip must be followed by a same-arm move to phase=hover",
                "After grip on, lift to hover. After grip off, retreat to hover.",
            )
        )


def _validate_scan(action, index, errors):
    path = f"expanded_actions[{index}]"
    camera_id = str(action.get("camera_id") or "").strip()
    if not camera_id:
        errors.append(_error(path, "missing_camera_id", "scan.camera_id is required"))
    action["camera_id"] = camera_id or "Cam1"

    color = str(action.get("color") or "any").strip().lower()
    if color not in SCAN_COLORS:
        errors.append(
            _error(
                path,
                "invalid_scan_color",
                "scan.color must be any, red, green, blue, or unknown",
                "Use the same color filter that came from camera_scan.",
            )
        )
    action["color"] = color

    verification = action.get("verification")
    if verification is not None and not isinstance(verification, dict):
        errors.append(
            _error(
                path,
                "invalid_scan_verification",
                "scan.verification must be an object when provided",
                "For completion checks use verification={type:'scan_absence', color:'green'}.",
            )
        )
        return
    if verification:
        verification_type = verification.get("type")
        if verification_type not in {"scan_absence"}:
            errors.append(
                _error(
                    path,
                    "unsupported_scan_verification",
                    f"Unsupported scan verification type '{verification_type}'",
                    "Use verification.type='scan_absence' to prove matching objects are no longer visible in the workspace.",
                )
            )


def _validate_handoff_order(expanded_actions, errors, allow_final_handoff=False, expected_final_destination=None):
    pending_retreat = {}
    pending_handoffs = []

    for index, action in enumerate(expanded_actions):
        if action.get("action") == "move":
            arm = action.get("arm_id")
            if _is_handoff_zone(action):
                for other_arm, needs_retreat in pending_retreat.items():
                    if needs_retreat and other_arm != arm:
                        errors.append(
                            _error(
                                f"expanded_actions[{index}]",
                                "handoff_receiver_entered_before_support_retreat",
                                "Receiver entered handoff_zone before the support arm retreated",
                                "After support grip off at handoff_zone, move support to its standard park before receiver enters.",
                            )
                        )
            if _is_standard_park_move(action):
                pending_retreat[arm] = False
                for token in pending_handoffs:
                    if token["support_arm"] == arm and not token["support_retreated"]:
                        token["support_retreated"] = True

        if action.get("action") == "place_to_storage_slot":
            arm = normalize_arm_id(action.get("robot_id") or action.get("arm_id"))
            for token in pending_handoffs:
                if token.get("receiver_arm") == arm and token.get("receiver_pick_index") is not None and token.get("final_release_index") is None:
                    token["final_release_index"] = index
                    token["final_release_target"] = action.get("reference")
                    break

        if action.get("action") == "grip" and action.get("state") == "off":
            completed_receiver_release = False
            for token in pending_handoffs:
                if token.get("receiver_arm") == action.get("arm_id") and token.get("final_release_index") is None:
                    previous_action = expanded_actions[index - 1] if index > 0 else None
                    token["final_release_index"] = index
                    token["final_release_target"] = _target_name(previous_action) if previous_action else None
                    completed_receiver_release = True
                    break
            if completed_receiver_release:
                continue

            if _is_handoff_release(expanded_actions, index):
                pending_retreat[action.get("arm_id")] = True
                if not allow_final_handoff:
                    pending_handoffs.append(
                        {
                            "support_arm": action.get("arm_id"),
                            "release_index": index,
                            "support_retreated": False,
                            "receiver_arm": None,
                            "receiver_pick_index": None,
                            "final_release_index": None,
                        }
                    )
                continue

            for token in pending_handoffs:
                if token.get("receiver_arm") == action.get("arm_id") and token.get("final_release_index") is None:
                    token["final_release_index"] = index
                    previous_action = expanded_actions[index - 1] if index > 0 else None
                    token["final_release_target"] = _target_name(previous_action) if previous_action else None
                    break

        if _is_handoff_pick(expanded_actions, index):
            arm = action.get("arm_id")
            for token in pending_handoffs:
                if token.get("receiver_pick_index") is None and token["support_arm"] != arm and token["support_retreated"]:
                    token["receiver_arm"] = arm
                    token["receiver_pick_index"] = index
                    break

    if allow_final_handoff:
        return

    for token in pending_handoffs:
        if not token["support_retreated"]:
            errors.append(
                _error(
                    f"expanded_actions[{token['release_index']}]",
                    "handoff_support_missing_retreat",
                    "Object was released at handoff_zone but the support arm did not retreat to park",
                    "After releasing at handoff_zone, move the support arm to its standard park before any receiver action.",
                )
            )
        if token["receiver_pick_index"] is None:
            errors.append(
                _error(
                    f"expanded_actions[{token['release_index']}]",
                    "handoff_object_left_unclaimed",
                    "Object was released at handoff_zone but no receiver picked it up",
                    "Complete the handoff: support release -> hover -> park -> scan/get_state -> receiver pick from handoff_zone -> final drop.",
                )
            )
            continue
        if token["final_release_index"] is None:
            errors.append(
                _error(
                    f"expanded_actions[{token['receiver_pick_index']}]",
                    "handoff_receiver_missing_final_drop",
                    "Receiver picked from handoff_zone but did not drop at the final destination",
                    "After receiver grip on at handoff_zone, move to the final zone surface, grip off, then retreat to hover.",
                )
            )
            continue
        if expected_final_destination and token.get("final_release_target") != expected_final_destination:
            errors.append(
                _error(
                    f"expanded_actions[{token['final_release_index']}]",
                    "handoff_receiver_wrong_final_destination",
                    f"Receiver dropped handoff object at '{token.get('final_release_target')}', not '{expected_final_destination}'",
                    "After receiver picks from handoff_zone, drop at the declared final destination zone.",
                )
            )
        elif token.get("final_release_target") == "handoff_zone" and not allow_final_handoff:
            errors.append(
                _error(
                    f"expanded_actions[{token['final_release_index']}]",
                    "handoff_receiver_released_back_to_handoff",
                    "Receiver picked from handoff_zone but released back to handoff_zone",
                    "After receiver picks from handoff_zone, drop at the final destination zone.",
                )
            )


def _validate_storage_place(action, index, errors):
    path = f"expanded_actions[{index}]"
    arm = normalize_arm_id(action.get("robot_id") or action.get("arm_id"))
    if not arm:
        errors.append(_error(path, "unknown_arm", "place_to_storage_slot.robot_id is required"))
        return None
    action["robot_id"] = arm
    if arm not in set(active_robot_ids(ARM_FRAME_TRANSFORMS)):
        errors.append(_error(path, "arm_inactive", f"Arm '{arm}' is not active", "Choose an active calibrated arm."))
    reference = action.get("reference")
    if reference is not None and not _storage_reference(reference):
        errors.append(_error(path, "invalid_storage_reference", f"Unknown Storage group or Slot '{reference}'", "Use LT, LB, RT, RB, or a named Slot such as LB3."))
    color = action.get("color")
    if color is not None:
        normalized_color = str(color).strip().lower()
        if normalized_color not in SCAN_COLORS - {"any"}:
            errors.append(_error(path, "invalid_storage_color", "Storage placement color must be red, green, blue, or unknown"))
        action["color"] = normalized_color
    try:
        result = resolve_storage(
            reference=_storage_resolve_reference(reference),
            color=color,
            arm_id=arm,
            active_arms=active_robot_ids(ARM_FRAME_TRANSFORMS),
        )
    except Exception as exc:
        result = {"ok": False, "error_code": "storage_registry_error", "error": str(exc)}
    if not result.get("ok"):
        errors.append(_error(path, result.get("error_code", "storage_unavailable"), result.get("error", "Storage Slot cannot be resolved"), "Refresh Storage slots and choose an active, transform-ready, reachable empty Slot.", result))
    else:
        action["resolved_slot_preview"] = result.get("slot_name")
    return arm


def _validate_storage_order(original_actions, expanded_actions, errors):
    has_storage_action = any(action.get("action") == "place_to_storage_slot" for action in expanded_actions)
    if any(_action_type(action) in {"drop", "place"} and _storage_reference((_target_from_action(action) or {}).get("name")) for action in original_actions if _is_record(action)):
        errors.append(_error("actions", "generic_storage_drop_forbidden", "Generic drop/place cannot target Storage", "Use place_to_storage_slot after pick; it owns reserve, placement, park, rescan, and verification."))
        has_storage_action = True
    if not has_storage_action:
        return
    scan_seen = False
    holding = {}
    for index, action in enumerate(expanded_actions):
        name = action.get("action")
        if name == "scan_storage_slots":
            scan_seen = True
        elif name == "grip":
            arm = normalize_arm_id(action.get("arm_id"))
            if arm:
                holding[arm] = action.get("state") == "on"
        elif name == "place_to_storage_slot":
            arm = normalize_arm_id(action.get("robot_id"))
            if not scan_seen:
                errors.append(_error(f"expanded_actions[{index}]", "missing_storage_scan", "Storage placement requires a preceding fresh scan_storage_slots action", "Scan Storage slots before the first placement decision."))
            if arm and not holding.get(arm, False):
                errors.append(_error(f"expanded_actions[{index}]", "storage_place_without_pick", f"{arm} is not holding a picked object", "Use pick first and require successful grip-on/lift before placement."))
            if arm:
                holding[arm] = False
    if any(holding.values()):
        errors.append(_error("actions", "storage_object_not_placed", "A picked object remains held without transactional Storage placement", "End each Storage pickup with place_to_storage_slot."))

def validate_semantic_plan(plan_doc):
    try:
        plan = coerce_plan_document(plan_doc)
    except ValueError as exc:
        return {
            "ok": False,
            "semantic_plan": True,
            "errors": [_error("plan", "invalid_plan_json", str(exc))],
        }

    actions = plan.get("actions")
    if not isinstance(actions, list) or not actions:
        return {
            "ok": False,
            "semantic_plan": True,
            "errors": [
                _error(
                    "actions",
                    "missing_actions",
                    "Plan must contain a non-empty actions array",
                    "Use semantic actions such as pick, move, drop, or handoff.",
                )
            ],
        }

    expanded_actions, expansion_errors = expand_semantic_actions(actions)
    errors = list(expansion_errors)

    for index, action in enumerate(expanded_actions):
        action_name = action.get("action")
        if action_name == "move":
            _validate_move(action, index, errors)
        elif action_name == "grip":
            _validate_grip(action, index, expanded_actions, errors)
        elif action_name == "scan":
            _validate_scan(action, index, errors)
        elif action_name == "scan_storage_slots":
            action["camera_id"] = str(action.get("camera_id") or "Cam1").strip() or "Cam1"
        elif action_name == "place_to_storage_slot":
            _validate_storage_place(action, index, errors)
        elif action_name == "get_state":
            required_clear_arms = action.get("must_be_clear_of")
            if required_clear_arms is not None:
                if not isinstance(required_clear_arms, list) or not required_clear_arms:
                    errors.append(_error(f"expanded_actions[{index}]", "invalid_state_gate", "get_state.must_be_clear_of must be a non-empty arm list"))
                else:
                    normalized_arms = [normalize_arm_id(arm) for arm in required_clear_arms]
                    if any(arm is None for arm in normalized_arms):
                        errors.append(_error(f"expanded_actions[{index}]", "unknown_arm", "get_state.must_be_clear_of contains an unknown arm"))
                    else:
                        action["must_be_clear_of"] = normalized_arms
            continue
        else:
            errors.append(
                _error(
                    f"expanded_actions[{index}]",
                    "unknown_expanded_action",
                    f"Unsupported expanded action '{action_name}'",
                )
            )

    _validate_storage_order(actions, expanded_actions, errors)

    expected_final_destination = plan.get("final_destination") or plan.get("destination")
    allow_final_handoff = bool(
        plan.get("allow_final_handoff_zone")
        or expected_final_destination == "handoff_zone"
    )
    _validate_handoff_order(
        expanded_actions,
        errors,
        allow_final_handoff=allow_final_handoff,
        expected_final_destination=expected_final_destination,
    )

    ok = len(errors) == 0
    return {
        "ok": ok,
        "semantic_plan": True,
        "plan_type": "semantic",
        "name": plan.get("name", "semantic-plan"),
        "action_count": len(actions),
        "expanded_step_count": len(expanded_actions),
        "actions": copy.deepcopy(actions),
        "expanded_actions": expanded_actions,
        "errors": errors,
        "feedback": "Plan is valid." if ok else "Plan was rejected. Use the errors repair_hint fields to fix missing or unsafe steps.",
    }


def _request_float(request, key):
    value = request.get(key)
    if not isinstance(value, (int, float)):
        raise ValueError(f"request.{key} must be a number")
    return float(value)


def _destination_owner(destination):
    active_arms = set(active_robot_ids(ARM_NAMED_TARGETS))
    allowed_arms = ZONE_ALLOWED_ARMS.get(destination)
    if allowed_arms:
        active_allowed = [arm for arm in allowed_arms if arm in active_arms]
        return active_allowed[0] if active_allowed else None

    owners = []
    for arm_id, targets in ARM_NAMED_TARGETS.items():
        if arm_id not in active_arms:
            continue
        target = targets.get(destination)
        if target and not target.get("deprecated_alias_of"):
            owners.append(arm_id)
    if len(owners) == 1:
        return owners[0]
    return None


def _single_active_arm():
    active = active_robot_ids(ARM_NAMED_TARGETS)
    return active[0] if len(active) == 1 else None


def _resolve_planning_destination(destination, arm_id=None):
    active_arm = normalize_arm_id(arm_id) if arm_id else None
    if active_arm:
        return resolve_destination_for_arm(active_arm, destination)

    owner = _destination_owner(destination)
    if owner:
        return resolve_destination_for_arm(owner, destination)

    only_arm = _single_active_arm()
    if only_arm:
        return resolve_destination_for_arm(only_arm, destination)

    return {
        "ok": False,
        "destination": destination,
        "requested_destination": destination,
        "error": f"Could not determine destination owner arm for '{destination}'",
    }


def _objects_from_matching_plan_request(request):
    raw_objects = request.get("objects")
    if raw_objects is None and isinstance(request.get("scan"), dict):
        raw_objects = request["scan"].get("objects")
    if raw_objects is None and isinstance(request.get("scan_result"), dict):
        raw_objects = request["scan_result"].get("objects")
    if not isinstance(raw_objects, list):
        raise ValueError("request.objects or request.scan.objects is required for plan_all_matching_objects")
    return raw_objects


def _handoff_source_object(request):
    """Return the scanned source object attached to a handoff request."""
    candidates = []
    if isinstance(request.get("object"), dict):
        candidates.append(request["object"])
    if isinstance(request.get("objects"), list):
        candidates.extend(item for item in request["objects"] if isinstance(item, dict))
    for key in ("scan", "scan_result"):
        scan = request.get(key)
        if isinstance(scan, dict) and isinstance(scan.get("objects"), list):
            candidates.extend(item for item in scan["objects"] if isinstance(item, dict))

    object_id = request.get("object_id")
    if object_id is not None:
        expected_id = str(object_id)
        return next((item for item in candidates if str(item.get("id")) == expected_id), None)
    return candidates[0] if len(candidates) == 1 else None


def _handoff_source_xy(request, support_arm):
    """Resolve a handoff pick point only from the support arm's scan plane.

    A named camera object must never be picked using a conversationally copied
    workspace coordinate. The only compatible raw-XY path is an explicitly
    unnamed developer request; normal ARC handoff planning supplies object_id
    and its scan object.
    """
    source = _handoff_source_object(request)
    object_id = request.get("object_id")
    if object_id is not None:
        if source is None:
            raise ValueError(
                "Handoff with object_id requires the matching scanned object under "
                "object, objects, scan.objects, or scan_result.objects; "
                "the runtime will derive per_arm_robot_plane[support_arm].x/y."
            )
        return _object_arm_xy(source, support_arm), source

    if source is not None:
        return _object_arm_xy(source, support_arm), source

    # Kept for direct developer integrations that have no camera object identity.
    return (
        (_request_float(request, "object_x"), _request_float(request, "object_y")),
        None,
    )

def _object_arm_xy(obj, arm_id):
    per_arm = obj.get("per_arm_robot_plane") if isinstance(obj.get("per_arm_robot_plane"), dict) else {}
    xy = per_arm.get(arm_id)
    if not isinstance(xy, dict) or not isinstance(xy.get("x"), (int, float)) or not isinstance(xy.get("y"), (int, float)):
        raise ValueError(f"object {obj.get('id', '<unknown>')} is missing per_arm_robot_plane[{arm_id}].x/y")
    return float(xy["x"]), float(xy["y"])


def _pick_action_for_object(obj, arm_id, object_x, object_y):
    return {
        "action": "pick",
        "arm_id": arm_id,
        "target": {
            "type": "pose",
            "x": object_x,
            "y": object_y,
            "r": float(obj.get("r", 0.0)),
            "object_id": obj.get("id"),
            "color": obj.get("color"),
        },
        "object_id": obj.get("id"),
        "color": obj.get("color"),
    }


def _drop_action(arm_id, destination):
    return {
        "action": "drop",
        "arm_id": arm_id,
        "target": {"type": "named", "name": destination},
    }


def _object_reach_diagnostics(obj, arm_id):
    arm_plane = (obj.get("per_arm_robot_plane") or {}).get(arm_id) or {}
    diagnostics = {
        "arm_id": arm_id,
        "robot_plane": None,
        "reachable": bool(arm_plane.get("reachable")),
        "reach_radius_mm": arm_plane.get("reach_radius_mm"),
    }
    if isinstance(arm_plane.get("x"), (int, float)) and isinstance(arm_plane.get("y"), (int, float)):
        diagnostics["robot_plane"] = {"x": float(arm_plane["x"]), "y": float(arm_plane["y"])}
        if diagnostics["reach_radius_mm"] is None:
            diagnostics["reach_radius_mm"] = math.sqrt(float(arm_plane["x"]) ** 2 + float(arm_plane["y"]) ** 2)
    return diagnostics


def _object_guardrail_reachable(obj, arm_id):
    try:
        object_x, object_y = _object_arm_xy(obj, arm_id)
    except ValueError:
        return False, None
    pose = {
        "x": object_x,
        "y": object_y,
        "z": surface_z_for_pose(arm_id, object_x, object_y),
        "r": float(obj.get("r", 0.0)),
    }
    result = _guardrails().check_pose(
        arm_id,
        pose,
        other_poses={},
        operation="plan_all_matching_objects:object_reach",
        check_collision=False,
    )
    return bool(result.get("ok")), result


def _build_storage_plan_all_matching_objects(request):
    destination = str(request.get("destination") or request.get("zone") or "").strip()
    if not destination:
        raise ValueError("request.destination is required for Storage planning")
    if not _storage_reference(destination):
        raise ValueError(f"'{destination}' is not a Storage group or Slot")
    color = str(request.get("color") or "any").strip().lower()
    if color not in SCAN_COLORS:
        raise ValueError("request.color must be any, red, green, blue, or unknown")

    reference = _storage_resolve_reference(destination)
    _, destination_group, _ = parse_storage_reference(reference)
    if not destination_group:
        raise ValueError("An explicit calibrated Storage group or named Slot is required")
    active_arms = active_robot_ids(ARM_FRAME_TRANSFORMS)
    destination_arm = next(
        (arm for arm in GROUP_ARM_ROUTING.get(destination_group, ()) if arm in active_arms),
        None,
    )
    if not destination_arm:
        raise ValueError(
            f"No active arm owns Storage group {destination_group}; "
            f"expected one of {list(GROUP_ARM_ROUTING.get(destination_group, ()))}"
        )

    camera_id = request.get("camera_id", "Cam1")
    objects = _objects_from_matching_plan_request(request)
    matching = [
        obj for obj in objects
        if isinstance(obj, dict)
        and (color == "any" or str(obj.get("color", "")).lower() == color)
    ]
    actions = [
        {"action": "get_state", "purpose": "Validate calibrated arm state before Storage work."},
        _scan_action(
            camera_id=camera_id,
            color=color,
            selection="all",
            purpose="Refresh loose objects before Storage execution.",
        ),
        _scan_storage_slots_action(
            camera_id,
            "Refresh Storage occupancy before selecting the first Slot.",
        ),
    ]
    planned = []
    unresolved = []
    for obj in matching:
        object_id = obj.get("id")
        reachable = [
            normalize_arm_id(arm)
            for arm in (obj.get("reachable_by") or [])
            if normalize_arm_id(arm)
        ]
        derived_reach_checks = {}
        for arm_id in active_arms:
            if arm_id in reachable:
                continue
            ok, check = _object_guardrail_reachable(obj, arm_id)
            if check is not None:
                derived_reach_checks[arm_id] = check
            if ok:
                reachable.append(arm_id)

        try:
            resolved = resolve_storage(
                reference=reference,
                color=obj.get("color"),
                arm_id=destination_arm,
                active_arms=active_arms,
            )
        except Exception as exc:
            resolved = {
                "ok": False,
                "error_code": "storage_registry_error",
                "error": str(exc),
            }
        if not resolved.get("ok"):
            unresolved.append({
                "object_id": object_id,
                "color": obj.get("color"),
                "reason": "destination_storage_unavailable",
                "destination": destination,
                "destination_arm": destination_arm,
                "storage_result": resolved,
            })
            continue

        if destination_arm in reachable:
            object_x, object_y = _object_arm_xy(obj, destination_arm)
            actions.append(
                _pick_action_for_object(obj, destination_arm, object_x, object_y)
            )
            actions.append(
                _place_to_storage_slot_action(
                    destination_arm,
                    reference=reference,
                    color=obj.get("color"),
                    object_id=object_id,
                    purpose="Transactional verified Storage placement.",
                )
            )
            planned.append({
                "object_id": object_id,
                "color": obj.get("color"),
                "arm_id": destination_arm,
                "destination": destination,
                "mode": "transactional_storage",
            })
            continue

        support_arm = next(
            (arm for arm in reachable if arm != destination_arm and arm in active_arms),
            None,
        )
        if not support_arm:
            unresolved.append({
                "object_id": object_id,
                "color": obj.get("color"),
                "reason": "no_support_arm_can_reach_object",
                "reachable_by": reachable,
                "destination_arm": destination_arm,
                "planner_guardrail_checks": derived_reach_checks,
            })
            continue

        object_x, object_y = _object_arm_xy(obj, support_arm)
        actions.append({
            "action": "handoff",
            "intent_arm": destination_arm,
            "support_arm": support_arm,
            "object_x": object_x,
            "object_y": object_y,
            "destination": destination,
            "object_id": object_id,
            "color": obj.get("color"),
            "camera_id": camera_id,
        })
        planned.append({
            "object_id": object_id,
            "color": obj.get("color"),
            "support_arm": support_arm,
            "destination_arm": destination_arm,
            "destination": destination,
            "mode": "handoff_to_transactional_storage",
        })

    if unresolved:
        raise ValueError(json.dumps({
            "error": "Storage plan has unresolved objects",
            "active_arm_ids": active_arms,
            "destination": destination,
            "destination_arm": destination_arm,
            "unresolved": unresolved,
        }, ensure_ascii=False))

    actions.append(
        _scan_action(
            camera_id=camera_id,
            color=color,
            purpose="Verify all selected matching objects have been removed from the workspace before reporting completion.",
            verification={"type": "scan_absence", "color": color},
        )
    )
    return {
        "name": request.get("name", f"plan-{color}-objects-to-{destination}"),
        "destination": destination,
        "requested_destination": destination,
        "final_destination": destination,
        "storage": True,
        "matching_object_plan": {
            "color": color,
            "camera_id": camera_id,
            "planned_object_count": len(planned),
            "destination_arm": destination_arm,
            "objects": planned,
            "requires_final_scan_absence": True,
            "transactional_storage": True,
        },
        "actions": actions,
    }

def _build_plan_all_matching_objects(request):
    destination = str(request.get("destination") or request.get("zone") or "").strip()
    if not destination:
        raise ValueError("request.destination is required for plan_all_matching_objects")

    destination_resolution = _resolve_planning_destination(
        destination,
        request.get("destination_arm"),
    )
    if not destination_resolution.get("ok"):
        raise ValueError(destination_resolution.get("error") or f"Could not determine destination owner arm for '{destination}'")
    destination_arm = destination_resolution["arm_id"]
    requested_destination = destination
    destination = destination_resolution["destination"]

    color = str(request.get("color") or "any").strip().lower()
    if color not in SCAN_COLORS:
        raise ValueError("request.color must be any, red, green, blue, or unknown")

    camera_id = request.get("camera_id", "Cam1")
    objects = _objects_from_matching_plan_request(request)
    matching_objects = [
        obj for obj in objects
        if isinstance(obj, dict) and (color == "any" or str(obj.get("color", "")).lower() == color)
    ]
    if not matching_objects:
        return {
            "name": request.get("name", f"plan-{color}-objects-to-{destination}"),
            "destination": destination,
            "requested_destination": requested_destination,
            "final_destination": destination,
            "matching_object_plan": {
                "color": color,
                "camera_id": camera_id,
                "planned_object_count": 0,
                "destination_arm": destination_arm,
                "destination_resolution": destination_resolution,
                "objects": [],
            },
            "actions": [
                _scan_action(
                    camera_id=camera_id,
                    color=color,
                    purpose="Verify no matching objects are visible before reporting the planned multi-object task complete.",
                    verification={"type": "scan_absence", "color": color},
                )
            ],
        }

    actions = []
    planned_objects = []
    unresolved = []
    for obj in matching_objects:
        object_id = obj.get("id")
        reachable_by = [normalize_arm_id(arm) for arm in (obj.get("reachable_by") or [])]
        reachable_by = [arm for arm in reachable_by if arm]
        derived_reach_checks = {}
        active_arms = active_robot_ids(ARM_NAMED_TARGETS)
        for arm_id in active_arms:
            if arm_id in reachable_by:
                continue
            ok, check = _object_guardrail_reachable(obj, arm_id)
            if check is not None:
                derived_reach_checks[arm_id] = check
            if ok:
                reachable_by.append(arm_id)

        if destination_arm in reachable_by:
            object_x, object_y = _object_arm_xy(obj, destination_arm)
            actions.extend(
                [
                    _pick_action_for_object(obj, destination_arm, object_x, object_y),
                    _drop_action(destination_arm, destination),
                ]
            )
            planned_objects.append(
                {
                    "object_id": object_id,
                    "color": obj.get("color"),
                    "mode": "direct",
                    "arm_id": destination_arm,
                    "destination": destination,
                    "reach_source": (
                        "camera_scan_reachable_by"
                        if destination_arm in (obj.get("reachable_by") or [])
                        else "planner_guardrail_xy_check"
                    ),
                }
            )
            continue

        support_arm = None
        for candidate in reachable_by:
            if candidate != destination_arm:
                support_arm = candidate
                break
        if not support_arm:
            diagnostic = _object_reach_diagnostics(obj, destination_arm)
            if derived_reach_checks:
                diagnostic["planner_guardrail_checks"] = derived_reach_checks
            unresolved.append(
                {
                    "object_id": object_id,
                    "color": obj.get("color"),
                    "reason": (
                        f"{destination_arm} cannot reach this object with the current reach limits"
                        if len(active_robot_ids()) == 1
                        else f"No active arm can reach object for destination arm {destination_arm}"
                    ),
                    "reachable_by": reachable_by,
                    "destination_arm": destination_arm,
                    "reach_diagnostic": diagnostic,
                }
            )
            continue

        object_x, object_y = _object_arm_xy(obj, support_arm)
        actions.append(
            {
                "action": "handoff",
                "intent_arm": destination_arm,
                "support_arm": support_arm,
                "object_x": object_x,
                "object_y": object_y,
                "destination": destination,
                "object_id": object_id,
                "color": obj.get("color"),
                "camera_id": camera_id,
            }
        )
        planned_objects.append(
            {
                "object_id": object_id,
                "color": obj.get("color"),
                "mode": "handoff",
                "support_arm": support_arm,
                "destination_arm": destination_arm,
                "destination": destination,
            }
        )

    if unresolved:
        raise ValueError(json.dumps({
            "error": "plan_all_matching_objects has objects outside active-arm reach limits",
            "active_arm_ids": active_robot_ids(),
            "destination_arm": destination_arm,
            "destination": destination,
            "unresolved": unresolved,
        }, ensure_ascii=False))

    actions.append(
        _scan_action(
            camera_id=camera_id,
            color=color,
            purpose="Verify all matching objects have been removed from the workspace before reporting completion.",
            verification={"type": "scan_absence", "color": color},
        )
    )
    return {
        "name": request.get("name", f"plan-{color}-objects-to-{destination}"),
        "destination": destination,
        "requested_destination": requested_destination,
        "final_destination": destination,
        "matching_object_plan": {
            "color": color,
            "camera_id": camera_id,
            "planned_object_count": len(planned_objects),
            "destination_arm": destination_arm,
            "destination_resolution": destination_resolution,
            "objects": planned_objects,
            "requires_final_scan_absence": True,
        },
        "actions": actions,
    }


def plan_task(request_doc):
    try:
        request = _coerce_json_document(request_doc, "request")
    except ValueError as exc:
        return {"ok": False, "semantic_plan": True, "error": str(exc)}
    if not isinstance(request, dict):
        return {"ok": False, "semantic_plan": True, "error": "request must be a JSON object"}

    if "actions" in request or "plan" in request:
        validation = validate_semantic_plan(request)
        return {
            "ok": validation.get("ok"),
            "semantic_plan": True,
            "plan": {
                "name": request.get("name", "semantic-plan"),
                "actions": request.get("actions", validation.get("actions", [])),
            },
            "validation": validation,
            "feedback": validation.get("feedback"),
        }

    intent = str(request.get("intent") or request.get("task") or "").strip().lower()
    try:
        if intent in {"pick_drop", "pick_place", "pick-and-drop", "pick-and-place", ""}:
            arm = normalize_arm_id(request.get("arm_id"))
            if not arm:
                raise ValueError("request.arm_id is required for pick_drop")
            destination = request.get("destination")
            if not destination:
                raise ValueError("request.destination is required for pick_drop")
            if _storage_reference(destination):
                color = request.get("color")
                plan = {
                    "name": request.get("name", "planned-storage-placement"),
                    "destination": destination,
                    "requested_destination": destination,
                    "storage": True,
                    "actions": [
                        {"action": "get_state", "purpose": "Validate calibrated arm state before Storage work."},
                        _scan_storage_slots_action(request.get("camera_id", "Cam1"), "Refresh Storage occupancy before selecting a Slot."),
                        {
                            "action": "pick",
                            "arm_id": arm,
                            "target": {
                                "type": "pose",
                                "x": _request_float(request, "object_x"),
                                "y": _request_float(request, "object_y"),
                                "r": float(request.get("r", 0.0)),
                            },
                            "object_id": request.get("object_id"),
                            "color": color,
                        },
                        _place_to_storage_slot_action(
                            arm,
                            reference=_storage_resolve_reference(destination),
                            color=color,
                            object_id=request.get("object_id"),
                            purpose="Resolve, reserve, place, park, rescan, and verify occupied state.",
                        ),
                    ],
                }
            else:
                destination_resolution = resolve_destination_for_arm(arm, destination)
                if not destination_resolution.get("ok"):
                    raise ValueError(destination_resolution.get("error") or f"Destination '{destination}' is not configured for {arm}")
                destination = destination_resolution["destination"]
                plan = {
                    "name": request.get("name", "planned-pick-drop"),
                    "destination": destination,
                    "requested_destination": destination_resolution.get("requested_destination"),
                    "destination_resolution": destination_resolution,
                    "actions": [
                        {
                            "action": "pick",
                            "arm_id": arm,
                            "target": {
                                "type": "pose",
                                "x": _request_float(request, "object_x"),
                                "y": _request_float(request, "object_y"),
                                "r": float(request.get("r", 0.0)),
                            },
                            "object_id": request.get("object_id"),
                            "color": request.get("color"),
                        },
                        {
                            "action": "drop",
                            "arm_id": arm,
                            "target": {"type": "named", "name": destination},
                        },
                    ],
                }
        elif intent in {"plan_all_matching_objects", "plan_matching_objects", "plan_all_objects_to_zone"}:
            destination_hint = str(request.get("destination") or request.get("zone") or "").strip()
            plan = _build_storage_plan_all_matching_objects(request) if _storage_reference(destination_hint) else _build_plan_all_matching_objects(request)
        elif intent == "handoff":
            support_arm = normalize_arm_id(request.get("support_arm"))
            if not support_arm:
                raise ValueError("request.support_arm is required for handoff")
            (object_x, object_y), source_object = _handoff_source_xy(request, support_arm)
            object_id = request.get("object_id") or (source_object or {}).get("id")
            plan = {
                "name": request.get("name", "planned-handoff"),
                "destination": request.get("destination"),
                "actions": [
                    {
                        "action": "handoff",
                        "intent_arm": request.get("intent_arm") or request.get("arm_id"),
                        "support_arm": support_arm,
                        "object_x": object_x,
                        "object_y": object_y,
                        "source_coordinate": f"per_arm_robot_plane[{support_arm}]",
                        "destination": request.get("destination"),
                        "object_id": object_id,
                        "color": request.get("color") or (source_object or {}).get("color"),
                        "camera_id": request.get("camera_id", "Cam1"),
                    }
                ],
            }
        elif intent == "move":
            plan = {
                "name": request.get("name", "planned-move"),
                "actions": [
                    {
                        "action": "move",
                        "arm_id": request.get("arm_id"),
                        "phase": request.get("phase"),
                        "target": request.get("target") or {
                            "type": request.get("target_type"),
                            "name": request.get("destination"),
                            "x": request.get("x"),
                            "y": request.get("y"),
                            "z": request.get("z"),
                            "r": request.get("r", 0.0),
                        },
                    }
                ],
            }
        else:
            return {
                "ok": False,
                "semantic_plan": True,
                "error": f"Unsupported planning intent '{intent}'",
                "feedback": "Use intent=pick_drop, handoff, plan_all_matching_objects, move, or provide actions directly.",
            }
    except ValueError as exc:
        return {
            "ok": False,
            "semantic_plan": True,
            "error": str(exc),
            "feedback": "Provide the missing structured fields and call plan_task again.",
        }

    validation = validate_semantic_plan(plan)
    return {
        "ok": validation.get("ok"),
        "semantic_plan": True,
        "plan": plan,
        "validation": validation,
        "feedback": validation.get("feedback"),
    }


def _parse_utc(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _duration_ms(started_at, completed_at):
    start = _parse_utc(started_at)
    end = _parse_utc(completed_at)
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _enrich_plan_state(state):
    enriched = copy.deepcopy(state)
    enriched["event_count"] = len(enriched.get("events", []))
    enriched["expanded_step_count"] = len(enriched.get("expanded_actions", []))
    enriched["duration_ms"] = _duration_ms(enriched.get("started_at"), enriched.get("completed_at"))
    return enriched


class SemanticPlanStore:
    def __init__(self):
        ensure_plan_state_dir()
        self.lock = threading.Lock()

    def create(self, plan_doc, validation, plan_id=None, mode="inline"):
        plan_id = plan_id or f"plan-{uuid.uuid4().hex[:12]}"
        state = {
            "ok": True,
            "semantic_plan": True,
            "plan_id": plan_id,
            "name": validation.get("name", plan_id),
            "status": "pending",
            "created_at": utc_now(),
            "started_at": None,
            "completed_at": None,
            "mode": mode,
            "cancel_requested": False,
            "current_step_index": None,
            "actions": copy.deepcopy(plan_doc.get("actions", [])),
            "expanded_actions": copy.deepcopy(validation.get("expanded_actions", [])),
            "events": [{"ts": utc_now(), "type": "plan_created", "message": "Plan created"}],
            "result": None,
            "incident": None,
        }
        self.save(state)
        return _enrich_plan_state(state)

    def load(self, plan_id):
        path = plan_state_path(plan_id)
        if not os.path.exists(path):
            return None
        with self.lock:
            last_error = None
            for _ in range(3):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return _enrich_plan_state(json.load(f))
                except json.JSONDecodeError as exc:
                    last_error = exc
                    time.sleep(0.05)
            if last_error:
                raise last_error
        return None

    def save(self, state):
        path = plan_state_path(state["plan_id"])
        temp_path = f"{path}.{threading.get_ident()}.tmp"
        with self.lock:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, path)

    def list(self):
        ensure_plan_state_dir()
        results = []
        for name in os.listdir(PLAN_STATE_DIR):
            if not name.endswith(".json"):
                continue
            path = os.path.join(PLAN_STATE_DIR, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    results.append(_enrich_plan_state(json.load(f)))
            except Exception:
                continue
        results.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return results

    def request_cancel(self, plan_id):
        state = self.load(plan_id)
        if not state:
            return {"ok": False, "semantic_plan": True, "plan_id": plan_id, "error": "Plan not found"}
        state["cancel_requested"] = True
        state["events"].append({"ts": utc_now(), "type": "cancel_requested", "message": "Cancel requested"})
        self.save(state)
        return {"ok": True, "semantic_plan": True, "plan_id": plan_id, "status": state["status"], "cancel_requested": True}


class SemanticPlanRunner:
    def __init__(self, state, store):
        self.state = state
        self.store = store
        self.controller = FleetController(ROBOT_FLEET_CONFIG)
        self.state_lock = threading.Lock()

    def _save(self):
        self.store.save(self.state)

    def _append_event(self, event_type, message, **extra):
        event = {"ts": utc_now(), "type": event_type, "message": message}
        event.update(extra)
        with self.state_lock:
            self.state["events"].append(event)
            self._save()

    def _set_status(self, status):
        with self.state_lock:
            self.state["status"] = status
            if status == "running" and not self.state.get("started_at"):
                self.state["started_at"] = utc_now()
            if status in {"completed", "failed", "blocked", "cancelled", "aborted"}:
                self.state["completed_at"] = utc_now()
            self._save()

    def _check_cancel_requested(self):
        refreshed = self.store.load(self.state["plan_id"])
        if refreshed and refreshed.get("cancel_requested"):
            return True
        return False

    def _execute_move(self, action):
        arm = action["arm_id"]
        phase = action["phase"]
        target = action.get("target") or {}

        if phase == "home" or target.get("type") == "home":
            return self.controller.home_arm(arm)

        if phase == "park":
            destination = target.get("name") or STANDARD_PARK_TARGETS.get(arm)
            return self.controller.move_to_named_pose(arm, destination, "hover")

        if target.get("type") == "named":
            return self.controller.move_to_named_pose(arm, target.get("name"), phase)

        if target.get("type") == "pose":
            if target.get("z") is None and phase in {"hover", "surface"}:
                return self.controller.move_to_work_pose(
                    arm,
                    float(target["x"]),
                    float(target["y"]),
                    phase,
                    float(target.get("r", 0.0)),
                )
            return self.controller.move_joint_to_pose_arm(
                arm,
                float(target["x"]),
                float(target["y"]),
                float(target["z"]),
                float(target.get("r", 0.0)),
            )

        return {"ok": False, "robot_id": arm, "error": f"Unsupported move target: {target}"}

    def _execute_scan(self, action):
        controller = CameraController()
        result = controller.scan_workspace(
            camera_id=action.get("camera_id", "Cam1"),
            color=action.get("color", "any"),
            selection=action.get("selection", "first"),
        )
        if not result.get("ok"):
            return result

        verification = action.get("verification") or {}
        if verification.get("type") == "scan_absence":
            color = str(verification.get("color") or action.get("color") or "any").strip().lower()
            objects = result.get("objects") or []
            remaining = [
                obj for obj in objects
                if isinstance(obj, dict) and (color == "any" or str(obj.get("color", "")).lower() == color)
            ]
            if remaining:
                return {
                    **result,
                    "ok": False,
                    "verification_failed": True,
                    "verification": verification,
                    "error": f"scan_absence failed: {len(remaining)} matching object(s) still visible",
                    "remaining_matching_objects": remaining,
                }
            return {
                **result,
                "verification_passed": True,
                "verification": verification,
                "remaining_matching_objects": [],
            }

        return result

    def _execute_storage_scan(self, action):
        result = scan_storage()
        if result.get("ok"):
            result["storage_scan"] = True
        return result

    def _execute_storage_place(self, action):
        arm = normalize_arm_id(action.get("robot_id") or action.get("arm_id"))
        reference = _storage_resolve_reference(action.get("reference"))
        result = self.controller.place_to_storage_slot(
            robot_id=arm,
            reference=reference,
            color=action.get("color"),
        )
        verification = result.get("verification") if isinstance(result, dict) else None
        verified = bool(
            isinstance(verification, dict)
            and verification.get("verified") is True
            and str(result.get("occupancy") or "").lower() == "occupied"
        )
        if not verified:
            return {
                **(result if isinstance(result, dict) else {"result": result}),
                "ok": False,
                "verification_required": True,
                "error_code": (result.get("error_code") if isinstance(result, dict) else None) or "capacity_unknown",
                "error": (result.get("error") if isinstance(result, dict) else None) or "Storage placement was not verified as occupied",
            }
        return result
    def _execute_action(self, action):
        action_name = action.get("action")
        if action_name == "move":
            return self._execute_move(action)
        if action_name == "grip":
            return self.controller.set_tool_state(action["arm_id"], action["state"] == "on")
        if action_name == "scan":
            return self._execute_scan(action)
        if action_name == "scan_storage_slots":
            return self._execute_storage_scan(action)
        if action_name == "place_to_storage_slot":
            return self._execute_storage_place(action)
        if action_name == "get_state":
            state = build_workspace_state(lambda arm_id: self.controller.get_arm_pose(arm_id))
            required_clear_arms = action.get("must_be_clear_of") or []
            blocked_arms = []
            for arm in required_clear_arms:
                arm_state = (state.get("arms") or {}).get(arm) or {}
                if not arm_state.get("ok") or arm_state.get("in_shared_zone") or not arm_state.get("is_at_standard_park"):
                    blocked_arms.append(arm)
            if blocked_arms:
                return {
                    "ok": False,
                    "error_code": "handoff_shared_zone_not_clear",
                    "error": "Required arm has not reached its configured safe park outside the shared zone",
                    "required_clear_arms": required_clear_arms,
                    "blocked_arms": blocked_arms,
                    "workspace_state": state,
                }
            return state
        return {"ok": False, "error": f"Unsupported expanded action: {action_name}"}

    def run(self):
        try:
            self._set_status("running")
            step_results = []
            for index, action in enumerate(self.state.get("expanded_actions", [])):
                if self._check_cancel_requested():
                    result = {"ok": False, "error": "Plan cancelled before step execution", "step_results": step_results}
                    with self.state_lock:
                        self.state["result"] = result
                        self._save()
                    self._set_status("cancelled")
                    self._append_event("plan_cancelled", "Plan cancelled")
                    return _enrich_plan_state(self.state)

                with self.state_lock:
                    self.state["current_step_index"] = index
                    self._save()
                self._append_event("step_started", f"{action.get('action')} started", step_index=index, action=action)
                result = self._execute_action(action)
                step_result = {"step_index": index, "action": action, "result": result, "ok": bool(result.get("ok"))}
                step_results.append(step_result)

                if result.get("ok"):
                    self._append_event("step_completed", f"{action.get('action')} completed", step_index=index, result=result)
                    continue

                status = "blocked" if result.get("guardrail_blocked") or result.get("fault_type") == "guardrail_blocked" else "failed"
                final_result = {"ok": False, "error": result.get("error", "Plan step failed"), "step_results": step_results}
                with self.state_lock:
                    self.state["incident"] = {"step_index": index, "action": action, "details": result}
                    self.state["result"] = final_result
                    self._save()
                self._set_status(status)
                self._append_event("step_failed", "Plan step failed", step_index=index, result=result)
                return _enrich_plan_state(self.state)

            final_result = {"ok": True, "step_results": step_results}
            with self.state_lock:
                self.state["current_step_index"] = None
                self.state["result"] = final_result
                self._save()
            self._set_status("completed")
            self._append_event("plan_completed", "Plan completed", result=final_result)
            return _enrich_plan_state(self.state)
        finally:
            self.controller.disconnect_all()


def _validated_plan(plan_doc):
    plan = coerce_plan_document(plan_doc)
    validation = validate_semantic_plan(plan)
    return plan, validation


def run_semantic_plan_inline(plan_doc, plan_id=None):
    plan, validation = _validated_plan(plan_doc)
    if not validation.get("ok"):
        return {
            "ok": False,
            "semantic_plan": True,
            "status": "rejected",
            "validation": validation,
            "error": "Plan validation failed",
        }
    store = SemanticPlanStore()
    state = store.create(plan, validation, plan_id=plan_id, mode="inline")
    runner = SemanticPlanRunner(state, store)
    return runner.run()


def start_semantic_plan_background(plan_doc, source_file=None):
    plan, validation = _validated_plan(plan_doc)
    if not validation.get("ok"):
        return {
            "ok": False,
            "semantic_plan": True,
            "status": "rejected",
            "validation": validation,
            "error": "Plan validation failed",
        }

    store = SemanticPlanStore()
    state = store.create(plan, validation, mode="background")
    plan_id = state["plan_id"]
    command = [sys.executable, os.fspath(arc_py_path()), "plan", "worker", "--plan-id", plan_id]
    if source_file:
        command.extend(["--file", os.path.abspath(source_file)])
    else:
        inline_path = os.path.join(PLAN_STATE_DIR, f"{plan_id}.plan.json")
        with open(inline_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        command.extend(["--file", inline_path])

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(
        command,
        cwd=os.fspath(project_path()),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )
    return {
        "ok": True,
        "semantic_plan": True,
        "plan_id": plan_id,
        "status": "pending",
        "mode": "background",
        "expanded_step_count": validation.get("expanded_step_count"),
    }


def run_semantic_plan_worker(plan_id, file_path):
    store = SemanticPlanStore()
    state = store.load(plan_id)
    plan = load_semantic_plan_document(file_path=file_path)
    validation = validate_semantic_plan(plan)
    if not validation.get("ok"):
        if state:
            state["status"] = "rejected"
            state["result"] = {"ok": False, "validation": validation}
            state["completed_at"] = utc_now()
            store.save(state)
            return _enrich_plan_state(state)
        return {"ok": False, "semantic_plan": True, "plan_id": plan_id, "status": "rejected", "validation": validation}

    if not state:
        state = store.create(plan, validation, plan_id=plan_id, mode="background")
    else:
        state["actions"] = copy.deepcopy(plan.get("actions", []))
        state["expanded_actions"] = copy.deepcopy(validation.get("expanded_actions", []))
        store.save(state)

    runner = SemanticPlanRunner(state, store)
    return runner.run()
