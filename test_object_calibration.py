import tempfile
import unittest
from pathlib import Path

from object_calibration import (
    append_point,
    apply_affine_transform,
    apply_robot_plane_correction,
    apply_report_to_workspace_calibration,
    compute_session_report,
    fit_affine_transform,
    fit_robot_plane_correction,
    new_session,
    round_object_candidates,
    store_scan,
)
from camera_controller import CameraController
from workspace_calibration import (
    STALE_WORKSPACE_CALIBRATION_ERROR,
    build_default_calibration_from_report,
    load_json,
    save_workspace_calibration,
)


REPORT_PATH = Path("runtime_state/calibration_grid_outer_test/live_report.json")


def robot_from_workspace(x, y):
    return {
        "x": 1.2 * x - 0.2 * y + 30.0,
        "y": 0.15 * x + 0.95 * y - 40.0,
        "z": -150.0,
        "r": 0.0,
    }


def synthetic_points(count=9):
    workspace_points = [
        (50.0, 40.0),
        (250.0, 40.0),
        (60.0, 190.0),
        (450.0, 40.0),
        (280.0, 210.0),
        (500.0, 180.0),
        (80.0, 340.0),
        (300.0, 350.0),
        (520.0, 330.0),
    ][:count]
    return [
        {
            "point_id": f"Arm1-P{index + 1}",
            "workspace_xy": {"x": x, "y": y},
            "robot_pose": robot_from_workspace(x, y),
        }
        for index, (x, y) in enumerate(workspace_points)
    ]


class ObjectCalibrationTest(unittest.TestCase):
    def test_affine_fit_recovers_workspace_to_robot_transform(self):
        result = fit_affine_transform(synthetic_points())
        transform = result["transform"]

        self.assertAlmostEqual(transform["a"], 1.2, places=6)
        self.assertAlmostEqual(transform["b"], -0.2, places=6)
        self.assertAlmostEqual(transform["tx"], 30.0, places=6)
        self.assertAlmostEqual(transform["c"], 0.15, places=6)
        self.assertAlmostEqual(transform["d"], 0.95, places=6)
        self.assertAlmostEqual(transform["ty"], -40.0, places=6)
        self.assertLess(result["fit_max_error_mm"], 1e-9)

    def test_affine_fit_accepts_three_or_more_points(self):
        result = fit_affine_transform(synthetic_points(3))

        self.assertEqual(len(result["fit_errors_mm"]), 3)
        self.assertLess(result["fit_max_error_mm"], 1e-9)

    def test_affine_fit_rejects_fewer_than_three_points(self):
        with self.assertRaisesRegex(ValueError, "At least 3 calibration points"):
            fit_affine_transform(synthetic_points(2))

    def test_robot_plane_correction_maps_predicted_to_actual_pose(self):
        points = [
            {
                "predicted_robot_plane": {"x": 100.0, "y": 20.0},
                "actual_robot_pose": {"x": 104.0, "y": 19.0},
            },
            {
                "predicted_robot_plane": {"x": 200.0, "y": 20.0},
                "actual_robot_pose": {"x": 206.0, "y": 20.5},
            },
            {
                "predicted_robot_plane": {"x": 100.0, "y": 120.0},
                "actual_robot_pose": {"x": 103.0, "y": 124.0},
            },
            {
                "predicted_robot_plane": {"x": 200.0, "y": 120.0},
                "actual_robot_pose": {"x": 205.0, "y": 125.5},
            },
        ]

        result = fit_robot_plane_correction(points)
        corrected = apply_robot_plane_correction(
            result["correction"],
            {"x": 150.0, "y": 70.0},
        )

        self.assertLess(result["fit_max_error_mm_after"], 1e-9)
        self.assertAlmostEqual(corrected["x"], 154.5)
        self.assertAlmostEqual(corrected["y"], 72.25)

    def test_compute_session_requires_configured_point_count(self):
        session = new_session("Arm1")
        session["required_point_count"] = 4
        session["points"] = synthetic_points(3)

        with self.assertRaisesRegex(ValueError, "4 calibration points are required"):
            compute_session_report(session)

    def test_compute_session_report_accepts_dynamic_required_count(self):
        session = new_session("Arm1")
        session["required_point_count"] = 4
        session["detected_required_point_count"] = 4
        session["points"] = synthetic_points(4)

        report = compute_session_report(session)

        self.assertEqual(report["point_count"], 4)
        self.assertEqual(report["required_point_count"], 4)
        self.assertLess(report["fit_max_error_mm"], 1e-9)

    def round_scan(self, count):
        return {
            "ok": True,
            "timestamp": "scan-test",
            "camera_id": "Cam1",
            "frame": {"width": 640, "height": 480},
            "object_count": count,
            "outputs": {},
            "objects": [
                {
                    "id": f"obj-{index + 1}",
                    "color": "green",
                    "circularity": 0.9,
                    "area": 3200.0,
                    "bbox": {"x": 10, "y": 20, "w": 48, "h": 46},
                    "center_px": {"x": 20 + index * 30, "y": 40},
                    "workspace_xy": {"x": 50.0 + index * 40.0, "y": 70.0 + index * 20.0},
                }
                for index in range(count)
            ],
        }

    def test_store_scan_sets_required_count_to_detected_round_objects(self):
        session = new_session("Arm1")

        with tempfile.TemporaryDirectory() as temp_dir:
            session, scan = store_scan(session, self.round_scan(4), scan_path=Path(temp_dir) / "scan.json")

        self.assertEqual(scan["round_object_count"], 4)
        self.assertEqual(session["required_point_count"], 4)
        self.assertEqual(session["detected_required_point_count"], 4)

    def test_store_scan_keeps_nine_object_sessions_dynamic(self):
        session = new_session("Arm1")

        with tempfile.TemporaryDirectory() as temp_dir:
            session, scan = store_scan(session, self.round_scan(9), scan_path=Path(temp_dir) / "scan.json")

        self.assertEqual(scan["round_object_count"], 9)
        self.assertEqual(session["required_point_count"], 9)
        self.assertEqual(session["detected_required_point_count"], 9)

    def test_store_scan_uses_three_point_minimum_when_fewer_objects_detected(self):
        session = new_session("Arm1")

        with tempfile.TemporaryDirectory() as temp_dir:
            session, scan = store_scan(session, self.round_scan(2), scan_path=Path(temp_dir) / "scan.json")

        self.assertEqual(scan["round_object_count"], 2)
        self.assertEqual(session["required_point_count"], 3)
        self.assertEqual(session["detected_required_point_count"], 2)
        with self.assertRaisesRegex(ValueError, "latest scan found 2"):
            compute_session_report(session)

    def test_rescan_clears_existing_points_and_report(self):
        session = new_session("Arm1")
        session["required_point_count"] = 9
        session["points"] = synthetic_points(3)
        session["report"] = {"ok": True}

        with tempfile.TemporaryDirectory() as temp_dir:
            session, _ = store_scan(session, self.round_scan(4), scan_path=Path(temp_dir) / "scan.json")

        self.assertEqual(session["required_point_count"], 4)
        self.assertEqual(session["points"], [])
        self.assertIsNone(session["report"])

    def test_round_object_candidates_filter_by_circularity_and_workspace(self):
        objects = [
            {
                "id": "round",
                "circularity": 0.8,
                "area": 3000.0,
                "bbox": {"w": 40, "h": 38},
                "workspace_xy": {"x": 1.0, "y": 2.0},
            },
            {
                "id": "small",
                "circularity": 0.95,
                "area": 800.0,
                "bbox": {"w": 20, "h": 20},
                "workspace_xy": {"x": 1.0, "y": 2.0},
            },
            {
                "id": "thin",
                "circularity": 0.2,
                "area": 3000.0,
                "bbox": {"w": 80, "h": 20},
                "workspace_xy": {"x": 1.0, "y": 2.0},
            },
            {
                "id": "outside",
                "circularity": 0.9,
                "area": 3000.0,
                "bbox": {"w": 40, "h": 40},
                "workspace_xy": None,
            },
        ]

        candidates = round_object_candidates(objects, min_circularity=0.55)

        self.assertEqual([item["id"] for item in candidates], ["round"])
        self.assertTrue(candidates[0]["round_candidate"])

    def test_append_point_records_object_and_pose(self):
        session = new_session("Arm1")
        selected_object = {
            "id": "obj-1",
            "slot": 1,
            "color": "green",
            "center_px": {"x": 10, "y": 20},
            "bbox": {"x": 5, "y": 15, "w": 10, "h": 10},
            "area": 100.0,
            "circularity": 0.85,
            "workspace_xy": {"x": 120.0, "y": 80.0},
        }
        pose_state = {
            "ok": True,
            "pose": {"x": 111.0, "y": -22.0, "z": -150.0, "r": 3.0},
            "robot_mode": 6,
            "robot_mode_name": "backdrive",
        }

        session, point = append_point(session, selected_object, pose_state)

        self.assertEqual(len(session["points"]), 1)
        self.assertEqual(point["point_id"], "Arm1-P1")
        self.assertEqual(point["point_index"], 1)
        self.assertEqual(point["object"]["id"], "obj-1")
        self.assertEqual(point["object"]["slot"], 1)
        self.assertEqual(point["workspace_xy"], {"x": 120.0, "y": 80.0})
        self.assertEqual(point["robot_pose"]["x"], 111.0)
        self.assertEqual(point["robot_mode_name"], "backdrive")

    def test_append_point_uses_object_slot_for_point_order(self):
        session = new_session("Arm1")
        selected_object = {
            "id": "obj-7",
            "slot": 7,
            "color": "red",
            "center_px": {"x": 70, "y": 80},
            "bbox": {"x": 10, "y": 20, "w": 100, "h": 100},
            "area": 9000.0,
            "circularity": 0.9,
            "workspace_xy": {"x": 320.0, "y": 240.0},
        }
        pose_state = {
            "ok": True,
            "pose": {"x": 222.0, "y": -33.0, "z": -150.0, "r": 0.0},
        }

        session, point = append_point(session, selected_object, pose_state)

        self.assertEqual(len(session["points"]), 1)
        self.assertEqual(point["point_id"], "Arm1-P7")
        self.assertEqual(point["point_index"], 7)
        self.assertEqual(session["points"][0]["point_index"], 7)

    def test_apply_report_to_workspace_calibration_persists_transform(self):
        report = {
            "session_id": "object_calibration_test",
            "arm_id": "Arm1",
            "point_count": 9,
            "fit_mean_error_mm": 0.25,
            "fit_max_error_mm": 0.5,
            "fit_rms_error_mm": 0.3,
            "transform": {
                "type": "affine",
                "a": 1.2,
                "b": -0.2,
                "tx": 30.0,
                "c": 0.15,
                "d": 0.95,
                "ty": -40.0,
            },
        }
        base = build_default_calibration_from_report(
            load_json(REPORT_PATH),
            outer_width_mm=600.0,
            outer_height_mm=400.0,
            robot_id="Arm1",
            robot_edge="bottom",
            robot_center_to_outer_center_mm=350.0,
            min_reach_mm=150.0,
            max_reach_mm=440.0,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "workspace_calibration.json"
            save_workspace_calibration(base, path)
            saved = apply_report_to_workspace_calibration(report, path)

        transform = saved["robot"]["workspace_to_robot_transform"]
        self.assertTrue(transform["enabled"])
        self.assertEqual(transform["source"], "object_calibration")
        self.assertEqual(transform["arm_id"], "Arm1")
        self.assertAlmostEqual(
            apply_affine_transform(transform, {"x": 100.0, "y": 50.0})["x"],
            140.0,
        )

    def test_camera_workspace_mapping_prefers_applied_object_transform(self):
        controller = CameraController()
        calibration = {
            "outer_box": {"width_mm": 600.0, "height_mm": 400.0},
            "robot": {
                "robot_id": "Arm1",
                "edge": "bottom",
                "robot_center_to_outer_center_mm": 350.0,
                "min_reach_mm": 0.0,
                "max_reach_mm": 1000.0,
                "workspace_to_robot_transform": {
                    "enabled": True,
                    "arm_id": "Arm1",
                    "type": "affine",
                    "a": 1.2,
                    "b": -0.2,
                    "tx": 30.0,
                    "c": 0.15,
                    "d": 0.95,
                    "ty": -40.0,
                },
            },
        }

        per_arm = controller._compute_workspace_robot_plane(
            calibration,
            {"x": 100.0, "y": 50.0},
        )

        self.assertEqual(per_arm["Arm1"]["calibration"], "object_workspace_affine")
        self.assertAlmostEqual(per_arm["Arm1"]["x"], 140.0)
        self.assertAlmostEqual(per_arm["Arm1"]["y"], 22.5)

    def test_camera_workspace_mapping_applies_robot_plane_correction(self):
        controller = CameraController()
        calibration = {
            "outer_box": {"width_mm": 600.0, "height_mm": 400.0},
            "robot": {
                "robot_id": "Arm1",
                "edge": "bottom",
                "robot_center_to_outer_center_mm": 350.0,
                "min_reach_mm": 0.0,
                "max_reach_mm": 1000.0,
                "workspace_to_robot_transform": {
                    "enabled": True,
                    "arm_id": "Arm1",
                    "type": "affine",
                    "a": 1.2,
                    "b": -0.2,
                    "tx": 30.0,
                    "c": 0.15,
                    "d": 0.95,
                    "ty": -40.0,
                    "robot_plane_correction": {
                        "enabled": True,
                        "type": "affine",
                        "source": "test",
                        "point_count": 3,
                        "a": 1.0,
                        "b": 0.0,
                        "tx": -2.0,
                        "c": 0.0,
                        "d": 1.0,
                        "ty": 3.0,
                        "fit_mean_error_mm_after": 0.1,
                        "fit_max_error_mm_after": 0.2,
                    },
                },
            },
        }

        per_arm = controller._compute_workspace_robot_plane(
            calibration,
            {"x": 100.0, "y": 50.0},
        )

        self.assertEqual(per_arm["Arm1"]["calibration"], "object_workspace_affine_corrected")
        self.assertAlmostEqual(per_arm["Arm1"]["x"], 138.0)
        self.assertAlmostEqual(per_arm["Arm1"]["y"], 25.5)
        self.assertAlmostEqual(per_arm["Arm1"]["uncorrected_robot_plane"]["x"], 140.0)
        self.assertEqual(per_arm["Arm1"]["robot_plane_correction"]["point_count"], 3)

    def test_camera_uses_detection_report_fallback_when_saved_calibration_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            missing_calibration = temp_path / "workspace_calibration.json"
            report_path = temp_path / "live_report.json"
            report_path.write_text(REPORT_PATH.read_text(encoding="utf-8"), encoding="utf-8")

            controller = CameraController(
                workspace_calibration_path=missing_calibration,
                workspace_detection_report_path=report_path,
            )
            calibration = controller._workspace_calibration()

        self.assertIsNotNone(calibration)
        self.assertEqual(calibration["_runtime_source"], "detection_report_fallback")
        self.assertTrue((calibration.get("validation") or {}).get("ok"))
        self.assertEqual(calibration["outer_box"]["width_mm"], 600.0)

    def test_camera_prefers_latest_detection_report_when_saved_calibration_is_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            calibration_path = temp_path / "workspace_calibration.json"
            report_path = temp_path / "live_report.json"
            report = load_json(REPORT_PATH)
            report_path.write_text(REPORT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            stale = build_default_calibration_from_report(
                report,
                outer_width_mm=600.0,
                outer_height_mm=400.0,
                robot_id="Arm1",
                robot_edge="bottom",
                robot_center_to_outer_center_mm=350.0,
                min_reach_mm=0.0,
                max_reach_mm=1000.0,
            )
            stale["source_report"]["timestamp"] = "old-report"
            stale["outer_box"]["corners_px"] = [
                [10.0, 10.0],
                [210.0, 10.0],
                [210.0, 110.0],
                [10.0, 110.0],
            ]
            stale["outer_box"]["transform_corners_px"] = [
                [10.0, 10.0],
                [210.0, 10.0],
                [10.0, 110.0],
                [210.0, 110.0],
            ]
            save_workspace_calibration(stale, calibration_path)

            controller = CameraController(
                workspace_calibration_path=calibration_path,
                workspace_detection_report_path=report_path,
            )
            calibration = controller._workspace_calibration()

        self.assertEqual(calibration["_runtime_source"], "detection_report_latest")
        self.assertEqual(calibration["source_report"]["timestamp"], report["timestamp"])
        self.assertEqual(calibration["outer_box"]["corners_px"], report["outer_box"]["corners_px"])

    def test_camera_assign_targets_requires_saved_current_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            calibration_path = temp_path / "workspace_calibration.json"
            report_path = temp_path / "live_report.json"
            report = load_json(REPORT_PATH)
            report_path.write_text(REPORT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            stale = build_default_calibration_from_report(
                report,
                outer_width_mm=600.0,
                outer_height_mm=400.0,
                robot_id="Arm1",
                robot_edge="bottom",
                robot_center_to_outer_center_mm=350.0,
                min_reach_mm=0.0,
                max_reach_mm=1000.0,
            )
            stale["source_report"]["timestamp"] = "old-report"
            save_workspace_calibration(stale, calibration_path)

            controller = CameraController(
                workspace_calibration_path=calibration_path,
                workspace_detection_report_path=report_path,
            )
            result = controller.assign_targets(
                [{"arm_id": "Arm1", "color": "any"}],
                require_saved_workspace=True,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], STALE_WORKSPACE_CALIBRATION_ERROR)
        self.assertEqual(result["workspace"]["source"], "none")
        self.assertTrue(result["workspace"]["stale_saved"])
        self.assertFalse(result["workspace"]["saved"])
        self.assertEqual(result["workspace"]["report_timestamp"], report["timestamp"])
        self.assertEqual(result["workspace"]["saved_report_timestamp"], "old-report")

    def test_camera_color_detector_finds_round_rgb_objects_inside_workspace_only(self):
        import cv2
        import numpy as np

        image = np.zeros((360, 480, 3), dtype=np.uint8)
        workspace = np.float32([[40, 40], [440, 40], [440, 320], [40, 320]]).reshape((-1, 1, 2))
        cv2.circle(image, (120, 120), 34, (0, 0, 210), -1)
        cv2.circle(image, (230, 170), 34, (0, 135, 0), -1)
        cv2.circle(image, (340, 225), 34, (190, 60, 40), -1)
        cv2.circle(image, (455, 150), 34, (0, 160, 0), -1)
        cv2.rectangle(image, (80, 260), (220, 305), (0, 0, 210), -1)

        matrix = cv2.getPerspectiveTransform(
            np.float32([[40, 40], [440, 40], [40, 320], [440, 320]]),
            np.float32([[0, 0], [400, 0], [0, 280], [400, 280]]),
        )
        controller = CameraController()
        config = {
            "mode": "bgr",
            "color_object_detection_enabled": True,
            "legacy_saturation_detection_enabled": False,
            "detectable_object_colors": ["red", "green", "blue"],
            "min_object_area": 1200,
            "max_object_area_px": 9000,
            "min_object_diameter_px": 30,
            "max_object_diameter_px": 90,
            "min_object_circularity": 0.65,
            "edge_reject_margin_px": 0,
            "color_hough_round_enabled": False,
        }

        objects = controller._detect_objects(
            image,
            workspace,
            config,
            matrix=None,
            arm_matrices={},
            workspace_report_matrix=matrix,
            width_mm=400.0,
            height_mm=280.0,
            workspace_calibration=None,
        )

        self.assertEqual([obj["color"] for obj in objects], ["red", "green", "blue"])
        self.assertTrue(all(obj["shape"] == "circle" for obj in objects))
        self.assertTrue(all(obj["inside_workspace"] for obj in objects))
        self.assertTrue(all(obj["detection_source"] == "color_contour" for obj in objects))


if __name__ == "__main__":
    unittest.main()
