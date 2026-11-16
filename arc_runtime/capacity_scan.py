"""Capacity / empty-slot marker scan (black disks).

Separate from work-object scan (red/green/blue). Reuses the same camera
calibration and workspace perspective path, but detects dark round markers
that mark free capacity slots.

Default assumption: black disk visible => slot empty.
"""

from __future__ import annotations

import json
import math
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .camera_controller import CameraController
from .config import CAMERA_CONFIG
from .state_paths import runtime_state_path
from .workspace_calibration import (
    DEFAULT_CALIBRATION_PATH,
    DEFAULT_DETECTION_REPORT_PATH,
    load_json,
)

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


DEFAULT_OUTPUT_DIR = runtime_state_path("capacity_scan")
DEFAULT_IMAGE_CANDIDATES = (
    runtime_state_path("local_calibration_captures"),
    runtime_state_path("calibration_captures"),
    runtime_state_path("calibration_grid_outer_test"),
)

# Tuned on live_tune_20260725_083336.jpg:
# - left column: solid black disks (~95px, mean V ~45)
# - right column: same disks but glare/uneven light (center dark, ring bright)
DEFAULT_BLACK_SCAN_CONFIG = {
    "purpose": "capacity_empty_slot_markers",
    "color_name": "black",
    "object_color_hsv_ranges": {
        # Absolute black for well-lit/solid markers (left side).
        "black": [
            {"lower": [0, 0, 0], "upper": [179, 140, 70]},
        ],
    },
    "min_object_area": 2500.0,
    "max_object_area_px": 20000.0,
    "min_object_diameter_px": 40.0,
    "max_object_diameter_px": 140.0,
    "min_object_circularity": 0.55,
    "min_object_aspect": 0.65,
    "max_object_aspect": 1.55,
    "object_duplicate_distance_px": 70.0,
    "color_morph_open_kernel": 5,
    "color_morph_close_kernel": 9,
    "edge_reject_margin_px": 4,
    # Markers sit just outside the outer-box polygon on both edges.
    "require_inside_workspace": False,
    "workspace_margin_px": 160.0,
    "max_mean_value": 70.0,
    "max_mean_saturation": 90.0,
    # Gray Hough + ring contrast recovers glare-side markers that absolute
    # black masks miss (right column under stronger lighting).
    "hough_enabled": True,
    "hough_on_gray": True,
    "hough_dp": 1.2,
    "hough_min_dist_px": 80.0,
    "hough_param1": 60.0,
    "hough_param2": 22.0,
    "hough_min_radius_px": 30,
    "hough_max_radius_px": 55,
    "hough_min_mask_coverage": 0.0,
    "hough_max_mean_value": 110.0,
    "hough_min_ring_contrast": 25.0,
    "hough_min_ring_mean": 120.0,
    "hough_roi_dilate_px": 200,
    "expected_count": 16,
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def resolve_current_calibration_image(
    explicit_path=None,
    manual_draft_path=None,
    report_path=None,
):
    """Pick the image that matches the current calibration draft/report."""
    if explicit_path:
        path = Path(explicit_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        if path.exists():
            return path
        return None

    draft_path = Path(manual_draft_path or runtime_state_path("manual_calibration_geometry.json"))
    if draft_path.exists():
        try:
            draft = load_json(draft_path)
            image_path = draft.get("image_path")
            if image_path:
                path = Path(image_path)
                if path.exists():
                    return path
        except Exception:
            pass

    report = Path(report_path or DEFAULT_DETECTION_REPORT_PATH)
    if report.exists():
        try:
            payload = load_json(report)
            outputs = payload.get("outputs") or {}
            for key in ("source_image", "image", "overlay"):
                value = outputs.get(key)
                if value and Path(value).exists():
                    return Path(value)
            live = report.parent / "live_source_image.png"
            if live.exists():
                return live
        except Exception:
            pass

    newest = None
    newest_mtime = -1.0
    for folder in DEFAULT_IMAGE_CANDIDATES:
        if not folder.exists():
            continue
        for path in folder.glob("*"):
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp"}:
                continue
            if "overlay" in path.stem.lower() or "mask" in path.stem.lower():
                continue
            mtime = path.stat().st_mtime
            if mtime > newest_mtime:
                newest = path
                newest_mtime = mtime
    return newest


def black_scan_camera_config(base_config=None, overrides=None):
    """Build a Cam1-like config that only detects black round markers."""
    config = deepcopy(base_config or CAMERA_CONFIG.get("Cam1") or {})
    scan = deepcopy(DEFAULT_BLACK_SCAN_CONFIG)
    if overrides:
        scan.update(overrides)

    config["mode"] = "bgr"
    config["color_object_detection_enabled"] = True
    config["legacy_saturation_detection_enabled"] = False
    config["hough_round_fallback_enabled"] = False
    config["color_hough_round_enabled"] = False
    config["detectable_object_colors"] = [scan["color_name"]]
    config["object_color_hsv_ranges"] = scan["object_color_hsv_ranges"]
    config["min_object_area"] = float(scan["min_object_area"])
    config["max_object_area_px"] = float(scan["max_object_area_px"])
    config["min_object_diameter_px"] = float(scan["min_object_diameter_px"])
    config["max_object_diameter_px"] = float(scan["max_object_diameter_px"])
    config["min_object_circularity"] = float(scan["min_object_circularity"])
    config["min_object_aspect"] = float(scan["min_object_aspect"])
    config["max_object_aspect"] = float(scan["max_object_aspect"])
    config["object_duplicate_distance_px"] = float(scan["object_duplicate_distance_px"])
    config["color_morph_open_kernel"] = int(scan["color_morph_open_kernel"])
    config["color_morph_close_kernel"] = int(scan["color_morph_close_kernel"])
    config["edge_reject_margin_px"] = int(scan["edge_reject_margin_px"])
    config["_capacity_scan"] = scan
    return config


def _point_near_workspace(workspace_contour, x, y, margin_px):
    if workspace_contour is None:
        return True
    if cv2 is None or np is None:
        return True
    contour = np.array(workspace_contour, dtype=np.float32).reshape((-1, 1, 2))
    distance = float(cv2.pointPolygonTest(contour, (float(x), float(y)), True))
    # positive = inside, 0 = edge, negative = outside distance
    return distance >= -float(margin_px)


def _mean_hsv_in_contour(hsv_img, contour):
    mask = np.zeros(hsv_img.shape[:2], dtype=np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)
    mean = cv2.mean(hsv_img, mask=mask)
    return {
        "h": float(mean[0]),
        "s": float(mean[1]),
        "v": float(mean[2]),
        "pixel_count": int(cv2.countNonZero(mask)),
    }


def _round_black_candidate(contour, config, workspace_contour, shape, hsv_img, scan_cfg):
    area = float(cv2.contourArea(contour))
    min_area = float(config.get("min_object_area", 2500.0))
    max_area = float(config.get("max_object_area_px", 20000.0))
    if area < min_area or (max_area > 0.0 and area > max_area):
        return None

    x, y, width, height = cv2.boundingRect(contour)
    image_height, image_width = shape
    edge_margin = int(config.get("edge_reject_margin_px", 0))
    if x <= edge_margin or y <= edge_margin:
        return None
    if x + width >= image_width - edge_margin or y + height >= image_height - edge_margin:
        return None

    min_diameter = float(config.get("min_object_diameter_px", 40.0))
    max_diameter = float(config.get("max_object_diameter_px", 140.0))
    if min(width, height) < min_diameter:
        return None
    if max_diameter > 0.0 and max(width, height) > max_diameter:
        return None

    aspect = float(width) / float(height or 1)
    if aspect < float(config.get("min_object_aspect", 0.65)):
        return None
    if aspect > float(config.get("max_object_aspect", 1.55)):
        return None

    perimeter = float(cv2.arcLength(contour, True))
    circularity = 0.0
    if perimeter > 0.0:
        circularity = float(4.0 * math.pi * area / (perimeter * perimeter))
    if circularity < float(config.get("min_object_circularity", 0.55)):
        return None

    moments = cv2.moments(contour)
    if moments.get("m00"):
        center_x = float(moments["m10"] / moments["m00"])
        center_y = float(moments["m01"] / moments["m00"])
    else:
        center_x = float(x + width / 2.0)
        center_y = float(y + height / 2.0)

    require_inside = bool(scan_cfg.get("require_inside_workspace", False))
    margin = float(scan_cfg.get("workspace_margin_px", 120.0))
    if require_inside:
        if workspace_contour is not None and cv2.pointPolygonTest(
            np.array(workspace_contour, dtype=np.float32).reshape((-1, 1, 2)),
            (center_x, center_y),
            False,
        ) < 0:
            return None
    elif not _point_near_workspace(workspace_contour, center_x, center_y, margin):
        return None

    mean_hsv = _mean_hsv_in_contour(hsv_img, contour)
    if mean_hsv["v"] > float(scan_cfg.get("max_mean_value", 70.0)):
        return None
    if mean_hsv["s"] > float(scan_cfg.get("max_mean_saturation", 90.0)):
        return None

    (_, _), radius = cv2.minEnclosingCircle(contour)
    return {
        "center_x": center_x,
        "center_y": center_y,
        "bbox": {"x": int(x), "y": int(y), "w": int(width), "h": int(height)},
        "area": area,
        "perimeter": perimeter,
        "circularity": circularity,
        "radius_px": float(radius),
        "mean_hsv": mean_hsv,
        "detection_source": "black_contour",
    }


def _has_nearby(objects, center_x, center_y, max_distance_px):
    for obj in objects:
        center = obj.get("center_px") or {}
        dx = float(center.get("x", 0.0)) - float(center_x)
        dy = float(center.get("y", 0.0)) - float(center_y)
        if math.sqrt(dx * dx + dy * dy) <= float(max_distance_px):
            return True
    return False


def _workspace_roi_mask(shape, workspace_contour, dilate_px):
    """Binary ROI around outer box so Hough ignores far background clutter."""
    roi = np.zeros(shape[:2], dtype=np.uint8)
    if workspace_contour is None:
        roi[:] = 255
        return roi
    contour = np.array(workspace_contour, dtype=np.int32).reshape((-1, 1, 2))
    cv2.drawContours(roi, [contour], -1, 255, -1)
    radius = max(0, int(round(float(dilate_px or 0))))
    if radius > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        roi = cv2.dilate(roi, kernel)
    return roi


def _ring_contrast_stats(gray, center_x, center_y, radius):
    """Inner disk vs outer ring brightness โ€” robust under uneven lighting."""
    inner = np.zeros(gray.shape, dtype=np.uint8)
    ring = np.zeros(gray.shape, dtype=np.uint8)
    inner_r = max(1, int(round(radius * 0.65)))
    ring_outer = max(inner_r + 2, int(round(radius * 1.55)))
    ring_inner = max(inner_r + 1, int(round(radius * 1.05)))
    cv2.circle(inner, (int(center_x), int(center_y)), inner_r, 255, -1)
    cv2.circle(ring, (int(center_x), int(center_y)), ring_outer, 255, -1)
    cv2.circle(ring, (int(center_x), int(center_y)), ring_inner, 0, -1)
    mean_inner = float(cv2.mean(gray, mask=inner)[0])
    mean_ring = float(cv2.mean(gray, mask=ring)[0])
    return {
        "mean_inner": mean_inner,
        "mean_ring": mean_ring,
        "contrast": mean_ring - mean_inner,
        "inner_mask": inner,
    }


def _append_hough_black(objects, object_index, gray, mask, hsv_img, workspace_contour, config, scan_cfg, build_object):
    if not bool(scan_cfg.get("hough_enabled", True)):
        return object_index
    min_radius = int(scan_cfg.get("hough_min_radius_px", 28))
    max_radius = int(scan_cfg.get("hough_max_radius_px", 70))
    if max_radius <= min_radius:
        return object_index

    use_gray = bool(scan_cfg.get("hough_on_gray", True))
    if use_gray:
        source = gray
        roi = _workspace_roi_mask(gray.shape, workspace_contour, scan_cfg.get("hough_roi_dilate_px", 200))
        # Keep far background from inventing circles; markers stay near board edge.
        source = source.copy()
        source[roi == 0] = 255
    else:
        source = mask if mask is not None else gray
        roi = None

    blurred = cv2.medianBlur(source, 5)
    circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=float(scan_cfg.get("hough_dp", 1.2)),
        minDist=float(scan_cfg.get("hough_min_dist_px", 70.0)),
        param1=float(scan_cfg.get("hough_param1", 80.0)),
        param2=float(scan_cfg.get("hough_param2", 28.0)),
        minRadius=min_radius,
        maxRadius=max_radius,
    )
    if circles is None:
        return object_index

    duplicate = float(config.get("object_duplicate_distance_px", 70.0))
    margin = float(scan_cfg.get("workspace_margin_px", 160.0))
    require_inside = bool(scan_cfg.get("require_inside_workspace", False))
    min_coverage = float(scan_cfg.get("hough_min_mask_coverage", 0.0))
    max_mean_v = float(scan_cfg.get("hough_max_mean_value", 110.0))
    min_ring_contrast = float(scan_cfg.get("hough_min_ring_contrast", 25.0))
    min_ring_mean = float(scan_cfg.get("hough_min_ring_mean", 120.0))

    for circle in np.round(circles[0]).astype(int):
        center_x, center_y, radius = [int(value) for value in circle]
        if radius < min_radius or radius > max_radius:
            continue
        if roi is not None and roi[center_y, center_x] == 0:
            continue
        if require_inside:
            if workspace_contour is not None and cv2.pointPolygonTest(
                np.array(workspace_contour, dtype=np.float32).reshape((-1, 1, 2)),
                (float(center_x), float(center_y)),
                False,
            ) < 0:
                continue
        elif not _point_near_workspace(workspace_contour, center_x, center_y, margin):
            continue
        if _has_nearby(objects, center_x, center_y, duplicate):
            continue

        ring = _ring_contrast_stats(gray, center_x, center_y, radius)
        if ring["mean_inner"] > max_mean_v:
            continue
        if use_gray:
            # Disk must be darker than local board; ring should look like bright board
            # (rejects grid-line blobs and dark background clutter).
            if ring["contrast"] < min_ring_contrast:
                continue
            if ring["mean_ring"] < min_ring_mean:
                continue

        circle_mask = ring["inner_mask"]
        mean_v = float(cv2.mean(hsv_img[:, :, 2], mask=circle_mask)[0])
        mean_s = float(cv2.mean(hsv_img[:, :, 1], mask=circle_mask)[0])
        if mean_v > max_mean_v:
            continue

        coverage = 1.0
        if mask is not None and min_coverage > 0.0:
            coverage = float(cv2.mean(mask, mask=circle_mask)[0]) / 255.0
            if coverage < min_coverage:
                continue

        area = math.pi * float(radius) * float(radius)
        candidate = {
            "center_x": float(center_x),
            "center_y": float(center_y),
            "bbox": {
                "x": int(center_x - radius),
                "y": int(center_y - radius),
                "w": int(radius * 2),
                "h": int(radius * 2),
            },
            "area": float(area),
            "perimeter": float(2.0 * math.pi * float(radius)),
            "circularity": 1.0,
            "radius_px": float(radius),
            "mean_hsv": {"h": 0.0, "s": mean_s, "v": mean_v, "pixel_count": int(cv2.countNonZero(circle_mask))},
            "mask_coverage": coverage,
            "ring_contrast": float(ring["contrast"]),
            "ring_mean": float(ring["mean_ring"]),
            "detection_source": "black_hough_gray" if use_gray else "black_hough",
        }
        objects.append(build_object(object_index, candidate))
        object_index += 1
    return object_index


def detect_black_capacity_markers(
    display_img,
    workspace_contour=None,
    config=None,
    matrix=None,
    arm_matrices=None,
    workspace_report_matrix=None,
    width_mm=600.0,
    height_mm=400.0,
    workspace_calibration=None,
    camera_controller=None,
):
    """Detect black round capacity markers in a BGR image."""
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV/numpy are required for capacity scan")

    config = config or black_scan_camera_config()
    scan_cfg = config.get("_capacity_scan") or DEFAULT_BLACK_SCAN_CONFIG
    color_name = str(scan_cfg.get("color_name") or "black")
    controller = camera_controller or CameraController()

    hsv_img = cv2.cvtColor(display_img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(display_img, cv2.COLOR_BGR2GRAY)
    mask = controller._object_color_mask(hsv_img, color_name, config)
    open_k = int(config.get("color_morph_open_kernel", 5))
    close_k = int(config.get("color_morph_close_kernel", 9))
    if open_k > 1:
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((open_k, open_k), np.uint8))
    if close_k > 1:
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((close_k, close_k), np.uint8))

    shape = hsv_img.shape[:2]
    objects = []
    object_index = 1

    def build_object(index, candidate):
        payload = controller._build_detected_object(
            index,
            color_name,
            candidate,
            config,
            matrix,
            arm_matrices or {},
            workspace_report_matrix,
            width_mm,
            height_mm,
            workspace_calibration,
            detection_source=candidate.get("detection_source") or "black_contour",
        )
        payload["id"] = f"slot-{index}"
        payload["role"] = "capacity_marker"
        payload["occupancy"] = "empty_candidate"
        payload["inside_workspace"] = bool(
            workspace_contour is not None
            and cv2.pointPolygonTest(
                np.array(workspace_contour, dtype=np.float32).reshape((-1, 1, 2)),
                (float(candidate["center_x"]), float(candidate["center_y"])),
                False,
            )
            >= 0
        )
        if candidate.get("mean_hsv"):
            payload["mean_hsv"] = candidate["mean_hsv"]
        if "mask_coverage" in candidate:
            payload["mask_coverage"] = float(candidate["mask_coverage"])
        for key in ("ring_contrast", "ring_mean"):
            if key in candidate:
                payload[key] = float(candidate[key])
        return payload

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        candidate = _round_black_candidate(contour, config, workspace_contour, shape, hsv_img, scan_cfg)
        if candidate is None:
            continue
        if _has_nearby(objects, candidate["center_x"], candidate["center_y"], config.get("object_duplicate_distance_px", 70.0)):
            continue
        objects.append(build_object(object_index, candidate))
        object_index += 1

    object_index = _append_hough_black(
        objects,
        object_index,
        gray,
        mask,
        hsv_img,
        workspace_contour,
        config,
        scan_cfg,
        build_object,
    )

    # Left-to-right columns, top-to-bottom within column (stable slot ids).
    objects.sort(key=lambda item: (item["center_px"]["x"], item["center_px"]["y"]))
    for index, obj in enumerate(objects, start=1):
        obj["id"] = f"slot-{index}"
        obj["slot_index"] = index
        side = "left" if float(obj["center_px"]["x"]) < (display_img.shape[1] * 0.5) else "right"
        obj["board_side"] = side
    return objects, mask


def write_capacity_overlay(display_img, workspace_contour, objects, overlay_path, expected_count=12):
    if cv2 is None or np is None:
        return None
    overlay = display_img.copy()
    image_height, image_width = overlay.shape[:2]
    scale = max(0.7, min(2.0, image_width / 1600.0))
    line = max(2, int(round(scale * 2.5)))
    font = cv2.FONT_HERSHEY_SIMPLEX
    draw_color = (40, 40, 40)
    accent = (0, 200, 255)

    if workspace_contour is not None:
        contour = np.array(workspace_contour, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(overlay, [contour], True, (255, 255, 255), line + 5, cv2.LINE_AA)
        cv2.polylines(overlay, [contour], True, (0, 180, 255), line + 1, cv2.LINE_AA)

    for obj in objects or []:
        center = obj.get("center_px") or {}
        bbox = obj.get("bbox") or {}
        cx = int(round(float(center.get("x") or 0.0)))
        cy = int(round(float(center.get("y") or 0.0)))
        x = int(round(float(bbox.get("x") or 0.0)))
        y = int(round(float(bbox.get("y") or 0.0)))
        w = int(round(float(bbox.get("w") or 0.0)))
        h = int(round(float(bbox.get("h") or 0.0)))
        radius = int(round(float(obj.get("radius_px") or max(w, h) / 2.0 or 20.0)))
        cv2.rectangle(overlay, (x, y), (x + w, y + h), accent, line, cv2.LINE_AA)
        cv2.circle(overlay, (cx, cy), max(4, radius), accent, line, cv2.LINE_AA)
        cv2.circle(overlay, (cx, cy), max(3, int(round(scale * 4))), (0, 0, 255), -1, cv2.LINE_AA)
        label = f"{obj.get('slot_index') or obj.get('id')} black c={float(obj.get('circularity') or 0):.2f}"
        ws = obj.get("workspace_xy") or {}
        if ws:
            label = f"{label} ({ws.get('x', 0):.0f},{ws.get('y', 0):.0f})"
        cv2.putText(
            overlay,
            label,
            (max(0, x), max(24, y - 8)),
            font,
            scale * 0.55,
            accent,
            max(1, int(round(line * 0.8))),
            cv2.LINE_AA,
        )

    count = len(objects or [])
    summary = f"capacity black markers {count} / expected {int(expected_count)}"
    cv2.rectangle(overlay, (12, 12), (12 + int(720 * scale), 12 + int(46 * scale)), (20, 20, 20), -1)
    cv2.putText(overlay, summary, (22, 12 + int(32 * scale)), font, scale * 0.7, (0, 255, 255), max(1, line), cv2.LINE_AA)

    target = Path(overlay_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(os.fspath(target), overlay):
        return None
    return os.fspath(target)


def scan_capacity_image(
    image_path=None,
    output_dir=None,
    camera_id="Cam1",
    expected_count=None,
    overrides=None,
    workspace_calibration_path=None,
    workspace_detection_report_path=None,
):
    """Run a capacity scan from an explicit image or one fresh live frame."""
    if cv2 is None or np is None:
        return {"ok": False, "error": "OpenCV/numpy dependencies are not available"}

    output_dir = Path(output_dir or DEFAULT_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    source_out = output_dir / f"capacity_source_{stamp}.jpg"
    overlay_out = output_dir / f"capacity_overlay_{stamp}.jpg"
    mask_out = output_dir / f"capacity_black_mask_{stamp}.png"
    report_out = output_dir / f"capacity_report_{stamp}.json"
    latest_report = output_dir / "latest_report.json"
    latest_overlay = output_dir / "latest_overlay.jpg"
    latest_mask = output_dir / "latest_black_mask.png"

    controller = CameraController(
        workspace_calibration_path=workspace_calibration_path or DEFAULT_CALIBRATION_PATH,
        workspace_detection_report_path=workspace_detection_report_path or DEFAULT_DETECTION_REPORT_PATH,
    )
    base = controller.get_camera(camera_id)
    if not base:
        return {"ok": False, "error": f"Camera '{camera_id}' not found"}

    if image_path is None:
        captured = controller.capture_image(camera_id=camera_id, output_image_path=source_out)
        if not captured.get("ok"):
            return {"ok": False, "error": captured.get("error") or "Live camera capture failed", "input_source": {"type": "live_camera", "camera_id": camera_id}}
        image_path = captured.get("image_path") or source_out
        input_source = {"type": "live_camera", "camera_id": camera_id, "captured_at": captured.get("captured_at")}
    else:
        image_path = resolve_current_calibration_image(image_path)
        if image_path is None or not Path(image_path).exists():
            return {"ok": False, "error": "Explicit capacity scan image not found"}
        input_source = {"type": "image_file", "camera_id": camera_id}

    scan_overrides = dict(overrides or {})
    if expected_count is not None:
        scan_overrides["expected_count"] = int(expected_count)
    config = black_scan_camera_config(base, scan_overrides)
    scan_cfg = config["_capacity_scan"]
    expected = int(scan_cfg.get("expected_count") or 12)

    image = cv2.imread(os.fspath(image_path))
    if image is None:
        return {"ok": False, "error": f"Image not found or unreadable: {image_path}"}

    from .workspace_calibration import (
        polygon_corners_from_box,
        perspective_matrix_from_outer,
        workspace_metadata_from_resolution,
    )

    workspace_resolution = controller._workspace_resolution(require_saved_workspace=False)
    file_config = deepcopy(config)
    file_config["mode"] = "bgr"

    workspace_calibration = workspace_resolution.get("calibration")
    workspace_metadata = workspace_metadata_from_resolution(workspace_resolution)
    width_mm = float(file_config.get("workspace_width_mm", 600.0))
    height_mm = float(file_config.get("workspace_height_mm", 400.0))
    workspace_report_matrix = None
    ordered_workspace_ccw = None
    workspace_source = "legacy"
    workspace_calibration_path_out = None

    if workspace_calibration:
        outer_box = workspace_calibration["outer_box"]
        width_mm = float(outer_box["width_mm"])
        height_mm = float(outer_box["height_mm"])
        polygon_quad = np.float32(polygon_corners_from_box(outer_box))
        ordered_workspace_ccw = polygon_quad.reshape((-1, 1, 2))
        workspace_report_matrix = perspective_matrix_from_outer(outer_box, width_mm, height_mm)
        workspace_source = str(workspace_calibration.get("_runtime_source") or "workspace_calibration")
        workspace_calibration_path_out = str(
            workspace_calibration.get("_runtime_path") or controller.workspace_calibration_path
        )

    matrix = None if workspace_calibration else controller._calibration_matrix(file_config.get("calibration") or {})
    arm_matrices = {} if workspace_calibration else controller._arm_calibration_matrices(file_config)

    objects, mask = detect_black_capacity_markers(
        display_img=image,
        workspace_contour=ordered_workspace_ccw,
        config=file_config,
        matrix=matrix,
        arm_matrices=arm_matrices,
        workspace_report_matrix=workspace_report_matrix,
        width_mm=width_mm,
        height_mm=height_mm,
        workspace_calibration=workspace_calibration,
        camera_controller=controller,
    )

    if Path(image_path).resolve() != source_out.resolve():
        cv2.imwrite(os.fspath(source_out), image)
    cv2.imwrite(os.fspath(mask_out), mask)
    write_capacity_overlay(image, ordered_workspace_ccw, objects, overlay_out, expected_count=expected)
    overlay_img = cv2.imread(os.fspath(overlay_out)) if Path(overlay_out).exists() else image
    if overlay_img is not None:
        cv2.imwrite(os.fspath(latest_overlay), overlay_img)
    cv2.imwrite(os.fspath(latest_mask), mask)

    count = len(objects)
    left_count = sum(1 for obj in objects if obj.get("board_side") == "left")
    right_count = sum(1 for obj in objects if obj.get("board_side") == "right")
    report = {
        "ok": True,
        "purpose": "capacity_black_marker_scan",
        "timestamp": utc_now(),
        "camera_id": camera_id,
        "image_path": str(image_path),
        "input_source": {**input_source, "image_path": str(image_path)},
        "expected_count": expected,
        "detected_count": count,
        "left_count": left_count,
        "right_count": right_count,
        "count_matches_expected": count == expected,
        "delta_from_expected": count - expected,
        "color": "black",
        "occupancy_rule": "black_marker_visible_means_empty; colored_object_detected_means_occupied; otherwise_unknown",
        "notes": {
            "right_side": "Uses gray Hough + ring contrast because absolute black mask misses glare-side disks",
            "left_side": "Absolute HSV black contour is primary",
        },
        "workspace": {
            "source": workspace_source,
            "calibration_path": workspace_calibration_path_out,
            "saved": bool(workspace_metadata.get("saved")),
            "stale_saved": bool(workspace_metadata.get("stale_saved")),
            "width_mm": width_mm,
            "height_mm": height_mm,
            "report_matrix_ready": workspace_report_matrix is not None,
        },
        "scan_config": {
            "min_object_area": file_config.get("min_object_area"),
            "min_object_circularity": file_config.get("min_object_circularity"),
            "min_object_diameter_px": file_config.get("min_object_diameter_px"),
            "max_object_diameter_px": file_config.get("max_object_diameter_px"),
            "require_inside_workspace": scan_cfg.get("require_inside_workspace"),
            "workspace_margin_px": scan_cfg.get("workspace_margin_px"),
            "max_mean_value": scan_cfg.get("max_mean_value"),
            "hsv_ranges": scan_cfg.get("object_color_hsv_ranges"),
        },
        "objects": objects,
        "outputs": {
            "source_image": str(source_out),
            "overlay": str(overlay_out),
            "black_mask": str(mask_out),
            "report": str(report_out),
            "latest_report": str(latest_report),
            "latest_overlay": str(latest_overlay),
            "latest_black_mask": str(latest_mask),
        },
    }
    write_json(report_out, report)
    write_json(latest_report, report)
    return report


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(description="Scan black capacity markers (separate from work-object scan)")
    parser.add_argument("--image", help="Image path for offline replay (omit to capture a fresh live frame)")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory")
    parser.add_argument("--expected-count", type=int, default=16, help="Expected black marker count")
    parser.add_argument("--camera-id", default="Cam1")
    parser.add_argument("--require-inside-workspace", action="store_true", help="Reject markers outside outer box")
    parser.add_argument("--workspace-margin-px", type=float, default=120.0)
    parser.add_argument("--max-mean-value", type=float, default=70.0)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    overrides = {
        "require_inside_workspace": bool(args.require_inside_workspace),
        "workspace_margin_px": float(args.workspace_margin_px),
        "max_mean_value": float(args.max_mean_value),
        "expected_count": int(args.expected_count),
    }
    report = scan_capacity_image(
        image_path=args.image,
        output_dir=args.output_dir,
        camera_id=args.camera_id,
        expected_count=args.expected_count,
        overrides=overrides,
    )
    if not report.get("ok"):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    print(f"image: {report['image_path']}")
    print(
        f"detected black capacity markers: {report['detected_count']} "
        f"(left {report.get('left_count', 0)}, right {report.get('right_count', 0)}; "
        f"expected {report['expected_count']})"
    )
    print(f"match expected: {report['count_matches_expected']} (delta {report['delta_from_expected']:+d})")
    for obj in report.get("objects") or []:
        center = obj.get("center_px") or {}
        ws = obj.get("workspace_xy") or {}
        mean = (obj.get("mean_hsv") or {})
        print(
            f"  {obj.get('id'):7s}  {str(obj.get('board_side') or '?'):5s}  "
            f"px=({center.get('x'):4}, {center.get('y'):4})  "
            f"circ={float(obj.get('circularity') or 0):.2f}  "
            f"V={float(mean.get('v') or 0):.1f}  "
            f"ws=({float(ws.get('x') or 0):.1f}, {float(ws.get('y') or 0):.1f})  "
            f"src={obj.get('detection_source')}"
        )
    outputs = report.get("outputs") or {}
    print(f"overlay: {outputs.get('latest_overlay')}")
    print(f"report:  {outputs.get('latest_report')}")
    return report


if __name__ == "__main__":
    main()




