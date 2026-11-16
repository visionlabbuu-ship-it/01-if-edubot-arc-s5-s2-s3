import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .state_paths import runtime_state_path

try:
    import cv2
except Exception:  # pragma: no cover - dependency may be absent on target host
    cv2 = None

try:
    import numpy as np
except Exception:  # pragma: no cover - dependency may be absent on target host
    np = None


DEFAULT_CALIBRATION_PATH = runtime_state_path("workspace_calibration.json")
DEFAULT_DETECTION_REPORT_PATH = runtime_state_path("calibration_grid_outer_test", "live_report.json")
CALIBRATION_VERSION = 2
VALID_ROBOT_EDGES = {"top", "right", "bottom", "left"}
ROBOT_CENTER_DISTANCE_FIELD = "robot_center_to_outer_center_mm"
LEGACY_BASE_DISTANCE_FIELD = "base_to_outer_center_mm"
STALE_WORKSPACE_CALIBRATION_ERROR = (
    "Workspace calibration is stale. Open /workspace, review, and Save calibration before picking."
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def order_quad_points(points):
    if np is None:
        pts = [[float(p[0]), float(p[1])] for p in points]
        pts.sort(key=lambda p: (p[1], p[0]))
        top = sorted(pts[:2], key=lambda p: p[0])
        bottom = sorted(pts[2:], key=lambda p: p[0])
        return [top[0], top[1], bottom[0], bottom[1]]

    pts = np.array(points, dtype=np.float32).reshape((4, 2))
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).reshape((4,))
    top_left = pts[np.argmin(sums)]
    bottom_right = pts[np.argmax(sums)]
    top_right = pts[np.argmin(diffs)]
    bottom_left = pts[np.argmax(diffs)]
    return np.float32([top_left, top_right, bottom_left, bottom_right])


def ordered_corners_from_box(box):
    corners = box.get("transform_corners_px")
    if isinstance(corners, list) and len(corners) == 4:
        return [[float(point[0]), float(point[1])] for point in corners]

    corners = box.get("corners_px")
    if isinstance(corners, list) and len(corners) == 4:
        ordered = order_quad_points(corners)
        if np is not None and hasattr(ordered, "tolist"):
            return ordered.tolist()
        return ordered

    bbox = box.get("bbox") or {}
    x = float(bbox.get("x", 0.0))
    y = float(bbox.get("y", 0.0))
    w = float(bbox.get("w", 0.0))
    h = float(bbox.get("h", 0.0))
    return [[x, y], [x + w, y], [x, y + h], [x + w, y + h]]


def polygon_corners_from_box(box):
    transform_order = ordered_corners_from_box(box)
    top_left, top_right, bottom_left, bottom_right = transform_order
    return [top_left, top_right, bottom_right, bottom_left]


def outer_corners_from_child_boxes(boxes):
    points = []
    for box in boxes:
        corners = box.get("transform_corners_px") or box.get("corners_px")
        if isinstance(corners, list) and len(corners) == 4:
            points.extend(corners)
    if len(points) < 4:
        return None

    if np is None:
        pts = [[float(p[0]), float(p[1])] for p in points]
        top_left = min(pts, key=lambda p: p[0] + p[1])
        top_right = max(pts, key=lambda p: p[0] - p[1])
        bottom_left = min(pts, key=lambda p: p[0] - p[1])
        bottom_right = max(pts, key=lambda p: p[0] + p[1])
        return [top_left, top_right, bottom_left, bottom_right]

    pts = np.array(points, dtype=np.float32).reshape((-1, 2))
    sums = pts.sum(axis=1)
    diffs = pts[:, 0] - pts[:, 1]
    top_left = pts[np.argmin(sums)]
    top_right = pts[np.argmax(diffs)]
    bottom_left = pts[np.argmin(diffs)]
    bottom_right = pts[np.argmax(sums)]
    return [top_left.tolist(), top_right.tolist(), bottom_left.tolist(), bottom_right.tolist()]


def target_points(width_mm, height_mm):
    points = [
        [0.0, 0.0],
        [float(width_mm), 0.0],
        [0.0, float(height_mm)],
        [float(width_mm), float(height_mm)],
    ]
    if np is None:
        return points
    return np.float32(points)


def perspective_matrix_from_outer(outer_box, width_mm, height_mm):
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV/numpy dependencies are required for calibration geometry")
    source = np.float32(ordered_corners_from_box(outer_box))
    target = target_points(width_mm, height_mm)
    return cv2.getPerspectiveTransform(source, target)


def transform_point(matrix, x, y):
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV/numpy dependencies are required for calibration geometry")
    point_px = np.array([[[float(x), float(y)]]], dtype=np.float32)
    point_world = cv2.perspectiveTransform(point_px, matrix)
    return {
        "x": float(point_world[0][0][0]),
        "y": float(point_world[0][0][1]),
    }


def transform_polygon(matrix, corners):
    return [transform_point(matrix, point[0], point[1]) for point in corners]


def bbox_from_polygon_mm(points):
    xs = [float(point["x"]) for point in points]
    ys = [float(point["y"]) for point in points]
    return {
        "x_range_mm": [min(xs), max(xs)],
        "y_range_mm": [min(ys), max(ys)],
    }


def point_in_polygon(point, polygon):
    x = float(point["x"])
    y = float(point["y"])
    inside = False
    count = len(polygon)
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi = float(polygon[i]["x"])
        yi = float(polygon[i]["y"])
        xj = float(polygon[j]["x"])
        yj = float(polygon[j]["y"])
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def robot_center_to_outer_center_mm(robot):
    value = robot.get(ROBOT_CENTER_DISTANCE_FIELD)
    if value in (None, ""):
        value = robot.get(LEGACY_BASE_DISTANCE_FIELD)
    return float(value or 0.0)


def normalize_robot_reference_fields(robot):
    robot[ROBOT_CENTER_DISTANCE_FIELD] = robot_center_to_outer_center_mm(robot)
    robot.pop(LEGACY_BASE_DISTANCE_FIELD, None)
    robot.pop("base_workspace_xy_mm", None)
    return robot


def migrate_calibration_schema(calibration):
    """Normalize legacy robot calibration into per-arm profiles."""
    result = deepcopy(calibration or {})
    arms = result.get("arms") if isinstance(result.get("arms"), dict) else {}
    legacy = result.get("robot")
    if isinstance(legacy, dict):
        arm_id = str(legacy.get("robot_id") or "Arm1")
        arms.setdefault(arm_id, deepcopy(legacy))
    for arm_id, profile in list(arms.items()):
        if not isinstance(profile, dict):
            profile = {}
            arms[arm_id] = profile
        profile["robot_id"] = str(arm_id)
        normalize_robot_reference_fields(profile)
    result["arms"] = arms
    result["robot"] = deepcopy(arms.get("Arm1", result.get("robot") or {}))
    result["version"] = max(int(result.get("version") or 1), CALIBRATION_VERSION)
    return result


def arm_profile(calibration, arm_id="Arm1"):
    normalized = migrate_calibration_schema(calibration)
    return normalized.setdefault("arms", {}).setdefault(str(arm_id), {"robot_id": str(arm_id)})


def calibration_arms(calibration):
    return migrate_calibration_schema(calibration).get("arms") or {}


def apply_workspace_transform(transform, point):
    return {
        "x": float(transform.get("a", 1.0)) * float(point["x"])
        + float(transform.get("b", 0.0)) * float(point["y"])
        + float(transform.get("tx", 0.0)),
        "y": float(transform.get("c", 0.0)) * float(point["x"])
        + float(transform.get("d", 1.0)) * float(point["y"])
        + float(transform.get("ty", 0.0)),
        "calibration": "workspace_affine",
    }

def robot_center_workspace_xy(robot, outer_width_mm, outer_height_mm):
    edge = str(robot.get("edge", "bottom")).lower()
    distance = robot_center_to_outer_center_mm(robot)
    center_x = float(outer_width_mm) / 2.0
    center_y = float(outer_height_mm) / 2.0

    if edge == "left":
        return {"x": center_x - distance, "y": center_y}
    if edge == "right":
        return {"x": center_x + distance, "y": center_y}
    if edge == "top":
        return {"x": center_x, "y": center_y - distance}
    if edge == "bottom":
        return {"x": center_x, "y": center_y + distance}
    raise ValueError(f"Unsupported robot edge: {edge}")


def robot_base_workspace_xy(robot, outer_width_mm, outer_height_mm):
    return robot_center_workspace_xy(robot, outer_width_mm, outer_height_mm)


def workspace_to_robot_local(point, robot, outer_width_mm, outer_height_mm):
    edge = str(robot.get("edge", "bottom")).lower()
    robot_center = robot_center_workspace_xy(robot, outer_width_mm, outer_height_mm)
    dx = float(point["x"]) - float(robot_center["x"])
    dy = float(point["y"]) - float(robot_center["y"])

    if edge == "left":
        local_x, local_y = dx, dy
    elif edge == "right":
        local_x, local_y = -dx, -dy
    elif edge == "top":
        local_x, local_y = dy, -dx
    elif edge == "bottom":
        local_x, local_y = -dy, dx
    else:
        raise ValueError(f"Unsupported robot edge: {edge}")

    return {
        "x": float(local_x),
        "y": float(local_y),
        "calibration": "workspace_edge_robot",
    }


def robot_local_to_workspace(point, robot, outer_width_mm, outer_height_mm):
    edge = str(robot.get("edge", "bottom")).lower()
    robot_center = robot_center_workspace_xy(robot, outer_width_mm, outer_height_mm)
    local_x = float(point["x"])
    local_y = float(point.get("y", 0.0))

    if edge == "left":
        dx, dy = local_x, local_y
    elif edge == "right":
        dx, dy = -local_x, -local_y
    elif edge == "top":
        dx, dy = -local_y, local_x
    elif edge == "bottom":
        dx, dy = local_y, -local_x
    else:
        raise ValueError(f"Unsupported robot edge: {edge}")

    return {"x": float(robot_center["x"] + dx), "y": float(robot_center["y"] + dy)}


def reach_for_local_xy(point, robot):
    x = float(point["x"])
    y = float(point["y"])
    r = math.sqrt(x * x + y * y)
    min_reach = float(robot.get("min_reach_mm", 0.0))
    max_reach = float(robot.get("max_reach_mm", 0.0))
    return {
        "r_mm": r,
        "min_reach_mm": min_reach,
        "max_reach_mm": max_reach,
        "reachable": r >= min_reach and (max_reach <= 0.0 or r <= max_reach),
    }


def validate_calibration(calibration):
    calibration = migrate_calibration_schema(calibration)
    errors = []
    warnings = []
    outer = calibration.get("outer_box") or {}
    if not outer:
        errors.append("outer_box is required")
    if float(outer.get("width_mm") or 0.0) <= 0.0:
        errors.append("outer_box.width_mm must be positive")
    if float(outer.get("height_mm") or 0.0) <= 0.0:
        errors.append("outer_box.height_mm must be positive")
    if len(ordered_corners_from_box(outer)) != 4:
        errors.append("outer_box must have four corners")
    arms = calibration.get("arms") or {}
    if not arms:
        errors.append("arms is required")
    for arm_id, robot in arms.items():
        if not isinstance(robot, dict):
            errors.append(f"arms.{arm_id} must be an object")
            continue
        # A seeded profile may remain an unconfigured draft while the other arm is calibrated.
        # Numeric fields are normalized to 0.0 before validation, so checking only for
        # non-None values would incorrectly reject an untouched draft as a real profile.
        if not bool(robot.get("configured", True)):
            edge_value = str(robot.get("edge") or "").strip().lower()
            try:
                center_value = float(robot.get("robot_center_to_outer_center_mm") or 0.0)
            except (TypeError, ValueError):
                center_value = 0.0
            try:
                min_reach_value = float(robot.get("min_reach_mm") or 0.0)
            except (TypeError, ValueError):
                min_reach_value = 0.0
            try:
                max_reach_value = float(robot.get("max_reach_mm") or 0.0)
            except (TypeError, ValueError):
                max_reach_value = 0.0
            if center_value <= 0.0 and min_reach_value <= 0.0 and max_reach_value <= 0.0:
                continue
        edge = str(robot.get("edge", "")).lower()
        if edge not in VALID_ROBOT_EDGES:
            errors.append(f"arms.{arm_id}.edge must be one of top/right/bottom/left")
        if robot_center_to_outer_center_mm(robot) <= 0.0:
            errors.append(f"arms.{arm_id}.robot_center_to_outer_center_mm must be positive")
        min_reach = float(robot.get("min_reach_mm") or 0.0)
        max_reach = float(robot.get("max_reach_mm") or 0.0)
        if min_reach < 0.0:
            errors.append(f"arms.{arm_id}.min_reach_mm must not be negative")
        if max_reach <= 0.0:
            warnings.append(f"arms.{arm_id}.max_reach_mm is not set; reach preview will be limited")
        if min_reach > 0.0 and max_reach > 0.0 and min_reach >= max_reach:
            errors.append(f"arms.{arm_id}.min_reach_mm must be less than max_reach_mm")
    names = set()
    for zone in calibration.get("zones") or []:
        name = str(zone.get("name") or zone.get("label") or "").strip()
        if not name:
            errors.append("zone name is required")
        if name in names:
            errors.append(f"duplicate zone name: {name}")
        names.add(name)
        if float(zone.get("width_mm") or 0.0) <= 0.0:
            warnings.append(f"zone {name or '?'} width_mm is not positive")
        if float(zone.get("height_mm") or 0.0) <= 0.0:
            warnings.append(f"zone {name or '?'} height_mm is not positive")
    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}

def enrich_calibration(calibration):
    enriched = migrate_calibration_schema(calibration)
    validation = validate_calibration(enriched)
    if not validation["ok"]:
        enriched["validation"] = validation
        return enriched
    outer = enriched["outer_box"]
    outer_width = float(outer["width_mm"])
    outer_height = float(outer["height_mm"])
    matrix = perspective_matrix_from_outer(outer, outer_width, outer_height)
    center_px = outer.get("center_px")
    outer["center_mm"] = transform_point(matrix, center_px["x"], center_px["y"]) if center_px else {"x": outer_width / 2.0, "y": outer_height / 2.0}
    outer["corners_px"] = polygon_corners_from_box(outer)
    outer["corners_mm"] = [{"x": 0.0, "y": 0.0}, {"x": outer_width, "y": 0.0}, {"x": outer_width, "y": outer_height}, {"x": 0.0, "y": outer_height}]
    for arm_id, robot in (enriched.get("arms") or {}).items():
        robot["robot_id"] = str(robot.get("robot_id") or arm_id)
        robot["edge"] = str(robot.get("edge", "bottom")).lower()
        normalize_robot_reference_fields(robot)
        robot["robot_center_workspace_xy_mm"] = robot_center_workspace_xy(robot, outer_width, outer_height)
        robot["outer_center_workspace_xy_mm"] = {"x": outer_width / 2.0, "y": outer_height / 2.0}
    for zone in enriched.get("zones") or []:
        zone["name"] = str(zone.get("name") or zone.get("label")).strip()
        zone["label"] = str(zone.get("label") or zone["name"]).strip()
        zone["corners_px"] = polygon_corners_from_box(zone)
        polygon = transform_polygon(matrix, zone["corners_px"])
        zone["polygon_mm"] = polygon
        zone["bounds_mm"] = bbox_from_polygon_mm(polygon)
        center_px = zone.get("center_px")
        if center_px:
            center_mm = transform_point(matrix, center_px["x"], center_px["y"])
        else:
            bounds = zone["bounds_mm"]
            center_mm = {"x": sum(bounds["x_range_mm"]) / 2.0, "y": sum(bounds["y_range_mm"]) / 2.0}
        zone["center_mm"] = center_mm
        zone["per_arm"] = {}
        for arm_id, robot in (enriched.get("arms") or {}).items():
            local = workspace_to_robot_local(center_mm, robot, outer_width, outer_height)
            transform = robot.get("workspace_to_robot_transform") or {}
            if transform.get("enabled"):
                local = apply_workspace_transform(transform, center_mm)
            zone["per_arm"][arm_id] = {"robot_local_center_mm": local, "reach_preview": reach_for_local_xy(local, robot), "transform_ready": bool(transform.get("enabled"))}
        if zone["per_arm"].get("Arm1"):
            zone["robot_local_center_mm"] = zone["per_arm"]["Arm1"]["robot_local_center_mm"]
            zone["reach_preview"] = zone["per_arm"]["Arm1"]["reach_preview"]
    enriched["robot"] = enriched.get("arms", {}).get("Arm1", enriched.get("robot", {}))
    enriched["validation"] = validation
    enriched["updated_at"] = enriched.get("updated_at") or utc_now()
    return enriched

def load_workspace_calibration(path=DEFAULT_CALIBRATION_PATH):
    target = Path(path)
    if not target.exists():
        return None
    return enrich_calibration(load_json(target))


def save_workspace_calibration(calibration, path=DEFAULT_CALIBRATION_PATH):
    enriched = enrich_calibration(calibration)
    enriched["version"] = CALIBRATION_VERSION
    enriched["updated_at"] = utc_now()
    if not (enriched.get("validation") or {}).get("ok"):
        return enriched
    write_json(path, enriched)
    return enriched


def source_report_timestamp(calibration):
    if not isinstance(calibration, dict):
        return None
    source_report = calibration.get("source_report") or {}
    timestamp = source_report.get("timestamp")
    return str(timestamp) if timestamp else None


def report_timestamp(report):
    if not isinstance(report, dict):
        return None
    timestamp = report.get("timestamp")
    return str(timestamp) if timestamp else None


def saved_matches_workspace_report(saved, report):
    saved_timestamp = source_report_timestamp(saved)
    current_timestamp = report_timestamp(report)
    return bool(saved_timestamp and current_timestamp and saved_timestamp == current_timestamp)


def load_detection_report(path=DEFAULT_DETECTION_REPORT_PATH):
    target = Path(path)
    if not target.exists():
        return None
    return load_json(target)


def _valid_saved_workspace_calibration(path):
    try:
        calibration = load_workspace_calibration(path)
    except Exception as exc:
        return None, str(exc)
    if not calibration:
        return None, None
    if not (calibration.get("validation") or {}).get("ok"):
        return None, "Saved workspace calibration is invalid"
    return calibration, None


def _default_runtime_workspace_calibration_from_report(report, safety_config=None, saved=None):
    safety = safety_config or {}
    calibration = build_default_calibration_from_report(
        report,
        outer_width_mm=600.0,
        outer_height_mm=400.0,
        robot_id="Arm1",
        robot_edge="bottom",
        robot_center_to_outer_center_mm=350.0,
        min_reach_mm=float(safety.get("min_reach_mm", 150.0)),
        max_reach_mm=float(safety.get("max_arm_length_mm", 440.0)),
    )
    if saved:
        calibration = merge_saved_workspace_settings(calibration, saved)
    return enrich_calibration(calibration)


def zone_key(zone, index):
    row = zone.get("row")
    column = zone.get("column")
    if row is not None and column is not None:
        return ("grid", int(row), int(column))
    name = str(zone.get("name") or zone.get("label") or "").strip()
    if name:
        return ("name", name)
    return ("index", int(index))


def merge_saved_workspace_settings(calibration, saved):
    if not saved:
        return calibration

    merged = migrate_calibration_schema(calibration)
    saved = migrate_calibration_schema(saved)
    for arm_id, saved_arm in (saved.get("arms") or {}).items():
        target_arm = merged.setdefault("arms", {}).setdefault(arm_id, {})
        for key in ("robot_id", "edge", "robot_center_to_outer_center_mm", "min_reach_mm", "max_reach_mm", "workspace_to_robot_transform", "fit", "status"):
            if key in saved_arm:
                target_arm[key] = deepcopy(saved_arm[key])
    saved_outer = saved.get("outer_box") or {}
    merged_outer = merged.setdefault("outer_box", {})
    for key in ("width_mm", "height_mm"):
        if saved_outer.get(key) not in (None, ""):
            merged_outer[key] = saved_outer[key]

    saved_robot = saved.get("robot") or {}
    merged_robot = merged.setdefault("robot", {})
    for key in (
        "robot_id",
        "edge",
        "robot_center_to_outer_center_mm",
        "min_reach_mm",
        "max_reach_mm",
        "workspace_to_robot_transform",
    ):
        if key in saved_robot:
            merged_robot[key] = deepcopy(saved_robot[key])

    saved_zones = saved.get("zones") or []
    saved_by_key = {zone_key(zone, index): zone for index, zone in enumerate(saved_zones)}
    saved_by_name = {
        str(zone.get("name") or zone.get("label") or "").strip(): zone
        for zone in saved_zones
        if str(zone.get("name") or zone.get("label") or "").strip()
    }
    for index, zone in enumerate(merged.get("zones") or []):
        saved_zone = saved_by_key.get(zone_key(zone, index))
        if saved_zone is None:
            saved_zone = saved_by_name.get(str(zone.get("name") or zone.get("label") or "").strip())
        if saved_zone is None:
            continue
        for key in ("name", "label", "role", "width_mm", "height_mm"):
            if saved_zone.get(key) not in (None, ""):
                zone[key] = deepcopy(saved_zone[key])

    return merged


def workspace_resolution_status(resolution):
    if not isinstance(resolution, dict):
        return {
            "using_saved": False,
            "saved_exists": False,
            "stale_saved": False,
            "detection_ready": False,
            "detection_error": None,
            "report_timestamp": None,
            "saved_report_timestamp": None,
        }
    return {
        "using_saved": bool(resolution.get("saved")),
        "saved_exists": bool(resolution.get("saved_exists")),
        "stale_saved": bool(resolution.get("stale_saved")),
        "detection_ready": bool(resolution.get("detection_ready")),
        "detection_error": resolution.get("detection_error"),
        "report_timestamp": resolution.get("report_timestamp"),
        "saved_report_timestamp": resolution.get("saved_report_timestamp"),
        "source": resolution.get("source"),
        "calibration_path": resolution.get("calibration_path"),
    }


def workspace_metadata_from_resolution(resolution):
    return {
        "source": resolution.get("source", "legacy"),
        "calibration_path": resolution.get("calibration_path"),
        "saved": bool(resolution.get("saved")),
        "stale_saved": bool(resolution.get("stale_saved")),
        "report_timestamp": resolution.get("report_timestamp"),
        "saved_report_timestamp": resolution.get("saved_report_timestamp"),
        "saved_exists": bool(resolution.get("saved_exists")),
        "detection_ready": bool(resolution.get("detection_ready")),
        "detection_error": resolution.get("detection_error"),
        "source_report": resolution.get("source_report"),
    }


def resolve_workspace_calibration(
    calibration_path=DEFAULT_CALIBRATION_PATH,
    report_path=DEFAULT_DETECTION_REPORT_PATH,
    *,
    require_saved=False,
    safety_config=None,
    calibration_from_report=None,
):
    saved, saved_error = _valid_saved_workspace_calibration(calibration_path)
    try:
        report = load_detection_report(report_path)
    except Exception as exc:
        report = None
        detection_error = str(exc)
    else:
        detection_error = None
    if report is None and detection_error is None:
        detection_error = f"Detection report not found: {report_path}"

    saved_current = bool(saved and saved_matches_workspace_report(saved, report))
    saved_exists = saved is not None
    report_ts = report_timestamp(report)
    saved_report_ts = source_report_timestamp(saved)
    report_builder = calibration_from_report or _default_runtime_workspace_calibration_from_report

    def annotate(calibration, source, path, *, saved_flag, stale_flag=None):
        if stale_flag is None:
            stale_flag = bool(saved_exists and not saved_current and report is not None)
        if calibration:
            calibration["_runtime_source"] = source
            calibration["_runtime_path"] = str(path) if path is not None else None
            calibration["_runtime_saved"] = bool(saved_flag)
            calibration["_runtime_stale_saved"] = bool(stale_flag)
            calibration["_runtime_report_timestamp"] = report_ts
            calibration["_runtime_saved_report_timestamp"] = saved_report_ts
            if saved and not saved_flag:
                calibration["_runtime_saved_path"] = str(calibration_path)
        return calibration

    base = {
        "ok": True,
        "calibration": None,
        "saved_calibration": saved,
        "report": report,
        "source": "none",
        "calibration_path": None,
        "saved": False,
        "saved_exists": saved_exists,
        "stale_saved": False,
        "source_report": None,
        "report_timestamp": report_ts,
        "saved_report_timestamp": saved_report_ts,
        "saved_error": saved_error,
        "detection_ready": detection_error is None,
        "detection_error": detection_error,
        "requires_saved": bool(require_saved),
        "error": None,
    }

    if saved_current:
        calibration = annotate(saved, "workspace_calibration", calibration_path, saved_flag=True, stale_flag=False)
        base.update(
            {
                "calibration": calibration,
                "source": "workspace_calibration",
                "calibration_path": str(calibration_path),
                "saved": True,
                "stale_saved": False,
                "source_report": saved.get("source_report"),
                "detection_ready": detection_error is None,
            }
        )
        return base

    if require_saved:
        if saved and not report:
            calibration = annotate(saved, "workspace_calibration", calibration_path, saved_flag=True, stale_flag=False)
            base.update(
                {
                    "calibration": calibration,
                    "source": "workspace_calibration",
                    "calibration_path": str(calibration_path),
                    "saved": True,
                    "stale_saved": False,
                    "source_report": saved.get("source_report"),
                    "detection_ready": False,
                    "error": None,
                }
            )
            return base
        if saved and report:
            try:
                report_calibration = report_builder(report, safety_config=safety_config, saved=saved)
            except Exception as exc:
                detection_error = str(exc)
                report_calibration = None
            if not report_calibration or not (report_calibration.get("validation") or {}).get("ok"):
                calibration = annotate(saved, "workspace_calibration", calibration_path, saved_flag=True, stale_flag=False)
                base.update(
                    {
                        "calibration": calibration,
                        "source": "workspace_calibration",
                        "calibration_path": str(calibration_path),
                        "saved": True,
                        "stale_saved": False,
                        "source_report": saved.get("source_report"),
                        "detection_ready": False,
                        "detection_error": detection_error,
                        "error": None,
                    }
                )
                return base
        error = STALE_WORKSPACE_CALIBRATION_ERROR
        if not saved:
            error = "Workspace calibration is not saved. Open /workspace, review, and Save calibration before picking."
        base.update(
            {
                "ok": False,
                "stale_saved": bool(saved),
                "detection_ready": detection_error is None,
                "error": error,
            }
        )
        return base

    if report:
        try:
            calibration = report_builder(report, safety_config=safety_config, saved=saved)
        except Exception as exc:
            detection_error = str(exc)
            calibration = None
        if calibration and (calibration.get("validation") or {}).get("ok"):
            source = "detection_report_latest" if saved else "detection_report_fallback"
            calibration = annotate(calibration, source, report_path, saved_flag=False, stale_flag=bool(saved))
            base.update(
                {
                    "calibration": calibration,
                    "source": source,
                    "calibration_path": str(report_path),
                    "saved": False,
                    "stale_saved": bool(saved),
                    "source_report": calibration.get("source_report"),
                    "detection_ready": True,
                    "detection_error": None,
                }
            )
            return base
        base["detection_error"] = detection_error
        base["detection_ready"] = False

    if saved:
        calibration = annotate(saved, "workspace_calibration", calibration_path, saved_flag=True, stale_flag=False)
        base.update(
            {
                "calibration": calibration,
                "source": "workspace_calibration",
                "calibration_path": str(calibration_path),
                "saved": True,
                "stale_saved": False,
                "source_report": saved.get("source_report"),
                "detection_ready": False if detection_error else bool(report is None),
                "detection_error": detection_error,
            }
        )
        return base

    return base


def build_default_calibration_from_report(
    report,
    outer_width_mm=600.0,
    outer_height_mm=400.0,
    robot_id="Arm1",
    robot_edge="bottom",
    robot_center_to_outer_center_mm=350.0,
    min_reach_mm=130.0,
    max_reach_mm=440.0,
    base_to_outer_center_mm=None,
):
    outer = deepcopy(report.get("outer_box") or {})
    boxes = [deepcopy(box) for box in report.get("boxes") or report.get("cells") or []]
    if not outer:
        raise ValueError("Detection report does not include outer_box")

    if str(outer.get("source")) == "outer_from_children":
        child_corners = outer_corners_from_child_boxes(boxes)
        if child_corners is not None:
            outer["corners_px"] = child_corners
            outer["transform_corners_px"] = child_corners
            outer["source"] = "outer_quad_from_children"
            outer["quad_source"] = "derived_from_child_corners"

    outer["width_mm"] = float(outer_width_mm)
    outer["height_mm"] = float(outer_height_mm)
    zones = []
    for box in boxes:
        label = str(box.get("label") or f"zone-{len(zones) + 1}")
        zone = deepcopy(box)
        zone["name"] = label
        zone["role"] = zone.get("role") or "custom"
        zone["width_mm"] = float(zone.get("width_mm") or 0.0)
        zone["height_mm"] = float(zone.get("height_mm") or 0.0)
        zones.append(zone)

    return {
        "version": CALIBRATION_VERSION,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "source_report": {
            "timestamp": report.get("timestamp"),
            "mode": report.get("mode"),
            "path": str(DEFAULT_DETECTION_REPORT_PATH),
        },
        "outer_box": outer,
        "zones": zones,
        "robot": {
            "robot_id": str(robot_id),
            "edge": str(robot_edge).lower(),
            "robot_center_to_outer_center_mm": float(
                robot_center_to_outer_center_mm
                if base_to_outer_center_mm is None
                else base_to_outer_center_mm
            ),
            "min_reach_mm": float(min_reach_mm),
            "max_reach_mm": float(max_reach_mm),
        },
    }
