import argparse
import json
import ast
import sys

from .camera_controller import CameraController
from .config import CAMERA_CONFIG, ARM_REACH_ZONES, ROBOT_FLEET_CONFIG, SIM_MODE
from .sim_world import WorldModel
from .fleet_controller import FleetController
from .storage_service import load as load_storage, list_slots as list_storage_slots, resolve as resolve_storage, scan as scan_storage
from .task_system import (
    TaskStore,
    load_task_document,
    preflight_task,
    run_background_worker,
    run_task_inline,
    start_task_background,
)
from .semantic_plan import (
    SemanticPlanStore,
    load_semantic_plan_document,
    plan_task,
    run_semantic_plan_inline,
    run_semantic_plan_worker,
    start_semantic_plan_background,
    validate_semantic_plan,
)
from .tool_context import build_tool_context
from .workspace_model import build_handoff_plan, build_pick_place_plan, build_workspace_layout, build_workspace_state, list_workspace_zones, list_storage_groups, plan_workspace_intent


def print_output(payload, as_json=False):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if payload.get("guardrail_blocked"):
        print("ARC guardrail blocked the command before robot motion.")
        if payload.get("robot_id"):
            print(f"- robot_id: {payload['robot_id']}")
        if payload.get("operation"):
            print(f"- operation: {payload['operation']}")
        if payload.get("fault_type"):
            print(f"- fault_type: {payload['fault_type']}")
        if payload.get("recommended_action"):
            print(f"- recommended_action: {payload['recommended_action']}")
        guardrail = payload.get("guardrail") or payload.get("details", {}).get("guardrail")
        if isinstance(guardrail, dict):
            if guardrail.get("guardrail"):
                print(f"- guardrail: {guardrail['guardrail']}")
            if guardrail.get("reason"):
                print(f"- reason: {guardrail['reason']}")
            if guardrail.get("shared_zone"):
                print(f"- shared_zone: {guardrail['shared_zone']}")
            if guardrail.get("target_global_x_mm") is not None:
                print(f"- target_global_x_mm: {guardrail['target_global_x_mm']}")
        if payload.get("error"):
            print(payload["error"])
        return

    if payload.get("guardrail_passed"):
        print("ARC guardrail preflight passed.")
        if payload.get("robot_id"):
            print(f"- robot_id: {payload['robot_id']}")
        if payload.get("operation"):
            print(f"- operation: {payload['operation']}")
        if payload.get("step_count") is not None:
            print(f"- step_count: {payload['step_count']}")
        if payload.get("target_pose"):
            print(f"- target_pose: {payload['target_pose']}")
        geometry = payload.get("safety_geometry")
        if isinstance(geometry, dict):
            print(f"- shared_zone: {geometry.get('shared_zone')}")
        return

    if "sdk_ready" in payload and "camera_id" in payload:
        print(f"{payload['camera_id']} status: {payload.get('status')}")
        print(f"- sdk_ready: {payload.get('sdk_ready')}")
        print(f"- calibration_ready: {payload.get('calibration_ready')}")
        if payload.get("sdk_error"):
            print(f"- sdk_error: {payload['sdk_error']}")
        return

    if "object_count" in payload and "camera_id" in payload:
        print(f"{payload['camera_id']} scan")
        print(f"- object_count: {payload['object_count']}")
        print(f"- workspace_detected: {payload.get('workspace', {}).get('detected')}")
        if payload.get("selected_object"):
            selected = payload["selected_object"]
            print(f"- selected_object: {selected['id']} color={selected['color']} robot_plane={selected['robot_plane']} reachable_by={selected['reachable_by']}")
        for obj in payload.get("objects", []):
            print(f"- {obj['id']} color={obj['color']} robot_plane={obj['robot_plane']} reachable_by={obj['reachable_by']}")
        return

    if payload.get("workspace_layout"):
        print("ARC workspace layout")
        print(f"- source: {payload.get('source', {}).get('runtime_geometry')}")
        print(f"- shared_zone: {payload.get('safety_geometry', {}).get('shared_zone')}")
        print(f"- clear_workspace: {payload.get('standard_parking', {}).get('clear_workspace')}")
        print(f"- home_is_clear_workspace: {payload.get('standard_parking', {}).get('home_is_clear_workspace')}")
        return

    if payload.get("workspace_state"):
        print("ARC workspace state")
        print(f"- shared_zone_occupancy: {payload.get('shared_zone_occupancy')}")
        for arm_id, arm in payload.get("arms", {}).items():
            print(f"- {arm_id}: in_shared_zone={arm.get('in_shared_zone')} park={arm.get('standard_park_target')} nearest={arm.get('nearest_named_target')}")
        return

    if payload.get("plan_type") == "station_mediated_handoff":
        print("ARC handoff plan")
        print(f"- ok: {payload.get('ok')}")
        print(f"- intent_arm: {payload.get('intent_arm')}")
        print(f"- support_arm: {payload.get('support_arm')}")
        print(f"- destination: {payload.get('destination')}")
        print(f"- step_count: {len(payload.get('steps', []))}")
        if payload.get("error"):
            print(f"- error: {payload['error']}")
        return

    if payload.get("plan_type") == "atomic_pick_place":
        print("ARC pick/place plan")
        print(f"- ok: {payload.get('ok')}")
        print(f"- arm_id: {payload.get('arm_id')}")
        print(f"- destination: {payload.get('destination')}")
        print(f"- step_count: {len(payload.get('steps', []))}")
        if payload.get("error"):
            print(f"- error: {payload['error']}")
        return

    if payload.get("semantic_plan"):
        print("ARC semantic plan")
        print(f"- ok: {payload.get('ok')}")
        if payload.get("plan_id"):
            print(f"- plan_id: {payload.get('plan_id')}")
        if payload.get("status"):
            print(f"- status: {payload.get('status')}")
        if payload.get("name"):
            print(f"- name: {payload.get('name')}")
        if payload.get("expanded_step_count") is not None:
            print(f"- expanded_step_count: {payload.get('expanded_step_count')}")
        validation = payload.get("validation")
        if isinstance(validation, dict) and validation.get("errors"):
            print(f"- validation_errors: {len(validation['errors'])}")
            for item in validation["errors"]:
                print(f"  - {item.get('code')}: {item.get('message')}")
        if payload.get("errors"):
            print(f"- errors: {len(payload['errors'])}")
            for item in payload["errors"]:
                print(f"  - {item.get('code')}: {item.get('message')}")
        if payload.get("feedback"):
            print(f"- feedback: {payload.get('feedback')}")
        if payload.get("error"):
            print(payload["error"])
        return

    if "targets" in payload and "camera_id" in payload:
        print(f"{payload['camera_id']} target assignment")
        for target in payload.get("targets", []):
            print(f"- {target['arm_id']} <= {target['object_id']} color={target['color']} robot_plane={target['robot_plane']}")
        for item in payload.get("unresolved", []):
            print(f"- unresolved {item['arm_id']} color={item['color']}: {item['reason']}")
        return

    if payload.get("pick") and payload.get("place") and payload.get("robot_id"):
        print(f"{payload['robot_id']} detected pick/place")
        if payload.get("assignment", {}).get("targets"):
            target = payload["assignment"]["targets"][0]
            print(f"- source color={target['color']} robot_plane={target['robot_plane']}")
        print(f"- destination: {payload.get('destination')}")
        print(f"- pick_ok: {payload['pick'].get('ok')}")
        print(f"- place_ok: {payload['place'].get('ok')}")
        verification = payload.get("post_pick_verification")
        if verification:
            print(f"- post_pick_verified: {verification.get('verified')}")
            if verification.get("nearby_matches"):
                print(f"- nearby_matches: {verification['nearby_matches']}")
        if not payload.get("ok"):
            print(payload.get("error", "Pick/place failed"))
        return

    if "named_target_pose" in payload and "robot_id" in payload:
        print(f"{payload['robot_id']} move to named target")
        print(f"- destination: {payload.get('destination')}")
        print(f"- target_pose: {payload['named_target_pose']}")
        if not payload.get("ok"):
            print(payload.get("error", "Move to named target failed"))
        return

    if "tool_enabled" in payload and "robot_id" in payload:
        print(f"{payload['robot_id']} tool state")
        print(f"- tool_enabled: {payload['tool_enabled']}")
        if not payload.get("ok"):
            print(payload.get("error", "Tool control failed"))
        return

    if "arms" in payload:
        print(f"Total arms: {payload['total_arms']}")
        print(f"Connected arms: {payload['connected_arms']}")
        for arm in payload["arms"]:
            status = "connected" if arm["connected"] else "disconnected"
            print(f"- {arm['robot_id']} ({arm['ip']}:{arm['port']}): {status}")
        return

    if "connected" in payload and "robot_id" in payload and "arms" not in payload:
        status = "connected" if payload["connected"] else "disconnected"
        print(f"{payload['robot_id']} ({payload['ip']}:{payload['port']}): {status}")
        return

    if payload.get("ok") and "state" in payload:
        state = payload["state"]
        print(f"{payload['robot_id']} state")
        print(f"- robot_mode: {state['robot_mode']}")
        print(f"- robot_mode_name: {state['robot_mode_name']}")
        print(f"- enable_status: {state['enable_status']}")
        print(f"- running_status: {state['running_status']}")
        print(f"- error_status: {state['error_status']}")
        print(f"- brake_status: {state['brake_status']}")
        print(f"- pose: {state['pose']}")
        return

    if payload.get("ok") and "pose" in payload:
        print(f"{payload['robot_id']} pose")
        print(f"robot_mode: {payload['robot_mode']} ({payload['robot_mode_name']})")
        print(payload["pose"])
        print(payload["joints"])
        return

    if payload.get("ok") and "incident" in payload:
        print(f"{payload['robot_id']} latest incident")
        print(payload["incident"])
        return

    if "task_id" in payload:
        print(f"Task {payload['task_id']}")
        print(f"status: {payload.get('status')}")
        if payload.get("name"):
            print(f"name: {payload['name']}")
        if payload.get("mode"):
            print(f"mode: {payload['mode']}")
        if payload.get("step_count") is not None:
            print(f"step_count: {payload['step_count']}")
        if payload.get("event_count") is not None:
            print(f"event_count: {payload['event_count']}")
        if payload.get("duration_ms") is not None:
            print(f"duration_ms: {payload['duration_ms']}")
        if payload.get("current_step_path"):
            print(f"current_step_path: {payload['current_step_path']}")
        if payload.get("incident"):
            print(f"incident: {payload['incident']}")
        if payload.get("error"):
            print(payload["error"])
        return

    if "tasks" in payload:
        print(f"Total tasks: {payload.get('total_tasks', 0)}")
        for task in payload.get("tasks", []):
            print(f"- {task['task_id']} [{task.get('status')}] {task.get('name')}")
        return

    if "path_type" in payload:
        print(f"{payload['robot_id']} path run: {payload['path_type']}")
        print(payload.get("results"))
        if not payload.get("ok"):
            print(payload.get("error", "Path failed"))
        return

    if "steps" in payload:
        print(f"{payload['robot_id']} recover-home flow")
        for item in payload["steps"]:
            print(f"- {item['step']}: {item['result']}")
        if not payload.get("ok"):
            print(payload.get("error", "Unknown error"))
        return

    if payload.get("pattern") == "square":
        print(f"Square pattern sent to {payload['robot_id']}: {payload['step']} mm per side")
        for item in payload["results"]:
            print(f"- {item['action']}: {item['result']}")
        if not payload.get("ok"):
            print(payload.get("error", "Square motion failed"))
        return

    if "result" in payload and "action" not in payload and "pattern" not in payload and "connected" not in payload:
        print(f"Command sent to {payload['robot_id']}")
        print(payload["result"])
        if not payload.get("ok"):
            print(payload.get("error", "Command failed"))
        return

    if "action" in payload:
        print(f"Move command sent to {payload['robot_id']}: {payload['action']} {payload['step']} mm")
        print(payload["result"])
        if not payload.get("ok"):
            print(payload.get("error", "Move failed"))
        return

    if all(k in payload for k in ["dx", "dy", "dz", "dr"]):
        print(f"Relative move sent to {payload['robot_id']}: dx={payload['dx']} dy={payload['dy']} dz={payload['dz']} dr={payload['dr']}")
        print(payload["result"])
        if not payload.get("ok"):
            print(payload.get("error", "Relative move failed"))
        return

    if "target_pose" in payload:
        print(f"Move-to-pose sent to {payload['robot_id']}: {payload['target_pose']}")
        print(payload["result"])
        if not payload.get("ok"):
            print(payload.get("error", "Move-to-pose failed"))
        return

    print(payload.get("error", "Unknown error"))


def build_parser():
    parser = argparse.ArgumentParser(prog="arc", description="ARC robot CLI")

    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Start the Scene Editor HTTP server")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8770)

    camera_parser = subparsers.add_parser("camera", help="Camera commands")
    camera_parser.add_argument("--json", "--as-json", action="store_true", dest="as_json")
    camera_subparsers = camera_parser.add_subparsers(dest="camera_command")

    camera_status_parser = camera_subparsers.add_parser("status", help="Get camera status")
    camera_status_parser.add_argument("--json", "--as-json", action="store_true", dest="as_json", default=argparse.SUPPRESS)
    camera_status_parser.add_argument("camera_id", nargs="?", default="Cam1")

    camera_scan_parser = camera_subparsers.add_parser("scan", help="Scan workspace and return objects with reachability")
    camera_scan_parser.add_argument("--json", "--as-json", action="store_true", dest="as_json", default=argparse.SUPPRESS)
    camera_scan_parser.add_argument("camera_id", nargs="?", default="Cam1")
    camera_scan_parser.add_argument("--color", default="any", choices=["any", "red", "green", "blue", "unknown"])
    camera_scan_parser.add_argument(
        "--selection",
        default="first",
        choices=["first", "largest", "leftmost", "rightmost", "topmost", "bottommost"],
    )

    camera_assign_parser = camera_subparsers.add_parser("assign-targets", help="Assign detected objects to arms by color")
    camera_assign_parser.add_argument("--json", "--as-json", action="store_true", dest="as_json", default=argparse.SUPPRESS)
    camera_assign_parser.add_argument("camera_id", nargs="?", default="Cam1")
    camera_assign_parser.add_argument("--assignments", required=True, help="Python/JSON list like [{'arm_id':'Arm1','color':'green'}]")

    workspace_parser = subparsers.add_parser("workspace", help="Workspace layout, state, and planning commands")
    workspace_parser.add_argument("--json", "--as-json", action="store_true", dest="as_json")
    workspace_subparsers = workspace_parser.add_subparsers(dest="workspace_command")

    workspace_subparsers.add_parser("layout", help="Return canonical workspace layout and handoff contract")
    workspace_subparsers.add_parser("state", help="Read arm poses and classify workspace/shared-zone occupancy")
    workspace_subparsers.add_parser("zones", help="List calibrated Outer Box and named work zones")
    workspace_subparsers.add_parser("storage-groups", help="List calibrated Storage groups and regions")
    workspace_subparsers.add_parser("context", help="Return compact calibrated runtime context without motion")

    calibrated_plan_parser = workspace_subparsers.add_parser("plan-intent", help="Scan and dry-run a calibrated zone/group/slot intent without moving hardware")
    calibrated_plan_parser.add_argument("--destination", required=True, help="Work zone, Storage group, or Storage slot")
    calibrated_plan_parser.add_argument("--source-zone", help="Optional calibrated work zone such as A1")
    calibrated_plan_parser.add_argument("--color", default="any", choices=["any", "red", "green", "blue", "unknown"])
    calibrated_plan_parser.add_argument("--robot-id", help="Optional explicit Arm1 or Arm2")
    calibrated_plan_parser.add_argument("--camera-id", default="Cam1")

    pick_place_plan_parser = workspace_subparsers.add_parser("plan-pick-place", help="Plan a direct one-arm pick/place as atomic tool steps without moving hardware")
    pick_place_plan_parser.add_argument("--arm-id", required=True, help="Arm that will pick and place, e.g. Arm2")
    pick_place_plan_parser.add_argument("--object-x", type=float, required=True, help="Object X in arm-local coordinates from camera_scan per_arm_robot_plane[arm]")
    pick_place_plan_parser.add_argument("--object-y", type=float, required=True, help="Object Y in arm-local coordinates from camera_scan per_arm_robot_plane[arm]")
    pick_place_plan_parser.add_argument("--destination", help="Final named destination; defaults to that arm's standard destination")
    pick_place_plan_parser.add_argument("--object-id", help="Optional detected object id")
    pick_place_plan_parser.add_argument("--color", choices=["red", "green", "blue", "unknown"], help="Optional object color")
    pick_place_plan_parser.add_argument("--camera-id", default="Cam1")

    handoff_plan_parser = workspace_subparsers.add_parser("plan-handoff", help="Plan a station-mediated handoff as atomic tool steps without moving hardware")
    handoff_plan_parser.add_argument("--intent-arm", required=True, help="Requested final/receiver arm, e.g. Arm2")
    handoff_plan_parser.add_argument("--support-arm", help="Optional support arm; defaults to the other arm")
    handoff_plan_parser.add_argument("--object-x", type=float, required=True, help="Object X in support-arm local coordinates from camera_scan per_arm_robot_plane[support]")
    handoff_plan_parser.add_argument("--object-y", type=float, required=True, help="Object Y in support-arm local coordinates from camera_scan per_arm_robot_plane[support]")
    handoff_plan_parser.add_argument("--destination", help="Final named destination for the intent arm; defaults to that arm's standard destination")
    handoff_plan_parser.add_argument("--object-id", help="Optional detected object id")
    handoff_plan_parser.add_argument("--color", choices=["red", "green", "blue", "unknown"], help="Optional object color")
    handoff_plan_parser.add_argument("--camera-id", default="Cam1")

    storage_parser = subparsers.add_parser("storage", help="Calibrated Storage Slot commands")
    storage_parser.add_argument("--json", "--as-json", action="store_true", dest="as_json")
    storage_subparsers = storage_parser.add_subparsers(dest="storage_command")

    storage_scan_parser = storage_subparsers.add_parser("scan", help="Fresh camera scan of all calibrated Storage Slots")
    storage_scan_parser.add_argument("--image-path")
    storage_scan_parser.add_argument("--expected-count", type=int, default=16)

    storage_list_parser = storage_subparsers.add_parser("list", help="Read latest cached Storage Slot registry")
    storage_list_parser.add_argument("--group", choices=["LT", "LB", "RT", "RB"])
    storage_list_parser.add_argument("--occupancy", choices=["empty", "occupied", "unknown", "reserved", "verification_pending"])

    storage_resolve_parser = storage_subparsers.add_parser("resolve", help="Resolve a usable Storage Slot without reservation or motion")
    storage_resolve_parser.add_argument("--reference")
    storage_resolve_parser.add_argument("--color")
    storage_resolve_parser.add_argument("--robot-id")
    storage_resolve_parser.add_argument("--allow-nonempty", action="store_true")

    storage_place_parser = storage_subparsers.add_parser("place", help="Place held object with reservation, park, and capacity verification")
    storage_place_parser.add_argument("--robot-id", required=True)
    storage_place_parser.add_argument("--reference")
    storage_place_parser.add_argument("--color")
    arm_parser = subparsers.add_parser("arm", help="Arm commands")
    arm_parser.add_argument("--json", "--as-json", action="store_true", dest="as_json")
    arm_subparsers = arm_parser.add_subparsers(dest="arm_command")

    arm_subparsers.add_parser("list", help="List configured arms and connection status")

    status_parser = arm_subparsers.add_parser("status", help="Get arm connection status")
    status_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")

    state_parser = arm_subparsers.add_parser("state", help="Get current arm feedback state")
    state_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")

    pose_parser = arm_subparsers.add_parser("pose", help="Get current arm pose and joints")
    pose_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")

    home_parser = arm_subparsers.add_parser("home", help="Send arm to home position")
    home_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")

    stop_parser = arm_subparsers.add_parser("stop", help="Stop arm motion")
    stop_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")

    recover_parser = arm_subparsers.add_parser("recover", help="Clear error and re-enable robot")
    recover_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")

    recover_home_parser = arm_subparsers.add_parser("recover-home", help="Recover robot if needed, then send it home")
    recover_home_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")

    move_parser = arm_subparsers.add_parser("move", help="Move arm in a basic relative direction")
    move_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    move_parser.add_argument("action", choices=["HOME", "UP", "DOWN", "LEFT", "RIGHT", "FORWARD", "BACK"])
    move_parser.add_argument("--step", type=float, default=25.0, help="Relative move distance in mm")

    move_relative_parser = arm_subparsers.add_parser("move-relative", help="Move arm by explicit relative deltas")
    move_relative_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    move_relative_parser.add_argument("--dx", type=float, default=0.0)
    move_relative_parser.add_argument("--dy", type=float, default=0.0)
    move_relative_parser.add_argument("--dz", type=float, default=0.0)
    move_relative_parser.add_argument("--dr", type=float, default=0.0)

    move_to_pose_parser = arm_subparsers.add_parser("move-to-pose", help="Move arm to an explicit pose")
    move_to_pose_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    move_to_pose_parser.add_argument("--x", type=float, required=True)
    move_to_pose_parser.add_argument("--y", type=float, required=True)
    move_to_pose_parser.add_argument("--z", type=float, required=True)
    move_to_pose_parser.add_argument("--r", type=float, default=0.0)

    preflight_pose_parser = arm_subparsers.add_parser("preflight-pose", help="Check ARC safety guardrails for an explicit pose without moving hardware")
    preflight_pose_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    preflight_pose_parser.add_argument("--x", type=float, required=True)
    preflight_pose_parser.add_argument("--y", type=float, required=True)
    preflight_pose_parser.add_argument("--z", type=float, required=True)
    preflight_pose_parser.add_argument("--r", type=float, default=0.0)
    preflight_pose_parser.add_argument("--skip-collision", action="store_true", help="Only check reach limits; do not query the other arm pose")

    move_joint_pose_parser = arm_subparsers.add_parser("move-joint-pose", help="Move arm to an explicit pose using MovJ")
    move_joint_pose_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    move_joint_pose_parser.add_argument("--x", type=float, required=True)
    move_joint_pose_parser.add_argument("--y", type=float, required=True)
    move_joint_pose_parser.add_argument("--z", type=float, required=True)
    move_joint_pose_parser.add_argument("--r", type=float, default=0.0)

    move_relative_joint_parser = arm_subparsers.add_parser("move-relative-joint", help="Move arm by explicit relative deltas using RelMovJ")
    move_relative_joint_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    move_relative_joint_parser.add_argument("--dx", type=float, default=0.0)
    move_relative_joint_parser.add_argument("--dy", type=float, default=0.0)
    move_relative_joint_parser.add_argument("--dz", type=float, default=0.0)
    move_relative_joint_parser.add_argument("--dr", type=float, default=0.0)

    move_named_target_parser = arm_subparsers.add_parser("move-to-named-target", help="Move an arm to a named destination without picking or placing")
    move_named_target_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    move_named_target_parser.add_argument("--destination", required=True, help="Named destination such as home or drop_zone")

    move_work_pose_parser = arm_subparsers.add_parser("move-work-pose", help="Atomic single motion to explicit x/y at configured hover or surface Z. Does not toggle suction.")
    move_work_pose_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    move_work_pose_parser.add_argument("--x", type=float, required=True)
    move_work_pose_parser.add_argument("--y", type=float, required=True)
    move_work_pose_parser.add_argument("--phase", required=True, choices=["hover", "surface"])
    move_work_pose_parser.add_argument("--r", type=float, default=0.0)

    move_named_pose_parser = arm_subparsers.add_parser("move-named-pose", help="Atomic single motion to a named target at hover or surface Z. Does not toggle suction.")
    move_named_pose_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    move_named_pose_parser.add_argument("--destination", required=True, help="Named destination such as handoff_zone, storage_zone, or drop_zone")
    move_named_pose_parser.add_argument("--phase", required=True, choices=["hover", "surface"])

    tool_state_parser = arm_subparsers.add_parser("tool-state", help="Set suction/tool output on or off")
    tool_state_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    tool_state_parser.add_argument("--enable", required=True, choices=["on", "off"])

    pick_detected_place_parser = arm_subparsers.add_parser("pick-detected-place", help="Detect a reachable colored object for an arm, pick it, and place it at a named destination")
    pick_detected_place_parser.add_argument("--json", "--as-json", action="store_true", dest="as_json", default=argparse.SUPPRESS)
    pick_detected_place_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    pick_detected_place_parser.add_argument("--color", required=True, choices=["red", "green", "blue", "unknown"])
    pick_detected_place_parser.add_argument("--destination", required=True, help="Named destination such as storage_zone or drop_zone")
    pick_detected_place_parser.add_argument("--camera-id", default="Cam1")
    pick_detected_place_parser.add_argument("--block", default=None, help="Optional block filter such as 'Block 1,1'")

    pick_all_detected_place_parser = arm_subparsers.add_parser("pick-all-detected-place", help="Loop scanโ’pickโ’place for all reachable matching objects until none remain")
    pick_all_detected_place_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    pick_all_detected_place_parser.add_argument("--json", "--as-json", action="store_true", dest="as_json", default=argparse.SUPPRESS)
    pick_all_detected_place_parser.add_argument("--destination", required=True, help="Named destination such as storage_zone or drop_zone")
    pick_all_detected_place_parser.add_argument("--color", default="any", choices=["any", "red", "green", "blue", "unknown"])
    pick_all_detected_place_parser.add_argument("--camera-id", default="Cam1")
    pick_all_detected_place_parser.add_argument("--max-picks", type=int, default=10)

    pick_at_parser = arm_subparsers.add_parser("pick-at", help="Primitive: approachโ’descendโ’suction-onโ’lift at explicit (x,y) using the arm's pick z profile. No reach/color filtering. AI must choose arm and pose itself.")
    pick_at_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    pick_at_parser.add_argument("--x", type=float, required=True, help="Target x in arm-local frame")
    pick_at_parser.add_argument("--y", type=float, required=True, help="Target y in arm-local frame")
    pick_at_parser.add_argument("--r", type=float, default=0.0)

    place_at_named_parser = arm_subparsers.add_parser("place-at-named", help="Primitive: approachโ’descendโ’suction-offโ’depart at a named destination. Use after pick-at to drop the carried object.")
    place_at_named_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    place_at_named_parser.add_argument("--destination", required=True, help="Named destination such as storage_zone, drop_zone, or home")

    place_at_pose_parser = arm_subparsers.add_parser("place-at-pose", help="Primitive: approachโ’descendโ’suction-offโ’lift at explicit (x,y) using the arm's pick z as place height (same height objects sat at during pick).")
    place_at_pose_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    place_at_pose_parser.add_argument("--x", type=float, required=True)
    place_at_pose_parser.add_argument("--y", type=float, required=True)
    place_at_pose_parser.add_argument("--r", type=float, default=0.0)

    native_motion_parser = arm_subparsers.add_parser("native-motion", help="Send a native motion command string to port 30003")
    native_motion_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    native_motion_parser.add_argument("--command", required=True, help="Native command such as MovJ(250,0,50,0)")

    motion_profile_parser = arm_subparsers.add_parser("motion-profile", help="Set native motion profile values")
    motion_profile_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    motion_profile_parser.add_argument("--speed-l", type=float)
    motion_profile_parser.add_argument("--acc-l", type=float)
    motion_profile_parser.add_argument("--speed-j", type=float)
    motion_profile_parser.add_argument("--acc-j", type=float)

    continue_parser = arm_subparsers.add_parser("continue", help="Resume the motion queue if it is blocked")
    continue_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")

    arc_parser = arm_subparsers.add_parser("arc", help="Run a native Arc motion from current pose through a point to a target point")
    arc_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    arc_parser.add_argument("--through-x", type=float, required=True)
    arc_parser.add_argument("--through-y", type=float, required=True)
    arc_parser.add_argument("--through-z", type=float, required=True)
    arc_parser.add_argument("--through-r", type=float, default=0.0)
    arc_parser.add_argument("--target-x", type=float, required=True)
    arc_parser.add_argument("--target-y", type=float, required=True)
    arc_parser.add_argument("--target-z", type=float, required=True)
    arc_parser.add_argument("--target-r", type=float, default=0.0)

    circle_parser = arm_subparsers.add_parser("circle", help="Run a native Circle motion using current pose as the start point on the circumference")
    circle_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    circle_parser.add_argument("--radius", type=float, required=True)
    circle_parser.add_argument("--count", type=int, default=1)
    circle_parser.add_argument("--clockwise", action="store_true")
    circle_parser.add_argument("--z", type=float)
    circle_parser.add_argument("--r", type=float)

    square_parser = arm_subparsers.add_parser("square", help="Trace a small square using relative moves")
    square_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    square_parser.add_argument("--step", type=float, default=25.0, help="Square side length in mm")

    run_path_parser = arm_subparsers.add_parser("run-path", help="Run a parameterized path")
    run_path_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    run_path_parser.add_argument("--type", required=True, choices=["rectangle", "circle", "arc", "waypoints"])
    run_path_parser.add_argument("--width", type=float)
    run_path_parser.add_argument("--height", type=float)
    run_path_parser.add_argument("--radius", type=float)
    run_path_parser.add_argument("--count", type=int, default=1)
    run_path_parser.add_argument("--segments", type=int, default=12)
    run_path_parser.add_argument("--clockwise", action="store_true")
    run_path_parser.add_argument("--waypoints", help="Python/JSON list of waypoint dicts")
    run_path_parser.add_argument("--through-pose", help="Python/JSON dict for arc through pose")
    run_path_parser.add_argument("--target-pose", help="Python/JSON dict for arc target pose")
    run_path_parser.add_argument("--z", type=float)
    run_path_parser.add_argument("--r", type=float)

    incident_parser = arm_subparsers.add_parser("incident", help="Show latest stored incident for an arm")
    incident_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")

    ack_incident_parser = arm_subparsers.add_parser("ack-incident", help="Acknowledge and handle a stored incident")
    ack_incident_parser.add_argument("arm_id", help="Arm ID, e.g. Arm1 or Arm2")
    ack_incident_parser.add_argument("action", choices=["recover", "recover-home", "abort", "retry-last"])

    task_parser = subparsers.add_parser("task", help="Task commands")
    task_parser.add_argument("--json", "--as-json", action="store_true", dest="as_json")
    task_subparsers = task_parser.add_subparsers(dest="task_command")

    task_run_parser = task_subparsers.add_parser("run", help="Run a task inline")
    task_run_parser.add_argument("--file", help="Task JSON file")
    task_run_parser.add_argument("--task-json", help="Inline task JSON")

    task_preflight_parser = task_subparsers.add_parser("preflight", help="Check ARC safety guardrails for a task without moving hardware")
    task_preflight_parser.add_argument("--file", help="Task JSON file")
    task_preflight_parser.add_argument("--task-json", help="Inline task JSON")

    task_start_parser = task_subparsers.add_parser("start", help="Start a task in background")
    task_start_parser.add_argument("--file", help="Task JSON file")
    task_start_parser.add_argument("--task-json", help="Inline task JSON")

    task_status_parser = task_subparsers.add_parser("status", help="Read task status")
    task_status_parser.add_argument("task_id")

    task_list_parser = task_subparsers.add_parser("list", help="List tasks")

    task_cancel_parser = task_subparsers.add_parser("cancel", help="Request cancellation")
    task_cancel_parser.add_argument("task_id")

    task_worker_parser = task_subparsers.add_parser("worker", help="Internal background task worker")
    task_worker_parser.add_argument("--task-id", required=True)
    task_worker_parser.add_argument("--file", required=True)

    plan_parser = subparsers.add_parser("plan", help="Semantic plan commands")
    plan_parser.add_argument("--json", "--as-json", action="store_true", dest="as_json")
    plan_subparsers = plan_parser.add_subparsers(dest="plan_command")

    plan_task_parser = plan_subparsers.add_parser("plan-task", help="Build or repair a semantic plan from structured intent")
    plan_task_parser.add_argument("--request-json", required=True, help="Planning request JSON")

    plan_validate_parser = plan_subparsers.add_parser("validate", help="Validate a semantic action plan without moving hardware")
    plan_validate_parser.add_argument("--file", help="Plan JSON file")
    plan_validate_parser.add_argument("--plan-json", help="Inline plan JSON")

    plan_execute_parser = plan_subparsers.add_parser("execute", help="Execute a validated semantic plan")
    plan_execute_parser.add_argument("--file", help="Plan JSON file")
    plan_execute_parser.add_argument("--plan-json", help="Inline plan JSON")
    plan_execute_parser.add_argument("--mode", choices=["inline", "background"], default="inline")

    plan_status_parser = plan_subparsers.add_parser("status", help="Read semantic plan execution status")
    plan_status_parser.add_argument("plan_id")

    plan_subparsers.add_parser("list", help="List semantic plans")

    plan_cancel_parser = plan_subparsers.add_parser("cancel", help="Request semantic plan cancellation")
    plan_cancel_parser.add_argument("plan_id")

    plan_worker_parser = plan_subparsers.add_parser("worker", help="Internal background semantic plan worker")
    plan_worker_parser.add_argument("--plan-id", required=True)
    plan_worker_parser.add_argument("--file", required=True)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "serve":
        from .sim_server import serve
        serve(args.host, args.port)
        return 0

    valid_camera_commands = {"status", "scan", "assign-targets"}
    valid_workspace_commands = {"layout", "state", "zones", "storage-groups", "context", "plan-intent", "plan-pick-place", "plan-handoff"}
    valid_storage_commands = {"scan", "list", "resolve", "place"}
    valid_arm_commands = {
        "list", "status", "state", "pose", "home", "stop", "recover", "recover-home",
        "move", "move-relative", "move-to-pose", "preflight-pose", "move-joint-pose", "move-relative-joint", "move-to-named-target", "move-work-pose", "move-named-pose", "tool-state", "pick-detected-place", "pick-all-detected-place", "pick-at", "place-at-named", "place-at-pose", "native-motion", "motion-profile", "continue", "arc", "circle", "square", "run-path", "incident", "ack-incident"
    }
    valid_task_commands = {"run", "preflight", "start", "status", "list", "cancel", "worker"}
    valid_plan_commands = {"plan-task", "validate", "execute", "status", "list", "cancel", "worker"}

    if (args.command == "camera" and args.camera_command not in valid_camera_commands) or (
        args.command == "workspace" and args.workspace_command not in valid_workspace_commands
    ) or (
        args.command == "storage" and args.storage_command not in valid_storage_commands
    ) or (
        args.command == "arm" and args.arm_command not in valid_arm_commands
    ) or (
        args.command == "task" and args.task_command not in valid_task_commands
    ) or (
        args.command == "plan" and args.plan_command not in valid_plan_commands
    ) or args.command not in {"camera", "workspace", "storage", "arm", "task", "plan"}:
        parser.print_help()
        return 1

    if args.command == "camera":
        world = None
        if SIM_MODE:
            world = WorldModel()
            # world เนเธซเธฅเธ”เนเธ __init__ เนเธฅเนเธง (singleton)
        controller = CameraController(CAMERA_CONFIG, ARM_REACH_ZONES, world_model=world)
        try:
            if args.camera_command == "status":
                payload = controller.get_status(args.camera_id)
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
            if args.camera_command == "scan":
                payload = controller.scan_workspace(
                    camera_id=args.camera_id,
                    color=args.color,
                    selection=args.selection,
                )
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
            if args.camera_command == "assign-targets":
                assignments = ast.literal_eval(args.assignments)
                payload = controller.assign_targets(assignments, camera_id=args.camera_id)
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
        except (ValueError, SyntaxError) as exc:
            payload = {"ok": False, "camera_id": getattr(args, "camera_id", "Cam1"), "error": str(exc)}
            print_output(payload, args.as_json)
            return 1

    if args.command == "storage":
        if args.storage_command == "scan":
            payload = scan_storage(image_path=args.image_path, expected_count=args.expected_count)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1
        if args.storage_command == "list":
            registry = load_storage()
            payload = {
                "ok": True,
                "registry": registry,
                "slots": list_storage_slots(registry=registry, group=args.group, occupancy=args.occupancy),
            }
            print_output(payload, args.as_json)
            return 0
        if args.storage_command == "resolve":
            controller = FleetController(ROBOT_FLEET_CONFIG) if args.robot_id else None
            try:
                if controller:
                    controller.connect_all()
                online = [arm_id for arm_id, robot in controller.robots.items() if robot.is_connected] if controller else None
                payload = resolve_storage(
                    reference=args.reference,
                    color=args.color,
                    arm_id=args.robot_id,
                    require_empty=not args.allow_nonempty,
                    active_arms=controller.active_arm_ids if controller else None,
                    online_arms=online,
                )
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
            finally:
                if controller:
                    controller.disconnect_all()
        if args.storage_command == "place":
            controller = FleetController(ROBOT_FLEET_CONFIG)
            try:
                controller.connect_all()
                payload = controller.place_to_storage_slot(args.robot_id, reference=args.reference, color=args.color)
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
            finally:
                controller.disconnect_all()
    if args.command == "workspace":
        if args.workspace_command == "context":
            payload = build_tool_context()
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.workspace_command == "layout":
            payload = build_workspace_layout()
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.workspace_command == "zones":
            payload = list_workspace_zones()
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.workspace_command == "storage-groups":
            payload = list_storage_groups()
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.workspace_command == "plan-intent":
            controller = FleetController(ROBOT_FLEET_CONFIG)
            try:
                scan = controller.camera.scan_workspace(camera_id=args.camera_id, color="any", selection="first", require_saved_workspace=True)
                payload = scan if not scan.get("ok") else plan_workspace_intent(
                    scan.get("objects", []), args.destination, source_zone=args.source_zone, color=args.color, robot_id=args.robot_id, active_arms=controller.active_arm_ids,
                )
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
            finally:
                controller.disconnect_all()
        if args.workspace_command == "plan-handoff":
            payload = build_handoff_plan(
                intent_arm=args.intent_arm,
                support_arm=args.support_arm,
                object_x=args.object_x,
                object_y=args.object_y,
                destination=args.destination,
                object_id=args.object_id,
                color=args.color,
                camera_id=args.camera_id,
            )
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.workspace_command == "plan-pick-place":
            payload = build_pick_place_plan(
                arm_id=args.arm_id,
                object_x=args.object_x,
                object_y=args.object_y,
                destination=args.destination,
                object_id=args.object_id,
                color=args.color,
                camera_id=args.camera_id,
            )
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.workspace_command == "state":
            controller = FleetController(ROBOT_FLEET_CONFIG)
            try:
                payload = build_workspace_state(lambda arm_id: controller.get_arm_pose(arm_id))
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
            finally:
                controller.disconnect_all()

    if args.command == "task":
        store = TaskStore()
        try:
            if args.task_command == "run":
                task_doc = load_task_document(file_path=args.file, json_text=args.task_json)
                payload = run_task_inline(task_doc)
                print_output(payload, args.as_json)
                return 0 if payload.get("status") == "completed" else 1
            if args.task_command == "preflight":
                task_doc = load_task_document(file_path=args.file, json_text=args.task_json)
                payload = preflight_task(task_doc)
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
            if args.task_command == "start":
                task_doc = load_task_document(file_path=args.file, json_text=args.task_json)
                payload = start_task_background(task_doc, source_file=args.file)
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
            if args.task_command == "status":
                payload = store.load(args.task_id) or {"ok": False, "task_id": args.task_id, "error": "Task not found"}
                print_output(payload, args.as_json)
                return 0 if payload.get("ok", True) else 1
            if args.task_command == "list":
                tasks = store.list()
                payload = {"ok": True, "total_tasks": len(tasks), "tasks": tasks}
                print_output(payload, args.as_json)
                return 0
            if args.task_command == "cancel":
                payload = store.request_cancel(args.task_id)
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
            if args.task_command == "worker":
                payload = run_background_worker(args.task_id, args.file)
                print_output(payload, args.as_json)
                return 0 if payload.get("status") == "completed" else 1
        except (ValueError, json.JSONDecodeError) as exc:
            payload = {"ok": False, "error": str(exc)}
            print_output(payload, args.as_json)
            return 1

    if args.command == "plan":
        store = SemanticPlanStore()
        try:
            if args.plan_command == "plan-task":
                payload = plan_task(args.request_json)
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
            if args.plan_command == "validate":
                plan_doc = load_semantic_plan_document(file_path=args.file, json_text=args.plan_json)
                payload = validate_semantic_plan(plan_doc)
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
            if args.plan_command == "execute":
                plan_doc = load_semantic_plan_document(file_path=args.file, json_text=args.plan_json)
                if args.mode == "background":
                    payload = start_semantic_plan_background(plan_doc, source_file=args.file)
                else:
                    payload = run_semantic_plan_inline(plan_doc)
                print_output(payload, args.as_json)
                if args.mode == "background":
                    return 0 if payload.get("ok") else 1
                return 0 if payload.get("status") == "completed" else 1
            if args.plan_command == "status":
                payload = store.load(args.plan_id) or {"ok": False, "semantic_plan": True, "plan_id": args.plan_id, "error": "Plan not found"}
                print_output(payload, args.as_json)
                return 0 if payload.get("ok", True) else 1
            if args.plan_command == "list":
                plans = store.list()
                payload = {"ok": True, "semantic_plan": True, "total_plans": len(plans), "plans": plans}
                print_output(payload, args.as_json)
                return 0
            if args.plan_command == "cancel":
                payload = store.request_cancel(args.plan_id)
                print_output(payload, args.as_json)
                return 0 if payload.get("ok") else 1
            if args.plan_command == "worker":
                payload = run_semantic_plan_worker(args.plan_id, args.file)
                print_output(payload, args.as_json)
                return 0 if payload.get("status") == "completed" else 1
        except (ValueError, json.JSONDecodeError) as exc:
            payload = {"ok": False, "semantic_plan": True, "error": str(exc)}
            print_output(payload, args.as_json)
            return 1

    controller = FleetController(ROBOT_FLEET_CONFIG)
    try:
        if args.arm_command == "list":
            controller.connect_all()
            payload = controller.list_arms()
            print_output(payload, args.as_json)
            return 0

        if args.arm_command == "status":
            payload = controller.get_arm_status(args.arm_id)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "state":
            payload = controller.get_arm_state(args.arm_id)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "pose":
            payload = controller.get_arm_pose(args.arm_id)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "home":
            payload = controller.home_arm(args.arm_id)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "stop":
            payload = controller.stop_arm(args.arm_id)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "recover":
            payload = controller.recover_arm(args.arm_id)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "recover-home":
            payload = controller.recover_and_home_arm(args.arm_id)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "move":
            payload = controller.move_arm(args.arm_id, args.action, args.step)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "move-relative":
            payload = controller.move_relative_arm(args.arm_id, args.dx, args.dy, args.dz, args.dr)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "move-to-pose":
            payload = controller.move_to_pose_arm(args.arm_id, args.x, args.y, args.z, args.r)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "preflight-pose":
            payload = controller.preflight_pose(
                args.arm_id,
                {"x": args.x, "y": args.y, "z": args.z, "r": args.r},
                check_collision=not args.skip_collision,
            )
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "move-joint-pose":
            payload = controller.move_joint_to_pose_arm(args.arm_id, args.x, args.y, args.z, args.r)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "move-relative-joint":
            payload = controller.move_relative_joint_arm(args.arm_id, args.dx, args.dy, args.dz, args.dr)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "move-to-named-target":
            payload = controller.move_to_named_target(args.arm_id, args.destination)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "move-work-pose":
            payload = controller.move_to_work_pose(args.arm_id, args.x, args.y, args.phase, args.r)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "move-named-pose":
            payload = controller.move_to_named_pose(args.arm_id, args.destination, args.phase)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "tool-state":
            payload = controller.set_tool_state(args.arm_id, args.enable == "on")
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "pick-detected-place":
            payload = controller.pick_detected_and_place(
                args.arm_id,
                color=args.color,
                destination=args.destination,
                camera_id=args.camera_id,
                block=args.block,
            )
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "pick-all-detected-place":
            payload = controller.pick_all_detected_and_place(
                args.arm_id,
                destination=args.destination,
                color=args.color,
                camera_id=args.camera_id,
                max_picks=args.max_picks,
            )
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "pick-at":
            payload = controller.pick_at(args.arm_id, args.x, args.y, args.r)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "place-at-named":
            payload = controller.place_to_named_target(args.arm_id, args.destination)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "place-at-pose":
            payload = controller.place_at(args.arm_id, args.x, args.y, args.r)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "native-motion":
            payload = controller.send_native_motion_arm(args.arm_id, args.command)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "motion-profile":
            payload = controller.set_motion_profile(
                args.arm_id,
                speed_l=args.speed_l,
                acc_l=args.acc_l,
                speed_j=args.speed_j,
                acc_j=args.acc_j,
            )
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "continue":
            payload = controller.continue_motion_queue(args.arm_id)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "arc":
            through_pose = {"x": args.through_x, "y": args.through_y, "z": args.through_z, "r": args.through_r}
            target_pose = {"x": args.target_x, "y": args.target_y, "z": args.target_z, "r": args.target_r}
            payload = controller.arc_arm(args.arm_id, through_pose, target_pose)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "circle":
            payload = controller.run_path(
                args.arm_id,
                "circle",
                radius=args.radius,
                count=args.count,
                clockwise=args.clockwise,
                z=args.z,
                r=args.r,
                prefer_native=True,
            )
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "square":
            payload = controller.trace_square(args.arm_id, args.step)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "run-path":
            kwargs = {}
            if args.type == "rectangle":
                kwargs = {"width": args.width, "height": args.height}
            elif args.type == "circle":
                kwargs = {
                    "radius": args.radius,
                    "count": args.count,
                    "segments": args.segments,
                    "clockwise": args.clockwise,
                    "z": args.z,
                    "r": args.r,
                    "prefer_native": True,
                }
            elif args.type == "arc":
                through_pose = ast.literal_eval(args.through_pose) if args.through_pose else None
                target_pose = ast.literal_eval(args.target_pose) if args.target_pose else None
                kwargs = {"through_pose": through_pose, "target_pose": target_pose}
            elif args.type == "waypoints":
                waypoints = ast.literal_eval(args.waypoints) if args.waypoints else []
                kwargs = {"waypoints": waypoints}
            payload = controller.run_path(args.arm_id, args.type, **kwargs)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "incident":
            payload = controller.get_latest_incident(args.arm_id)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        if args.arm_command == "ack-incident":
            action = args.action.replace("-", "_")
            payload = controller.acknowledge_incident(args.arm_id, action)
            print_output(payload, args.as_json)
            return 0 if payload.get("ok") else 1

        parser.print_help()
        return 1
    finally:
        controller.disconnect_all()


if __name__ == "__main__":
    raise SystemExit(main())

