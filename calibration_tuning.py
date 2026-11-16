import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from calibrate_grid_viewer import draw_label, rectangle_contour_candidates


ROOT = Path(__file__).resolve().parent
DEFAULT_PROFILE_PATH = ROOT / "runtime_state" / "calibration_line_tuning_profile.json"
DEFAULT_OUTPUT_DIR = ROOT / "runtime_state" / "calibration_line_tuning"
DEFAULT_REPORT_PATH = ROOT / "runtime_state" / "calibration_grid_outer_test" / "live_report.json"
LEGACY_CAPTURE_DIR = ROOT / "runtime_state" / "calibration_captures"
DEFAULT_CAPTURE_DIR = ROOT / "runtime_state" / "local_calibration_captures"
CAPTURE_IMAGE_DIRS = (DEFAULT_CAPTURE_DIR, LEGACY_CAPTURE_DIR)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def json_safe(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def deep_merge(defaults, override):
    merged = deepcopy(defaults)
    if not isinstance(override, dict):
        return merged
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def default_profile(width=2592, height=1944):
    return {
        "version": 1,
        "updated_at": utc_now(),
        "source_image": None,
        "frame": {"width": int(width), "height": int(height)},
        "roi": {"x": 0, "y": 0, "w": int(width), "h": int(height)},
        "preprocess": {"blur_px": 3},
        "vertical": {
            "threshold": 90,
            "open_kernel_px": 1,
            "close_gap_px": 45,
            "min_length_px": 80,
            "min_length_ratio": 0.10,
            "max_thickness_px": 45,
            "position_tolerance_px": 16,
            "segment_gap_px": 60,
        },
        "horizontal": {
            "threshold": 90,
            "open_kernel_px": 1,
            "close_gap_px": 45,
            "min_length_px": 80,
            "min_length_ratio": 0.10,
            "max_thickness_px": 45,
            "position_tolerance_px": 16,
            "segment_gap_px": 60,
        },
        "workspace": {
            "selected_candidate_index": 0,
            "min_area_px": 200000,
            "max_area_ratio": 0.98,
            "min_width_px": 160,
            "min_height_px": 160,
            "min_aspect": 0.25,
            "max_aspect": 4.0,
            "min_fill_ratio": 0.005,
            "max_fill_ratio": 0.60,
            "min_edge_support": 0.30,
            "min_corner_support": 0.12,
            "min_edges": 3,
            "min_corners": 2,
            "line_match_tolerance_px": 14,
            "line_corner_span_px": 42,
            "rect_contour_close_kernel": 3,
            "rect_reject_border_px": 0,
            "box_epsilon": 0.03,
            "max_candidates": 12,
        },
    }


def clamp(value, low, high):
    return max(low, min(high, value))


def normalize_profile(profile, width, height):
    normalized = deep_merge(default_profile(width, height), profile or {})
    normalized["frame"] = {"width": int(width), "height": int(height)}

    roi = normalized.setdefault("roi", {})
    x = int(round(float(roi.get("x", 0))))
    y = int(round(float(roi.get("y", 0))))
    w = int(round(float(roi.get("w", width))))
    h = int(round(float(roi.get("h", height))))
    x = clamp(x, 0, max(0, width - 1))
    y = clamp(y, 0, max(0, height - 1))
    w = clamp(w, 1, width - x)
    h = clamp(h, 1, height - y)
    normalized["roi"] = {"x": x, "y": y, "w": w, "h": h}

    for axis in ("vertical", "horizontal"):
        settings = normalized[axis]
        settings["threshold"] = clamp(int(settings.get("threshold", 90)), 0, 255)
        settings["open_kernel_px"] = max(1, int(settings.get("open_kernel_px", 1)))
        settings["close_gap_px"] = max(1, int(settings.get("close_gap_px", 45)))
        settings["min_length_px"] = max(1, int(settings.get("min_length_px", 80)))
        settings["min_length_ratio"] = max(0.0, float(settings.get("min_length_ratio", 0.10)))
        settings["max_thickness_px"] = max(1, int(settings.get("max_thickness_px", 45)))
        settings["position_tolerance_px"] = max(0.5, float(settings.get("position_tolerance_px", 16)))
        settings["segment_gap_px"] = max(0.0, float(settings.get("segment_gap_px", 60)))

    workspace = normalized["workspace"]
    workspace["selected_candidate_index"] = max(0, int(workspace.get("selected_candidate_index", 0)))
    workspace["min_area_px"] = max(1.0, float(workspace.get("min_area_px", 200000)))
    workspace["max_area_ratio"] = max(0.01, float(workspace.get("max_area_ratio", 0.98)))
    workspace["min_width_px"] = max(1, int(workspace.get("min_width_px", 160)))
    workspace["min_height_px"] = max(1, int(workspace.get("min_height_px", 160)))
    workspace["min_edges"] = clamp(int(workspace.get("min_edges", 3)), 1, 4)
    workspace["min_corners"] = clamp(int(workspace.get("min_corners", 2)), 0, 4)
    workspace["max_candidates"] = max(1, int(workspace.get("max_candidates", 12)))
    return normalized


def load_profile(path=DEFAULT_PROFILE_PATH, width=2592, height=1944):
    path = Path(path)
    if not path.exists():
        return default_profile(width, height)
    return normalize_profile(json.loads(path.read_text(encoding="utf-8")), width, height)


def save_profile(profile, path=DEFAULT_PROFILE_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = deepcopy(profile)
    payload["updated_at"] = utc_now()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=json_safe), encoding="utf-8")
    return path


def roi_mask(shape, roi):
    mask = np.zeros(shape[:2], dtype=np.uint8)
    x, y, w, h = int(roi["x"]), int(roi["y"]), int(roi["w"]), int(roi["h"])
    mask[y : y + h, x : x + w] = 255
    return mask


def axis_line_mask(gray, profile, axis):
    settings = profile[axis]
    blur_px = int(profile.get("preprocess", {}).get("blur_px", 3))
    if blur_px <= 1:
        blur = gray
    else:
        if blur_px % 2 == 0:
            blur_px += 1
        blur = cv2.GaussianBlur(gray, (blur_px, blur_px), 0)

    _, dark = cv2.threshold(blur, int(settings["threshold"]), 255, cv2.THRESH_BINARY_INV)
    dark = cv2.bitwise_and(dark, dark, mask=roi_mask(gray.shape, profile["roi"]))

    open_kernel = int(settings["open_kernel_px"])
    if open_kernel > 1:
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((open_kernel, open_kernel), np.uint8), iterations=1)

    close_gap = int(settings["close_gap_px"])
    roi = profile["roi"]
    min_length = max(
        int(settings["min_length_px"]),
        int((roi["h"] if axis == "vertical" else roi["w"]) * float(settings["min_length_ratio"])),
    )

    if axis == "vertical":
        close_kernel = np.ones((close_gap, 1), np.uint8)
        open_axis_kernel = np.ones((min_length, 1), np.uint8)
    else:
        close_kernel = np.ones((1, close_gap), np.uint8)
        open_axis_kernel = np.ones((1, min_length), np.uint8)

    closed = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, close_kernel, iterations=1)
    return cv2.morphologyEx(closed, cv2.MORPH_OPEN, open_axis_kernel, iterations=1)


def extract_axis_segments(axis_mask, profile, axis):
    contours, _ = cv2.findContours(axis_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    settings = profile[axis]
    roi = profile["roi"]
    min_length = max(
        int(settings["min_length_px"]),
        int((roi["h"] if axis == "vertical" else roi["w"]) * float(settings["min_length_ratio"])),
    )
    max_thickness = int(settings["max_thickness_px"])
    segments = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if axis == "vertical":
            length = h
            thickness = w
            coord = float(x + w / 2.0)
            start = float(y)
            end = float(y + h - 1)
        else:
            length = w
            thickness = h
            coord = float(y + h / 2.0)
            start = float(x)
            end = float(x + w - 1)
        if length < min_length or thickness > max_thickness:
            continue
        segments.append(
            {
                "coord": coord,
                "start": start,
                "end": end,
                "length": float(length),
                "thickness": float(thickness),
                "bbox": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)},
            }
        )
    return sorted(segments, key=lambda item: (item["coord"], item["start"]))


def merge_intervals(intervals, gap):
    merged = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + gap:
            merged.append([float(start), float(end)])
        else:
            merged[-1][1] = max(float(end), merged[-1][1])
    return merged


def cluster_segments(segments, profile, axis):
    tolerance = float(profile[axis]["position_tolerance_px"])
    gap = float(profile[axis]["segment_gap_px"])
    clusters = []
    for segment in segments:
        matched = None
        for cluster in clusters:
            if abs(float(segment["coord"]) - float(cluster["position"])) <= tolerance:
                matched = cluster
                break
        if matched is None:
            matched = {"position": float(segment["coord"]), "segments": []}
            clusters.append(matched)
        matched["segments"].append(segment)
        weights = np.array([item["length"] for item in matched["segments"]], dtype=np.float32)
        coords = np.array([item["coord"] for item in matched["segments"]], dtype=np.float32)
        matched["position"] = float(np.average(coords, weights=np.maximum(weights, 1.0)))

    for cluster in clusters:
        intervals = merge_intervals([(item["start"], item["end"]) for item in cluster["segments"]], gap)
        cluster["intervals"] = [{"start": start, "end": end} for start, end in intervals]
        cluster["support_length"] = float(sum(max(0.0, end - start) for start, end in intervals))
        cluster["segment_count"] = len(cluster["segments"])
    return sorted(clusters, key=lambda item: item["position"])


def candidate_args(profile):
    workspace = profile["workspace"]
    return argparse.Namespace(
        rect_contour_close_kernel=int(workspace["rect_contour_close_kernel"]),
        box_min_area=float(workspace["min_area_px"]),
        box_max_area=0.0,
        box_max_workspace_ratio=float(workspace["max_area_ratio"]),
        rect_reject_border_px=int(workspace["rect_reject_border_px"]),
        box_min_width=int(workspace["min_width_px"]),
        box_min_height=int(workspace["min_height_px"]),
        box_min_aspect=float(workspace["min_aspect"]),
        box_max_aspect=float(workspace["max_aspect"]),
        rect_min_fill_ratio=float(workspace["min_fill_ratio"]),
        rect_max_fill_ratio=float(workspace["max_fill_ratio"]),
        line_match_tolerance_px=float(workspace["line_match_tolerance_px"]),
        rect_min_edge_support=float(workspace["min_edge_support"]),
        line_corner_span_px=float(workspace["line_corner_span_px"]),
        rect_min_corner_support=float(workspace["min_corner_support"]),
        rect_min_corners=int(workspace["min_corners"]),
        rect_min_edges=int(workspace["min_edges"]),
        box_epsilon=float(workspace["box_epsilon"]),
    )


def summarize_candidate(candidate):
    result = {}
    for key in (
        "area",
        "source",
        "score",
        "corner_count",
        "edge_count",
        "bbox",
        "center_px",
        "corners_px",
        "transform_corners_px",
        "aspect",
        "edge_supports",
        "fill_ratio",
        "rectangularity",
    ):
        if key in candidate:
            result[key] = candidate[key]
    return result


def detect_candidates(vertical_mask, horizontal_mask, profile):
    line_mask = cv2.bitwise_or(vertical_mask, horizontal_mask)
    identity = np.eye(3, dtype=np.float32)
    candidates, contour_mask, contours = rectangle_contour_candidates(
        line_mask,
        horizontal_mask,
        vertical_mask,
        identity,
        candidate_args(profile),
    )
    roi = profile["roi"]
    x1, y1 = roi["x"], roi["y"]
    x2, y2 = x1 + roi["w"], y1 + roi["h"]
    filtered = []
    for candidate in candidates:
        center = candidate.get("center_px") or {}
        cx = float(center.get("x", -1))
        cy = float(center.get("y", -1))
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            filtered.append(candidate)
    filtered.sort(
        key=lambda item: (
            float(item.get("area", 0.0)),
            float(item.get("score", 0.0)),
            int(item.get("edge_count", 0)),
            int(item.get("corner_count", 0)),
        ),
        reverse=True,
    )
    max_candidates = int(profile["workspace"]["max_candidates"])
    return filtered[:max_candidates], contour_mask, contours


def draw_overlay(image, profile, vertical_mask, horizontal_mask, candidates, selected_index):
    overlay = image.copy()
    line_layer = np.zeros_like(overlay)
    line_layer[vertical_mask > 0] = (255, 120, 0)
    line_layer[horizontal_mask > 0] = (0, 220, 80)
    overlay = cv2.addWeighted(overlay, 1.0, line_layer, 0.75, 0)

    roi = profile["roi"]
    rx, ry, rw, rh = roi["x"], roi["y"], roi["w"], roi["h"]
    cv2.rectangle(overlay, (rx, ry), (rx + rw, ry + rh), (0, 255, 255), 3, cv2.LINE_AA)

    v_clusters = cluster_segments(extract_axis_segments(vertical_mask, profile, "vertical"), profile, "vertical")
    h_clusters = cluster_segments(extract_axis_segments(horizontal_mask, profile, "horizontal"), profile, "horizontal")
    for cluster in v_clusters:
        x = int(round(cluster["position"]))
        cv2.line(overlay, (x, ry), (x, ry + rh), (255, 0, 0), 1, cv2.LINE_AA)
    for cluster in h_clusters:
        y = int(round(cluster["position"]))
        cv2.line(overlay, (rx, y), (rx + rw, y), (0, 180, 0), 1, cv2.LINE_AA)

    for index, candidate in enumerate(candidates):
        corners = np.array(candidate["corners_px"], dtype=np.int32)
        color = (255, 0, 255) if index == selected_index else (0, 180, 255)
        thickness = 5 if index == selected_index else 2
        cv2.polylines(overlay, [corners], True, color, thickness, cv2.LINE_AA)
        center = candidate.get("center_px") or {}
        draw_label(
            overlay,
            f"C{index + 1}",
            (int(center.get("x", 0)) - 14, int(center.get("y", 0)) + 14),
            fg=(255, 255, 255),
            bg=(100, 0, 100) if index == selected_index else (0, 80, 110),
            scale=0.65,
        )

    draw_label(
        overlay,
        f"V={len(v_clusters)} H={len(h_clusters)} candidates={len(candidates)} selected=C{selected_index + 1 if candidates else 0}",
        (20, 42),
        fg=(255, 255, 255),
        bg=(0, 70, 70),
        scale=0.72,
    )
    return overlay


def selected_candidate(candidates, profile):
    if not candidates:
        return None, None
    index = int(profile["workspace"].get("selected_candidate_index", 0))
    index = clamp(index, 0, len(candidates) - 1)
    return index, candidates[index]


def write_png(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)
    return str(path)


def build_report(image_path, profile, candidates, selected, outputs):
    outer = None
    if selected is not None:
        outer = summarize_candidate(selected)
        outer["label"] = "OUTER"
        outer["role"] = "outer_frame"
        outer["source"] = "tuning_profile_rectangle"
        outer["profile_path"] = str(DEFAULT_PROFILE_PATH)

    return {
        "ok": True,
        "timestamp": utc_now(),
        "mode": "boxes",
        "source": "line_tuning_profile",
        "source_image": str(image_path),
        "workspace": {
            "detected": outer is not None,
            "source": "line_tuning_profile",
            "roi": profile["roi"],
            "ordered_corners_px": None if outer is None else outer.get("transform_corners_px"),
        },
        "box_count": 0,
        "outer_box": outer,
        "detector": {
            "source": "line_tuning_profile",
            "profile_path": str(DEFAULT_PROFILE_PATH),
            "roi": profile["roi"],
            "candidate_count": len(candidates),
            "selected_candidate_index": profile["workspace"].get("selected_candidate_index", 0),
            "candidates": [summarize_candidate(candidate) for candidate in candidates],
        },
        "boxes": [],
        "cells": [],
        "outputs": outputs,
    }


def analyze_image(image_path, profile=None, output_dir=DEFAULT_OUTPUT_DIR, prefix="preview", write_report=False, report_path=DEFAULT_REPORT_PATH):
    image_path = Path(image_path)
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"Cannot read image: {image_path}")

    height, width = image.shape[:2]
    profile = normalize_profile(profile, width, height)
    profile["source_image"] = str(image_path)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    vertical_mask = axis_line_mask(gray, profile, "vertical")
    horizontal_mask = axis_line_mask(gray, profile, "horizontal")
    candidates, contour_mask, contours = detect_candidates(vertical_mask, horizontal_mask, profile)
    selected_index, selected = selected_candidate(candidates, profile)
    if selected_index is None:
        selected_index = 0
    profile["workspace"]["selected_candidate_index"] = selected_index

    overlay = draw_overlay(image, profile, vertical_mask, horizontal_mask, candidates, selected_index)
    combined_mask = cv2.bitwise_or(vertical_mask, horizontal_mask)
    output_dir = Path(output_dir)
    outputs = {
        "source_image": str(image_path),
        "overlay": write_png(output_dir / f"{prefix}_overlay.png", overlay),
        "vertical_mask": write_png(output_dir / f"{prefix}_vertical_mask.png", vertical_mask),
        "horizontal_mask": write_png(output_dir / f"{prefix}_horizontal_mask.png", horizontal_mask),
        "combined_mask": write_png(output_dir / f"{prefix}_combined_mask.png", combined_mask),
        "contour_mask": write_png(output_dir / f"{prefix}_contour_mask.png", contour_mask),
    }

    v_segments = extract_axis_segments(vertical_mask, profile, "vertical")
    h_segments = extract_axis_segments(horizontal_mask, profile, "horizontal")
    v_clusters = cluster_segments(v_segments, profile, "vertical")
    h_clusters = cluster_segments(h_segments, profile, "horizontal")
    result = {
        "ok": True,
        "timestamp": utc_now(),
        "image_path": str(image_path),
        "frame": {"width": width, "height": height},
        "profile": profile,
        "roi": profile["roi"],
        "vertical": {"segment_count": len(v_segments), "line_count": len(v_clusters), "lines": v_clusters},
        "horizontal": {"segment_count": len(h_segments), "line_count": len(h_clusters), "lines": h_clusters},
        "candidate_count": len(candidates),
        "selected_candidate_index": selected_index,
        "selected_candidate": None if selected is None else summarize_candidate(selected),
        "candidates": [summarize_candidate(candidate) for candidate in candidates],
        "contour_count": len(contours),
        "outputs": outputs,
    }

    preview_report_path = output_dir / f"{prefix}_report.json"
    preview_report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=json_safe), encoding="utf-8")
    result["outputs"]["preview_report"] = str(preview_report_path)

    if write_report:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = build_report(image_path, profile, candidates, selected, outputs)
        report["outputs"]["report"] = str(report_path)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=json_safe), encoding="utf-8")
        result["report_path"] = str(report_path)
        result["report"] = report

    return result


def latest_capture_images(limit=20, directories=None):
    image_dirs = directories or CAPTURE_IMAGE_DIRS
    files = []
    seen = set()
    for directory in image_dirs:
        directory = Path(directory)
        if not directory.exists():
            continue
        for path in directory.glob("*.*"):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(path)
    files = sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)
    images = []
    for path in files:
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp"}:
            continue
        images.append(
            {
                "path": str(path),
                "name": path.name,
                "size": path.stat().st_size,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            }
        )
        if len(images) >= int(limit):
            break
    return images
