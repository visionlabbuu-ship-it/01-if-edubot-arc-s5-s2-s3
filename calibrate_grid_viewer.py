import argparse
import json
import os
import sys
import time
from ctypes import POINTER, byref, cast, c_ubyte, memmove, memset, sizeof
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from config import CAMERA_CONFIG


DEFAULT_OUTPUT_DIR = Path("runtime_state/calibration_grid")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def order_quad_points(points):
    pts = np.array(points, dtype=np.float32).reshape((4, 2))
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).reshape((4,))
    top_left = pts[np.argmin(sums)]
    bottom_right = pts[np.argmax(sums)]
    top_right = pts[np.argmin(diffs)]
    bottom_left = pts[np.argmax(diffs)]
    return np.float32([top_left, top_right, bottom_left, bottom_right])


def order_polygon_ccw(points):
    quad = order_quad_points(points)
    top_left, top_right, bottom_left, bottom_right = quad
    return np.float32([top_left, top_right, bottom_right, bottom_left])


def row_name(row_index):
    name = ""
    value = int(row_index)
    while True:
        value, remainder = divmod(value, 26)
        name = chr(ord("A") + remainder) + name
        if value == 0:
            return name
        value -= 1


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


def draw_label(image, text, origin, fg=(255, 255, 255), bg=(0, 0, 0), scale=0.75):
    x, y = int(origin[0]), int(origin[1])
    font = cv2.FONT_HERSHEY_SIMPLEX
    thickness = 2
    (text_w, text_h), baseline = cv2.getTextSize(text, font, scale, thickness)
    pad = 5
    x1 = max(0, x)
    y1 = max(0, y - text_h - baseline - pad * 2)
    x2 = min(image.shape[1] - 1, x + text_w + pad * 2)
    y2 = min(image.shape[0] - 1, y + baseline)
    cv2.rectangle(image, (x1, y1), (x2, y2), bg, -1)
    cv2.putText(image, text, (x1 + pad, y2 - baseline - pad), font, scale, fg, thickness, cv2.LINE_AA)


def prepare_mvs_sdk(config):
    project_root = str(config["project_root"])
    mvs_import = str(Path(project_root) / "MvImport")
    for path in (project_root, mvs_import):
        if path not in sys.path:
            sys.path.insert(0, path)

    dll_dir = config.get("dll_dir")
    if dll_dir and hasattr(os, "add_dll_directory") and os.path.isdir(dll_dir):
        os.add_dll_directory(str(dll_dir))


class MvsCameraStream:
    def __init__(self, config, frame_timeout_ms=None):
        self.config = config
        self.frame_timeout_ms = int(frame_timeout_ms or config.get("frame_timeout_ms", 1000))
        self.sdk = None
        self.cam = None
        self.frame_out = None

    def open(self):
        prepare_mvs_sdk(self.config)

        from MvImport.MvCameraControl_class import (
            MV_ACCESS_Exclusive,
            MV_CC_DEVICE_INFO,
            MV_CC_DEVICE_INFO_LIST,
            MV_FRAME_OUT_INFO_EX,
            MVCC_INTVALUE,
            MvCamera,
        )

        device_list = MV_CC_DEVICE_INFO_LIST()
        ret = MvCamera.MV_CC_EnumDevices(0x00000004, device_list)
        if ret != 0 or device_list.nDeviceNum == 0:
            raise RuntimeError("No MVS camera detected")

        cam = MvCamera()
        device_info = cast(device_list.pDeviceInfo[0], POINTER(MV_CC_DEVICE_INFO)).contents
        cam.MV_CC_CreateHandle(device_info)
        cam.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
        cam.MV_CC_StopGrabbing()
        cam.MV_CC_SetEnumValue("PixelFormat", int(self.config.get("pixel_format", 17301512)))
        cam.MV_CC_SetEnumValue("BalanceWhiteAuto", 2)
        cam.MV_CC_SetBoolValue("AcquisitionFrameRateEnable", True)
        cam.MV_CC_SetBoolValue("GammaEnable", True)
        self._apply_camera_params(cam)

        payload_size = None
        if hasattr(cam, "MV_CC_GetIntValue"):
            payload = MVCC_INTVALUE()
            memset(byref(payload), 0, sizeof(MVCC_INTVALUE))
            ret = cam.MV_CC_GetIntValue("PayloadSize", payload)
            if ret == 0:
                payload_size = int(payload.nCurValue)
        cam.MV_CC_StartGrabbing()

        frame_out = MV_FRAME_OUT_INFO_EX()
        memset(byref(frame_out), 0, sizeof(frame_out))

        self.sdk = {
            "MvCamera": MvCamera,
            "payload_size": payload_size,
        }
        self.cam = cam
        self.frame_out = frame_out

    def _apply_camera_params(self, cam):
        mode = str(self.config.get("mode", "rgb"))
        params = (self.config.get("params") or {}).get(mode, {})
        for name, value in params.items():
            try:
                if isinstance(value, int):
                    cam.MV_CC_SetIntValue(name, int(value))
                else:
                    cam.MV_CC_SetFloatValue(name, float(value))
            except Exception:
                pass

    def grab(self, retries=10, retry_delay_s=0.1):
        if self.cam is None or self.frame_out is None:
            raise RuntimeError("Camera is not open")

        try:
            if hasattr(self.cam, "MV_CC_GetImageBuffer"):
                ret = None
                for _ in range(max(1, int(retries))):
                    ret = self.cam.MV_CC_GetImageBuffer(self.frame_out, self.frame_timeout_ms)
                    if ret == 0:
                        break
                    time.sleep(float(retry_delay_s))
                if ret != 0:
                    raise RuntimeError(f"Failed to read camera frame: code={ret}")
                frame_len = int(self.frame_out.stFrameInfo.nFrameLen)
                data = (c_ubyte * frame_len)()
                memmove(data, self.frame_out.pBufAddr, frame_len)
                height = int(self.frame_out.stFrameInfo.nHeight)
                width = int(self.frame_out.stFrameInfo.nWidth)
            else:
                frame_len = int(self.sdk.get("payload_size") or 0)
                if frame_len <= 0:
                    raise RuntimeError("Camera payload size is not available")
                data = (c_ubyte * frame_len)()
                ret = None
                for _ in range(max(1, int(retries))):
                    ret = self.cam.MV_CC_GetOneFrameTimeout(data, frame_len, self.frame_out, self.frame_timeout_ms)
                    if ret == 0:
                        break
                    time.sleep(float(retry_delay_s))
                if ret != 0:
                    raise RuntimeError(f"Failed to read camera frame: code={ret}")
                height = int(self.frame_out.nHeight)
                width = int(self.frame_out.nWidth)
                frame_len = min(frame_len, int(width * height * 3))
            image = np.frombuffer(data, dtype=np.uint8)

            if frame_len == width * height:
                raw = image.reshape((height, width))
                return cv2.cvtColor(raw, cv2.COLOR_BayerGB2BGR)
            if frame_len == width * height * 3:
                return image.reshape((height, width, 3)).copy()
            raise RuntimeError(f"Unsupported frame shape: width={width} height={height} bytes={frame_len}")
        finally:
            try:
                self.cam.MV_CC_FreeImageBuffer(self.frame_out)
            except Exception:
                pass

    def close(self):
        if self.cam is None:
            return
        for method_name in ("MV_CC_StopGrabbing", "MV_CC_CloseDevice", "MV_CC_DestroyHandle"):
            try:
                getattr(self.cam, method_name)()
            except Exception:
                pass
        self.cam = None
        self.frame_out = None


def detect_workspace_quad(image, config, args):
    fixed = config.get("fixed_workspace_corners_px")
    if args.fixed_workspace:
        if isinstance(fixed, list) and len(fixed) == 4:
            return np.array(fixed, dtype=np.float32).reshape((-1, 1, 2)), {
                "detected": True,
                "source": "fixed_config",
                "candidates": [],
            }
        return None, {"detected": False, "source": "fixed_config_missing", "candidates": []}

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, int(args.canny_low), int(args.canny_high))
    kernel = np.ones((int(args.edge_dilate), int(args.edge_dilate)), np.uint8)
    if args.edge_dilate > 1:
        edges = cv2.dilate(edges, kernel, iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    selected = None
    selected_area = 0.0
    candidates = []
    min_area = float(args.workspace_min_area or config.get("workspace_min_area", 200000))
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area <= min_area:
            continue
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, float(args.workspace_epsilon) * perimeter, True)
        candidate = {
            "area": area,
            "quad": len(approx) == 4,
            "points": approx.reshape((-1, 2)).tolist() if len(approx) == 4 else None,
        }
        candidates.append(candidate)
        if len(approx) == 4 and area > selected_area:
            selected = approx
            selected_area = area

    candidates.sort(key=lambda item: item["area"], reverse=True)
    if selected is not None:
        return selected.astype(np.float32), {
            "detected": True,
            "source": "auto_edges",
            "area": selected_area,
            "candidates": candidates[:12],
        }

    if getattr(args, "workspace_line_fallback", False):
        line_quad, line_info = detect_workspace_quad_from_frame_lines(image, config, args)
        if line_quad is not None:
            line_info["edge_candidates"] = candidates[:12]
            return line_quad, line_info

    if not args.no_fixed_fallback and isinstance(fixed, list) and len(fixed) == 4:
        return np.array(fixed, dtype=np.float32).reshape((-1, 1, 2)), {
            "detected": True,
            "source": "fixed_fallback",
            "candidates": candidates[:12],
        }

    return None, {"detected": False, "source": "auto_edges", "candidates": candidates[:12]}


def rectangle_candidate_summary(candidate):
    keys = (
        "area",
        "source",
        "score",
        "corner_count",
        "edge_count",
        "bbox",
        "center_px",
        "corners_px",
        "aspect",
        "edge_supports",
        "fill_ratio",
        "rectangularity",
    )
    return {key: candidate[key] for key in keys if key in candidate}


def detect_workspace_quad_from_frame_lines(image, config, args):
    line_mask, _, _ = build_frame_line_mask(image, args)
    horizontal_mask = axis_line_mask(line_mask, "horizontal", args)
    vertical_mask = axis_line_mask(line_mask, "vertical", args)
    identity = np.eye(3, dtype=np.float32)
    candidates, _, contours = rectangle_contour_candidates(
        line_mask,
        horizontal_mask,
        vertical_mask,
        identity,
        workspace_rectangle_args(config, args),
    )
    min_area = float(args.workspace_min_area or config.get("workspace_min_area", 200000))
    candidates = [candidate for candidate in candidates if float(candidate.get("area", 0.0)) >= min_area]
    candidates.sort(
        key=lambda item: (
            int(item.get("corner_count", 0)),
            int(item.get("edge_count", 0)),
            float(item.get("score", 0.0)),
            float(item.get("area", 0.0)),
        ),
        reverse=True,
    )
    if not candidates:
        return None, {
            "detected": False,
            "source": "frame_line_fallback",
            "candidates": [],
            "contour_count": len(contours),
            "accepted_contour_count": 0,
        }

    selected = candidates[0]
    return np.array(selected["corners_px"], dtype=np.float32).reshape((-1, 1, 2)), {
        "detected": True,
        "source": "frame_line_fallback",
        "area": float(selected.get("area", 0.0)),
        "score": float(selected.get("score", 0.0)),
        "bbox": selected.get("bbox"),
        "edge_supports": selected.get("edge_supports"),
        "contour_count": len(contours),
        "accepted_contour_count": len(candidates),
        "candidates": [rectangle_candidate_summary(candidate) for candidate in candidates[:12]],
    }


def workspace_rectangle_args(config, args):
    workspace_args = argparse.Namespace(**vars(args))
    workspace_args.box_min_area = float(args.workspace_min_area or config.get("workspace_min_area", 200000))
    workspace_args.box_max_area = 0.0
    workspace_args.box_max_workspace_ratio = 0.98
    workspace_args.box_min_width = max(int(args.box_min_width), 160)
    workspace_args.box_min_height = max(int(args.box_min_height), 160)
    workspace_args.rect_min_edges = min(int(args.rect_min_edges), 4)
    workspace_args.rect_min_corners = min(int(args.rect_min_corners), 3)
    workspace_args.rect_max_fill_ratio = max(float(args.rect_max_fill_ratio), 0.55)
    return workspace_args


def build_dark_line_mask(warped, threshold):
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, int(threshold), 255, cv2.THRESH_BINARY_INV)
    mask = cv2.medianBlur(mask, 5)
    close_kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)
    return mask


def rect_iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    intersection = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def rect_area(rect):
    x1, y1, x2, y2 = rect
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def should_suppress_box(candidate, kept_box, args):
    iou = rect_iou(candidate["bbox_tuple"], kept_box["bbox_tuple"])
    if iou <= float(args.box_nms_iou):
        return False

    candidate_area = rect_area(candidate["bbox_tuple"])
    kept_area = rect_area(kept_box["bbox_tuple"])
    larger_area = max(candidate_area, kept_area)
    if larger_area <= 0.0:
        return True

    area_ratio = min(candidate_area, kept_area) / larger_area
    return area_ratio >= float(args.box_nms_min_area_ratio)


def candidate_analysis_rect(candidate):
    warp_rect = candidate.get("warp_rect") or {}
    if {"x1", "y1", "x2", "y2"}.issubset(warp_rect):
        return (
            float(warp_rect["x1"]),
            float(warp_rect["y1"]),
            float(warp_rect["x2"]),
            float(warp_rect["y2"]),
        )
    bbox = candidate["bbox"]
    return (
        float(bbox["x"]),
        float(bbox["y"]),
        float(bbox["x"] + bbox["w"]),
        float(bbox["y"] + bbox["h"]),
    )


def candidate_analysis_size(candidate):
    x1, y1, x2, y2 = candidate_analysis_rect(candidate)
    return abs(x2 - x1), abs(y2 - y1)


def sizes_match(candidate, seed, width_ratio, height_ratio):
    width, height = candidate_analysis_size(candidate)
    seed_width, seed_height = candidate_analysis_size(seed)
    if min(width, height, seed_width, seed_height) <= 0.0:
        return False
    width_delta = abs(width - seed_width) / max(width, seed_width)
    height_delta = abs(height - seed_height) / max(height, seed_height)
    return width_delta <= width_ratio and height_delta <= height_ratio


def rect_contains_center(container, item):
    cx = float(item["center_px"]["x"])
    cy = float(item["center_px"]["y"])
    x1, y1, x2, y2 = container["bbox_tuple"]
    return min(x1, x2) <= cx <= max(x1, x2) and min(y1, y2) <= cy <= max(y1, y2)


def apply_size_consensus_filter(boxes, args):
    min_count = int(args.size_consensus_min_count)
    if args.disable_size_consensus or len(boxes) < min_count:
        return boxes, {
            "enabled": False,
            "reason": "disabled_or_too_few_boxes",
            "input_count": len(boxes),
            "output_count": len(boxes),
        }

    width_ratio = float(args.size_consensus_width_ratio)
    height_ratio = float(args.size_consensus_height_ratio)
    best_group = []
    for seed in boxes:
        group = [box for box in boxes if sizes_match(box, seed, width_ratio, height_ratio)]
        if len(group) > len(best_group):
            best_group = group
            continue
        if len(group) == len(best_group) and group:
            group_score = float(np.mean([float(item.get("score", 0.0)) for item in group]))
            best_score = float(np.mean([float(item.get("score", 0.0)) for item in best_group])) if best_group else 0.0
            if group_score > best_score:
                best_group = group

    if len(best_group) < min_count:
        return boxes, {
            "enabled": True,
            "reason": "no_consensus_group",
            "input_count": len(boxes),
            "output_count": len(boxes),
            "best_group_count": len(best_group),
        }

    group_ids = {id(item) for item in best_group}
    group_areas = [rect_area(item["bbox_tuple"]) for item in best_group]
    median_group_area = float(np.median(group_areas)) if group_areas else 0.0
    container_ids = set()
    min_children = min(int(args.size_consensus_container_min_children), len(best_group))
    container_area_ratio = float(args.size_consensus_container_min_area_ratio)
    for box in boxes:
        if id(box) in group_ids or median_group_area <= 0.0:
            continue
        if rect_area(box["bbox_tuple"]) < median_group_area * container_area_ratio:
            continue
        child_count = sum(1 for child in best_group if rect_contains_center(box, child))
        if child_count >= min_children:
            container_ids.add(id(box))

    output = [box for box in boxes if id(box) in group_ids or id(box) in container_ids]
    return output, {
        "enabled": True,
        "reason": "applied",
        "input_count": len(boxes),
        "output_count": len(output),
        "group_count": len(best_group),
        "container_count": len(container_ids),
        "median_group_area": median_group_area,
        "width_ratio": width_ratio,
        "height_ratio": height_ratio,
    }


def build_outer_box_from_children(boxes, inverse_matrix, args):
    min_count = int(args.outer_min_children)
    if args.disable_outer_box or len(boxes) < min_count:
        return None, {
            "enabled": False,
            "reason": "disabled_or_too_few_boxes",
            "child_count": len(boxes),
        }

    child_corners = []
    for box in boxes:
        corners = box.get("analysis_corners")
        if isinstance(corners, list) and len(corners) == 4:
            child_corners.extend(corners)
            continue
        x1, y1, x2, y2 = candidate_analysis_rect(box)
        child_corners.extend([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])

    outer_transform = outer_quad_from_points(child_corners)
    padding = float(args.outer_padding_px)
    if padding:
        outer_transform = pad_quad(outer_transform, padding)
    outer = make_quad_candidate(
        outer_transform,
        inverse_matrix,
        {"top": 1.0, "bottom": 1.0, "left": 1.0, "right": 1.0},
        4,
        "outer_quad_from_children",
    )
    outer["label"] = str(args.outer_label)
    outer["role"] = "outer_frame"
    outer["child_count"] = len(boxes)
    return outer, {
        "enabled": True,
        "reason": "quad_from_children",
        "child_count": len(boxes),
        "padding_px": padding,
        "warp_rect": outer["warp_rect"],
        "analysis_corners": outer["analysis_corners"],
    }


def quad_from_contour(contour, epsilon_ratio):
    perimeter = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, float(epsilon_ratio) * perimeter, True)
    if len(approx) == 4:
        return approx.reshape((4, 2)).astype(np.float32)

    rect = cv2.minAreaRect(contour)
    box = cv2.boxPoints(rect).astype(np.float32)
    return box


def contour_rectangularity(contour, quad):
    area = float(cv2.contourArea(contour))
    quad_area = float(abs(cv2.contourArea(quad.reshape((-1, 1, 2)))))
    if quad_area <= 1.0:
        return 0.0
    return area / quad_area


def dark_edge_ratios(mask, x, y, width, height, probe):
    image_h, image_w = mask.shape[:2]
    x1 = int(max(0, x))
    y1 = int(max(0, y))
    x2 = int(min(image_w, x + width))
    y2 = int(min(image_h, y + height))
    probe = int(max(2, probe))

    strips = []
    strips.append(mask[max(0, y1 - probe) : y1, x1:x2])
    strips.append(mask[y2 : min(image_h, y2 + probe), x1:x2])
    strips.append(mask[y1:y2, max(0, x1 - probe) : x1])
    strips.append(mask[y1:y2, x2 : min(image_w, x2 + probe)])
    ratios = []
    for strip in strips:
        if strip.size == 0:
            ratios.append(0.0)
        else:
            ratios.append(float(cv2.countNonZero(strip)) / float(strip.size))
    return ratios


def workspace_warp(image, workspace_quad, config, args):
    if workspace_quad is None:
        h, w = image.shape[:2]
        identity = np.eye(3, dtype=np.float32)
        whole = np.float32([[0.0, 0.0], [w - 1.0, 0.0], [0.0, h - 1.0], [w - 1.0, h - 1.0]])
        return image.copy(), identity, identity, whole

    ordered = order_quad_points(workspace_quad)
    width_mm = float(config.get("workspace_width_mm", 600.0))
    height_mm = float(config.get("workspace_height_mm", 400.0))
    content_w = int(args.warp_width)
    content_h = int(round(content_w * height_mm / max(1.0, width_mm)))
    content_h = max(300, content_h)
    padding = int(max(0, args.workspace_padding_px))
    warp_w = content_w + padding * 2
    warp_h = content_h + padding * 2
    target = np.float32(
        [
            [float(padding), float(padding)],
            [float(padding + content_w - 1), float(padding)],
            [float(padding), float(padding + content_h - 1)],
            [float(padding + content_w - 1), float(padding + content_h - 1)],
        ]
    )
    matrix = cv2.getPerspectiveTransform(ordered, target)
    inverse_matrix = cv2.getPerspectiveTransform(target, ordered)
    warped = cv2.warpPerspective(image, matrix, (warp_w, warp_h))
    return warped, matrix, inverse_matrix, target


def project_mask_to_source(mask, inverse_matrix, source_shape):
    source_h, source_w = source_shape[:2]
    return cv2.warpPerspective(mask, inverse_matrix, (source_w, source_h))


def build_frame_line_mask(warped, args):
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blur, int(args.canny_low), int(args.canny_high))

    # OpenCV line/contour operations treat white as foreground, so black frame
    # lines are intentionally inverted into white pixels here.
    _, dark_lines = cv2.threshold(blur, int(args.black_threshold), 255, cv2.THRESH_BINARY_INV)
    if int(args.line_mask_open) > 1:
        kernel = np.ones((int(args.line_mask_open), int(args.line_mask_open)), np.uint8)
        dark_lines = cv2.morphologyEx(dark_lines, cv2.MORPH_OPEN, kernel, iterations=1)

    near_dark = cv2.dilate(dark_lines, np.ones((5, 5), np.uint8), iterations=1)
    edge_lines = cv2.bitwise_and(edges, edges, mask=near_dark)
    line_mask = cv2.bitwise_or(dark_lines, edge_lines)
    return line_mask, edges, dark_lines


def axis_line_mask(line_mask, axis, args):
    h, w = line_mask.shape[:2]
    min_length = max(int(args.line_min_length_px), int((w if axis == "horizontal" else h) * float(args.line_min_length_ratio)))
    close_gap = max(1, int(args.line_close_gap_px))

    if axis == "horizontal":
        close_kernel = np.ones((1, close_gap), np.uint8)
        open_kernel = np.ones((1, min_length), np.uint8)
    else:
        close_kernel = np.ones((close_gap, 1), np.uint8)
        open_kernel = np.ones((min_length, 1), np.uint8)

    closed = cv2.morphologyEx(line_mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)
    return cv2.morphologyEx(closed, cv2.MORPH_OPEN, open_kernel, iterations=1)


def extract_line_segments(axis_mask, axis, args):
    contours, _ = cv2.findContours(axis_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_length = int(args.line_min_length_px)
    max_thickness = int(args.line_max_thickness_px)
    segments = []

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if axis == "horizontal":
            length = w
            thickness = h
            coord = float(y + h / 2.0)
            start = float(x)
            end = float(x + w - 1)
        else:
            length = h
            thickness = w
            coord = float(x + w / 2.0)
            start = float(y)
            end = float(y + h - 1)

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

    return segments


def merge_intervals(intervals, gap):
    if not intervals:
        return []

    merged = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + gap:
            merged.append([float(start), float(end)])
        else:
            merged[-1][1] = max(float(end), merged[-1][1])
    return merged


def cluster_line_positions(segments, args):
    tolerance = float(args.line_position_tolerance_px)
    gap = float(args.line_segment_gap_px)
    lines = []

    for segment in sorted(segments, key=lambda item: item["coord"]):
        matched = None
        for line in lines:
            if abs(segment["coord"] - line["position"]) <= tolerance:
                matched = line
                break

        if matched is None:
            matched = {"position": segment["coord"], "segments": []}
            lines.append(matched)

        matched["segments"].append(segment)
        weights = np.array([item["length"] for item in matched["segments"]], dtype=np.float32)
        coords = np.array([item["coord"] for item in matched["segments"]], dtype=np.float32)
        matched["position"] = float(np.average(coords, weights=np.maximum(weights, 1.0)))

    for line in lines:
        intervals = [(item["start"], item["end"]) for item in line["segments"]]
        merged = merge_intervals(intervals, gap)
        line["intervals"] = [{"start": float(start), "end": float(end)} for start, end in merged]
        line["support_length"] = float(sum(max(0.0, end - start) for start, end in merged))
        line["segment_count"] = len(line["segments"])

    return sorted(lines, key=lambda item: item["position"])


def edge_support_ratio(axis_mask, axis, position, start, end, tolerance):
    image_h, image_w = axis_mask.shape[:2]
    start = int(round(max(0.0, start)))
    end = int(round(end))
    tolerance = int(max(1, round(tolerance)))

    if axis == "horizontal":
        end = min(image_w - 1, end)
        y1 = max(0, int(round(position)) - tolerance)
        y2 = min(image_h - 1, int(round(position)) + tolerance)
        if end <= start or y2 < y1:
            return 0.0
        roi = axis_mask[y1 : y2 + 1, start : end + 1]
        if roi.size == 0:
            return 0.0
        column_has_line = np.any(roi > 0, axis=0)
        return float(np.count_nonzero(column_has_line)) / float(max(1, end - start + 1))

    end = min(image_h - 1, end)
    x1 = max(0, int(round(position)) - tolerance)
    x2 = min(image_w - 1, int(round(position)) + tolerance)
    if end <= start or x2 < x1:
        return 0.0
    roi = axis_mask[start : end + 1, x1 : x2 + 1]
    if roi.size == 0:
        return 0.0
    row_has_line = np.any(roi > 0, axis=1)
    return float(np.count_nonzero(row_has_line)) / float(max(1, end - start + 1))


def corner_support(horizontal_mask, vertical_mask, x, y, args):
    span = float(args.line_corner_span_px)
    tolerance = float(args.line_match_tolerance_px)
    h_score = edge_support_ratio(horizontal_mask, "horizontal", y, x - span, x + span, tolerance)
    v_score = edge_support_ratio(vertical_mask, "vertical", x, y - span, y + span, tolerance)
    return h_score >= float(args.rect_min_corner_support) and v_score >= float(args.rect_min_corner_support)


def polygon_order_from_transform_order(corners):
    top_left, top_right, bottom_left, bottom_right = corners
    return np.float32([top_left, top_right, bottom_right, bottom_left])


def axis_rect_transform_corners(x1, x2, y1, y2):
    return np.float32([[x1, y1], [x2, y1], [x1, y2], [x2, y2]])


def bbox_from_points(points):
    pts = np.array(points, dtype=np.float32).reshape((-1, 2))
    min_xy = np.min(pts, axis=0)
    max_xy = np.max(pts, axis=0)
    bbox = {
        "x": int(round(float(min_xy[0]))),
        "y": int(round(float(min_xy[1]))),
        "w": int(round(float(max_xy[0] - min_xy[0]))),
        "h": int(round(float(max_xy[1] - min_xy[1]))),
    }
    rect = (
        float(bbox["x"]),
        float(bbox["y"]),
        float(bbox["x"] + bbox["w"]),
        float(bbox["y"] + bbox["h"]),
    )
    return bbox, rect


def outer_quad_from_points(points):
    pts = np.array(points, dtype=np.float32).reshape((-1, 2))
    sums = pts.sum(axis=1)
    diffs = pts[:, 0] - pts[:, 1]
    top_left = pts[np.argmin(sums)]
    top_right = pts[np.argmax(diffs)]
    bottom_left = pts[np.argmin(diffs)]
    bottom_right = pts[np.argmax(sums)]
    return np.float32([top_left, top_right, bottom_left, bottom_right])


def pad_quad(transform_corners, padding):
    corners = np.array(transform_corners, dtype=np.float32).reshape((4, 2))
    center = np.mean(corners, axis=0)
    padded = []
    for corner in corners:
        vector = corner - center
        length = float(np.linalg.norm(vector))
        if length <= 1e-6:
            padded.append(corner)
            continue
        padded.append(corner + (vector / length) * float(padding))
    return np.float32(padded)


def cross_2d(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def quad_center_from_transform_order(transform_corners):
    top_left, top_right, bottom_left, bottom_right = np.array(transform_corners, dtype=np.float32).reshape((4, 2))
    diagonal_a = bottom_right - top_left
    diagonal_b = bottom_left - top_right
    denominator = cross_2d(diagonal_a, diagonal_b)
    if abs(denominator) <= 1e-6:
        return np.mean(np.float32([top_left, top_right, bottom_left, bottom_right]), axis=0)
    offset = top_right - top_left
    t = cross_2d(offset, diagonal_b) / denominator
    return top_left + diagonal_a * float(t)


def make_quad_candidate(transform_corners, inverse_matrix, supports, corner_count, source):
    analysis_transform = order_quad_points(transform_corners)
    analysis_polygon = polygon_order_from_transform_order(analysis_transform)
    source_transform = cv2.perspectiveTransform(
        analysis_transform.reshape((-1, 1, 2)),
        inverse_matrix,
    ).reshape((-1, 2))
    source_polygon = polygon_order_from_transform_order(source_transform)
    center_analysis = quad_center_from_transform_order(analysis_transform).reshape((1, 1, 2)).astype(np.float32)
    center_source = cv2.perspectiveTransform(center_analysis, inverse_matrix).reshape((2,))
    bbox, rect = bbox_from_points(source_polygon)
    analysis_bbox, _ = bbox_from_points(analysis_polygon)
    warp_rect = {
        "x1": float(analysis_bbox["x"]),
        "y1": float(analysis_bbox["y"]),
        "x2": float(analysis_bbox["x"] + analysis_bbox["w"]),
        "y2": float(analysis_bbox["y"] + analysis_bbox["h"]),
    }
    width = float(
        (
            np.linalg.norm(analysis_transform[1] - analysis_transform[0])
            + np.linalg.norm(analysis_transform[3] - analysis_transform[2])
        )
        / 2.0
    )
    height = float(
        (
            np.linalg.norm(analysis_transform[2] - analysis_transform[0])
            + np.linalg.norm(analysis_transform[3] - analysis_transform[1])
        )
        / 2.0
    )
    support_values = [float(supports[key]) for key in ("top", "bottom", "left", "right")]
    score = float(np.mean(support_values))
    return {
        "area": float(abs(cv2.contourArea(analysis_polygon.reshape((-1, 1, 2))))),
        "source": source,
        "score": score,
        "edge_supports": supports,
        "corner_count": int(corner_count),
        "bbox_tuple": rect,
        "bbox": bbox,
        "center_px": {"x": float(center_source[0]), "y": float(center_source[1])},
        "corners_px": source_polygon.tolist(),
        "transform_corners_px": source_transform.tolist(),
        "analysis_corners": analysis_polygon.tolist(),
        "analysis_transform_corners": analysis_transform.tolist(),
        "warp_rect": warp_rect,
        "aspect": width / float(max(1.0, height)),
    }


def make_rectangle_candidate(x1, x2, y1, y2, inverse_matrix, supports, corner_count, source):
    return make_quad_candidate(
        axis_rect_transform_corners(x1, x2, y1, y2),
        inverse_matrix,
        supports,
        corner_count,
        source,
    )


def rectangle_contour_candidates(line_mask, horizontal_mask, vertical_mask, inverse_matrix, args):
    contour_mask = line_mask.copy()
    close_size = int(args.rect_contour_close_kernel)
    if close_size > 1:
        kernel = np.ones((close_size, close_size), np.uint8)
        contour_mask = cv2.morphologyEx(contour_mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    contours, hierarchy = cv2.findContours(contour_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    min_area = float(args.box_min_area)
    max_area = float(args.box_max_area)
    image_h, image_w = line_mask.shape[:2]
    if max_area <= 0:
        max_area = float(image_w * image_h)
    max_area = min(max_area, float(image_w * image_h) * float(args.box_max_workspace_ratio))

    candidates = []
    contour_info = []
    for index, contour in enumerate(contours):
        x, y, w, h = cv2.boundingRect(contour)
        bbox_area = float(w * h)
        contour_area = float(abs(cv2.contourArea(contour)))
        roi = contour_mask[y : y + h, x : x + w]
        fill_ratio = float(cv2.countNonZero(roi)) / float(max(1.0, bbox_area))
        parent = None
        child = None
        if hierarchy is not None and len(hierarchy) > 0:
            child = int(hierarchy[0][index][2])
            parent = int(hierarchy[0][index][3])

        item = {
            "bbox": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)},
            "bbox_area": bbox_area,
            "contour_area": contour_area,
            "fill_ratio": fill_ratio,
            "parent": parent,
            "child": child,
        }
        contour_info.append(item)

        border_margin = int(max(0, args.rect_reject_border_px))
        if (
            border_margin > 0
            and (x <= border_margin or y <= border_margin or x + w >= image_w - border_margin or y + h >= image_h - border_margin)
        ):
            item["rejected"] = "warp_border"
            continue

        if bbox_area < min_area or bbox_area > max_area:
            item["rejected"] = "area"
            continue
        if w < int(args.box_min_width) or h < int(args.box_min_height):
            item["rejected"] = "size"
            continue

        aspect = float(w) / float(max(1, h))
        if aspect < float(args.box_min_aspect) or aspect > float(args.box_max_aspect):
            item["rejected"] = "aspect"
            continue
        if fill_ratio < float(args.rect_min_fill_ratio) or fill_ratio > float(args.rect_max_fill_ratio):
            item["rejected"] = "fill_ratio"
            continue

        x1 = float(x)
        y1 = float(y)
        x2 = float(x + w - 1)
        y2 = float(y + h - 1)
        tolerance = float(args.line_match_tolerance_px)
        supports = {
            "top": edge_support_ratio(line_mask, "horizontal", y1, x1, x2, tolerance),
            "bottom": edge_support_ratio(line_mask, "horizontal", y2, x1, x2, tolerance),
            "left": edge_support_ratio(line_mask, "vertical", x1, y1, y2, tolerance),
            "right": edge_support_ratio(line_mask, "vertical", x2, y1, y2, tolerance),
        }
        edge_count = sum(1 for value in supports.values() if value >= float(args.rect_min_edge_support))
        if edge_count < int(args.rect_min_edges):
            item["rejected"] = "edge_support"
            item["edge_supports"] = supports
            item["edge_count"] = edge_count
            continue

        corners = [
            corner_support(horizontal_mask, vertical_mask, x1, y1, args),
            corner_support(horizontal_mask, vertical_mask, x2, y1, args),
            corner_support(horizontal_mask, vertical_mask, x1, y2, args),
            corner_support(horizontal_mask, vertical_mask, x2, y2, args),
        ]
        corner_count = sum(1 for value in corners if value)
        if corner_count < int(args.rect_min_corners):
            item["rejected"] = "corner_support"
            item["edge_supports"] = supports
            item["edge_count"] = edge_count
            item["corner_count"] = corner_count
            continue

        quad = quad_from_contour(contour, float(args.box_epsilon))
        quad_transform = order_quad_points(quad)
        quad_polygon = polygon_order_from_transform_order(quad_transform)
        rectangularity = contour_rectangularity(contour, quad_polygon)

        candidate = make_quad_candidate(
            quad_transform,
            inverse_matrix,
            supports,
            corner_count,
            "contour_quad",
        )
        candidate["contour_area"] = contour_area
        candidate["fill_ratio"] = fill_ratio
        candidate["rectangularity"] = rectangularity
        candidate["edge_count"] = edge_count
        candidate["hierarchy_parent"] = parent
        candidate["hierarchy_child"] = child
        candidates.append(candidate)
        item["accepted"] = True
        item["edge_supports"] = supports
        item["edge_count"] = edge_count
        item["corner_count"] = corner_count
        item["quad_corners"] = quad_polygon.tolist()
        item["rectangularity"] = rectangularity

    return candidates, contour_mask, contour_info


def rectangle_component_candidates(line_mask, horizontal_mask, vertical_mask, inverse_matrix, args):
    component_mask = line_mask.copy()
    close_size = int(args.rect_component_close_kernel)
    if close_size > 1:
        kernel = np.ones((close_size, close_size), np.uint8)
        component_mask = cv2.morphologyEx(component_mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(component_mask, connectivity=8)
    min_area = float(args.box_min_area)
    max_area = float(args.box_max_area)
    image_h, image_w = line_mask.shape[:2]
    if max_area <= 0:
        max_area = float(image_w * image_h)
    max_area = min(max_area, float(image_w * image_h) * float(args.box_max_workspace_ratio))

    candidates = []
    components = []
    for label in range(1, count):
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])
        foreground_area = float(stats[label, cv2.CC_STAT_AREA])
        bbox_area = float(w * h)
        fill_ratio = foreground_area / float(max(1.0, bbox_area))

        component_info = {
            "bbox": {"x": x, "y": y, "w": w, "h": h},
            "foreground_area": foreground_area,
            "bbox_area": bbox_area,
            "fill_ratio": fill_ratio,
        }
        components.append(component_info)

        if bbox_area < min_area or bbox_area > max_area:
            component_info["rejected"] = "area"
            continue
        if w < int(args.box_min_width) or h < int(args.box_min_height):
            component_info["rejected"] = "size"
            continue

        aspect = float(w) / float(max(1, h))
        if aspect < float(args.box_min_aspect) or aspect > float(args.box_max_aspect):
            component_info["rejected"] = "aspect"
            continue
        if fill_ratio < float(args.rect_min_fill_ratio) or fill_ratio > float(args.rect_max_fill_ratio):
            component_info["rejected"] = "fill_ratio"
            continue

        x1 = float(x)
        y1 = float(y)
        x2 = float(x + w - 1)
        y2 = float(y + h - 1)
        tolerance = float(args.line_match_tolerance_px)
        supports = {
            "top": edge_support_ratio(horizontal_mask, "horizontal", y1, x1, x2, tolerance),
            "bottom": edge_support_ratio(horizontal_mask, "horizontal", y2, x1, x2, tolerance),
            "left": edge_support_ratio(vertical_mask, "vertical", x1, y1, y2, tolerance),
            "right": edge_support_ratio(vertical_mask, "vertical", x2, y1, y2, tolerance),
        }
        edge_count = sum(1 for value in supports.values() if value >= float(args.rect_min_edge_support))
        if edge_count < int(args.rect_min_edges):
            component_info["rejected"] = "edge_support"
            component_info["edge_supports"] = supports
            component_info["edge_count"] = edge_count
            continue

        corners = [
            corner_support(horizontal_mask, vertical_mask, x1, y1, args),
            corner_support(horizontal_mask, vertical_mask, x2, y1, args),
            corner_support(horizontal_mask, vertical_mask, x1, y2, args),
            corner_support(horizontal_mask, vertical_mask, x2, y2, args),
        ]
        corner_count = sum(1 for item in corners if item)
        if corner_count < int(args.rect_min_corners):
            component_info["rejected"] = "corner_support"
            component_info["edge_supports"] = supports
            component_info["edge_count"] = edge_count
            component_info["corner_count"] = corner_count
            continue

        candidate = make_rectangle_candidate(
            x1,
            x2,
            y1,
            y2,
            inverse_matrix,
            supports,
            corner_count,
            "component_rect",
        )
        candidate["foreground_area"] = foreground_area
        candidate["fill_ratio"] = fill_ratio
        candidate["edge_count"] = edge_count
        candidates.append(candidate)
        component_info["accepted"] = True
        component_info["edge_supports"] = supports
        component_info["edge_count"] = edge_count
        component_info["corner_count"] = corner_count

    return candidates, component_mask, components


def pair_line_rectangle_candidates(horizontal_mask, vertical_mask, horizontal_lines, vertical_lines, inverse_matrix, args):
    h, w = horizontal_mask.shape[:2]
    min_area = float(args.box_min_area)
    max_area = float(args.box_max_area)
    if max_area <= 0:
        max_area = float(w * h)
    max_area = min(max_area, float(w * h) * float(args.box_max_workspace_ratio))

    candidates = []
    match_tolerance = float(args.line_match_tolerance_px)
    min_edge_support = float(args.rect_min_edge_support)
    min_corners = int(args.rect_min_corners)

    for left_index, left in enumerate(vertical_lines):
        for right in vertical_lines[left_index + 1 :]:
            x1 = float(left["position"])
            x2 = float(right["position"])
            width = x2 - x1
            if width < float(args.box_min_width):
                continue

            for top_index, top in enumerate(horizontal_lines):
                for bottom in horizontal_lines[top_index + 1 :]:
                    y1 = float(top["position"])
                    y2 = float(bottom["position"])
                    height = y2 - y1
                    if height < float(args.box_min_height):
                        continue

                    area = width * height
                    if area < min_area or area > max_area:
                        continue

                    aspect = width / float(max(1.0, height))
                    if aspect < float(args.box_min_aspect) or aspect > float(args.box_max_aspect):
                        continue

                    supports = {
                        "top": edge_support_ratio(horizontal_mask, "horizontal", y1, x1, x2, match_tolerance),
                        "bottom": edge_support_ratio(horizontal_mask, "horizontal", y2, x1, x2, match_tolerance),
                        "left": edge_support_ratio(vertical_mask, "vertical", x1, y1, y2, match_tolerance),
                        "right": edge_support_ratio(vertical_mask, "vertical", x2, y1, y2, match_tolerance),
                    }
                    if min(supports.values()) < min_edge_support:
                        continue

                    corners = [
                        corner_support(horizontal_mask, vertical_mask, x1, y1, args),
                        corner_support(horizontal_mask, vertical_mask, x2, y1, args),
                        corner_support(horizontal_mask, vertical_mask, x1, y2, args),
                        corner_support(horizontal_mask, vertical_mask, x2, y2, args),
                    ]
                    corner_count = sum(1 for item in corners if item)
                    if corner_count < min_corners:
                        continue

                    candidates.append(
                        make_rectangle_candidate(
                            x1,
                            x2,
                            y1,
                            y2,
                            inverse_matrix,
                            supports,
                            corner_count,
                            "line_pair_rect",
                        )
                    )

    return candidates


def detect_rectangle_boxes(image, workspace_quad, config, args):
    warped, _, inverse_matrix, _ = workspace_warp(image, workspace_quad, config, args)
    line_mask, edges, dark_lines = build_frame_line_mask(warped, args)
    horizontal_mask = axis_line_mask(line_mask, "horizontal", args)
    vertical_mask = axis_line_mask(line_mask, "vertical", args)

    horizontal_segments = extract_line_segments(horizontal_mask, "horizontal", args)
    vertical_segments = extract_line_segments(vertical_mask, "vertical", args)
    horizontal_lines = cluster_line_positions(horizontal_segments, args)
    vertical_lines = cluster_line_positions(vertical_segments, args)

    candidates, contour_mask, contours = rectangle_contour_candidates(
        line_mask,
        horizontal_mask,
        vertical_mask,
        inverse_matrix,
        args,
    )
    component_mask = np.zeros(line_mask.shape, dtype=np.uint8)
    components = []
    if args.enable_component_fallback:
        component_candidates, component_mask, components = rectangle_component_candidates(
            line_mask,
            horizontal_mask,
            vertical_mask,
            inverse_matrix,
            args,
        )
        candidates.extend(component_candidates)

    if args.enable_line_pair_fallback:
        candidates.extend(
            pair_line_rectangle_candidates(
                horizontal_mask,
                vertical_mask,
                horizontal_lines,
                vertical_lines,
                inverse_matrix,
                args,
            )
        )

    candidates.sort(key=lambda item: (item["score"], item["corner_count"], item["edge_count"], item["area"]), reverse=True)
    kept = []
    for candidate in candidates:
        if any(should_suppress_box(candidate, item, args) for item in kept):
            continue
        kept.append(candidate)
        if len(kept) >= int(args.max_boxes):
            break

    filtered, size_consensus_info = apply_size_consensus_filter(kept, args)
    outer_box, outer_info = build_outer_box_from_children(filtered, inverse_matrix, args)

    if workspace_quad is None:
        debug = {
            "source_image": warped,
            "source_edges": edges,
            "source_line_mask": line_mask,
            "source_dark_line_mask": dark_lines,
            "source_contour_mask": contour_mask,
            "source_component_mask": component_mask,
            "source_horizontal_line_mask": horizontal_mask,
            "source_vertical_line_mask": vertical_mask,
        }
        analysis_space = "full_frame"
    else:
        debug = {
            "warped_image": warped,
            "warped_edges": edges,
            "warped_line_mask": line_mask,
            "warped_dark_line_mask": dark_lines,
            "warped_contour_mask": contour_mask,
            "warped_component_mask": component_mask,
            "warped_horizontal_line_mask": horizontal_mask,
            "warped_vertical_line_mask": vertical_mask,
            "source_line_mask": project_mask_to_source(line_mask, inverse_matrix, image.shape),
            "source_contour_mask": project_mask_to_source(contour_mask, inverse_matrix, image.shape),
        }
        analysis_space = "workspace_warp"
    detector_info = {
        "source": "contour_quad",
        "analysis_space": analysis_space,
        "analysis_size": {"width": int(warped.shape[1]), "height": int(warped.shape[0])},
        "workspace_padding_px": int(max(0, args.workspace_padding_px)) if workspace_quad is not None else 0,
        "candidate_count": len(candidates),
        "kept_count": len(kept),
        "filtered_count": len(filtered),
        "size_consensus": size_consensus_info,
        "outer_box": outer_info,
        "contour_count": len(contours),
        "accepted_contour_count": sum(1 for item in contours if item.get("accepted")),
        "component_count": max(0, len(components)),
        "accepted_component_count": sum(1 for item in components if item.get("accepted")),
        "horizontal_segment_count": len(horizontal_segments),
        "vertical_segment_count": len(vertical_segments),
        "horizontal_line_count": len(horizontal_lines),
        "vertical_line_count": len(vertical_lines),
        "horizontal_lines": [
            {
                "position": item["position"],
                "support_length": item["support_length"],
                "segment_count": item["segment_count"],
            }
            for item in horizontal_lines
        ],
        "vertical_lines": [
            {
                "position": item["position"],
                "support_length": item["support_length"],
                "segment_count": item["segment_count"],
            }
            for item in vertical_lines
        ],
        "contours": contours[:20],
        "components": components[:80],
    }
    return assign_box_labels(filtered, args), outer_box, debug, detector_info


def assign_box_labels(boxes, args):
    if not boxes:
        return []

    boxes_by_y = sorted(boxes, key=lambda item: (item["center_px"]["y"], item["center_px"]["x"]))
    heights = [float(item["bbox"]["h"]) for item in boxes_by_y]
    median_h = float(np.median(heights)) if heights else 1.0
    row_tolerance = max(float(args.row_tolerance_px), median_h * float(args.row_tolerance_ratio))

    rows = []
    for box in boxes_by_y:
        cy = float(box["center_px"]["y"])
        matched_row = None
        for row in rows:
            if abs(cy - row["mean_y"]) <= row_tolerance:
                matched_row = row
                break
        if matched_row is None:
            matched_row = {"mean_y": cy, "items": []}
            rows.append(matched_row)
        matched_row["items"].append(box)
        matched_row["mean_y"] = float(np.mean([item["center_px"]["y"] for item in matched_row["items"]]))

    rows.sort(key=lambda row: row["mean_y"])
    labeled = []
    for row_index, row in enumerate(rows):
        row["items"].sort(key=lambda item: item["center_px"]["x"])
        for col_index, box in enumerate(row["items"]):
            item = {key: value for key, value in box.items() if key != "bbox_tuple"}
            item["label"] = f"{row_name(row_index)}{col_index + 1}"
            item["row"] = row_index + 1
            item["column"] = col_index + 1
            labeled.append(item)
    labeled.sort(key=lambda item: (item["row"], item["column"]))
    return labeled


def draw_boxes_overlay(image, boxes, workspace_quad, workspace_info, args, outer_box=None):
    overlay = image.copy()
    blend = image.copy()
    if workspace_quad is not None:
        workspace_int = workspace_quad.reshape((-1, 2)).astype(np.int32)
        cv2.polylines(overlay, [workspace_int], True, (0, 255, 0), 4, cv2.LINE_AA)

    if outer_box is not None:
        outer_corners = np.array(outer_box["corners_px"], dtype=np.int32)
        cv2.polylines(overlay, [outer_corners], True, (255, 0, 255), 5, cv2.LINE_AA)
        center = outer_box["center_px"]
        draw_label(
            overlay,
            outer_box.get("label", "OUTER"),
            (int(center["x"]) - 42, int(center["y"]) + 16),
            fg=(255, 255, 255),
            bg=(110, 0, 110),
            scale=0.78,
        )

    for box in boxes:
        corners = np.array(box["corners_px"], dtype=np.int32)
        cv2.polylines(overlay, [corners], True, (0, 255, 255), 3, cv2.LINE_AA)
        cv2.fillPoly(blend, [corners], (0, 80, 80))
        center = box["center_px"]
        cv2.circle(overlay, (int(center["x"]), int(center["y"])), 5, (255, 0, 0), -1, cv2.LINE_AA)
        draw_label(
            overlay,
            box["label"],
            (int(center["x"]) - 18, int(center["y"]) + 12),
            fg=(255, 255, 255),
            bg=(20, 20, 20),
            scale=0.78,
        )

    cv2.addWeighted(blend, 0.16, overlay, 0.84, 0, overlay)
    header = (
        f"boxes={len(boxes)} outer={1 if outer_box is not None else 0} "
        f"workspace={workspace_info.get('source')} canny={args.canny_low}/{args.canny_high}"
    )
    draw_label(overlay, header, (20, 42), fg=(255, 255, 255), bg=(0, 80, 0), scale=0.75)
    return overlay


def analyze_boxes(image, config, args):
    workspace_quad = None
    workspace_info = {"detected": False, "source": "full_frame", "candidates": []}
    if args.boxes_use_workspace:
        workspace_quad, workspace_info = detect_workspace_quad(image, config, args)
        if workspace_quad is not None:
            workspace_quad = order_polygon_ccw(workspace_quad).reshape((-1, 1, 2))

    boxes, outer_box, debug, detector_info = detect_rectangle_boxes(image, workspace_quad, config, args)
    overlay = draw_boxes_overlay(image, boxes, workspace_quad, workspace_info, args, outer_box=outer_box)
    return {
        "ok": True,
        "timestamp": utc_now(),
        "mode": "boxes",
        "workspace": {
            **workspace_info,
            "ordered_corners_px": None if workspace_quad is None else order_quad_points(workspace_quad).tolist(),
        },
        "box_count": len(boxes),
        "outer_box": outer_box,
        "detector": detector_info,
        "boxes": boxes,
        "cells": boxes,
        "overlay": overlay,
        "debug": debug,
    }


def smooth_projection(values, window_size):
    window_size = max(3, int(window_size))
    if window_size % 2 == 0:
        window_size += 1
    kernel = np.ones(window_size, dtype=np.float32) / float(window_size)
    return np.convolve(values.astype(np.float32), kernel, mode="same")


def line_projection(mask, axis, rows, cols):
    h, w = mask.shape[:2]
    if axis == "x":
        kernel_h = max(20, h // max(3, rows * 2))
        line_mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((kernel_h, 3), np.uint8))
        return line_mask.sum(axis=0).astype(np.float32) / 255.0, line_mask

    kernel_w = max(20, w // max(3, cols * 2))
    line_mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, kernel_w), np.uint8))
    return line_mask.sum(axis=1).astype(np.float32) / 255.0, line_mask


def locate_grid_lines(mask, count, axis, rows, cols):
    h, w = mask.shape[:2]
    length = w if axis == "x" else h
    perpendicular = h if axis == "x" else w
    projection, line_mask = line_projection(mask, axis, rows, cols)
    projection = smooth_projection(projection, max(7, length // 180))
    expected = np.linspace(0, length - 1, count + 1)
    search_radius = max(10, int((length / max(1, count)) * 0.28))
    min_support = max(12.0, perpendicular * 0.08)

    lines = []
    for index, center in enumerate(expected):
        lo = max(0, int(round(center - search_radius)))
        hi = min(length - 1, int(round(center + search_radius)))
        if index == 0:
            lo = 0
            hi = max(hi, int(length * 0.12))
        elif index == count:
            lo = min(lo, int(length * 0.88))
            hi = length - 1

        window = projection[lo : hi + 1]
        if window.size == 0:
            position = float(center)
            peak = 0.0
            detected = False
        else:
            local_peak_index = int(np.argmax(window))
            peak = float(window[local_peak_index])
            peak_position = lo + local_peak_index
            cluster_threshold = max(min_support, peak * 0.55)
            cluster = np.where(window >= cluster_threshold)[0]
            if peak >= min_support and cluster.size > 0:
                cluster_positions = cluster + lo
                weights = projection[cluster_positions]
                position = float(np.average(cluster_positions, weights=np.maximum(weights, 1.0)))
                detected = True
            elif peak >= min_support:
                position = float(peak_position)
                detected = True
            else:
                position = float(center)
                detected = False

        lines.append(
            {
                "index": index,
                "position": position,
                "expected": float(center),
                "detected": detected,
                "peak_support": peak,
            }
        )

    positions = np.array([item["position"] for item in lines], dtype=np.float32)
    positions = np.maximum.accumulate(positions)
    for index, position in enumerate(positions):
        lines[index]["position"] = float(position)

    return lines, line_mask


def build_grid_cells(v_lines, h_lines, rows, cols, inverse_matrix):
    cells = []
    for row in range(rows):
        for col in range(cols):
            x1 = float(v_lines[col]["position"])
            x2 = float(v_lines[col + 1]["position"])
            y1 = float(h_lines[row]["position"])
            y2 = float(h_lines[row + 1]["position"])
            warp_corners = np.float32([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
            source_corners = cv2.perspectiveTransform(warp_corners.reshape((-1, 1, 2)), inverse_matrix).reshape((-1, 2))
            center_warp = np.float32([[[0.5 * (x1 + x2), 0.5 * (y1 + y2)]]])
            center_source = cv2.perspectiveTransform(center_warp, inverse_matrix).reshape((2,))
            label = f"{row_name(row)}{col + 1}"
            cells.append(
                {
                    "label": label,
                    "row": row + 1,
                    "column": col + 1,
                    "warp_rect": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                    "corners_px": source_corners.tolist(),
                    "center_px": {"x": float(center_source[0]), "y": float(center_source[1])},
                    "line_detection": {
                        "left": bool(v_lines[col]["detected"]),
                        "right": bool(v_lines[col + 1]["detected"]),
                        "top": bool(h_lines[row]["detected"]),
                        "bottom": bool(h_lines[row + 1]["detected"]),
                    },
                }
            )
    return cells


def draw_grid_overlay(image, cells, workspace_quad, workspace_info, rows, cols, threshold):
    overlay = image.copy()
    blend = image.copy()
    workspace_int = workspace_quad.reshape((-1, 2)).astype(np.int32)
    cv2.polylines(overlay, [workspace_int], True, (0, 255, 0), 4, cv2.LINE_AA)

    for cell in cells:
        corners = np.array(cell["corners_px"], dtype=np.int32)
        cv2.polylines(overlay, [corners], True, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.fillPoly(blend, [corners], (0, 80, 80))
        center = cell["center_px"]
        label_origin = (int(center["x"]) - 18, int(center["y"]) + 10)
        draw_label(overlay, cell["label"], label_origin, fg=(255, 255, 255), bg=(20, 20, 20), scale=0.7)

    cv2.addWeighted(blend, 0.18, overlay, 0.82, 0, overlay)
    header = (
        f"grid={rows}x{cols} cells={len(cells)} workspace={workspace_info.get('source')} "
        f"black_threshold={threshold}"
    )
    draw_label(overlay, header, (20, 42), fg=(255, 255, 255), bg=(0, 80, 0), scale=0.75)
    return overlay


def analyze_grid(image, config, args):
    workspace_quad, workspace_info = detect_workspace_quad(image, config, args)
    overlay = image.copy()
    if workspace_quad is None:
        draw_label(overlay, "workspace not detected", (20, 42), fg=(255, 255, 255), bg=(0, 0, 180), scale=0.85)
        return {
            "ok": False,
            "timestamp": utc_now(),
            "workspace": workspace_info,
            "cells": [],
            "overlay": overlay,
            "debug": {},
            "error": "workspace not detected",
        }

    ordered = order_quad_points(workspace_quad)
    width_mm = float(config.get("workspace_width_mm", 600.0))
    height_mm = float(config.get("workspace_height_mm", 400.0))
    warp_w = int(args.warp_width)
    warp_h = int(round(warp_w * height_mm / max(1.0, width_mm)))
    warp_h = max(int(args.rows) * 120, warp_h)

    target = np.float32([[0.0, 0.0], [warp_w - 1.0, 0.0], [0.0, warp_h - 1.0], [warp_w - 1.0, warp_h - 1.0]])
    matrix = cv2.getPerspectiveTransform(ordered, target)
    inverse_matrix = cv2.getPerspectiveTransform(target, ordered)
    warped = cv2.warpPerspective(image, matrix, (warp_w, warp_h))
    mask = build_dark_line_mask(warped, args.black_threshold)
    v_lines, v_mask = locate_grid_lines(mask, int(args.cols), "x", int(args.rows), int(args.cols))
    h_lines, h_mask = locate_grid_lines(mask, int(args.rows), "y", int(args.rows), int(args.cols))
    cells = build_grid_cells(v_lines, h_lines, int(args.rows), int(args.cols), inverse_matrix)
    overlay = draw_grid_overlay(image, cells, ordered.reshape((-1, 1, 2)), workspace_info, int(args.rows), int(args.cols), int(args.black_threshold))

    return {
        "ok": True,
        "timestamp": utc_now(),
        "workspace": {
            **workspace_info,
            "ordered_corners_px": ordered.tolist(),
            "warp_size": {"width": warp_w, "height": warp_h},
        },
        "grid": {
            "rows": int(args.rows),
            "cols": int(args.cols),
            "cell_count": len(cells),
            "vertical_lines": v_lines,
            "horizontal_lines": h_lines,
        },
        "cells": cells,
        "overlay": overlay,
        "debug": {
            "warped": warped,
            "dark_mask": mask,
            "vertical_line_mask": v_mask,
            "horizontal_line_mask": h_mask,
        },
    }


def analyze_frame(image, config, args):
    if args.mode == "grid":
        return analyze_grid(image, config, args)
    return analyze_boxes(image, config, args)


def resize_for_display(image, max_width, max_height):
    h, w = image.shape[:2]
    scale = min(float(max_width) / float(w), float(max_height) / float(h), 1.0)
    if scale >= 0.999:
        return image
    return cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def write_outputs(result, output_dir, prefix):
    output_dir.mkdir(parents=True, exist_ok=True)
    overlay_path = output_dir / f"{prefix}_overlay.png"
    report_path = output_dir / f"{prefix}_report.json"
    cv2.imwrite(str(overlay_path), result["overlay"])

    debug_paths = {}
    for name, image in result.get("debug", {}).items():
        path = output_dir / f"{prefix}_{name}.png"
        cv2.imwrite(str(path), image)
        debug_paths[name] = str(path)

    report = {key: value for key, value in result.items() if key not in {"overlay", "debug"}}
    report["outputs"] = {"overlay": str(overlay_path), "report": str(report_path), **debug_paths}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=json_safe), encoding="utf-8")
    return report["outputs"]


def run_image_mode(args, config):
    image = cv2.imread(str(args.image))
    if image is None:
        raise RuntimeError(f"Cannot read image: {args.image}")
    result = analyze_frame(image, config, args)
    outputs = write_outputs(result, Path(args.output_dir), str(args.output_prefix or "image"))
    if not args.no_window:
        cv2.imshow(args.window_name, resize_for_display(result["overlay"], args.display_width, args.display_height))
        cv2.waitKey(0)
        cv2.destroyWindow(args.window_name)
    print(json.dumps({key: value for key, value in result.items() if key not in {"overlay", "debug"}}, ensure_ascii=False, indent=2, default=json_safe))
    print(json.dumps({"outputs": outputs}, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def run_live_mode(args, config):
    stream = MvsCameraStream(config, frame_timeout_ms=args.frame_timeout_ms)
    stream.open()
    output_dir = Path(args.output_dir)
    last_result = None
    try:
        for _ in range(max(0, int(args.warmup_frames))):
            stream.grab()
            time.sleep(0.05)

        if not args.no_window:
            cv2.namedWindow(args.window_name, cv2.WINDOW_NORMAL)

        while True:
            frame = stream.grab()
            result = analyze_frame(frame, config, args)
            last_result = result

            if args.once:
                outputs = write_outputs(result, output_dir, str(args.output_prefix or "live"))
                print(json.dumps({key: value for key, value in result.items() if key not in {"overlay", "debug"}}, ensure_ascii=False, indent=2, default=json_safe))
                print(json.dumps({"outputs": outputs}, ensure_ascii=False, indent=2))
                return 0 if result.get("ok") else 1

            display = resize_for_display(result["overlay"], args.display_width, args.display_height)
            draw_label(display, "q/ESC quit  s snapshot  +/- dark  [/]=canny low  {/}=canny high", (20, display.shape[0] - 20), scale=0.65)
            if not args.no_window:
                cv2.imshow(args.window_name, display)
                key = cv2.waitKey(1) & 0xFF
            else:
                time.sleep(0.05)
                key = -1

            if key in (27, ord("q")):
                break
            if key == ord("s"):
                prefix = datetime.now().strftime("snapshot_%Y%m%d_%H%M%S")
                outputs = write_outputs(result, output_dir, prefix)
                print(json.dumps({"saved": outputs}, ensure_ascii=False, indent=2))
            if key in (ord("+"), ord("=")):
                args.black_threshold = min(255, int(args.black_threshold) + 5)
            if key in (ord("-"), ord("_")):
                args.black_threshold = max(1, int(args.black_threshold) - 5)
            if key == ord("["):
                args.canny_low = max(1, int(args.canny_low) - 5)
            if key == ord("]"):
                args.canny_low = min(254, int(args.canny_low) + 5)
            if key == ord("{"):
                args.canny_high = max(int(args.canny_low) + 1, int(args.canny_high) - 5)
            if key == ord("}"):
                args.canny_high = min(255, int(args.canny_high) + 5)
    finally:
        stream.close()
        if not args.no_window:
            cv2.destroyAllWindows()

    if last_result is not None:
        outputs = write_outputs(last_result, output_dir, "last")
        print(json.dumps({"last_outputs": outputs}, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Live calibration viewer for flexible black-line workspace boxes.")
    parser.add_argument("--camera-id", default="Cam1")
    parser.add_argument("--mode", choices=["boxes", "grid"], default="boxes")
    parser.add_argument("--rows", type=int, default=3)
    parser.add_argument("--cols", type=int, default=3)
    parser.add_argument("--image", type=Path, help="Analyze a saved image instead of opening the camera.")
    parser.add_argument("--once", action="store_true", help="Capture one live frame, save outputs, then exit.")
    parser.add_argument("--no-window", action="store_true", help="Do not open an OpenCV display window.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--output-prefix", help="Filename prefix for saved report, overlay, and debug images.")
    parser.add_argument("--window-name", default="ARC Calibration Grid")
    parser.add_argument("--display-width", type=int, default=1280)
    parser.add_argument("--display-height", type=int, default=960)
    parser.add_argument("--warp-width", type=int, default=900)
    parser.add_argument("--workspace-padding-px", type=int, default=40)
    parser.add_argument("--black-threshold", type=int, default=90)
    parser.add_argument("--workspace-min-area", type=float, default=None)
    parser.add_argument("--workspace-epsilon", type=float, default=0.02)
    parser.add_argument("--canny-low", type=int, default=40)
    parser.add_argument("--canny-high", type=int, default=150)
    parser.add_argument("--edge-dilate", type=int, default=5)
    parser.add_argument("--box-min-area", type=float, default=15000.0)
    parser.add_argument("--box-max-area", type=float, default=0.0)
    parser.add_argument("--box-max-workspace-ratio", type=float, default=1.02)
    parser.add_argument("--box-min-width", type=int, default=60)
    parser.add_argument("--box-min-height", type=int, default=60)
    parser.add_argument("--box-min-aspect", type=float, default=0.25)
    parser.add_argument("--box-max-aspect", type=float, default=4.0)
    parser.add_argument("--box-min-rectangularity", type=float, default=0.55)
    parser.add_argument("--box-epsilon", type=float, default=0.03)
    parser.add_argument("--box-nms-iou", type=float, default=0.25)
    parser.add_argument("--box-nms-min-area-ratio", type=float, default=0.55)
    parser.add_argument("--box-min-border-edges", type=int, default=1)
    parser.add_argument("--max-boxes", type=int, default=40)
    parser.add_argument("--row-tolerance-px", type=float, default=45.0)
    parser.add_argument("--row-tolerance-ratio", type=float, default=0.45)
    parser.add_argument("--cell-bright-threshold", type=int, default=105)
    parser.add_argument("--cell-max-saturation", type=int, default=80)
    parser.add_argument("--cell-open-kernel", type=int, default=5)
    parser.add_argument("--cell-close-kernel", type=int, default=13)
    parser.add_argument("--cell-border-probe", type=int, default=24)
    parser.add_argument("--cell-min-border-dark-ratio", type=float, default=0.08)
    parser.add_argument("--cell-min-border-edges", type=int, default=2)
    parser.add_argument("--line-mask-open", type=int, default=1)
    parser.add_argument("--line-min-length-px", type=int, default=80)
    parser.add_argument("--line-min-length-ratio", type=float, default=0.10)
    parser.add_argument("--line-close-gap-px", type=int, default=45)
    parser.add_argument("--line-max-thickness-px", type=int, default=45)
    parser.add_argument("--line-position-tolerance-px", type=float, default=16.0)
    parser.add_argument("--line-segment-gap-px", type=float, default=60.0)
    parser.add_argument("--line-match-tolerance-px", type=float, default=12.0)
    parser.add_argument("--line-corner-span-px", type=float, default=42.0)
    parser.add_argument("--rect-min-edge-support", type=float, default=0.42)
    parser.add_argument("--rect-min-corner-support", type=float, default=0.20)
    parser.add_argument("--rect-min-corners", type=int, default=3)
    parser.add_argument("--rect-min-edges", type=int, default=4)
    parser.add_argument("--rect-contour-close-kernel", type=int, default=3)
    parser.add_argument("--rect-reject-border-px", type=int, default=3)
    parser.add_argument("--rect-component-close-kernel", type=int, default=9)
    parser.add_argument("--rect-min-fill-ratio", type=float, default=0.01)
    parser.add_argument("--rect-max-fill-ratio", type=float, default=0.45)
    parser.add_argument("--enable-component-fallback", action="store_true")
    parser.add_argument("--enable-line-pair-fallback", action="store_true")
    parser.add_argument("--disable-size-consensus", action="store_true")
    parser.add_argument("--size-consensus-min-count", type=int, default=4)
    parser.add_argument("--size-consensus-width-ratio", type=float, default=0.22)
    parser.add_argument("--size-consensus-height-ratio", type=float, default=0.22)
    parser.add_argument("--size-consensus-container-min-children", type=int, default=2)
    parser.add_argument("--size-consensus-container-min-area-ratio", type=float, default=2.2)
    parser.add_argument("--disable-outer-box", action="store_true")
    parser.add_argument("--outer-min-children", type=int, default=4)
    parser.add_argument("--outer-padding-px", type=float, default=0.0)
    parser.add_argument("--outer-label", default="OUTER")
    parser.add_argument("--boxes-use-workspace", action="store_true", help="Crop/warp boxes mode to the detected workspace.")
    parser.add_argument("--fixed-workspace", action="store_true", help="Use fixed_workspace_corners_px from config.py.")
    parser.add_argument("--no-fixed-fallback", action="store_true", help="Disable fallback to fixed_workspace_corners_px.")
    parser.add_argument("--workspace-line-fallback", action="store_true", help="Use frame-line rectangle fallback when Canny workspace detection fails.")
    parser.add_argument("--frame-timeout-ms", type=int, default=None)
    parser.add_argument("--warmup-frames", type=int, default=3)
    return parser


def main():
    args = build_parser().parse_args()
    if args.rows <= 0 or args.cols <= 0:
        raise SystemExit("--rows and --cols must be positive")
    config = CAMERA_CONFIG.get(args.camera_id)
    if not config:
        raise SystemExit(f"Camera '{args.camera_id}' not found in config.py")

    if args.image:
        return run_image_mode(args, config)
    return run_live_mode(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
