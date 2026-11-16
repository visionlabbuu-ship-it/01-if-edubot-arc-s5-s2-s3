import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .workspace_calibration import load_workspace_calibration, save_workspace_calibration
from .state_paths import runtime_state_path

try:
    import numpy as np
except Exception:  # pragma: no cover - dependency may be absent on target host
    np = None


LEGACY_OBJECT_CALIBRATION_DIR = runtime_state_path("object_calibration")
DEFAULT_OBJECT_CALIBRATION_DIR = runtime_state_path("local_object_calibration")
CURRENT_SESSION_PATH = DEFAULT_OBJECT_CALIBRATION_DIR / "current_session.json"
LATEST_SCAN_PATH = DEFAULT_OBJECT_CALIBRATION_DIR / "latest_scan.json"
LATEST_SCAN_IMAGE_PATH = DEFAULT_OBJECT_CALIBRATION_DIR / "latest_scan.jpg"
LATEST_SCAN_OBJECTS_IMAGE_PATH = DEFAULT_OBJECT_CALIBRATION_DIR / "latest_scan_objects.jpg"
REQUIRED_POINT_COUNT = 9
MIN_AFFINE_POINT_COUNT = 3
MIN_CORRECTION_POINT_COUNT = 3
DEFAULT_ARM_ID = "Arm1"
MIN_ROUND_CIRCULARITY = 0.55
MIN_ROUND_AREA_PX = 2500.0
MIN_ROUND_DIAMETER_PX = 28.0


def arm_calibration_dir(arm_id, root=DEFAULT_OBJECT_CALIBRATION_DIR):
    return Path(root) / str(arm_id or DEFAULT_ARM_ID)


def session_path_for_arm(arm_id, root=DEFAULT_OBJECT_CALIBRATION_DIR):
    return arm_calibration_dir(arm_id, root) / "current_session.json"


def scan_path_for_arm(arm_id, root=DEFAULT_OBJECT_CALIBRATION_DIR):
    return arm_calibration_dir(arm_id, root) / "latest_scan.json"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def required_point_count_for_detection(round_object_count):
    return max(int(round_object_count or 0), MIN_AFFINE_POINT_COUNT)


def session_required_point_count(session):
    try:
        required = int(session.get("required_point_count") or MIN_AFFINE_POINT_COUNT)
    except (TypeError, ValueError):
        required = MIN_AFFINE_POINT_COUNT
    return max(required, MIN_AFFINE_POINT_COUNT)


def _optional_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def new_session(arm_id=DEFAULT_ARM_ID):
    now = utc_now()
    compact = now.replace(":", "").replace("-", "").split(".")[0]
    return {
        "ok": True,
        "session_id": f"object_calibration_{compact}",
        "purpose": "workspace_to_robot_pose_calibration",
        "arm_id": str(arm_id or DEFAULT_ARM_ID),
        "required_point_count": MIN_AFFINE_POINT_COUNT,
        "detected_required_point_count": None,
        "created_at": now,
        "updated_at": now,
        "points": [],
        "last_scan": None,
        "report": None,
    }


def load_session(path=CURRENT_SESSION_PATH, arm_id=DEFAULT_ARM_ID):
    target = Path(path)
    if not target.exists():
        return new_session(arm_id)
    session = read_json(target)
    session.setdefault("ok", True)
    session.setdefault("arm_id", str(arm_id or DEFAULT_ARM_ID))
    session.setdefault("required_point_count", MIN_AFFINE_POINT_COUNT)
    session["required_point_count"] = session_required_point_count(session)
    session.setdefault("detected_required_point_count", None)
    session.setdefault("points", [])
    session.setdefault("last_scan", None)
    session.setdefault("report", None)
    return session


def save_session(session, path=CURRENT_SESSION_PATH):
    payload = deepcopy(session)
    payload["updated_at"] = utc_now()
    write_json(path, payload)
    return payload


def reset_session(path=CURRENT_SESSION_PATH, arm_id=DEFAULT_ARM_ID):
    return save_session(new_session(arm_id), path)


def round_object_candidates(
    objects,
    min_circularity=MIN_ROUND_CIRCULARITY,
    min_area_px=MIN_ROUND_AREA_PX,
    min_diameter_px=MIN_ROUND_DIAMETER_PX,
):
    candidates = []
    for obj in objects or []:
        circularity = float(obj.get("circularity") or 0.0)
        area = float(obj.get("area") or 0.0)
        bbox = obj.get("bbox") or {}
        diameter = min(float(bbox.get("w") or 0.0), float(bbox.get("h") or 0.0))
        workspace_xy = obj.get("workspace_xy")
        if circularity < float(min_circularity):
            continue
        if area < float(min_area_px):
            continue
        if diameter < float(min_diameter_px):
            continue
        if not workspace_xy:
            continue
        candidate = deepcopy(obj)
        candidate["round_candidate"] = True
        candidates.append(candidate)
    return candidates


def store_scan(
    session,
    scan_payload,
    scan_path=LATEST_SCAN_PATH,
    min_circularity=MIN_ROUND_CIRCULARITY,
    min_area_px=MIN_ROUND_AREA_PX,
    min_diameter_px=MIN_ROUND_DIAMETER_PX,
):
    scan = deepcopy(scan_payload)
    candidates = round_object_candidates(
        scan.get("objects") or [],
        min_circularity=min_circularity,
        min_area_px=min_area_px,
        min_diameter_px=min_diameter_px,
    )
    for index, candidate in enumerate(candidates, start=1):
        candidate["slot"] = index
    scan["round_object_count"] = len(candidates)
    scan["round_objects"] = candidates
    scan["round_filter"] = {
        "min_circularity": float(min_circularity),
        "min_area_px": float(min_area_px),
        "min_diameter_px": float(min_diameter_px),
    }
    write_json(scan_path, scan)
    required_count = required_point_count_for_detection(len(candidates))
    session["last_scan"] = {
        "timestamp": scan.get("timestamp"),
        "camera_id": scan.get("camera_id"),
        "frame": scan.get("frame"),
        "object_count": scan.get("object_count", 0),
        "round_object_count": len(candidates),
        "round_filter": scan["round_filter"],
        "objects": candidates,
        "source_image": (scan.get("outputs") or {}).get("source_image"),
        "objects_overlay": (scan.get("outputs") or {}).get("objects_overlay"),
        "overlay": (scan.get("outputs") or {}).get("overlay"),
        "input_source": scan.get("input_source"),
    }
    session["required_point_count"] = required_count
    session["detected_required_point_count"] = len(candidates)
    session["points"] = []
    session["report"] = None
    return session, scan


def find_last_scan_object(session, object_id=None, object_slot=None):
    object_id = str(object_id or "")
    slot = int(object_slot or 0)
    last_scan = session.get("last_scan") or {}
    for obj in last_scan.get("objects") or []:
        if object_id and str(obj.get("id")) == object_id:
            return deepcopy(obj)
        if slot and int(obj.get("slot") or 0) == slot:
            return deepcopy(obj)
    return None


def _point_index_for_object(session, selected_object, point_index=None):
    if point_index not in (None, ""):
        return int(point_index)
    slot = selected_object.get("slot")
    if slot not in (None, ""):
        return int(slot)

    object_id = str(selected_object.get("id") or "")
    last_scan = session.get("last_scan") or {}
    for index, obj in enumerate(last_scan.get("objects") or [], start=1):
        if str(obj.get("id")) == object_id:
            return index

    return len(session.get("points") or []) + 1


def append_point(session, selected_object, arm_pose_state, point_index=None):
    points = [deepcopy(point) for point in (session.get("points") or []) if point]
    required = session_required_point_count(session)
    if not selected_object:
        raise ValueError("selected object is required")
    workspace_xy = selected_object.get("workspace_xy")
    if not workspace_xy:
        raise ValueError("selected object does not have workspace_xy")
    if not arm_pose_state or not arm_pose_state.get("ok"):
        raise ValueError("robot pose is not available")

    pose = arm_pose_state.get("pose") or {}
    for key in ("x", "y"):
        if key not in pose:
            raise ValueError(f"robot pose missing {key}")

    arm_id = str(session.get("arm_id") or DEFAULT_ARM_ID)
    point_index = _point_index_for_object(session, selected_object, point_index)
    if point_index < 1:
        raise ValueError("point index must be positive")
    existing_index = next(
        (
            index
            for index, point in enumerate(points)
            if int(point.get("point_index") or 0) == point_index
        ),
        None,
    )
    if existing_index is None and len(points) >= required:
        raise ValueError(f"session already has {required} points")
    point = {
        "point_id": f"{arm_id}-P{point_index}",
        "point_index": point_index,
        "arm_id": arm_id,
        "timestamp": utc_now(),
        "object": {
            "id": selected_object.get("id"),
            "slot": selected_object.get("slot"),
            "color": selected_object.get("color"),
            "center_px": selected_object.get("center_px"),
            "bbox": selected_object.get("bbox"),
            "area": selected_object.get("area"),
            "circularity": selected_object.get("circularity"),
        },
        "workspace_xy": {
            "x": float(workspace_xy["x"]),
            "y": float(workspace_xy["y"]),
        },
        "robot_pose": {
            "x": float(pose["x"]),
            "y": float(pose["y"]),
            "z": float(pose.get("z", 0.0)),
            "r": float(pose.get("r", 0.0)),
            "rx": float(pose.get("rx", 0.0)),
            "ry": float(pose.get("ry", 0.0)),
        },
        "joints": arm_pose_state.get("joints"),
        "robot_mode": arm_pose_state.get("robot_mode"),
        "robot_mode_name": arm_pose_state.get("robot_mode_name"),
        "enable_status": arm_pose_state.get("enable_status"),
        "running_status": arm_pose_state.get("running_status"),
        "error_status": arm_pose_state.get("error_status"),
    }
    if existing_index is None:
        points.append(point)
    else:
        points[existing_index] = point
    session["points"] = sorted(points, key=lambda item: int(item.get("point_index") or 0))
    session["report"] = None
    return session, point


def _fit_axis(workspace_points, robot_values):
    basis = np.column_stack(
        [
            workspace_points[:, 0],
            workspace_points[:, 1],
            np.ones(len(workspace_points)),
        ]
    )
    coefficients, *_ = np.linalg.lstsq(basis, robot_values, rcond=None)
    return coefficients, basis


def fit_affine_transform(points):
    if np is None:
        raise RuntimeError("numpy is required for object calibration")
    points = [point for point in (points or []) if point]
    if len(points) < MIN_AFFINE_POINT_COUNT:
        raise ValueError(
            f"At least {MIN_AFFINE_POINT_COUNT} calibration points are required "
            "for affine calibration"
        )

    workspace_points = np.float64(
        [[point["workspace_xy"]["x"], point["workspace_xy"]["y"]] for point in points]
    )
    robot_points = np.float64(
        [[point["robot_pose"]["x"], point["robot_pose"]["y"]] for point in points]
    )

    coef_x, basis = _fit_axis(workspace_points, robot_points[:, 0])
    coef_y, _ = _fit_axis(workspace_points, robot_points[:, 1])
    predicted = np.column_stack([basis @ coef_x, basis @ coef_y])
    residual_vectors = predicted - robot_points
    residuals = np.linalg.norm(residual_vectors, axis=1)

    transform = {
        "type": "affine",
        "a": float(coef_x[0]),
        "b": float(coef_x[1]),
        "tx": float(coef_x[2]),
        "c": float(coef_y[0]),
        "d": float(coef_y[1]),
        "ty": float(coef_y[2]),
    }

    return {
        "transform": transform,
        "fit_predicted_robot_points": [
            {"x": float(point[0]), "y": float(point[1])} for point in predicted.tolist()
        ],
        "fit_errors_mm": [float(value) for value in residuals.tolist()],
        "fit_mean_error_mm": float(residuals.mean()),
        "fit_max_error_mm": float(residuals.max()),
        "fit_rms_error_mm": float(math.sqrt(float(np.mean(residuals * residuals)))),
    }


def apply_affine_transform(transform, workspace_xy):
    return {
        "x": float(transform["a"]) * float(workspace_xy["x"])
        + float(transform["b"]) * float(workspace_xy["y"])
        + float(transform["tx"]),
        "y": float(transform["c"]) * float(workspace_xy["x"])
        + float(transform["d"]) * float(workspace_xy["y"])
        + float(transform["ty"]),
    }


def _xy_from_mapping(item, key_options):
    for key in key_options:
        value = item.get(key)
        if isinstance(value, dict) and "x" in value and "y" in value:
            return {"x": float(value["x"]), "y": float(value["y"])}
    if "x" in item and "y" in item:
        return {"x": float(item["x"]), "y": float(item["y"])}
    raise ValueError(f"point missing one of {', '.join(key_options)}")


def fit_robot_plane_correction(points):
    if np is None:
        raise RuntimeError("numpy is required for robot plane correction")
    points = [point for point in (points or []) if point]
    if len(points) < MIN_CORRECTION_POINT_COUNT:
        raise ValueError(f"{MIN_CORRECTION_POINT_COUNT} correction points are required")

    predicted_points = np.float64(
        [
            [
                _xy_from_mapping(point, ("predicted_robot_plane", "predicted", "before"))["x"],
                _xy_from_mapping(point, ("predicted_robot_plane", "predicted", "before"))["y"],
            ]
            for point in points
        ]
    )
    actual_points = np.float64(
        [
            [
                _xy_from_mapping(point, ("actual_robot_pose", "actual_robot_plane", "actual", "after"))["x"],
                _xy_from_mapping(point, ("actual_robot_pose", "actual_robot_plane", "actual", "after"))["y"],
            ]
            for point in points
        ]
    )

    coef_x, basis = _fit_axis(predicted_points, actual_points[:, 0])
    coef_y, _ = _fit_axis(predicted_points, actual_points[:, 1])
    corrected = np.column_stack([basis @ coef_x, basis @ coef_y])
    before_errors = np.linalg.norm(predicted_points - actual_points, axis=1)
    after_vectors = corrected - actual_points
    after_errors = np.linalg.norm(after_vectors, axis=1)

    correction = {
        "enabled": True,
        "type": "affine",
        "source": "manual_point_comparison",
        "input_frame": "robot_plane_before_correction",
        "output_frame": "robot_pose_xy",
        "a": float(coef_x[0]),
        "b": float(coef_x[1]),
        "tx": float(coef_x[2]),
        "c": float(coef_y[0]),
        "d": float(coef_y[1]),
        "ty": float(coef_y[2]),
    }

    return {
        "correction": correction,
        "point_count": len(points),
        "fit_corrected_robot_points": [
            {"x": float(point[0]), "y": float(point[1])} for point in corrected.tolist()
        ],
        "fit_before_errors_mm": [float(value) for value in before_errors.tolist()],
        "fit_after_errors_mm": [float(value) for value in after_errors.tolist()],
        "fit_mean_error_mm_before": float(before_errors.mean()),
        "fit_max_error_mm_before": float(before_errors.max()),
        "fit_rms_error_mm_before": float(math.sqrt(float(np.mean(before_errors * before_errors)))),
        "fit_mean_error_mm_after": float(after_errors.mean()),
        "fit_max_error_mm_after": float(after_errors.max()),
        "fit_rms_error_mm_after": float(math.sqrt(float(np.mean(after_errors * after_errors)))),
    }


def apply_robot_plane_correction(correction, robot_xy):
    if not correction or not correction.get("enabled"):
        return {"x": float(robot_xy["x"]), "y": float(robot_xy["y"])}
    correction_type = str(correction.get("type") or "affine").lower()
    if correction_type == "affine":
        return apply_affine_transform(correction, robot_xy)
    if correction_type == "affine_residual":
        dx = correction.get("dx") or {}
        dy = correction.get("dy") or {}
        x = float(robot_xy["x"])
        y = float(robot_xy["y"])
        return {
            "x": x
            + float(dx.get("x", 0.0)) * x
            + float(dx.get("y", 0.0)) * y
            + float(dx.get("offset", 0.0)),
            "y": y
            + float(dy.get("x", 0.0)) * x
            + float(dy.get("y", 0.0)) * y
            + float(dy.get("offset", 0.0)),
        }
    raise ValueError(f"Unsupported robot plane correction type: {correction_type}")


def compute_session_report(session):
    points = [point for point in (session.get("points") or []) if point]
    points = sorted(points, key=lambda item: int(item.get("point_index") or 0))
    required = session_required_point_count(session)
    detected = _optional_int(session.get("detected_required_point_count"))
    if detected is None:
        detected = _optional_int((session.get("last_scan") or {}).get("round_object_count"))
    if detected is not None and detected < MIN_AFFINE_POINT_COUNT:
        raise ValueError(
            f"Affine calibration requires at least {MIN_AFFINE_POINT_COUNT} "
            f"round objects; latest scan found {detected}"
        )
    if len(points) < required:
        raise ValueError(f"{required} calibration points are required")
    fit = fit_affine_transform(points)
    report = {
        "ok": True,
        "session_id": session.get("session_id"),
        "arm_id": session.get("arm_id") or DEFAULT_ARM_ID,
        "computed_at": utc_now(),
        "point_count": len(points),
        "required_point_count": required,
        "transform": fit["transform"],
        "fit_predicted_robot_points": fit["fit_predicted_robot_points"],
        "fit_errors_mm": fit["fit_errors_mm"],
        "fit_mean_error_mm": fit["fit_mean_error_mm"],
        "fit_max_error_mm": fit["fit_max_error_mm"],
        "fit_rms_error_mm": fit["fit_rms_error_mm"],
    }
    return report


def apply_report_to_workspace_calibration(report, calibration_path):
    calibration = load_workspace_calibration(calibration_path)
    if calibration is None:
        raise FileNotFoundError(f"Workspace calibration not found: {calibration_path}")
    arm_id = str(report.get("arm_id") or DEFAULT_ARM_ID)
    arms = calibration.setdefault("arms", {})
    if not arms and isinstance(calibration.get("robot"), dict):
        arms["Arm1"] = deepcopy(calibration["robot"])
    robot = arms.setdefault(arm_id, {"robot_id": arm_id})
    transform = deepcopy(report["transform"])
    transform.update(
        {
            "enabled": True,
            "source": "object_calibration",
            "arm_id": arm_id,
            "session_id": report.get("session_id"),
            "applied_at": utc_now(),
            "point_count": int(report.get("point_count") or 0),
            "fit_mean_error_mm": float(report.get("fit_mean_error_mm") or 0.0),
            "fit_max_error_mm": float(report.get("fit_max_error_mm") or 0.0),
            "fit_rms_error_mm": float(report.get("fit_rms_error_mm") or 0.0),
        }
    )
    robot["robot_id"] = arm_id
    robot["workspace_to_robot_transform"] = transform
    robot["fit"] = {
        "point_count": int(report.get("point_count") or 0),
        "mean_error_mm": float(report.get("fit_mean_error_mm") or 0.0),
        "max_error_mm": float(report.get("fit_max_error_mm") or 0.0),
        "rms_error_mm": float(report.get("fit_rms_error_mm") or 0.0),
        "session_id": report.get("session_id"),
        "updated_at": utc_now(),
    }
    saved = save_workspace_calibration(calibration, calibration_path)
    validation = saved.get("validation") or {}
    if not validation.get("ok"):
        raise ValueError("; ".join(validation.get("errors") or ["invalid workspace calibration"]))
    return saved
