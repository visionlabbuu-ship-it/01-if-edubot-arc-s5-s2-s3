import sys
import time
from ctypes import POINTER, byref, cast, c_ubyte, memmove, memset, sizeof

import cv2
import numpy as np


PROJECT_ROOT = r"D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan"
WORKSPACE_WIDTH_MM = 600.0
WORKSPACE_HEIGHT_MM = 400.0

PTS_CAMERA = np.float32([
    [1026, 127],
    [2330, 123],
    [1032, 899],
    [2334, 901],
])

PTS_WORKSPACE = np.float32([
    [0.0, WORKSPACE_HEIGHT_MM],
    [WORKSPACE_WIDTH_MM, WORKSPACE_HEIGHT_MM],
    [0.0, 0.0],
    [WORKSPACE_WIDTH_MM, 0.0],
])


def workspace_bounds_contains(x_mm, y_mm):
    return 0.0 <= float(x_mm) <= WORKSPACE_WIDTH_MM and 0.0 <= float(y_mm) <= WORKSPACE_HEIGHT_MM


def normalize_workspace_pose(x_mm, y_mm):
    # Current hypothesis from live observations:
    # workspace left/right is mirrored relative to the canonical layout.
    return WORKSPACE_WIDTH_MM - float(x_mm), float(y_mm)


def classify_workspace_block(x_mm, y_mm):
    if not workspace_bounds_contains(x_mm, y_mm):
        return None
    if x_mm < 200.0:
        column = 1
    elif x_mm < 400.0:
        column = 2
    else:
        column = 3
    row = 1 if y_mm >= 200.0 else 2
    return f"Block {row},{column}"


def touches_image_edge(x, y, width, height, image_width, image_height, margin):
    return (
        x <= margin
        or y <= margin
        or (x + width) >= (image_width - margin)
        or (y + height) >= (image_height - margin)
    )


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


def build_hypotheses():
    return {
        "identity": lambda u, v: (u, v),
        "flip_x": lambda u, v: (1.0 - u, v),
        "flip_y": lambda u, v: (u, 1.0 - v),
        "rot_180": lambda u, v: (1.0 - u, 1.0 - v),
        "swap_xy": lambda u, v: (v, u),
        "rot_90_cw": lambda u, v: (v, 1.0 - u),
        "rot_90_ccw": lambda u, v: (1.0 - v, u),
        "swap_flip": lambda u, v: (1.0 - v, 1.0 - u),
    }


def normalized_to_workspace_mm(u, v):
    return float(u) * WORKSPACE_WIDTH_MM, float(v) * WORKSPACE_HEIGHT_MM


def clamp01(value):
    return max(0.0, min(1.0, float(value)))


def main():
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)

    from MvImport.MvCameraControl_class import MV_ACCESS_Exclusive, MV_CC_DEVICE_INFO, MV_CC_DEVICE_INFO_LIST, MV_FRAME_OUT, MvCamera

    workspace_matrix = cv2.getPerspectiveTransform(PTS_CAMERA, PTS_WORKSPACE)

    device_list = MV_CC_DEVICE_INFO_LIST()
    ret = MvCamera.MV_CC_EnumDevices(0x00000004, device_list)
    if ret != 0 or device_list.nDeviceNum == 0:
        print("camera_error: no camera detected")
        return 1

    cam = MvCamera()
    device_info = cast(device_list.pDeviceInfo[0], POINTER(MV_CC_DEVICE_INFO)).contents
    cam.MV_CC_CreateHandle(device_info)
    cam.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
    cam.MV_CC_StopGrabbing()
    cam.MV_CC_SetEnumValue("PixelFormat", 17301512)
    cam.MV_CC_SetEnumValue("BalanceWhiteAuto", 2)
    cam.MV_CC_SetBoolValue("AcquisitionFrameRateEnable", True)
    cam.MV_CC_SetBoolValue("GammaEnable", True)
    cam.MV_CC_SetFloatValue("ExposureTime", 52000.0)
    cam.MV_CC_SetFloatValue("Gain", 10.0)
    cam.MV_CC_SetFloatValue("Gamma", 0.7)
    cam.MV_CC_SetFloatValue("AcquisitionFrameRate", 60.0)
    cam.MV_CC_SetIntValue("Sharpness", 50)
    cam.MV_CC_StartGrabbing()

    frame_out = MV_FRAME_OUT()
    memset(byref(frame_out), 0, sizeof(frame_out))

    try:
        ret = None
        for _ in range(10):
            time.sleep(0.2)
            ret = cam.MV_CC_GetImageBuffer(frame_out, 2000)
            if ret == 0:
                break
        if ret != 0:
            print(f"camera_error: frame read failed code={ret}")
            return 1

        data = (c_ubyte * frame_out.stFrameInfo.nFrameLen)()
        memmove(data, frame_out.pBufAddr, frame_out.stFrameInfo.nFrameLen)
        image = np.frombuffer(data, dtype=np.uint8)
        height = frame_out.stFrameInfo.nHeight
        width = frame_out.stFrameInfo.nWidth
        image = image.reshape((height, width))
    finally:
        try:
            cam.MV_CC_FreeImageBuffer(frame_out)
        except Exception:
            pass

    display_img = cv2.cvtColor(image, cv2.COLOR_BayerGB2BGR)

    gray = cv2.cvtColor(display_img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 40, 150)
    kernel = np.ones((5, 5), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    workspace_contour = None
    max_area = 0.0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area <= 200000:
            continue
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) == 4 and area > max_area:
            workspace_contour = approx
            max_area = area

    min_x = 0.0
    max_x = WORKSPACE_WIDTH_MM
    min_y = 0.0
    max_y = WORKSPACE_HEIGHT_MM
    if workspace_contour is not None:
        contour_points = np.array(workspace_contour, dtype=np.float32).reshape((-1, 1, 2))
        contour_mm = cv2.perspectiveTransform(contour_points, workspace_matrix).reshape((-1, 2))
        min_x = float(np.min(contour_mm[:, 0]))
        max_x = float(np.max(contour_mm[:, 0]))
        min_y = float(np.min(contour_mm[:, 1]))
        max_y = float(np.max(contour_mm[:, 1]))
        print(f"workspace_detected: true")
        print(f"workspace_est_mm: width={max_x - min_x:.1f} height={max_y - min_y:.1f}")
        print(f"workspace_bounds_mm: x=({min_x:.1f},{max_x:.1f}) y=({min_y:.1f},{max_y:.1f})")
    else:
        print("workspace_detected: false")

    hsv_img = cv2.cvtColor(display_img, cv2.COLOR_BGR2HSV)
    saturation = hsv_img[:, :, 1]
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
    obj_contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    detections = []
    workspace_area = cv2.contourArea(workspace_contour) if workspace_contour is not None else 0.0
    for contour in obj_contours:
        area = cv2.contourArea(contour)
        if area <= 1500:
            continue
        if workspace_area > 0 and area >= workspace_area * 0.2:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if touches_image_edge(x, y, w, h, width, height, 20):
            continue
        cx = x + (w // 2)
        cy = y + (h // 2)

        pt_px = np.array([[[cx, cy]]], dtype=np.float32)
        pt_workspace = cv2.perspectiveTransform(pt_px, workspace_matrix)
        raw_workspace_x = float(pt_workspace[0][0][0])
        raw_workspace_y = float(pt_workspace[0][0][1])
        workspace_x, workspace_y = normalize_workspace_pose(raw_workspace_x, raw_workspace_y)

        inside_contour = True
        if workspace_contour is not None:
            inside_contour = cv2.pointPolygonTest(workspace_contour, (float(cx), float(cy)), False) >= 0
        if not inside_contour:
            continue

        norm_u = clamp01((raw_workspace_x - min_x) / max(1.0, max_x - min_x))
        norm_v = clamp01((raw_workspace_y - min_y) / max(1.0, max_y - min_y))

        detections.append({
            "area": float(area),
            "center_px": (cx, cy),
            "raw_workspace_x": raw_workspace_x,
            "raw_workspace_y": raw_workspace_y,
            "norm_u": norm_u,
            "norm_v": norm_v,
            "workspace_x": workspace_x,
            "workspace_y": workspace_y,
            "block": classify_workspace_block(workspace_x, workspace_y),
            "color": estimate_color(hsv_img, contour, thresh.shape),
            "inside_contour": inside_contour,
        })

    detections.sort(key=lambda item: (item["workspace_y"] * -1.0, item["workspace_x"]))

    hypotheses = build_hypotheses()
    hypothesis_results = {}
    for name, transform in hypotheses.items():
        transformed = []
        for item in detections:
            u2, v2 = transform(item["norm_u"], item["norm_v"])
            x2, y2 = normalized_to_workspace_mm(u2, v2)
            transformed.append(
                {
                    "color": item["color"],
                    "block": classify_workspace_block(x2, y2),
                    "x": x2,
                    "y": y2,
                }
            )
        hypothesis_results[name] = transformed

    print(f"object_count: {len(detections)}")
    print("detected_blocks:", ", ".join(sorted({item["block"] for item in detections if item["block"]})) or "-")
    for idx, item in enumerate(detections, start=1):
        print(
            f"obj{idx}: block={item['block']} color={item['color']} "
            f"workspace=({item['workspace_x']:.1f},{item['workspace_y']:.1f}) "
            f"raw=({item['raw_workspace_x']:.1f},{item['raw_workspace_y']:.1f}) "
            f"norm=({item['norm_u']:.3f},{item['norm_v']:.3f}) "
            f"px={item['center_px']} area={item['area']:.1f} "
            f"inside_contour={item['inside_contour']}"
        )

    print("orientation_hypotheses:")
    for name, transformed in hypothesis_results.items():
        blue_blocks = sorted(
            [entry["block"] for entry in transformed if entry["color"] == "blue" and entry["block"] is not None]
        )
        all_blocks = sorted({entry["block"] for entry in transformed if entry["block"] is not None})
        print(f"- {name}: blue_blocks={blue_blocks} blocks={all_blocks}")

    cam.MV_CC_StopGrabbing()
    cam.MV_CC_CloseDevice()
    cam.MV_CC_DestroyHandle()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
