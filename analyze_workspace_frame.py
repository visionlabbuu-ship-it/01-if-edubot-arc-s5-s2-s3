import json
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
ARTIFACTS_DIR = ROOT / "artifacts" / "diagnostics"
INPUT_IMAGE = ROOT / "artifacts" / "captures" / "workspace_frame_capture.png"
OVERLAY_IMAGE = ARTIFACTS_DIR / "workspace_debug_overlay.png"
EDGES_IMAGE = ARTIFACTS_DIR / "workspace_debug_edges.png"
THRESH_IMAGE = ARTIFACTS_DIR / "workspace_debug_thresh.png"
REPORT_JSON = ARTIFACTS_DIR / "workspace_debug_report.json"

WORKSPACE_WIDTH_MM = 600.0
WORKSPACE_HEIGHT_MM = 400.0

CALIB_CAMERA = np.float32([
    [1026.0, 127.0],
    [2330.0, 123.0],
    [1032.0, 899.0],
    [2334.0, 901.0],
])
CALIB_ROBOT = np.float32([
    [139.992, -190.566],
    [128.977, 199.616],
    [368.753, -184.948],
    [362.773, 197.274],
])


def build_workspace_target_points():
    return np.float32([
        [0.0, 0.0],
        [0.0, WORKSPACE_HEIGHT_MM],
        [WORKSPACE_WIDTH_MM, 0.0],
        [WORKSPACE_WIDTH_MM, WORKSPACE_HEIGHT_MM],
    ])


def order_quad_points(points):
    pts = np.array(points, dtype=np.float32).reshape((4, 2))
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).reshape((4,))
    top_left = pts[np.argmin(sums)]
    bottom_right = pts[np.argmax(sums)]
    top_right = pts[np.argmin(diffs)]
    bottom_left = pts[np.argmax(diffs)]
    return np.float32([top_left, top_right, bottom_left, bottom_right])


def classify_block(x_mm, y_mm):
    if not (0.0 <= x_mm <= WORKSPACE_WIDTH_MM and 0.0 <= y_mm <= WORKSPACE_HEIGHT_MM):
        return None
    if x_mm < 200.0:
        col = 1
    elif x_mm < 400.0:
        col = 2
    else:
        col = 3
    row = 1 if y_mm >= 200.0 else 2
    return f"Block {row},{col}"


def estimate_color(hsv_img, contour, shape):
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)
    mean_h, mean_s, mean_v, _ = cv2.mean(hsv_img, mask=mask)
    if mean_s < 25 or mean_v < 25:
        return "unknown"
    if mean_h < 15 or mean_h > 160:
        return "red"
    if 40 < mean_h < 85:
        return "green"
    if 90 < mean_h < 140:
        return "blue"
    return "unknown"


def touches_edge(x, y, w, h, img_w, img_h, margin=5):
    return x <= margin or y <= margin or (x + w) >= (img_w - margin) or (y + h) >= (img_h - margin)


def main():
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    image = cv2.imread(str(INPUT_IMAGE))
    if image is None:
        raise SystemExit(f"missing image: {INPUT_IMAGE}")

    overlay = image.copy()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 40, 150)
    edge_kernel = np.ones((5, 5), np.uint8)
    edges = cv2.dilate(edges, edge_kernel, iterations=1)
    cv2.imwrite(str(EDGES_IMAGE), edges)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    workspace_contour = None
    max_area = 0.0
    workspace_candidates = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        perimeter = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * perimeter, True)
        workspace_candidates.append({
            "area": float(area),
            "points": approx.reshape((-1, 2)).tolist() if len(approx) == 4 else None,
            "quad": len(approx) == 4,
        })
        if area > 200000 and len(approx) == 4 and area > max_area:
            max_area = area
            workspace_contour = approx

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    if workspace_contour is not None:
        workspace_mask = np.zeros(saturation.shape, dtype=np.uint8)
        cv2.drawContours(workspace_mask, [workspace_contour], -1, 255, -1)
        saturation = cv2.bitwise_and(saturation, saturation, mask=workspace_mask)
    blurred_s = cv2.GaussianBlur(saturation, (5, 5), 0)
    _, thresh = cv2.threshold(blurred_s, 40, 255, cv2.THRESH_BINARY)
    open_kernel = np.ones((5, 5), np.uint8)
    close_kernel = np.ones((7, 7), np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, open_kernel)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, close_kernel)
    cv2.imwrite(str(THRESH_IMAGE), thresh)

    calibration_matrix = cv2.getPerspectiveTransform(CALIB_CAMERA, CALIB_ROBOT)
    report_matrix = None
    ordered_workspace = None
    if workspace_contour is not None:
        ordered_workspace = order_quad_points(workspace_contour)
        report_matrix = cv2.getPerspectiveTransform(ordered_workspace, build_workspace_target_points())
        cv2.drawContours(overlay, [workspace_contour], -1, (0, 255, 0), 4)
        for idx, (px, py) in enumerate(ordered_workspace.astype(int), start=1):
            cv2.circle(overlay, (int(px), int(py)), 12, (0, 0, 255), -1)
            cv2.putText(overlay, str(idx), (int(px) + 10, int(py) - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)

    obj_contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    objects = []
    img_h, img_w = thresh.shape
    workspace_area = cv2.contourArea(workspace_contour) if workspace_contour is not None else 0.0
    for i, cnt in enumerate(obj_contours, start=1):
        area = cv2.contourArea(cnt)
        if area <= 1500:
            continue
        if workspace_area > 0 and area >= workspace_area * 0.2:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        if touches_edge(x, y, w, h, img_w, img_h):
            continue
        cx = x + (w // 2)
        cy = y + (h // 2)
        inside_contour = True
        if workspace_contour is not None:
            inside_contour = cv2.pointPolygonTest(workspace_contour, (float(cx), float(cy)), False) >= 0
        raw_pt = cv2.perspectiveTransform(np.array([[[float(cx), float(cy)]]], dtype=np.float32), calibration_matrix)
        raw_x = float(raw_pt[0][0][0])
        raw_y = float(raw_pt[0][0][1])
        ws_x = None
        ws_y = None
        block = None
        if report_matrix is not None:
            ws_pt = cv2.perspectiveTransform(np.array([[[float(cx), float(cy)]]], dtype=np.float32), report_matrix)
            ws_x = float(ws_pt[0][0][0])
            ws_y = float(ws_pt[0][0][1])
            block = classify_block(ws_x, ws_y)
        color = estimate_color(hsv, cnt, thresh.shape)
        objects.append({
            "id": f"obj{i}",
            "color": color,
            "area": float(area),
            "bbox": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)},
            "center_px": {"x": int(cx), "y": int(cy)},
            "inside_workspace_contour": bool(inside_contour),
            "raw_robot_plane": {"x": raw_x, "y": raw_y},
            "workspace_report": None if ws_x is None else {"x": ws_x, "y": ws_y, "block": block},
        })

        box_color = (0, 255, 255) if inside_contour else (0, 0, 255)
        cv2.rectangle(overlay, (x, y), (x + w, y + h), box_color, 3)
        cv2.circle(overlay, (cx, cy), 6, (255, 0, 0), -1)
        label1 = f"{objects[-1]['id']} {color}"
        label2 = f"raw=({raw_x:.0f},{raw_y:.0f})"
        label3 = f"ws={('-' if ws_x is None else f'{ws_x:.0f},{ws_y:.0f}')}"
        label4 = f"block={block}"
        cv2.putText(overlay, label1, (x, y - 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.putText(overlay, label2, (x, y - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.putText(overlay, label3, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.putText(overlay, label4, (x, y + h + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 220, 0), 2)

    if report_matrix is not None and ordered_workspace is not None:
        for gx in [200.0, 400.0]:
            line = np.array([[[gx, 0.0]], [[gx, WORKSPACE_HEIGHT_MM]]], dtype=np.float32)
            inv = cv2.perspectiveTransform(line, np.linalg.inv(report_matrix))
            p1 = tuple(inv[0][0].astype(int))
            p2 = tuple(inv[1][0].astype(int))
            cv2.line(overlay, p1, p2, (255, 0, 255), 2)
        for gy in [200.0]:
            line = np.array([[[0.0, gy]], [[WORKSPACE_WIDTH_MM, gy]]], dtype=np.float32)
            inv = cv2.perspectiveTransform(line, np.linalg.inv(report_matrix))
            p1 = tuple(inv[0][0].astype(int))
            p2 = tuple(inv[1][0].astype(int))
            cv2.line(overlay, p1, p2, (255, 0, 255), 2)

    cv2.imwrite(str(OVERLAY_IMAGE), overlay)

    report = {
        "input_image": str(INPUT_IMAGE),
        "workspace_detected": workspace_contour is not None,
        "workspace_contour_area": float(max_area),
        "workspace_candidates": workspace_candidates[:20],
        "ordered_workspace_corners_px": None if ordered_workspace is None else ordered_workspace.tolist(),
        "objects": objects,
        "outputs": {
            "overlay_image": str(OVERLAY_IMAGE),
            "edges_image": str(EDGES_IMAGE),
            "thresh_image": str(THRESH_IMAGE),
        },
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(str(OVERLAY_IMAGE))
    print(str(EDGES_IMAGE))
    print(str(THRESH_IMAGE))
    print(str(REPORT_JSON))


if __name__ == "__main__":
    main()
