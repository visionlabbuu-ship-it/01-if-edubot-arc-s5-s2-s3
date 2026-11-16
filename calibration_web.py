"""Compatibility loader for the preserved workspace calibration web application."""
import os
import subprocess
import sys
from pathlib import Path

# The preserved web bytecode is Python 3.12. Forward older launchers to the
# project virtual environment so `python -m tools.calibration.web` still works.
if sys.version_info[:2] != (3, 12):
    _project_root = Path(__file__).resolve().parent
    _venv_python = _project_root / ".venv" / "Scripts" / "python.exe"
    if _venv_python.exists():
        raise SystemExit(subprocess.call([str(_venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]))
    raise RuntimeError("ARC calibration web requires Python 3.12; run .venv\\Scripts\\python.exe")

import importlib.util
from importlib.machinery import SourcelessFileLoader
from pathlib import Path

_legacy_path = Path(__file__).resolve().parent / "artifacts" / "calibration_web_legacy.cpython-312.pyc"
_loader = SourcelessFileLoader("_arc_calibration_web_legacy", str(_legacy_path))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
_legacy = importlib.util.module_from_spec(_spec)
_loader.exec_module(_legacy)

for _name, _value in vars(_legacy).items():
    if _name not in {"__name__", "__loader__", "__spec__", "__file__", "__package__"}:
        globals()[_name] = _value
# Dual-arm calibration extensions. The legacy application remains the UI base;
# these overrides isolate per-arm state and route all object calibration actions.
from copy import deepcopy
from http import HTTPStatus
from urllib.parse import parse_qs, urlparse
from config import ROBOT_FLEET_CONFIG, ROBOT_SAFETY_CONFIG, SIM_MODE, active_robot_ids
from fleet_controller import FleetController
from object_calibration import (
    append_point, apply_report_to_workspace_calibration, compute_session_report,
    find_last_scan_object, load_session, reset_session, save_session, store_scan,
    scan_path_for_arm, session_path_for_arm,
)
from workspace_calibration import calibration_arms, resolve_workspace_calibration

STORAGE_GROUPS = ("LT", "LB", "RT", "RB")

# Keep the web state aligned with the runtime registry used by motion/storage
# commands. The legacy UI loader points at artifacts/runtime_state, which can
# lag behind the canonical runtime_state registry after a live scan.
from arc_runtime.storage_service import load as _load_runtime_storage_registry


def load_capacity_slots_payload():
    return _load_runtime_storage_registry()


def configured_robot_ids():
    """Return every arm defined by the calibration/runtime configuration.

    Calibration must expose profiles for configured arms even when the runtime
    is temporarily restricted to a subset through ARC_ACTIVE_ROBOT_IDS.
    """
    return [str(arm_id) for arm_id in ROBOT_FLEET_CONFIG.keys()]


def ensure_calibration_arm_profiles(calibration):
    """Expose configured arm profiles without sharing mutable state across arms."""
    payload = deepcopy(calibration or {})
    profiles = calibration_arms(payload)
    for arm_id in configured_robot_ids():
        key = str(arm_id)
        profiles.setdefault(key, {"robot_id": key, "configured": False})
    for arm_id, profile in profiles.items():
        if not isinstance(profile, dict):
            profile = {}
            profiles[arm_id] = profile
        profile["robot_id"] = str(arm_id)
    payload["arms"] = profiles
    payload["robot"] = deepcopy(profiles.get("Arm1", payload.get("robot") or {}))
    return payload
def normalize_storage_boxes(geometry):
    """Normalize four user-drawn storage regions to canonical logical groups."""
    payload = dict(geometry or {})
    boxes = list(payload.get("storage_boxes") or payload.get("storage_regions") or [])
    normalized = []
    errors = []
    seen = set()
    for index, box in enumerate(boxes, start=1):
        item = dict(box or {})
        group = str(item.get("group") or "").strip().upper()
        if group not in STORAGE_GROUPS:
            errors.append(f"Storage Box {index} must select one of LT, LB, RT, RB")
        elif group in seen:
            errors.append(f"Storage group {group} is assigned more than once")
        else:
            seen.add(group)
        item["group"] = group
        item["region_role"] = "storage_region"
        item["region_id"] = str(item.get("region_id") or f"storage-region-{index}")
        item["label"] = str(item.get("label") or f"Storage Box {index}")
        normalized.append(item)
    if len(normalized) != 4:
        errors.append("Exactly four Storage Boxes are required")
    missing = [group for group in STORAGE_GROUPS if group not in seen]
    if missing:
        errors.append("Missing Storage groups: " + ", ".join(missing))
    payload["storage_boxes"] = normalized
    payload["storage_regions"] = normalized
    payload["storage_box_validation"] = {"ok": not errors, "errors": errors, "groups": [item.get("group") for item in normalized]}
    return payload


_legacy_build_manual_geometry_report = build_manual_geometry_report


def build_manual_geometry_report(geometry, image_path=None, report_path=None):
    normalized = normalize_storage_boxes(geometry)
    report = _legacy_build_manual_geometry_report(normalized, image_path=image_path, report_path=report_path)
    report["storage_boxes"] = normalized.get("storage_boxes", [])
    report["storage_regions"] = normalized.get("storage_regions", [])
    report["storage_box_validation"] = normalized["storage_box_validation"]
    for box in report.get("storage_boxes") or []:
        box["region_role"] = "storage_region"
        box["group"] = str(box.get("group") or "").upper()
        box["region_id"] = str(box.get("region_id") or box.get("name") or "")
    return report


def object_paths_for_arm(arm_id):
    root = Path(__file__).resolve().parent
    session = root / session_path_for_arm(arm_id)
    scan = root / scan_path_for_arm(arm_id)
    return {"session": session, "scan": scan, "image": scan.with_suffix(".jpg"), "objects_image": scan.with_name("latest_scan_objects.jpg")}



def online_calibration_arms(calibration):
    profiles = calibration_arms(ensure_calibration_arm_profiles(calibration))
    active_ids = set(active_robot_ids(ROBOT_FLEET_CONFIG))
    result = []
    for arm_id in configured_robot_ids():
        if arm_id not in ROBOT_FLEET_CONFIG:
            continue
        profile = profiles.get(arm_id) or {}
        transform = profile.get("workspace_to_robot_transform") or {}
        geometry_ready = (str(profile.get("edge") or "").lower() in {"top", "right", "bottom", "left"}
            and float(profile.get("robot_center_to_outer_center_mm") or 0.0) > 0
            and float(profile.get("max_reach_mm") or 0.0) > 0)
        active = arm_id in active_ids
        if SIM_MODE and active:
            online = True
            status = {"connected": True, "simulated": True}
        elif not active:
            online = False
            status = {"ok": False, "connected": False, "error_code": "arm_inactive"}
        else:
            online = False
            status = None
            controller = None
            try:
                controller = FleetController(ROBOT_FLEET_CONFIG)
                status = controller.get_arm_status(arm_id)
                online = bool(status.get("ok") and status.get("connected"))
            except Exception as exc:
                status = {"ok": False, "connected": False, "error": str(exc)}
            finally:
                if controller is not None:
                    controller.disconnect_all()
        result.append({"arm_id": arm_id, "active": active, "online": online,
            "geometry_ready": geometry_ready, "transform_ready": bool(transform.get("enabled")),
            "point_count": int(transform.get("point_count") or (profile.get("fit") or {}).get("point_count") or 0),
            "fit_mean_error_mm": transform.get("fit_mean_error_mm", (profile.get("fit") or {}).get("mean_error_mm")),
            "status": status})
    return result

def require_online_arm(arm_id, calibration):
    selected = str(arm_id or "Arm1")
    available = {item["arm_id"]: item for item in online_calibration_arms(calibration)}
    if selected not in available:
        raise ValueError(f"Arm '{selected}' is not configured")
    if not available[selected].get("active"):
        raise ValueError(f"Arm '{selected}' is inactive; enable it before calibration motion")
    if not available[selected].get("online"):
        raise ValueError(f"Arm '{selected}' is offline")
    return selected, available[selected]


def ensure_storage_boxes(calibration, report=None):
    """Expose legacy storage/drop zones as editable storage regions."""
    if not isinstance(calibration, dict):
        return calibration
    existing = calibration.get("storage_boxes") or calibration.get("storage_regions")
    if existing:
        calibration["storage_boxes"] = [dict(box) for box in existing]
        calibration["storage_regions"] = calibration["storage_boxes"]
        return calibration
    source = list((report or {}).get("storage_boxes") or [])
    if not source:
        source = [
            zone for zone in (calibration.get("zones") or [])
            if str((zone or {}).get("role") or "").lower() in {"storage", "drop", "storage_region"}
        ]
    boxes = []
    for index, box in enumerate(source, start=1):
        item = deepcopy(box)
        item["region_id"] = str(item.get("region_id") or f"storage-region-{index}")
        item["region_role"] = "storage_region"
        item.setdefault("group", "")
        boxes.append(item)
    calibration["storage_boxes"] = boxes
    calibration["storage_regions"] = boxes
    return calibration

def response_payload(report_path, calibration_path, arm_id=None):
    resolution = resolve_workspace_calibration(calibration_path, report_path, safety_config=ROBOT_SAFETY_CONFIG)
    if not resolution.get("calibration") and resolution.get("detection_error"):
        raise ValueError(resolution["detection_error"])
    calibration = ensure_calibration_arm_profiles(ensure_storage_boxes(resolution.get("calibration"), resolution.get("report")))
    arms = online_calibration_arms(calibration)
    selected = str(arm_id or (arms[0]["arm_id"] if arms else "Arm1"))
    paths = object_paths_for_arm(selected)
    return {
        "ok": True, "saved": bool(resolution.get("saved")), "saved_exists": bool(resolution.get("saved_exists")),
        "calibration_status": calibration_status(resolution), "report": resolution.get("report"),
        "calibration": calibration, "arms": arms, "selected_arm_id": selected,
        "capacity_slots": load_capacity_slots_payload(),
        "object_session": load_session(paths["session"], arm_id=selected),
        "object_images": latest_capture_images(limit=30),
    }


def _handler_calibration(self):
    calibration = resolve_workspace_calibration(self.server.calibration_path, self.server.report_path, safety_config=ROBOT_SAFETY_CONFIG).get("calibration")
    return ensure_calibration_arm_profiles(calibration)


def _require_handler_arm(self, requested):
    return require_online_arm(requested, _handler_calibration(self))[0]



def _object_arm_connection_test(self):
    payload = self.read_json_body()
    arm_id = str(payload.get("arm_id") or "Arm1")
    if arm_id not in configured_robot_ids():
        self.send_json({"ok": False, "error": f"Arm '{arm_id}' is not configured"}, status=HTTPStatus.BAD_REQUEST)
        return

    controller = None
    try:
        controller = FleetController(ROBOT_FLEET_CONFIG)
        status = controller.get_arm_status(arm_id)
        pose = controller.get_arm_pose(arm_id) if status.get("ok") else None
    except Exception as exc:
        status = {"ok": False, "connected": False, "error": str(exc)}
        pose = None
    finally:
        if controller is not None:
            controller.disconnect_all()

    self.send_json({
        "ok": True,
        "arm_id": arm_id,
        "connected": bool(status.get("connected")),
        "status": status,
        "pose": pose,
    })


def _object_home_arm(self):
    payload = self.read_json_body()
    if not bool(payload.get("confirmed")):
        self.send_json(
            {"ok": False, "error": "Home command requires explicit confirmation"},
            status=HTTPStatus.BAD_REQUEST,
        )
        return

    try:
        arm_id = _require_handler_arm(self, payload.get("arm_id"))
    except ValueError as exc:
        self.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        return

    controller = None
    try:
        controller = FleetController(ROBOT_FLEET_CONFIG)
        pose = controller.get_arm_pose(arm_id)
        if not pose.get("ok"):
            self.send_json(
                {"ok": False, "error": pose.get("error") or f"{arm_id} pose is not available"},
                status=HTTPStatus.CONFLICT,
            )
            return
        if bool(pose.get("error_status")):
            self.send_json(
                {"ok": False, "error": f"{arm_id} is faulted; recover it before Home"},
                status=HTTPStatus.CONFLICT,
            )
            return
        if bool(pose.get("running_status")):
            self.send_json(
                {"ok": False, "error": f"{arm_id} is already moving"},
                status=HTTPStatus.CONFLICT,
            )
            return
        if not bool(pose.get("enable_status")):
            self.send_json(
                {"ok": False, "error": f"{arm_id} is not enabled"},
                status=HTTPStatus.CONFLICT,
            )
            return

        result = controller.home_arm(arm_id)
    except Exception as exc:
        self.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return
    finally:
        if controller is not None:
            controller.disconnect_all()

    if not result.get("ok"):
        self.send_json(
            {"ok": False, "error": result.get("error") or f"{arm_id} could not move Home", "result": result},
            status=HTTPStatus.CONFLICT,
        )
        return
    self.send_json({"ok": True, "arm_id": arm_id, "result": result})

def _object_scan(self):
    payload = self.read_json_body()
    try:
        arm_id = _require_handler_arm(self, payload.get("arm_id"))
    except ValueError as exc:
        self.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST); return
    paths = object_paths_for_arm(arm_id)
    controller = CameraController(workspace_calibration_path=self.server.calibration_path, workspace_detection_report_path=self.server.report_path)
    source = str(payload.get("source") or "live").lower()
    if source == "image":
        try:
            image_path = safe_runtime_file(payload.get("image_path") or "")
        except (FileNotFoundError, PermissionError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST); return
        scan = controller.analyze_image_file(image_path, camera_id="Cam1", color="any", selection="first", output_image_path=paths["image"])
        scan["input_source"] = {"type": "calibration_image", "path": str(image_path)}
    else:
        scan = controller.scan_workspace(camera_id="Cam1", color="any", selection="first", output_image_path=paths["image"])
        scan["input_source"] = {"type": "live_camera"}
    if not scan.get("ok"):
        self.send_json(scan, status=HTTPStatus.INTERNAL_SERVER_ERROR); return
    session = load_session(paths["session"], arm_id=arm_id)
    session, stored = store_scan(session, scan, scan_path=paths["scan"])
    session = save_session(session, paths["session"])
    self.send_json({"ok": True, "arm_id": arm_id, "session": session, "scan": stored})


def _object_save_pose(self):
    payload = self.read_json_body()
    try:
        arm_id = _require_handler_arm(self, payload.get("arm_id"))
    except ValueError as exc:
        self.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST); return
    paths = object_paths_for_arm(arm_id)
    session = load_session(paths["session"], arm_id=arm_id)
    selected = find_last_scan_object(session, payload.get("object_id"), payload.get("object_slot"))
    if not selected:
        self.send_json({"ok": False, "error": "Object was not found in the latest scan"}, status=HTTPStatus.BAD_REQUEST); return
    controller = FleetController(ROBOT_FLEET_CONFIG)
    try:
        pose = controller.get_arm_pose(arm_id)
    finally:
        controller.disconnect_all()
    if not pose.get("ok"):
        self.send_json({"ok": False, "error": pose.get("error") or f"{arm_id} pose is not available"}, status=HTTPStatus.BAD_REQUEST); return
    try:
        session, point = append_point(session, selected, pose, point_index=payload.get("object_slot"))
    except ValueError as exc:
        self.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST); return
    self.send_json({"ok": True, "arm_id": arm_id, "session": save_session(session, paths["session"]), "point": point})


def _object_compute_apply(self):
    payload = self.read_json_body()
    try:
        arm_id = _require_handler_arm(self, payload.get("arm_id"))
        paths = object_paths_for_arm(arm_id)
        session = load_session(paths["session"], arm_id=arm_id)
        report = compute_session_report(session)
        calibration = apply_report_to_workspace_calibration(report, self.server.calibration_path)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        self.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST); return
    session["report"] = report
    session = save_session(session, paths["session"])
    archive = paths["session"].parent / f"{session['session_id']}.json"
    archive.write_text(json.dumps({**session, "applied_calibration_path": str(self.server.calibration_path)}, ensure_ascii=False, indent=2), encoding="utf-8")
    self.send_json({"ok": True, "arm_id": arm_id, "session": session, "report": report, "calibration": calibration})


def _object_reset(self):
    payload = self.read_json_body()
    try:
        arm_id = _require_handler_arm(self, payload.get("arm_id"))
    except ValueError as exc:
        self.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST); return
    self.send_json({"ok": True, "arm_id": arm_id, "session": reset_session(object_paths_for_arm(arm_id)["session"], arm_id=arm_id)})



def _save_with_storage_validation(handler):
    payload = handler.read_json_body()
    normalized = normalize_storage_boxes(ensure_calibration_arm_profiles(payload))
    validation = normalized.get("storage_box_validation") or {}
    # Empty storage_boxes remains compatible with old calibration drafts. Once
    # storage regions are supplied, they must be exactly LT/LB/RT/RB.
    if normalized.get("storage_boxes") and not validation.get("ok"):
        handler.send_json({"ok": False, "error_code": "storage_box_mapping_invalid", "error": "; ".join(validation.get("errors") or []), "storage_box_validation": validation}, status=HTTPStatus.BAD_REQUEST)
        return
    try:
        calibration = save_workspace_calibration(normalized, handler.server.calibration_path)
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        return
    if not (calibration.get("validation") or {}).get("ok"):
        handler.send_json({"ok": False, "error": "; ".join((calibration.get("validation") or {}).get("errors") or ["Calibration is invalid"]), "calibration": calibration}, status=HTTPStatus.BAD_REQUEST)
        return
    handler.send_json({"ok": True, "calibration": calibration, "storage_box_validation": validation})
_legacy_do_get = CalibrationHandler.do_GET
_legacy_do_post = CalibrationHandler.do_POST

def _do_get(self):
    parsed = urlparse(self.path)
    if parsed.path == "/api/state":
        arm_id = (parse_qs(parsed.query).get("arm_id") or [None])[0]
        self.send_json(response_payload(self.server.report_path, self.server.calibration_path, arm_id)); return
    return _legacy_do_get(self)

def _do_post(self):
    path = urlparse(self.path).path
    if path == "/api/save":
        return _save_with_storage_validation(self)
    handlers = {
        "/api/object-calibration/scan": _object_scan,
        "/api/object-calibration/save-pose": _object_save_pose,
        "/api/object-calibration/compute-apply": _object_compute_apply,
        "/api/object-calibration/reset": _object_reset,
        "/api/object-calibration/arm-status": _object_arm_connection_test,
        "/api/object-calibration/home": _object_home_arm,
    }
    if path in handlers:
        return handlers[path](self)
    return _legacy_do_post(self)

CalibrationHandler.do_GET = _do_get
CalibrationHandler.do_POST = _do_post
_legacy.response_payload = response_payload

# Keep the existing page layout but send arm selection with all object operations.
INDEX_HTML = INDEX_HTML.replace('<h2>Robot</h2>', '<h2>Robot</h2><label for="workspaceArmSelect">Robot profile</label><select id="workspaceArmSelect"></select><div id="workspaceArmSelectionStatus" class="message"></div>')
INDEX_HTML = INDEX_HTML.replace('selectedObjectImage: ""', 'selectedObjectImage: "", arms: [], activeArmId: new URLSearchParams(window.location.search).get("arm_id") || "Arm1", workspaceArmId: "Arm1"')
INDEX_HTML = INDEX_HTML.replace('fetch("/api/state", { cache: "no-store" })', 'fetch(`/api/state?arm_id=${encodeURIComponent(state.activeArmId || "Arm1")}`, { cache: "no-store" })')
INDEX_HTML = INDEX_HTML.replace('body: "{}"\n        });\n        state.objectSession = payload.session;', 'body: JSON.stringify({ arm_id: state.activeArmId || "Arm1" })\n        });\n        state.objectSession = payload.session;')

_legacy.ROOT = Path(__file__).resolve().parent
ROOT = _legacy.ROOT


_dual_arm_ui = r'''
<script>
(() => {
  function renderWorkspaceArmSelector(select) {
    if (!select) return;
    select.innerHTML = (state.arms || []).map((arm) => {
      const suffix = arm.active ? (arm.online ? " (online)" : " (offline)") : " (inactive)";
      return `<option value="${arm.arm_id}">${arm.arm_id}${suffix}</option>`;
    }).join("");
    select.value = state.workspaceArmId || "Arm1";
    const selected = (state.arms || []).find((arm) => arm.arm_id === select.value);
    const status = document.getElementById("workspaceArmSelectionStatus");
    if (status && selected) {
      status.className = selected.active && selected.online ? "message good" : "message bad";
      status.textContent = `Editing ${selected.arm_id} geometry ยท ${selected.active ? (selected.online ? "online" : "offline") : "inactive"}`;
    }
  }

  function renderArmSelector(select) {
    if (!select) return;
    select.innerHTML = (state.arms || []).map((arm) => {
      const suffix = arm.active ? (arm.online ? " (online)" : " (offline)") : " (inactive)";
      return `<option value="${arm.arm_id}">${arm.arm_id}${suffix}</option>`;
    }).join("");
    select.value = state.activeArmId || "Arm1";
    const selected = (state.arms || []).find((arm) => arm.arm_id === select.value);
    const status = document.getElementById("armSelectionStatus");
    if (status && selected) {
      status.className = selected.active && selected.online ? "message good" : "message bad";
      status.textContent = `Editing ${selected.arm_id} ยท ${selected.active ? (selected.online ? "online" : "offline") : "inactive"}`;
    }
  }

  const priorRequestJson = requestJson;
  requestJson = async (url, options = {}) => {
    let nextUrl = url;
    let nextOptions = options;
    if (url === "/api/state") nextUrl = `/api/state?arm_id=${encodeURIComponent(state.activeArmId || "Arm1")}`;
    if (url.startsWith("/api/object-calibration/")) {
      const body = options.body ? JSON.parse(options.body) : {};
      body.arm_id = state.activeArmId || "Arm1";
      nextOptions = { ...options, body: JSON.stringify(body) };
    }
    const payload = await priorRequestJson(nextUrl, nextOptions);
    if (url === "/api/state") {
      state.arms = payload.arms || [];
      state.activeArmId = payload.selected_arm_id || state.activeArmId || "Arm1";
      const select = document.getElementById("armSelect");
      if (select) renderArmSelector(select);
      const workspaceSelect = document.getElementById("workspaceArmSelect");
      if (workspaceSelect) renderWorkspaceArmSelector(workspaceSelect);
    }
    return payload;
  };
  const priorSyncStateFromForm = syncStateFromForm;
  syncStateFromForm = function() {
    const cal = state.calibration;
    cal.arms = cal.arms || {};
    const armId = state.workspaceArmId || "Arm1";
    const profile = cal.arms[armId] = cal.arms[armId] || { robot_id: armId };

    // The legacy form reads/writes calibration.robot. Point it at the
    // selected profile for the duration of the sync so Arm2 edits cannot
    // leak into the Arm1 compatibility profile.
    cal.robot = profile;
    priorSyncStateFromForm();
    profile.robot_id = armId;
    profile.edge = el("robotEdge").value;
    profile.robot_center_to_outer_center_mm = numberValue("baseDistance");
    profile.min_reach_mm = numberValue("minReach");
    profile.max_reach_mm = numberValue("maxReach");
    cal.robot = cal.arms.Arm1 || profile;
  };

  const select = document.getElementById("armSelect");
  if (select) select.addEventListener("change", async () => {
    syncStateFromForm();
    const draftArms = state.calibration.arms || {};
    state.activeArmId = select.value;
    const payload = await requestJson("/api/state");
    state.calibration = payload.calibration;
    state.calibration.arms = { ...(state.calibration.arms || {}), ...draftArms };
    state.calibration.arms[state.activeArmId] = state.calibration.arms[state.activeArmId] || { robot_id: state.activeArmId };
    state.calibration.robot = state.calibration.arms.Arm1 || state.calibration.arms[state.activeArmId];
    state.objectSession = payload.object_session;
    state.selectedObjectId = null;
    state.selectedObjectSlot = null;
    state.imageVersion = Date.now();
    render();
  });
  const workspaceSelect = document.getElementById("workspaceArmSelect");
  if (workspaceSelect) workspaceSelect.addEventListener("change", async () => {
    syncStateFromForm();
    const draftArms = state.calibration.arms || {};
    state.workspaceArmId = workspaceSelect.value;
    const payload = await requestJson("/api/state");
    state.calibration = payload.calibration;
    state.calibration.arms = { ...(state.calibration.arms || {}), ...draftArms };
    state.calibration.arms[state.workspaceArmId] = state.calibration.arms[state.workspaceArmId] || { robot_id: state.workspaceArmId };
    state.calibration.robot = state.calibration.arms.Arm1 || state.calibration.arms[state.workspaceArmId];
    render();
  });

  const priorRender = render;
  render = function() {
    const active = state.activeArmId || "Arm1";
    const workspaceArm = state.workspaceArmId || "Arm1";
    const calibration = state.calibration || {};
    const profiles = calibration.arms || {};
    const legacyRobot = calibration.robot;
    if (profiles[workspaceArm]) calibration.robot = profiles[workspaceArm];
    priorRender();
    if (profiles.Arm1) calibration.robot = profiles.Arm1;
    else if (legacyRobot) calibration.robot = legacyRobot;
    const robotId = document.getElementById("robotId");
    if (robotId) robotId.value = workspaceArm;
    const objectArm = document.getElementById("objectActiveArmMetric");
    if (objectArm) objectArm.textContent = active;
    renderArmSelector(document.getElementById("armSelect"));
    renderWorkspaceArmSelector(document.getElementById("workspaceArmSelect"));
  };
})();
</script>
'''
INDEX_HTML = INDEX_HTML.replace('    function bindTopForm() {\n      ["outerWidth", "outerHeight", "robotId", "robotEdge", "baseDistance", "minReach", "maxReach"].forEach((id) => {\n        el(id).addEventListener("input", async () => {\n          await enrichDraft(false);\n          render();\n        });\n      });\n    }\n', '    function bindTopForm() {\n      let validationTimer = null;\n      let validationSerial = 0;\n      const scheduleValidation = () => {\n        const serial = ++validationSerial;\n        clearTimeout(validationTimer);\n        validationTimer = setTimeout(async () => {\n          syncStateFromForm();\n          try {\n            const payload = await requestJson("/api/validate", {\n              method: "POST",\n              body: JSON.stringify(state.calibration)\n            });\n            if (serial !== validationSerial) return;\n            state.calibration = payload.calibration;\n            render();\n          } catch (error) {\n            if (serial === validationSerial) setStatus(error.message);\n          }\n        }, 180);\n      };\n      ["outerWidth", "outerHeight", "robotId", "robotEdge", "baseDistance", "minReach", "maxReach"].forEach((id) => {\n        el(id).addEventListener("input", scheduleValidation);\n      });\n    }\n')
INDEX_HTML = INDEX_HTML.replace("</body>", _dual_arm_ui + "</body>")

# Populate the selector after the original page's initial state request has settled.
INDEX_HTML = INDEX_HTML.replace("})();\n</script>\n</body>", "requestJson('/api/state').then((payload) => { const select = document.getElementById('armSelect'); if (select) { state.arms = payload.arms || []; state.activeArmId = payload.selected_arm_id || state.activeArmId; state.workspaceArmId = state.workspaceArmId || state.activeArmId; renderArmSelector(select); renderWorkspaceArmSelector(document.getElementById('workspaceArmSelect')); } }).catch(() => {});\n})();\n</script>\n</body>")

INDEX_HTML = INDEX_HTML.replace('<h2>Object Calibration</h2>', '<h2>Object Calibration</h2><label for="armSelect">Calibration arm</label><select id="armSelect"></select><div id="armSelectionStatus" class="message"></div>')
INDEX_HTML = INDEX_HTML.replace('<span>Arm</span><span>Arm1</span>', '<span>Arm</span><span id="objectActiveArmMetric">Arm1</span>')
INDEX_HTML = INDEX_HTML.replace('Save current Arm1 pose for selected object', 'Save current selected arm pose for selected object')
INDEX_HTML = INDEX_HTML.replace('<label for="robotId">Robot ID</label>', '<label for="robotId">Selected arm</label>')
INDEX_HTML = INDEX_HTML.replace('<input id="robotId" />', '<input id="robotId" readonly />')

_object_arm_controls_ui = r"""
<script>
(() => {
  const scanButton = document.getElementById("objectScanBtn");
  if (!scanButton || !scanButton.parentElement) return;
  const controls = scanButton.parentElement;
  const testButton = document.createElement("button");
  testButton.id = "objectArmTestBtn";
  testButton.type = "button";
  testButton.title = "Read the selected arm connection and current pose";
  testButton.textContent = "Test arm";
  const homeButton = document.createElement("button");
  homeButton.id = "objectHomeBtn";
  homeButton.type = "button";
  homeButton.title = "Move the selected arm to its guarded Home target";
  homeButton.textContent = "Home";
  controls.insertBefore(testButton, scanButton);
  controls.insertBefore(homeButton, scanButton);

  let requestInFlight = false;
  function selectedArm() { return state.activeArmId || "Arm1"; }
  function selectedArmInfo() {
    return (state.arms || []).find((arm) => arm.arm_id === selectedArm()) || null;
  }
  function setControlState() {
    const arm = selectedArmInfo();
    testButton.disabled = requestInFlight || !arm;
    homeButton.disabled = requestInFlight || !arm || !arm.online;
  }
  function formatPose(pose) {
    if (!pose || !pose.ok) return "";
    const point = pose.pose || pose;
    const values = [point.x, point.y, point.z].map((value) => Number(value));
    if (!values.every(Number.isFinite)) return "";
    return " | pose " + values.map((value) => value.toFixed(1)).join(", ") + " mm";
  }
  async function testArmConnection() {
    requestInFlight = true;
    setControlState();
    const armId = selectedArm();
    try {
      const payload = await requestJson("/api/object-calibration/arm-status", {
        method: "POST", body: "{}"
      });
      const status = payload.status || {};
      const connected = Boolean(payload.connected && status.ok);
      setStatus(armId + (connected ? " connected" : " offline") + formatPose(payload.pose));
    } catch (error) {
      setStatus(error.message);
    } finally {
      requestInFlight = false;
      setControlState();
    }
  }
  async function moveSelectedArmHome() {
    const armId = selectedArm();
    if (!window.confirm("Move " + armId + " to Home? Confirm the workspace and path are clear first.")) return;
    requestInFlight = true;
    setControlState();
    try {
      await requestJson("/api/object-calibration/home", {
        method: "POST", body: JSON.stringify({ confirmed: true })
      });
      setStatus(armId + " moved to Home");
    } catch (error) {
      setStatus(error.message);
    } finally {
      requestInFlight = false;
      setControlState();
    }
  }
  testButton.addEventListener("click", testArmConnection);
  homeButton.addEventListener("click", moveSelectedArmHome);
  const priorRender = render;
  render = function() {
    priorRender();
    setControlState();
  };
  setControlState();
})();
</script>
"""
INDEX_HTML = INDEX_HTML.replace('</body>', _object_arm_controls_ui + '</body>')
_storage_mapping_ui = r'''
<script>
(() => {
  const groups = ["LT", "LB", "RT", "RB"];
  const priorRender = render;
  function storageBoxes() {
    return (state.calibration && (state.calibration.storage_boxes || state.calibration.storage_regions)) || [];
  }
  function drawStorageMapping() {
    const root = document.getElementById("storageBoxMapping");
    const status = document.getElementById("storageBoxMappingStatus");
    if (!root || !status || !state.calibration) return;
    const boxes = storageBoxes();
    root.innerHTML = "";
    boxes.forEach((box, index) => {
      const label = document.createElement("label");
      label.textContent = "Storage Box " + (index + 1) + " ";
      const select = document.createElement("select");
      select.dataset.storageBoxIndex = String(index);
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "Select group";
      select.appendChild(blank);
      groups.forEach((group) => {
        const option = document.createElement("option");
        option.value = group;
        option.textContent = group;
        option.selected = String(box.group || "").toUpperCase() === group;
        select.appendChild(option);
      });
      select.addEventListener("change", () => {
        boxes[index].group = select.value;
        boxes[index].region_role = "storage_region";
        state.calibration.storage_boxes = boxes;
        state.calibration.storage_regions = boxes;
        drawStorageMapping();
      });
      label.appendChild(select);
      root.appendChild(label);
    });
    const selected = boxes.map((box) => String(box.group || "").toUpperCase()).filter(Boolean);
    const missing = groups.filter((group) => !new Set(selected).has(group));
    const valid = boxes.length === 4 && selected.length === 4 && new Set(selected).size === 4 && missing.length === 0;
    status.className = valid ? "message good" : "message bad";
    status.textContent = boxes.length === 0 ? "Draw four Storage Boxes, then assign LT, LB, RT, and RB." : valid ? "Storage Box mapping ready: LT, LB, RT, RB" : "Mapping incomplete. Missing: " + (missing.join(", ") || "duplicate group");
  }
  render = function() { priorRender(); drawStorageMapping(); };
})();
</script>
'''
INDEX_HTML = INDEX_HTML.replace('<h2>Validation</h2>', '<section class="section"><h2>Storage Boxes</h2><div id="storageBoxMapping"></div><div id="storageBoxMappingStatus" class="message"></div></section><h2>Validation</h2>')
INDEX_HTML = INDEX_HTML.replace('</body>', _storage_mapping_ui + '</body>')
_legacy.CalibrationHandler = CalibrationHandler
_legacy.INDEX_HTML = INDEX_HTML

if __name__ == '__main__':
    _legacy.main()














