import importlib
import math
import os
import sys
from copy import deepcopy
from ctypes import POINTER, byref, cast, c_ubyte, memmove, memset, sizeof
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    ARM_FRAME_TRANSFORMS,
    ARM_REACH_ZONES,
    CAMERA_CONFIG,
    ROBOT_SAFETY_CONFIG,
    active_robot_ids,
)
from .workspace_calibration import (
    DEFAULT_CALIBRATION_PATH,
    DEFAULT_DETECTION_REPORT_PATH,
    calibration_arms,
    ordered_corners_from_box,
    polygon_corners_from_box,
    perspective_matrix_from_outer,
    point_in_polygon,
    reach_for_local_xy,
    resolve_workspace_calibration,
    workspace_metadata_from_resolution,
    workspace_to_robot_local,
)

from .object_calibration import apply_affine_transform, apply_robot_plane_correction

try:
    import cv2
except Exception:  # pragma: no cover - dependency may be absent on target host
    cv2 = None

try:
    import numpy as np
except Exception:  # pragma: no cover - dependency may be absent on target host
    np = None


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
    tl, tr, bl, br = quad
    return np.float32([tl, tr, br, bl])


def build_workspace_target_points_longside_width(width_mm: float, height_mm: float):
    return np.float32([
        [0.0, 0.0],
        [0.0, height_mm],
        [width_mm, 0.0],
        [width_mm, height_mm],
    ])


def classify_block(x_mm: float, y_mm: float, width_mm: float, height_mm: float):
    if not (0.0 <= float(x_mm) <= float(width_mm) and 0.0 <= float(y_mm) <= float(height_mm)):
        return None
    if x_mm < width_mm / 3.0:
        col = 1
    elif x_mm < 2.0 * width_mm / 3.0:
        col = 2
    else:
        col = 3
    row = 1 if y_mm >= height_mm / 2.0 else 2
    return f"Block {row},{col}"


def apply_arm_transform(transform: dict, x: float, y: float):
    a = float(transform.get("a", 1.0))
    b = float(transform.get("b", 0.0))
    tx = float(transform.get("tx", 0.0))
    c = float(transform.get("c", 0.0))
    d = float(transform.get("d", 1.0))
    ty = float(transform.get("ty", 0.0))
    return a * x + b * y + tx, c * x + d * y + ty


class CameraController:
    def __init__(
        self,
        camera_config=None,
        arm_reach_zones=None,
        arm_frame_transforms=None,
        workspace_calibration_path=None,
        workspace_detection_report_path=None,
        active_arm_ids=None,
        world_model=None,
    ):
        self.camera_config = camera_config or CAMERA_CONFIG
        self.active_arm_ids = list(active_arm_ids) if active_arm_ids is not None else active_robot_ids(ARM_FRAME_TRANSFORMS)
        self.world_model = world_model
        reach_source = arm_reach_zones if arm_reach_zones is not None else ARM_REACH_ZONES
        transform_source = arm_frame_transforms if arm_frame_transforms is not None else ARM_FRAME_TRANSFORMS
        self.arm_reach_zones = {
            arm_id: value
            for arm_id, value in reach_source.items()
            if arm_id in self.active_arm_ids
        }
        self.arm_frame_transforms = {
            arm_id: value
            for arm_id, value in transform_source.items()
            if arm_id in self.active_arm_ids
        }
        self.workspace_calibration_path = workspace_calibration_path or DEFAULT_CALIBRATION_PATH
        self.workspace_detection_report_path = (
            workspace_detection_report_path or DEFAULT_DETECTION_REPORT_PATH
        )

    def get_camera(self, camera_id: str):
        cid = str(camera_id or "").strip()
        if not cid:
            cid = "Cam1"
        return self.camera_config.get(cid)

    def get_status(self, camera_id: str = "Cam1"):
        config = self.get_camera(camera_id)
        if not config:
            return {"ok": False, "camera_id": camera_id, "error": f"Camera '{camera_id}' not found"}

        project_root = config["project_root"]
        sdk_ready, sdk_error = self._sdk_healthcheck(config)
        calibration = config.get("calibration", {})
        workspace_resolution = self._workspace_resolution()
        workspace_calibration = workspace_resolution.get("calibration")
        configured_arms = set(self.arm_reach_zones.keys())
        if workspace_calibration:
            configured_arms.update(
                arm_id for arm_id in calibration_arms(workspace_calibration)
                if arm_id in self.active_arm_ids
            )

        return {
            "ok": True,
            "camera_id": camera_id,
            "status": "ready" if sdk_ready else "degraded",
            "project_root": project_root,
            "project_root_exists": os.path.isdir(project_root),
            "sdk_ready": sdk_ready,
            "sdk_error": sdk_error,
            "mode": config.get("mode", "rgb"),
            "calibration_ready": self._calibration_ready(calibration) or workspace_calibration is not None,
            "workspace_calibration_ready": workspace_calibration is not None,
            "workspace_calibration_path": str(self.workspace_calibration_path),
            "workspace": workspace_metadata_from_resolution(workspace_resolution),
            "configured_arms": sorted(configured_arms),
            "timestamp": utc_now(),
        }

    def scan_workspace(
        self,
        camera_id: str = "Cam1",
        color: str = "any",
        selection: str = "first",
        output_image_path=None,
        require_saved_workspace: bool = False,
    ):
        if self.world_model is not None:
            return self.simulate_scan(camera_id, color, selection)
        config = self.get_camera(camera_id)
        if not config:
            return {"ok": False, "camera_id": camera_id, "error": f"Camera '{camera_id}' not found"}

        if cv2 is None or np is None:
            return {
                "ok": False,
                "camera_id": camera_id,
                "error": "OpenCV/numpy dependencies are not available",
            }

        workspace_resolution = self._workspace_resolution(require_saved_workspace=require_saved_workspace)
        workspace_calibration = workspace_resolution.get("calibration")
        if require_saved_workspace and not workspace_resolution.get("ok"):
            return {
                "ok": False,
                "camera_id": camera_id,
                "error": workspace_resolution.get("error") or "Workspace calibration is not ready",
                "workspace": workspace_metadata_from_resolution(workspace_resolution),
            }
        if not self._calibration_ready(config.get("calibration")) and workspace_calibration is None:
            return {
                "ok": False,
                "camera_id": camera_id,
                "error": "Calibration not ready",
                "workspace": workspace_metadata_from_resolution(workspace_resolution),
            }

        opened = self._open_camera(config)
        if not opened.get("ok"):
            return {"ok": False, "camera_id": camera_id, "error": opened["error"]}

        cam = opened["cam"]
        frame_out = opened["frame_out"]
        sdk = opened["sdk"]
        payload_size = opened.get("payload_size")

        try:
            frame = self._grab_frame(cam, frame_out, config, payload_size=payload_size)
            if not frame.get("ok"):
                return {"ok": False, "camera_id": camera_id, "error": frame["error"]}

            payload = self._analyze_frame(
                camera_id=camera_id,
                config=config,
                image=frame["image"],
                color=color,
                selection=selection,
                output_image_path=output_image_path,
                workspace_resolution=workspace_resolution,
            )
            return payload
        finally:
            self._close_camera(cam, sdk)

    def capture_image(self, camera_id: str = "Cam1", output_image_path=None):
        """Capture one fresh frame without running object detection."""
        config = self.get_camera(camera_id)
        if not config:
            return {"ok": False, "camera_id": camera_id, "error": f"Camera '{camera_id}' not found"}
        if cv2 is None or np is None:
            return {"ok": False, "camera_id": camera_id, "error": "OpenCV/numpy dependencies are not available"}
        opened = self._open_camera(config)
        if not opened.get("ok"):
            return {"ok": False, "camera_id": camera_id, "error": opened.get("error") or "Camera open failed"}
        cam = opened["cam"]
        try:
            frame = self._grab_frame(cam, opened["frame_out"], config, payload_size=opened.get("payload_size"))
            if not frame.get("ok"):
                return {"ok": False, "camera_id": camera_id, "error": frame.get("error") or "Frame capture failed"}
            image = frame["image"]
            mode = str(config.get("mode", "rgb")).lower()
            if mode == "rgb":
                display_img = cv2.cvtColor(image, cv2.COLOR_BayerGB2BGR)
            elif mode == "gray":
                display_img = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            else:
                display_img = image.copy()
            payload = {"ok": True, "camera_id": camera_id, "captured_at": utc_now(), "width": int(display_img.shape[1]), "height": int(display_img.shape[0])}
            if output_image_path:
                image_path = os.fspath(output_image_path)
                parent = os.path.dirname(image_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                if not cv2.imwrite(image_path, display_img):
                    return {"ok": False, "camera_id": camera_id, "error": f"Failed to write captured image: {image_path}"}
                payload["image_path"] = image_path
            return payload
        finally:
            self._close_camera(cam, opened["sdk"])
    def simulate_scan(
        self,
        camera_id: str = "Cam1",
        color: str = "any",
        selection: str = "first",
    ) -> dict:
        """scan เน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธเธเธขยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธโฌเน€เธยเนโฌยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธเธเธขย world model เน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธเธเธขยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธยเนยเธเนโฌยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธเธเธขยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธโฌเน€เธยเธขยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธเธเธขยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธโฌเน€เธยเนโฌยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธเธเธขยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธเธเธขยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธโฌเน€เธยเนโฌยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธเธเธขยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธเธเธขยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธโฌเน€เธยเนโฌเธเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธเธเธขยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธโฌเน€เธยเธขยเน€เธโฌเน€เธยเนยเธเน€เธโฌเน€เธยเธขยเน€เธเธเธขย"""
        if self.world_model is None:
            return {"ok": False, "camera_id": camera_id, "error": "World model not set"}
        scan = self.world_model.simulate_scan(camera_id)
        self._annotate_simulation_display_coordinates(scan, camera_id)
        if color != "any":
            scan["objects"] = [o for o in scan["objects"] if o.get("color") == color]
            scan["object_count"] = len(scan["objects"])
            if scan["objects"]:
                scan["selected_object"] = scan["objects"][0] if selection == "first" else scan["objects"][-1]
            else:
                scan["selected_object"] = None
        return scan

    def _annotate_simulation_display_coordinates(self, scan, camera_id):
        """Project simulator points through ARC's camera calibration for UI use."""

        if cv2 is None or np is None or not isinstance(scan, dict):
            return
        config = self.get_camera(camera_id) or {}
        calibration = config.get("calibration") or {}
        if not self._calibration_ready(calibration):
            return
        try:
            robot_to_pixel = np.linalg.inv(self._calibration_matrix(calibration))
        except (TypeError, ValueError, np.linalg.LinAlgError):
            return

        display_width = int((scan.get("frame") or {}).get("width") or 0)
        display_height = int((scan.get("frame") or {}).get("height") or 0)
        sim_scan_image = None
        try:
            from .config import SIM_SCAN_IMAGE

            sim_scan_image = os.fspath(SIM_SCAN_IMAGE)
            image = cv2.imread(sim_scan_image)
            if image is not None:
                display_height, display_width = image.shape[:2]
        except Exception:
            pass
        if display_width <= 0 or display_height <= 0:
            return
        scan["display_frame"] = {"width": display_width, "height": display_height}
        if sim_scan_image:
            scan["outputs"] = {
                "source_image": sim_scan_image,
                "detection_source": "arc_simulation_image_detection",
            }

        reference_objects = []
        try:
            if not sim_scan_image:
                raise FileNotFoundError("simulator scan image is not configured")
            reference_scan = self.analyze_image_file(
                sim_scan_image,
                camera_id=camera_id,
                color="any",
                selection="first",
                require_saved_workspace=False,
            )
            if reference_scan.get("ok"):
                reference_objects = [
                    item for item in (reference_scan.get("objects") or [])
                    if isinstance(item, dict) and isinstance(item.get("center_px"), dict)
                ]
        except Exception:
            reference_objects = []

        used_reference_ids = set()
        for object_record in scan.get("objects") or []:
            if not isinstance(object_record, dict):
                continue
            point = object_record.get("robot_plane")
            if not isinstance(point, dict):
                continue
            try:
                projected = cv2.perspectiveTransform(
                    np.array([[[float(point["x"]), float(point["y"])] ]], dtype=np.float32),
                    robot_to_pixel,
                )[0][0]
                x = float(projected[0])
                y = float(projected[1])
                if not (math.isfinite(x) and math.isfinite(y)):
                    continue
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            object_record["display_center_px"] = {"x": x, "y": y}
            object_record["display_coordinate_source"] = "arc_camera_calibration"

            candidates = [
                (index, reference)
                for index, reference in enumerate(reference_objects)
                if index not in used_reference_ids
                and str(reference.get("color") or "") == str(object_record.get("color") or "")
            ]
            if candidates:
                matched_index, matched = min(
                    candidates,
                    key=lambda item: math.hypot(
                        float(item[1]["center_px"]["x"]) - x,
                        float(item[1]["center_px"]["y"]) - y,
                    ),
                )
                matched_center = matched["center_px"]
                object_record["display_center_px"] = {
                    "x": float(matched_center["x"]),
                    "y": float(matched_center["y"]),
                }
                object_record["display_coordinate_source"] = "arc_simulation_image_detection"
                used_reference_ids.add(matched_index)

    def analyze_image_file(
        self,
        image_path,
        camera_id: str = "Cam1",
        color: str = "any",
        selection: str = "first",
        output_image_path=None,
        require_saved_workspace: bool = False,
    ):
        config = self.get_camera(camera_id)
        if not config:
            return {"ok": False, "camera_id": camera_id, "error": f"Camera '{camera_id}' not found"}
        if cv2 is None or np is None:
            return {
                "ok": False,
                "camera_id": camera_id,
                "error": "OpenCV/numpy dependencies are not available",
            }
        image = cv2.imread(os.fspath(image_path))
        if image is None:
            return {"ok": False, "camera_id": camera_id, "error": f"Image not found or unreadable: {image_path}"}
        workspace_resolution = self._workspace_resolution(require_saved_workspace=require_saved_workspace)
        workspace_calibration = workspace_resolution.get("calibration")
        if require_saved_workspace and not workspace_resolution.get("ok"):
            return {
                "ok": False,
                "camera_id": camera_id,
                "error": workspace_resolution.get("error") or "Workspace calibration is not ready",
                "workspace": workspace_metadata_from_resolution(workspace_resolution),
            }
        if not self._calibration_ready(config.get("calibration")) and workspace_calibration is None:
            return {
                "ok": False,
                "camera_id": camera_id,
                "error": "Calibration not ready",
                "workspace": workspace_metadata_from_resolution(workspace_resolution),
            }
        file_config = deepcopy(config)
        file_config["mode"] = "bgr"
        return self._analyze_frame(
            camera_id=camera_id,
            config=file_config,
            image=image,
            color=color,
            selection=selection,
            output_image_path=output_image_path,
            workspace_resolution=workspace_resolution,
        )

    def assign_targets(self, assignments, camera_id: str = "Cam1", require_saved_workspace: bool = False):
        if not isinstance(assignments, list) or not assignments:
            return {"ok": False, "camera_id": camera_id, "error": "assignments must be a non-empty list"}

        scan = self.scan_workspace(
            camera_id=camera_id,
            color="any",
            selection="first",
            require_saved_workspace=require_saved_workspace,
        )
        if not scan.get("ok"):
            return scan

        remaining = list(scan.get("objects", []))
        selected_targets = []
        unresolved = []

        for item in assignments:
            arm_id = item.get("arm_id")
            target_color = str(item.get("color", "any")).lower()
            target_block = item.get("block")
            target_object_id = str(item.get("object_id") or "").strip()
            candidate = None

            for obj in remaining:
                if target_object_id and str(obj.get("id") or "") != target_object_id:
                    continue
                if target_color != "any" and obj["color"] != target_color:
                    continue
                if target_block and obj.get("block") != target_block:
                    continue
                if arm_id not in obj["reachable_by"]:
                    continue
                candidate = obj
                break

            if candidate is None:
                unresolved.append(
                    {
                        "arm_id": arm_id,
                        "color": target_color,
                        "block": target_block,
                        "object_id": target_object_id or None,
                        "reason": "No reachable matching object found",
                    }
                )
                continue

            remaining = [obj for obj in remaining if obj["id"] != candidate["id"]]
            arm_plane = (candidate.get("per_arm_robot_plane") or {}).get(arm_id)
            if not arm_plane:
                unresolved.append(
                    {
                        "arm_id": arm_id,
                        "color": target_color,
                        "block": target_block,
                        "object_id": candidate.get("id"),
                        "reason": "transform_not_ready",
                    }
                )
                continue
            selected_targets.append(
                {
                    "arm_id": arm_id,
                    "color": target_color,
                    "block": candidate.get("block"),
                    "object_id": candidate["id"],
                    "robot_plane": {"x": float(arm_plane["x"]), "y": float(arm_plane["y"])},
                    "calibrated_robot_plane": candidate.get("robot_plane"),
                    "reachable_by": candidate.get("reachable_by", []),
                    "preferred_arm": candidate.get("preferred_arm"),
                }
            )

        return {
            "ok": len(unresolved) == 0,
            "camera_id": camera_id,
            "timestamp": scan.get("timestamp"),
            "object_count": scan.get("object_count", 0),
            "workspace": scan.get("workspace"),
            "targets": selected_targets,
            "unresolved": unresolved,
            "remaining_objects": remaining,
        }

    def _sdk_healthcheck(self, config):
        try:
            self._load_sdk(config)
            return True, None
        except Exception as exc:  # pragma: no cover - hardware environment dependent
            return False, str(exc)

    def _load_sdk(self, config):
        project_root = str(config["project_root"])
        mvs_import = str(Path(project_root) / "MvImport")
        for path in (project_root, mvs_import):
            if path not in sys.path:
                sys.path.insert(0, path)

        dll_dir = config.get("dll_dir")
        if dll_dir and hasattr(os, "add_dll_directory") and os.path.isdir(dll_dir):
            os.add_dll_directory(str(dll_dir))

        sdk_module = importlib.import_module("MvImport.MvCameraControl_class")
        pixel_module = importlib.import_module("MvImport.PixelType_header")
        return {
            "MvCamera": sdk_module.MvCamera,
            "MV_CC_DEVICE_INFO_LIST": sdk_module.MV_CC_DEVICE_INFO_LIST,
            "MV_CC_DEVICE_INFO": sdk_module.MV_CC_DEVICE_INFO,
            "MV_ACCESS_Exclusive": sdk_module.MV_ACCESS_Exclusive,
            "MV_FRAME_OUT": getattr(sdk_module, "MV_FRAME_OUT", None),
            "MV_FRAME_OUT_INFO_EX": getattr(sdk_module, "MV_FRAME_OUT_INFO_EX", None),
            "MVCC_INTVALUE": getattr(sdk_module, "MVCC_INTVALUE", None),
            "PixelTypeModule": pixel_module,
        }

    def _open_camera(self, config):
        try:
            sdk = self._load_sdk(config)
            device_list = sdk["MV_CC_DEVICE_INFO_LIST"]()
            ret = sdk["MvCamera"].MV_CC_EnumDevices(0x00000004, device_list)
            if ret != 0 or device_list.nDeviceNum == 0:
                return {"ok": False, "error": "No camera detected"}

            cam = sdk["MvCamera"]()
            device_info = cast(device_list.pDeviceInfo[0], POINTER(sdk["MV_CC_DEVICE_INFO"])).contents
            cam.MV_CC_CreateHandle(device_info)
            cam.MV_CC_OpenDevice(sdk["MV_ACCESS_Exclusive"], 0)
            cam.MV_CC_StopGrabbing()
            cam.MV_CC_SetEnumValue("PixelFormat", config.get("pixel_format", 17301512))
            cam.MV_CC_SetEnumValue("BalanceWhiteAuto", 2)
            cam.MV_CC_SetBoolValue("AcquisitionFrameRateEnable", True)
            cam.MV_CC_SetBoolValue("GammaEnable", True)
            self._apply_camera_params(cam, config)

            payload_size = None
            if hasattr(cam, "MV_CC_GetIntValue") and sdk.get("MVCC_INTVALUE") is not None:
                payload = sdk["MVCC_INTVALUE"]()
                memset(byref(payload), 0, sizeof(sdk["MVCC_INTVALUE"]))
                ret = cam.MV_CC_GetIntValue("PayloadSize", payload)
                if ret == 0:
                    payload_size = int(payload.nCurValue)
            cam.MV_CC_StartGrabbing()

            frame_type = sdk.get("MV_FRAME_OUT") or sdk.get("MV_FRAME_OUT_INFO_EX")
            if frame_type is None:
                raise RuntimeError("MVS SDK frame output type is not available")
            frame_out = frame_type()
            memset(byref(frame_out), 0, sizeof(frame_out))
            return {"ok": True, "cam": cam, "frame_out": frame_out, "sdk": sdk, "payload_size": payload_size}
        except Exception as exc:  # pragma: no cover - hardware environment dependent
            return {"ok": False, "error": f"Camera open failed: {exc}"}

    def _apply_camera_params(self, cam, config):
        mode = str(config.get("mode", "rgb")).lower()
        params = config.get("params", {}).get(mode, {})
        for key, value in params.items():
            if isinstance(value, int):
                cam.MV_CC_SetIntValue(key, value)
            else:
                cam.MV_CC_SetFloatValue(key, float(value))

    def _grab_frame(self, cam, frame_out, config, payload_size=None):
        try:
            timeout_ms = int(config.get("frame_timeout_ms", 1000))
            if hasattr(cam, "MV_CC_GetImageBuffer"):
                ret = cam.MV_CC_GetImageBuffer(frame_out, timeout_ms)
                if ret != 0:
                    return {"ok": False, "error": f"Failed to read camera frame: code {ret}"}

                frame_len = int(frame_out.stFrameInfo.nFrameLen)
                data = (c_ubyte * frame_len)()
                memmove(data, frame_out.pBufAddr, frame_len)
                height = int(frame_out.stFrameInfo.nHeight)
                width = int(frame_out.stFrameInfo.nWidth)
            else:
                frame_len = int(payload_size or 0)
                if frame_len <= 0:
                    return {"ok": False, "error": "Camera payload size is not available"}
                data = (c_ubyte * frame_len)()
                ret = cam.MV_CC_GetOneFrameTimeout(data, frame_len, frame_out, timeout_ms)
                if ret != 0:
                    return {"ok": False, "error": f"Failed to read camera frame: code {ret}"}
                height = int(frame_out.nHeight)
                width = int(frame_out.nWidth)
                frame_len = min(frame_len, int(width * height * 3))

            image = np.frombuffer(data, dtype=np.uint8, count=frame_len)
            if frame_len == width * height:
                image = image.reshape((height, width))
            elif frame_len == width * height * 3:
                image = image.reshape((height, width, 3))
            else:
                return {"ok": False, "error": f"Unsupported frame shape: width={width} height={height} bytes={frame_len}"}
            return {"ok": True, "image": image, "height": height, "width": width}
        except Exception as exc:
            return {"ok": False, "error": f"Frame decode failed: {exc}"}
        finally:
            if hasattr(cam, "MV_CC_FreeImageBuffer"):
                try:
                    cam.MV_CC_FreeImageBuffer(frame_out)
                except Exception:
                    pass

    def _close_camera(self, cam, sdk):
        try:
            cam.MV_CC_StopGrabbing()
        except Exception:
            pass
        try:
            cam.MV_CC_CloseDevice()
        except Exception:
            pass
        try:
            cam.MV_CC_DestroyHandle()
        except Exception:
            pass

    def _analyze_frame(
        self,
        camera_id: str,
        config,
        image,
        color: str,
        selection: str,
        output_image_path=None,
        workspace_resolution=None,
    ):
        mode = str(config.get("mode", "rgb")).lower()
        if mode == "bgr":
            display_img = image.copy()
        elif mode == "rgb":
            display_img = cv2.cvtColor(image, cv2.COLOR_BayerGB2BGR)
        else:
            display_img = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        outputs = {}
        if output_image_path:
            image_path = os.fspath(output_image_path)
            os.makedirs(os.path.dirname(image_path), exist_ok=True)
            cv2.imwrite(image_path, display_img)
            outputs["source_image"] = image_path
        workspace_resolution = workspace_resolution or self._workspace_resolution()
        workspace_calibration = workspace_resolution.get("calibration")
        detected_workspace = None
        effective_workspace = None

        ordered_workspace_ccw = None
        workspace_report_matrix = None
        width_mm = float(config.get("workspace_width_mm", 600.0))
        height_mm = float(config.get("workspace_height_mm", 400.0))
        workspace_source = "legacy"
        workspace_calibration_path = None
        workspace_metadata = workspace_metadata_from_resolution(workspace_resolution)

        if workspace_calibration:
            outer_box = workspace_calibration["outer_box"]
            width_mm = float(outer_box["width_mm"])
            height_mm = float(outer_box["height_mm"])
            ordered_quad = np.float32(ordered_corners_from_box(outer_box))
            polygon_quad = np.float32(polygon_corners_from_box(outer_box))
            effective_workspace = polygon_quad.reshape((-1, 1, 2))
            ordered_workspace_ccw = polygon_quad.reshape((-1, 1, 2))
            workspace_report_matrix = perspective_matrix_from_outer(
                outer_box,
                width_mm,
                height_mm,
            )
            workspace_source = str(workspace_calibration.get("_runtime_source") or "workspace_calibration")
            workspace_calibration_path = str(
                workspace_calibration.get("_runtime_path") or self.workspace_calibration_path
            )
            workspace_metadata["source"] = workspace_source
            workspace_metadata["calibration_path"] = workspace_calibration_path
        else:
            detected_workspace = self._detect_workspace(display_img, config)
            fallback_contour = self._fallback_workspace_contour(config)
            effective_workspace = detected_workspace if detected_workspace is not None else fallback_contour
            workspace_metadata["source"] = workspace_source
            workspace_metadata["calibration_path"] = workspace_calibration_path

        if workspace_report_matrix is None and effective_workspace is not None:
            ordered_quad = order_quad_points(effective_workspace)
            ordered_workspace_ccw = order_polygon_ccw(effective_workspace).reshape((-1, 1, 2))
            workspace_report_matrix = cv2.getPerspectiveTransform(
                ordered_quad,
                build_workspace_target_points_longside_width(width_mm, height_mm),
            )

        matrix = None if workspace_calibration else self._calibration_matrix(config["calibration"])
        arm_matrices = {} if workspace_calibration else self._arm_calibration_matrices(config)
        objects = self._detect_objects(
            display_img,
            ordered_workspace_ccw,
            config,
            matrix,
            arm_matrices,
            workspace_report_matrix,
            width_mm,
            height_mm,
            workspace_calibration,
        )

        color = str(color or "any").lower()
        filtered = [obj for obj in objects if color == "any" or obj["color"] == color]
        selected_object = self._select_object(filtered, selection)
        if output_image_path:
            overlay_path = self._object_overlay_path(output_image_path)
            written_overlay = self._write_object_overlay(
                display_img,
                ordered_workspace_ccw,
                objects,
                overlay_path,
            )
            if written_overlay:
                outputs["objects_overlay"] = written_overlay
                outputs["overlay"] = written_overlay

        height, width = display_img.shape[:2]
        return {
            "ok": True,
            "camera_id": camera_id,
            "timestamp": utc_now(),
            "frame": {
                "width": int(width),
                "height": int(height),
                "mode": mode,
            },
            "calibration_ready": True,
            "calibration_arm": config.get("calibration_arm", "Arm1"),
            "workspace": {
                "detected": detected_workspace is not None,
                "source": workspace_source,
                "calibration_path": workspace_calibration_path,
                "saved": bool(workspace_metadata.get("saved")),
                "stale_saved": bool(workspace_metadata.get("stale_saved")),
                "report_timestamp": workspace_metadata.get("report_timestamp"),
                "saved_report_timestamp": workspace_metadata.get("saved_report_timestamp"),
                "saved_exists": bool(workspace_metadata.get("saved_exists")),
                "detection_ready": bool(workspace_metadata.get("detection_ready")),
                "detection_error": workspace_metadata.get("detection_error"),
                "source_report": workspace_metadata.get("source_report"),
                "width_mm": width_mm,
                "height_mm": height_mm,
                "report_matrix_ready": workspace_report_matrix is not None,
                "zones": [
                    {
                        "name": zone.get("name"),
                        "role": zone.get("role"),
                        "bounds_mm": zone.get("bounds_mm"),
                    }
                    for zone in (workspace_calibration.get("zones", []) if workspace_calibration else [])
                ],
                "robot": workspace_calibration.get("robot") if workspace_calibration else None,
            },
            "object_count": len(objects),
            "objects": objects,
            "selected_object": selected_object,
            "outputs": outputs,
        }

    def _detect_workspace(self, display_img, config):
        gray = cv2.cvtColor(display_img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 40, 150)
        kernel = np.ones((5, 5), np.uint8)
        edges = cv2.dilate(edges, kernel, iterations=1)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        selected = None
        max_area = 0.0
        min_area = float(config.get("workspace_min_area", 200000))
        for contour in contours:
            area = cv2.contourArea(contour)
            if area <= min_area:
                continue
            perimeter = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
            if len(approx) == 4 and area > max_area:
                selected = approx
                max_area = area
        return selected

    def _detect_objects(
        self,
        display_img,
        workspace_contour,
        config,
        matrix,
        arm_matrices,
        workspace_report_matrix,
        width_mm,
        height_mm,
        workspace_calibration=None,
    ):
        hsv_img = cv2.cvtColor(display_img, cv2.COLOR_BGR2HSV)
        if bool(config.get("color_object_detection_enabled", True)):
            objects = self._detect_color_round_objects(
                hsv_img,
                workspace_contour,
                config,
                matrix,
                arm_matrices,
                workspace_report_matrix,
                width_mm,
                height_mm,
                workspace_calibration,
            )
            if objects or not bool(config.get("legacy_saturation_detection_enabled", False)):
                objects.sort(key=lambda item: (item["center_px"]["x"], item["center_px"]["y"]))
                return objects

        saturation = hsv_img[:, :, 1]
        # Detect blobs before workspace clipping. Calibration pucks can sit on
        # frame lines, and pre-masking cuts those pucks into thin slivers.
        blurred = cv2.GaussianBlur(saturation, (5, 5), 0)
        _, thresh = cv2.threshold(
            blurred,
            int(config.get("saturation_threshold", 40)),
            255,
            cv2.THRESH_BINARY,
        )
        open_kernel_size = int(config.get("morph_open_kernel", 0))
        close_kernel_size = int(config.get("morph_close_kernel", 0))
        if open_kernel_size > 1:
            open_kernel = np.ones((open_kernel_size, open_kernel_size), np.uint8)
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, open_kernel)
        if close_kernel_size > 1:
            close_kernel = np.ones((close_kernel_size, close_kernel_size), np.uint8)
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, close_kernel)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = float(config.get("min_object_area", 1500))
        workspace_area = cv2.contourArea(workspace_contour) if workspace_contour is not None else 0.0
        workspace_min_area = float(config.get("workspace_min_area", 200000))
        max_ratio = float(config.get("max_object_area_ratio", 0.2))
        image_height, image_width = thresh.shape
        edge_margin = int(config.get("edge_reject_margin_px", 0))
        objects = []
        object_index = 1

        for contour in contours:
            area = cv2.contourArea(contour)
            if area <= min_area:
                continue
            if workspace_area >= workspace_min_area and area >= workspace_area * max_ratio:
                continue

            x, y, width, height = cv2.boundingRect(contour)
            perimeter = cv2.arcLength(contour, True)
            circularity = 0.0
            if perimeter > 0.0:
                circularity = float(4.0 * math.pi * area / (perimeter * perimeter))
            if self._touches_image_edge(x, y, width, height, image_width, image_height, edge_margin):
                continue
            center_x = x + (width // 2)
            center_y = y + (height // 2)
            inside_workspace = self._point_inside_workspace(workspace_contour, center_x, center_y)

            calibrated_xy = None
            workspace_xy = None
            block = None
            zone_info = None
            if workspace_report_matrix is not None:
                ws_pt = cv2.perspectiveTransform(
                    np.array([[[float(center_x), float(center_y)]]], dtype=np.float32),
                    workspace_report_matrix,
                )
                workspace_xy = {"x": float(ws_pt[0][0][0]), "y": float(ws_pt[0][0][1])}
                if workspace_calibration:
                    zone_info = self._zone_for_workspace_point(workspace_calibration, workspace_xy)
                    block = zone_info.get("name") if zone_info else None
                else:
                    block = classify_block(workspace_xy["x"], workspace_xy["y"], width_mm, height_mm)
                if not inside_workspace:
                    inside_workspace = block is not None

            if not inside_workspace:
                continue

            color_name = self._estimate_color(hsv_img, contour, thresh.shape)
            if workspace_calibration and workspace_xy is not None:
                per_arm_robot_plane = self._compute_workspace_robot_plane(
                    workspace_calibration,
                    workspace_xy,
                )
                robot_id = str(workspace_calibration.get("robot", {}).get("robot_id", "Arm1"))
                calibrated_xy = per_arm_robot_plane.get(robot_id)
                reachable_by = self._reachable_workspace_robot(workspace_calibration, per_arm_robot_plane)
            else:
                calibrated_xy = self._pixel_to_robot_plane(matrix, center_x, center_y)
                per_arm_robot_plane = self._compute_per_arm_robot_plane(
                    config,
                    calibrated_xy,
                    arm_matrices,
                    center_x,
                    center_y,
                )
                reachable_by = self._reachable_arms(per_arm_robot_plane)
            preferred_arm = reachable_by[0] if reachable_by else None

            objects.append(
                {
                    "id": f"obj-{object_index}",
                    "color": color_name,
                    "center_px": {"x": int(center_x), "y": int(center_y)},
                    "bbox": {"x": int(x), "y": int(y), "w": int(width), "h": int(height)},
                    "area": float(area),
                    "perimeter": float(perimeter),
                    "circularity": float(circularity),
                    "inside_workspace": inside_workspace,
                    "block": block,
                    "zone": zone_info,
                    "workspace_xy": workspace_xy,
                    "robot_plane": calibrated_xy,
                    "per_arm_robot_plane": per_arm_robot_plane,
                    "reachable_by": reachable_by,
                    "preferred_arm": preferred_arm,
                }
            )
            object_index += 1

        if bool(config.get("hough_round_fallback_enabled", True)):
            object_index = self._append_hough_round_objects(
                objects,
                object_index,
                display_img,
                hsv_img,
                saturation,
                workspace_contour,
                config,
                workspace_report_matrix,
                width_mm,
                height_mm,
                workspace_calibration,
                matrix,
                arm_matrices,
            )

        objects.sort(key=lambda item: (item["center_px"]["x"], item["center_px"]["y"]))
        return objects

    def _detect_color_round_objects(
        self,
        hsv_img,
        workspace_contour,
        config,
        matrix,
        arm_matrices,
        workspace_report_matrix,
        width_mm,
        height_mm,
        workspace_calibration=None,
    ):
        colors = config.get("detectable_object_colors") or ["red", "green", "blue"]
        object_index = 1
        objects = []
        shape = hsv_img.shape[:2]
        open_kernel_size = int(config.get("color_morph_open_kernel", config.get("morph_open_kernel", 5)))
        close_kernel_size = int(config.get("color_morph_close_kernel", config.get("morph_close_kernel", 7)))
        open_kernel = np.ones((open_kernel_size, open_kernel_size), np.uint8) if open_kernel_size > 1 else None
        close_kernel = np.ones((close_kernel_size, close_kernel_size), np.uint8) if close_kernel_size > 1 else None

        for color_name in colors:
            color_name = str(color_name).lower()
            mask = self._object_color_mask(hsv_img, color_name, config)
            if open_kernel is not None:
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_kernel)
            if close_kernel is not None:
                mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                candidate = self._round_contour_candidate(contour, config, workspace_contour, shape)
                if candidate is None:
                    continue
                if self._has_nearby_object(objects, candidate["center_x"], candidate["center_y"], color_name, config):
                    continue
                objects.append(
                    self._build_detected_object(
                        object_index,
                        color_name,
                        candidate,
                        config,
                        matrix,
                        arm_matrices,
                        workspace_report_matrix,
                        width_mm,
                        height_mm,
                        workspace_calibration,
                        detection_source="color_contour",
                    )
                )
                object_index += 1

            object_index = self._append_color_hough_round_objects(
                objects,
                object_index,
                hsv_img,
                mask,
                color_name,
                workspace_contour,
                config,
                matrix,
                arm_matrices,
                workspace_report_matrix,
                width_mm,
                height_mm,
                workspace_calibration,
            )

        return objects

    def _object_color_mask(self, hsv_img, color_name, config):
        ranges = config.get("object_color_hsv_ranges") or {}
        default_ranges = {
            "red": [
                {"lower": [0, 70, 25], "upper": [18, 255, 255]},
                {"lower": [160, 70, 25], "upper": [179, 255, 255]},
            ],
            "green": [{"lower": [30, 25, 20], "upper": [95, 255, 255]}],
            "blue": [{"lower": [85, 35, 20], "upper": [145, 255, 255]}],
        }
        selected_ranges = ranges.get(color_name) or default_ranges.get(color_name) or []
        mask = np.zeros(hsv_img.shape[:2], dtype=np.uint8)
        for item in selected_ranges:
            lower = np.array(item.get("lower", [0, 0, 0]), dtype=np.uint8)
            upper = np.array(item.get("upper", [179, 255, 255]), dtype=np.uint8)
            mask = cv2.bitwise_or(mask, cv2.inRange(hsv_img, lower, upper))
        return mask

    def _round_contour_candidate(self, contour, config, workspace_contour, shape):
        area = float(cv2.contourArea(contour))
        min_area = float(config.get("min_object_area", 1500.0))
        max_area = float(config.get("max_object_area_px", 45000.0))
        if area < min_area or (max_area > 0.0 and area > max_area):
            return None

        x, y, width, height = cv2.boundingRect(contour)
        image_height, image_width = shape
        edge_margin = int(config.get("edge_reject_margin_px", 0))
        if self._touches_image_edge(x, y, width, height, image_width, image_height, edge_margin):
            return None

        min_diameter = float(config.get("min_object_diameter_px", 35.0))
        max_diameter = float(config.get("max_object_diameter_px", 190.0))
        if min(width, height) < min_diameter:
            return None
        if max_diameter > 0.0 and max(width, height) > max_diameter:
            return None

        aspect = float(width) / float(height or 1)
        if aspect < float(config.get("min_object_aspect", 0.55)):
            return None
        if aspect > float(config.get("max_object_aspect", 1.75)):
            return None

        perimeter = float(cv2.arcLength(contour, True))
        circularity = 0.0
        if perimeter > 0.0:
            circularity = float(4.0 * math.pi * area / (perimeter * perimeter))
        if circularity < float(config.get("min_object_circularity", 0.45)):
            return None

        moments = cv2.moments(contour)
        if moments.get("m00"):
            center_x = float(moments["m10"] / moments["m00"])
            center_y = float(moments["m01"] / moments["m00"])
        else:
            center_x = float(x + width / 2.0)
            center_y = float(y + height / 2.0)

        if not self._point_inside_workspace(workspace_contour, center_x, center_y):
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
        }

    def _append_color_hough_round_objects(
        self,
        objects,
        object_index,
        hsv_img,
        mask,
        color_name,
        workspace_contour,
        config,
        matrix,
        arm_matrices,
        workspace_report_matrix,
        width_mm,
        height_mm,
        workspace_calibration,
    ):
        if not bool(config.get("color_hough_round_enabled", False)):
            return object_index

        min_radius = int(config.get("hough_round_min_radius_px", 38))
        max_radius = int(config.get("hough_round_max_radius_px", 72))
        if max_radius <= min_radius:
            return object_index

        blurred = cv2.medianBlur(mask, 5)
        circles = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=float(config.get("hough_round_dp", 1.2)),
            minDist=float(config.get("hough_round_min_dist_px", 85.0)),
            param1=float(config.get("hough_round_param1", 80.0)),
            param2=float(config.get("hough_round_param2", 12.0)),
            minRadius=min_radius,
            maxRadius=max_radius,
        )
        if circles is None:
            return object_index

        duplicate_distance = float(config.get("hough_round_duplicate_distance_px", max_radius * 0.9))
        min_mean_saturation = float(
            (config.get("color_hough_min_mean_saturation") or {}).get(
                color_name,
                config.get("hough_round_min_mean_saturation", 35.0),
            )
        )
        min_mask_coverage = float(config.get("color_hough_min_mask_coverage", 0.35))
        min_circle_area = float(config.get("hough_round_min_area_px", 2500.0))

        for circle in np.round(circles[0]).astype(int):
            center_x, center_y, radius = [int(value) for value in circle]
            if radius < min_radius or radius > max_radius:
                continue
            if not self._point_inside_workspace(workspace_contour, center_x, center_y):
                continue
            if self._has_nearby_object(objects, center_x, center_y, color_name, config, duplicate_distance):
                continue

            circle_mask = np.zeros(mask.shape, dtype=np.uint8)
            cv2.circle(circle_mask, (center_x, center_y), max(1, int(radius * 0.75)), 255, -1)
            mean_saturation = float(cv2.mean(hsv_img[:, :, 1], mask=circle_mask)[0])
            if mean_saturation < min_mean_saturation:
                continue
            mask_coverage = float(cv2.mean(mask, mask=circle_mask)[0]) / 255.0
            if mask_coverage < min_mask_coverage:
                continue

            area = math.pi * float(radius) * float(radius)
            if area < min_circle_area:
                continue

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
                "mean_saturation": mean_saturation,
                "mask_coverage": mask_coverage,
            }
            objects.append(
                self._build_detected_object(
                    object_index,
                    color_name,
                    candidate,
                    config,
                    matrix,
                    arm_matrices,
                    workspace_report_matrix,
                    width_mm,
                    height_mm,
                    workspace_calibration,
                    detection_source="color_hough_round",
                )
            )
            object_index += 1
        return object_index

    def _has_nearby_object(self, objects, center_x, center_y, color_name=None, config=None, max_distance_px=None):
        if max_distance_px is None:
            max_distance_px = float((config or {}).get("object_duplicate_distance_px", 65.0))
        for obj in objects:
            if color_name and obj.get("color") != color_name:
                continue
            center = obj.get("center_px") or {}
            dx = float(center.get("x", 0.0)) - float(center_x)
            dy = float(center.get("y", 0.0)) - float(center_y)
            if math.sqrt(dx * dx + dy * dy) <= float(max_distance_px):
                return True
        return False

    def _build_detected_object(
        self,
        object_index,
        color_name,
        candidate,
        config,
        matrix,
        arm_matrices,
        workspace_report_matrix,
        width_mm,
        height_mm,
        workspace_calibration=None,
        detection_source="color_contour",
    ):
        center_x = float(candidate["center_x"])
        center_y = float(candidate["center_y"])
        workspace_xy = None
        block = None
        zone_info = None
        if workspace_report_matrix is not None:
            ws_pt = cv2.perspectiveTransform(
                np.array([[[center_x, center_y]]], dtype=np.float32),
                workspace_report_matrix,
            )
            workspace_xy = {"x": float(ws_pt[0][0][0]), "y": float(ws_pt[0][0][1])}
            if workspace_calibration:
                zone_info = self._zone_for_workspace_point(workspace_calibration, workspace_xy)
                block = zone_info.get("name") if zone_info else None
            else:
                block = classify_block(workspace_xy["x"], workspace_xy["y"], width_mm, height_mm)

        if workspace_calibration and workspace_xy is not None:
            per_arm_robot_plane = self._compute_workspace_robot_plane(workspace_calibration, workspace_xy)
            robot_id = str(workspace_calibration.get("robot", {}).get("robot_id", "Arm1"))
            calibrated_xy = per_arm_robot_plane.get(robot_id)
            reachable_by = self._reachable_workspace_robot(workspace_calibration, per_arm_robot_plane)
        else:
            calibrated_xy = self._pixel_to_robot_plane(matrix, center_x, center_y) if matrix is not None else None
            per_arm_robot_plane = self._compute_per_arm_robot_plane(
                config=config,
                calibrated_xy=calibrated_xy or {"x": center_x, "y": center_y},
                arm_matrices=arm_matrices,
                pixel_x=center_x,
                pixel_y=center_y,
            )
            reachable_by = self._reachable_arms(per_arm_robot_plane)

        payload = {
            "id": f"obj-{object_index}",
            "color": str(color_name),
            "shape": "circle",
            "center_px": {"x": int(round(center_x)), "y": int(round(center_y))},
            "bbox": candidate["bbox"],
            "area": float(candidate["area"]),
            "perimeter": float(candidate["perimeter"]),
            "circularity": float(candidate["circularity"]),
            "radius_px": float(candidate.get("radius_px") or 0.0),
            "inside_workspace": True,
            "block": block,
            "zone": zone_info,
            "workspace_xy": workspace_xy,
            "robot_plane": calibrated_xy,
            "per_arm_robot_plane": per_arm_robot_plane,
            "reachable_by": reachable_by,
            "preferred_arm": reachable_by[0] if reachable_by else None,
            "detection_source": detection_source,
        }
        for key in ("mean_saturation", "mask_coverage"):
            if key in candidate:
                payload[key] = float(candidate[key])
        return payload

    def _object_overlay_path(self, source_path):
        path = Path(os.fspath(source_path))
        suffix = path.suffix or ".jpg"
        return path.with_name(f"{path.stem}_objects{suffix}")

    def _write_object_overlay(self, display_img, workspace_contour, objects, overlay_path):
        if cv2 is None or np is None:
            return None
        overlay = display_img.copy()
        image_height, image_width = overlay.shape[:2]
        scale = max(0.7, min(2.0, image_width / 1600.0))
        line = max(2, int(round(scale * 2.5)))
        font = cv2.FONT_HERSHEY_SIMPLEX
        palette = {
            "red": (40, 40, 230),
            "green": (50, 190, 70),
            "blue": (235, 130, 30),
            "unknown": (0, 220, 220),
        }

        if workspace_contour is not None:
            contour = np.array(workspace_contour, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(overlay, [contour], True, (255, 255, 255), line + 5, cv2.LINE_AA)
            cv2.polylines(overlay, [contour], True, (0, 180, 255), line + 1, cv2.LINE_AA)

        counts = {}
        for index, obj in enumerate(objects or [], start=1):
            color_name = str(obj.get("color") or "unknown")
            counts[color_name] = counts.get(color_name, 0) + 1
            draw_color = palette.get(color_name, (0, 220, 220))
            center = obj.get("center_px") or {}
            bbox = obj.get("bbox") or {}
            center_x = int(round(float(center.get("x") or 0.0)))
            center_y = int(round(float(center.get("y") or 0.0)))
            x = int(round(float(bbox.get("x") or 0.0)))
            y = int(round(float(bbox.get("y") or 0.0)))
            w = int(round(float(bbox.get("w") or 0.0)))
            h = int(round(float(bbox.get("h") or 0.0)))
            radius = int(round(float(obj.get("radius_px") or max(w, h) / 2.0 or 18.0)))
            cv2.rectangle(overlay, (x, y), (x + w, y + h), draw_color, line, cv2.LINE_AA)
            cv2.circle(overlay, (center_x, center_y), max(4, radius), draw_color, line, cv2.LINE_AA)
            cv2.circle(overlay, (center_x, center_y), max(4, int(round(scale * 4))), draw_color, -1, cv2.LINE_AA)

            circularity = float(obj.get("circularity") or 0.0)
            source = str(obj.get("detection_source") or "detected").replace("_", " ")
            label = f"{index} {color_name} {obj.get('shape') or 'object'} c={circularity:.2f}"
            if source:
                label = f"{label} {source}"
            self._draw_label(
                overlay,
                label,
                (max(0, x), max(0, y - int(round(10 * scale)))),
                draw_color,
                font,
                scale,
                line,
            )

        summary_parts = [f"objects {len(objects or [])}"]
        for color_name in ("red", "green", "blue"):
            summary_parts.append(f"{color_name} {counts.get(color_name, 0)}")
        summary = " | ".join(summary_parts)
        self._draw_label(
            overlay,
            summary,
            (int(round(18 * scale)), min(image_height - 10, int(round(42 * scale)))),
            (30, 30, 30),
            font,
            scale,
            line,
            filled=True,
        )

        target = Path(overlay_path)
        if str(target.parent):
            target.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(os.fspath(target), overlay):
            return None
        return os.fspath(target)

    def _draw_label(self, image, text, anchor, color, font, scale, line, filled=False):
        x, y = int(anchor[0]), int(anchor[1])
        thickness = max(1, int(round(line * 0.8)))
        text_scale = scale * 0.62
        (text_width, text_height), baseline = cv2.getTextSize(text, font, text_scale, thickness)
        pad_x = max(5, int(round(6 * scale)))
        pad_y = max(4, int(round(5 * scale)))
        top = max(0, y - text_height - baseline - pad_y * 2)
        left = max(0, min(x, image.shape[1] - text_width - pad_x * 2 - 1))
        bottom = min(image.shape[0] - 1, top + text_height + baseline + pad_y * 2)
        right = min(image.shape[1] - 1, left + text_width + pad_x * 2)
        bg = color if filled else (20, 20, 20)
        cv2.rectangle(image, (left, top), (right, bottom), bg, -1, cv2.LINE_AA)
        cv2.rectangle(image, (left, top), (right, bottom), color, max(1, line // 2), cv2.LINE_AA)
        text_color = (255, 255, 255)
        cv2.putText(
            image,
            text,
            (left + pad_x, bottom - baseline - pad_y),
            font,
            text_scale,
            text_color,
            thickness,
            cv2.LINE_AA,
        )

    def _compute_per_arm_robot_plane(self, config, calibrated_xy, arm_matrices=None, pixel_x=None, pixel_y=None):
        calibration_arm = str(config.get("calibration_arm", "Arm1"))
        base_x = float(calibrated_xy["x"])
        base_y = float(calibrated_xy["y"])
        per_arm = {}
        for arm_id, transform in self.arm_frame_transforms.items():
            matrix = (arm_matrices or {}).get(arm_id)
            if matrix is not None and pixel_x is not None and pixel_y is not None:
                arm_xy = self._pixel_to_robot_plane(matrix, pixel_x, pixel_y)
                arm_xy["calibration"] = "arm_specific"
                per_arm[arm_id] = arm_xy
                continue
            if arm_id == calibration_arm:
                per_arm[arm_id] = {"x": base_x, "y": base_y, "calibration": "primary"}
                continue
            ax, ay = apply_arm_transform(transform, base_x, base_y)
            per_arm[arm_id] = {
                "x": ax,
                "y": ay,
                "verified": bool(transform.get("verified", True)),
                "calibration": "frame_transform",
            }
        return per_arm

    def _compute_workspace_robot_plane(self, workspace_calibration, workspace_xy):
        per_arm = {}
        for arm_id, robot in calibration_arms(workspace_calibration).items():
            if arm_id not in self.active_arm_ids or not isinstance(robot, dict):
                continue
            transform = robot.get("workspace_to_robot_transform") or {}
            # Runtime object coordinates are authoritative only after object calibration.
            if not transform.get("enabled") or str(transform.get("arm_id") or arm_id) != arm_id:
                continue
            uncorrected_robot_xy = apply_affine_transform(transform, workspace_xy)
            correction = transform.get("robot_plane_correction") or {}
            correction_enabled = bool(correction.get("enabled"))
            robot_xy = apply_robot_plane_correction(correction, uncorrected_robot_xy) if correction_enabled else uncorrected_robot_xy
            reach = reach_for_local_xy(robot_xy, robot)
            per_arm[arm_id] = {
                **robot_xy,
                "calibration": "object_workspace_affine_corrected" if correction_enabled else "object_workspace_affine",
                "uncorrected_robot_plane": uncorrected_robot_xy,
                "robot_plane_correction": correction if correction_enabled else None,
                "reachable": bool(reach["reachable"]),
                "reach_radius_mm": float(reach["r_mm"]),
                "fit_mean_error_mm": float(transform.get("fit_mean_error_mm") or 0.0),
                "fit_max_error_mm": float(transform.get("fit_max_error_mm") or 0.0),
            }
        return per_arm

    def _reachable_workspace_robot(self, workspace_calibration, per_arm_robot_plane):
        return [
            arm_id for arm_id in self.active_arm_ids
            if bool((per_arm_robot_plane.get(arm_id) or {}).get("reachable"))
        ]
    def _zone_for_workspace_point(self, workspace_calibration, workspace_xy):
        for zone in workspace_calibration.get("zones") or []:
            if str(zone.get("role", "")).lower() == "ignore":
                continue
            polygon = zone.get("polygon_mm") or []
            if point_in_polygon(workspace_xy, polygon):
                return {
                    "name": zone.get("name"),
                    "label": zone.get("label"),
                    "role": zone.get("role"),
                    "bounds_mm": zone.get("bounds_mm"),
                    "center_mm": zone.get("center_mm"),
                }
        return None

    def _append_hough_round_objects(
        self,
        objects,
        object_index,
        display_img,
        hsv_img,
        saturation,
        workspace_contour,
        config,
        workspace_report_matrix,
        width_mm,
        height_mm,
        workspace_calibration,
        matrix,
        arm_matrices,
    ):
        min_radius = int(config.get("hough_round_min_radius_px", 42))
        max_radius = int(config.get("hough_round_max_radius_px", 68))
        if max_radius <= min_radius:
            return object_index

        blurred = cv2.medianBlur(saturation, 5)
        circles = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=float(config.get("hough_round_dp", 1.2)),
            minDist=float(config.get("hough_round_min_dist_px", 90.0)),
            param1=float(config.get("hough_round_param1", 80.0)),
            param2=float(config.get("hough_round_param2", 20.0)),
            minRadius=min_radius,
            maxRadius=max_radius,
        )
        if circles is None:
            return object_index

        duplicate_distance = float(config.get("hough_round_duplicate_distance_px", max_radius * 0.9))
        duplicate_circularity = float(config.get("hough_round_duplicate_circularity", 0.55))
        min_mean_saturation = float(config.get("hough_round_min_mean_saturation", 45.0))
        min_circle_area = float(config.get("hough_round_min_area_px", 2500.0))
        image_height, image_width = saturation.shape
        edge_margin = int(config.get("edge_reject_margin_px", 0))

        for circle in np.round(circles[0]).astype(int):
            center_x, center_y, radius = [int(value) for value in circle]
            if radius < min_radius or radius > max_radius:
                continue
            if self._touches_image_edge(
                center_x - radius,
                center_y - radius,
                radius * 2,
                radius * 2,
                image_width,
                image_height,
                edge_margin,
            ):
                continue
            if not self._point_inside_workspace(workspace_contour, center_x, center_y):
                continue
            if self._has_nearby_round_object(
                objects,
                center_x,
                center_y,
                duplicate_distance,
                duplicate_circularity,
            ):
                continue

            circle_mask = np.zeros(saturation.shape, dtype=np.uint8)
            cv2.circle(
                circle_mask,
                (center_x, center_y),
                max(1, int(radius * 0.75)),
                255,
                -1,
            )
            mean_saturation = float(cv2.mean(saturation, mask=circle_mask)[0])
            if mean_saturation < min_mean_saturation:
                continue

            area = math.pi * float(radius) * float(radius)
            if area < min_circle_area:
                continue

            self._remove_nearby_low_quality_round_objects(
                objects,
                center_x,
                center_y,
                duplicate_distance,
                duplicate_circularity,
            )

            workspace_xy = None
            block = None
            zone_info = None
            inside_workspace = True
            if workspace_report_matrix is not None:
                ws_pt = cv2.perspectiveTransform(
                    np.array([[[float(center_x), float(center_y)]]], dtype=np.float32),
                    workspace_report_matrix,
                )
                workspace_xy = {"x": float(ws_pt[0][0][0]), "y": float(ws_pt[0][0][1])}
                if workspace_calibration:
                    zone_info = self._zone_for_workspace_point(workspace_calibration, workspace_xy)
                    block = zone_info.get("name") if zone_info else None
                else:
                    block = classify_block(workspace_xy["x"], workspace_xy["y"], width_mm, height_mm)
                if block is not None:
                    inside_workspace = True

            color_name = self._estimate_color(hsv_img, circle_mask, saturation.shape)
            if workspace_calibration and workspace_xy is not None:
                per_arm_robot_plane = self._compute_workspace_robot_plane(
                    workspace_calibration,
                    workspace_xy,
                )
                robot_id = str(workspace_calibration.get("robot", {}).get("robot_id", "Arm1"))
                calibrated_xy = per_arm_robot_plane.get(robot_id)
                reachable_by = self._reachable_workspace_robot(workspace_calibration, per_arm_robot_plane)
            else:
                calibrated_xy = self._pixel_to_robot_plane(matrix, center_x, center_y)
                per_arm_robot_plane = self._compute_per_arm_robot_plane(
                    config,
                    calibrated_xy,
                    arm_matrices,
                    center_x,
                    center_y,
                )
                reachable_by = self._reachable_arms(per_arm_robot_plane)

            objects.append(
                {
                    "id": f"obj-{object_index}",
                    "color": color_name,
                    "center_px": {"x": int(center_x), "y": int(center_y)},
                    "bbox": {
                        "x": int(center_x - radius),
                        "y": int(center_y - radius),
                        "w": int(radius * 2),
                        "h": int(radius * 2),
                    },
                    "area": float(area),
                    "perimeter": float(2.0 * math.pi * float(radius)),
                    "circularity": 1.0,
                    "inside_workspace": inside_workspace,
                    "block": block,
                    "zone": zone_info,
                    "workspace_xy": workspace_xy,
                    "robot_plane": calibrated_xy,
                    "per_arm_robot_plane": per_arm_robot_plane,
                    "reachable_by": reachable_by,
                    "preferred_arm": reachable_by[0] if reachable_by else None,
                    "detection_source": "hough_round_fallback",
                    "mean_saturation": mean_saturation,
                    "radius_px": float(radius),
                }
            )
            object_index += 1

        return object_index

    def _has_nearby_round_object(
        self,
        objects,
        center_x,
        center_y,
        max_distance_px,
        min_circularity,
    ):
        for obj in objects:
            if float(obj.get("circularity") or 0.0) < float(min_circularity):
                continue
            center = obj.get("center_px") or {}
            dx = float(center.get("x", 0.0)) - float(center_x)
            dy = float(center.get("y", 0.0)) - float(center_y)
            if math.sqrt(dx * dx + dy * dy) <= max_distance_px:
                return True
        return False

    def _remove_nearby_low_quality_round_objects(
        self,
        objects,
        center_x,
        center_y,
        max_distance_px,
        min_circularity,
    ):
        keep = []
        for obj in objects:
            center = obj.get("center_px") or {}
            dx = float(center.get("x", 0.0)) - float(center_x)
            dy = float(center.get("y", 0.0)) - float(center_y)
            nearby = math.sqrt(dx * dx + dy * dy) <= float(max_distance_px)
            low_quality = float(obj.get("circularity") or 0.0) < float(min_circularity)
            if nearby and low_quality:
                continue
            keep.append(obj)
        objects[:] = keep

    def _estimate_color(self, hsv_img, contour, shape):
        mask = np.zeros(shape, dtype=np.uint8)
        if isinstance(contour, np.ndarray) and contour.shape == shape:
            mask = contour
        else:
            cv2.drawContours(mask, [contour], -1, 255, -1)
        mean_h, mean_s, mean_v, _ = cv2.mean(hsv_img, mask=mask)
        hue = mean_h
        saturation = mean_s
        value = mean_v

        if saturation < 25 or value < 25:
            return "unknown"

        if hue < 15 or hue > 160:
            return "red"
        if 40 < hue < 85:
            return "green"
        if 90 < hue < 140:
            return "blue"
        return "unknown"

    def _point_inside_workspace(self, workspace_contour, center_x, center_y):
        if workspace_contour is None:
            return True
        return cv2.pointPolygonTest(workspace_contour, (float(center_x), float(center_y)), False) >= 0

    def _fallback_workspace_contour(self, config):
        fixed = config.get("fixed_workspace_corners_px")
        if isinstance(fixed, list) and len(fixed) == 4:
            return np.array(fixed, dtype=np.float32).reshape((-1, 1, 2))
        calibration = config.get("calibration", {})
        camera_points = calibration.get("camera_points")
        if not isinstance(camera_points, list) or len(camera_points) != 4:
            return None
        return np.array(camera_points, dtype=np.float32).reshape((-1, 1, 2))

    def _workspace_resolution(self, require_saved_workspace: bool = False):
        return resolve_workspace_calibration(
            self.workspace_calibration_path,
            self.workspace_detection_report_path,
            require_saved=require_saved_workspace,
            safety_config=ROBOT_SAFETY_CONFIG,
        )

    def _workspace_calibration(self):
        return self._workspace_resolution().get("calibration")

    def _touches_image_edge(self, x, y, width, height, image_width, image_height, margin):
        if margin <= 0:
            return False
        return (
            x <= margin
            or y <= margin
            or (x + width) >= (image_width - margin)
            or (y + height) >= (image_height - margin)
        )

    def _calibration_ready(self, calibration):
        if not calibration:
            return False
        camera_points = calibration.get("camera_points")
        robot_points = calibration.get("robot_points")
        return isinstance(camera_points, list) and len(camera_points) == 4 and isinstance(robot_points, list) and len(robot_points) == 4

    def _calibration_matrix(self, calibration):
        camera_points = np.float32(calibration["camera_points"])
        robot_points = np.float32(calibration["robot_points"])
        return cv2.getPerspectiveTransform(camera_points, robot_points)

    def _arm_calibration_matrices(self, config):
        matrices = {}
        for arm_id, calibration in (config.get("arm_calibrations") or {}).items():
            if arm_id not in self.active_arm_ids:
                continue
            if self._calibration_ready(calibration):
                matrices[arm_id] = self._calibration_matrix(calibration)
        return matrices

    def _pixel_to_robot_plane(self, matrix, pixel_x: int, pixel_y: int):
        point_px = np.array([[[float(pixel_x), float(pixel_y)]]], dtype=np.float32)
        point_robot = cv2.perspectiveTransform(point_px, matrix)
        return {
            "x": float(point_robot[0][0][0]),
            "y": float(point_robot[0][0][1]),
        }

    def _reachable_arms(self, per_arm_robot_plane):
        reachable = []
        for arm_id, polygon in self.arm_reach_zones.items():
            arm_xy = per_arm_robot_plane.get(arm_id)
            if not arm_xy:
                continue
            contour = np.array(polygon, dtype=np.float32)
            if cv2.pointPolygonTest(contour, (float(arm_xy["x"]), float(arm_xy["y"])), False) >= 0:
                reachable.append(arm_id)
        return sorted(reachable)

    def _robot_plane_in_workspace(self, robot_x: float, robot_y: float):
        xs = []
        ys = []
        for polygon in self.arm_reach_zones.values():
            for point_x, point_y in polygon:
                xs.append(float(point_x))
                ys.append(float(point_y))
        if not xs or not ys:
            return True
        return min(xs) <= float(robot_x) <= max(xs) and min(ys) <= float(robot_y) <= max(ys)

    def _select_object(self, objects, selection: str):
        if not objects:
            return None

        selection = str(selection or "first").lower()
        if selection == "largest":
            return max(objects, key=lambda item: item["area"])
        if selection == "leftmost":
            return min(objects, key=lambda item: item["center_px"]["x"])
        if selection == "rightmost":
            return max(objects, key=lambda item: item["center_px"]["x"])
        if selection == "topmost":
            return min(objects, key=lambda item: item["center_px"]["y"])
        if selection == "bottommost":
            return max(objects, key=lambda item: item["center_px"]["y"])
        return objects[0]


