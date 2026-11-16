import tempfile
import unittest
from pathlib import Path

import calibration_web
from workspace_calibration import load_json, save_workspace_calibration


REPORT_PATH = Path("runtime_state/calibration_grid_outer_test/image_report.json")


class CalibrationWebStateTest(unittest.TestCase):
    def manual_geometry(self, boxes=None):
        return {
            "frame": {"width": 400, "height": 300},
            "outer_box": {
                "corners_px": [
                    [300, 40],
                    [40, 30],
                    [310, 220],
                    [35, 230],
                ]
            },
            "boxes": boxes or [],
        }

    def test_calibration_exposes_all_configured_arms_for_selection(self):
        payload = calibration_web.ensure_calibration_arm_profiles({})

        self.assertEqual(set(payload["arms"]), {"Arm1", "Arm2"})
        self.assertEqual(payload["arms"]["Arm1"]["robot_id"], "Arm1")
        self.assertEqual(payload["arms"]["Arm2"]["robot_id"], "Arm2")
        self.assertIn('id="armSelect"', calibration_web.INDEX_HTML)
        self.assertIn('id="armSelectionStatus"', calibration_web.INDEX_HTML)
        self.assertIn('id="workspaceArmSelect"', calibration_web.INDEX_HTML)
        self.assertIn('id="workspaceArmSelectionStatus"', calibration_web.INDEX_HTML)
        self.assertNotIn('Robot</h2><label for="armSelect"', calibration_web.INDEX_HTML)
        self.assertLess(
            calibration_web.INDEX_HTML.index('<h2>Robot</h2>'),
            calibration_web.INDEX_HTML.index('id="workspaceArmSelect"'),
        )
        self.assertLess(
            calibration_web.INDEX_HTML.index('<h2>Object Calibration</h2>'),
            calibration_web.INDEX_HTML.index('id="armSelect"'),
        )
    def test_stale_saved_calibration_uses_latest_report_geometry(self):
        report = load_json(REPORT_PATH)
        saved = calibration_web.enrich_calibration(calibration_web.draft_from_report(report))
        saved["source_report"]["timestamp"] = "old-report"
        saved["outer_box"]["width_mm"] = 400.0
        saved["outer_box"]["height_mm"] = 600.0
        saved["robot"]["robot_center_to_outer_center_mm"] = 250.0
        saved["zones"][0]["role"] = "pick"
        saved["zones"][0]["width_mm"] = 123.0

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "live_report.json"
            calibration_path = Path(temp_dir) / "workspace_calibration.json"
            report_path.write_text(REPORT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            save_workspace_calibration(saved, calibration_path)

            payload = calibration_web.response_payload(report_path, calibration_path)

        self.assertFalse(payload["saved"])
        self.assertTrue(payload["saved_exists"])
        self.assertTrue(payload["calibration_status"]["stale_saved"])
        self.assertEqual(payload["calibration"]["source_report"]["timestamp"], report["timestamp"])
        self.assertEqual(payload["calibration"]["outer_box"]["corners_px"], report["outer_box"]["corners_px"])
        self.assertEqual(payload["calibration"]["outer_box"]["width_mm"], 400.0)
        self.assertEqual(payload["calibration"]["outer_box"]["height_mm"], 600.0)
        self.assertEqual(payload["calibration"]["robot"]["robot_center_to_outer_center_mm"], 250.0)
        self.assertEqual(payload["calibration"]["zones"][0]["role"], "pick")
        self.assertEqual(payload["calibration"]["zones"][0]["width_mm"], 123.0)

    def test_matching_saved_calibration_is_used_directly(self):
        report = load_json(REPORT_PATH)
        saved = calibration_web.enrich_calibration(calibration_web.draft_from_report(report))
        saved["outer_box"]["width_mm"] = 401.0

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "live_report.json"
            calibration_path = Path(temp_dir) / "workspace_calibration.json"
            report_path.write_text(REPORT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            save_workspace_calibration(saved, calibration_path)

            payload = calibration_web.response_payload(report_path, calibration_path)

        self.assertTrue(payload["saved"])
        self.assertFalse(payload["calibration_status"]["stale_saved"])
        self.assertEqual(payload["calibration"]["outer_box"]["width_mm"], 401.0)

    def test_report_image_paths_survive_workspace_move(self):
        stale_absolute_path = r"PATH_TO_IF_EDUBOT_ARC\runtime_state\calibration_grid_outer_test\live_source_image.png"

        resolved = calibration_web.resolve_runtime_path(stale_absolute_path)

        self.assertEqual(
            resolved,
            calibration_web.ROOT / "runtime_state" / "calibration_grid_outer_test" / "live_source_image.png",
        )
        self.assertTrue(resolved.exists())

    def test_missing_outer_box_falls_back_to_saved_calibration(self):
        report_path = Path("runtime_state/calibration_grid_outer_test/live_report.json")
        report = load_json(report_path)
        if report.get("outer_box") is not None:
            self.skipTest("live report currently includes outer_box")
        saved = calibration_web.enrich_calibration(calibration_web.draft_from_report(load_json(REPORT_PATH)))

        with tempfile.TemporaryDirectory() as temp_dir:
            calibration_path = Path(temp_dir) / "workspace_calibration.json"
            save_workspace_calibration(saved, calibration_path)

            payload = calibration_web.response_payload(report_path, calibration_path)

        self.assertTrue(payload["saved"])
        self.assertFalse(payload["calibration_status"]["detection_ready"])
        self.assertEqual(payload["calibration"]["outer_box"]["corners_px"], saved["outer_box"]["corners_px"])

    def test_manual_outer_only_report_is_detection_ready(self):
        report = calibration_web.build_manual_geometry_report(self.manual_geometry())

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "live_report.json"
            calibration_path = Path(temp_dir) / "workspace_calibration.json"
            report_path.write_text(calibration_web.json.dumps(report, ensure_ascii=False), encoding="utf-8")

            payload = calibration_web.response_payload(report_path, calibration_path)

        self.assertTrue(payload["calibration_status"]["detection_ready"])
        self.assertEqual(payload["report"]["source"], "manual_geometry")
        self.assertEqual(payload["calibration"]["zones"], [])

    def test_manual_inner_boxes_become_calibration_zones(self):
        report = calibration_web.build_manual_geometry_report(
            self.manual_geometry(
                boxes=[
                    {"corners_px": [[70, 60], [160, 58], [164, 130], [68, 132]]},
                    {"label": "Custom", "corners_px": [[180, 70], [250, 70], [248, 145], [182, 142]]},
                ]
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "live_report.json"
            calibration_path = Path(temp_dir) / "workspace_calibration.json"
            report_path.write_text(calibration_web.json.dumps(report, ensure_ascii=False), encoding="utf-8")

            payload = calibration_web.response_payload(report_path, calibration_path)

        self.assertTrue(payload["calibration_status"]["detection_ready"])
        self.assertEqual([zone["name"] for zone in payload["calibration"]["zones"]], ["A1", "Custom"])
        self.assertEqual(len(payload["calibration"]["zones"]), 2)

    def test_manual_storage_boxes_become_storage_zones(self):
        geometry = self.manual_geometry(
            boxes=[
                {"label": "A1", "corners_px": [[70, 60], [160, 58], [164, 130], [68, 132]]},
            ]
        )
        geometry["storage_boxes"] = [
            {
                "label": "S1",
                "role": "storage",
                "corners_px": [[20, 200], [90, 200], [90, 280], [20, 280]],
            },
            {
                "label": "DropLeft",
                "role": "drop",
                "corners_px": [[300, 200], [370, 200], [370, 280], [300, 280]],
            },
        ]
        report = calibration_web.build_manual_geometry_report(geometry)

        self.assertEqual(report["inner_box_count"], 1)
        self.assertEqual(report["storage_box_count"], 2)
        self.assertEqual(report["box_count"], 3)
        self.assertEqual([box["label"] for box in report["storage_boxes"]], ["S1", "DropLeft"])
        self.assertEqual([box["role"] for box in report["storage_boxes"]], ["storage", "drop"])

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "live_report.json"
            calibration_path = Path(temp_dir) / "workspace_calibration.json"
            report_path.write_text(calibration_web.json.dumps(report, ensure_ascii=False), encoding="utf-8")
            payload = calibration_web.response_payload(report_path, calibration_path)

        zones = payload["calibration"]["zones"]
        self.assertEqual([zone["name"] for zone in zones], ["A1", "S1", "DropLeft"])
        self.assertEqual([zone["role"] for zone in zones], ["custom", "storage", "drop"])

    def test_manual_transform_corner_order_matches_schema(self):
        report = calibration_web.build_manual_geometry_report(self.manual_geometry())
        outer = report["outer_box"]

        self.assertEqual(outer["corners_px"], [[40.0, 30.0], [300.0, 40.0], [310.0, 220.0], [35.0, 230.0]])
        self.assertEqual(
            outer["transform_corners_px"],
            [[40.0, 30.0], [300.0, 40.0], [35.0, 230.0], [310.0, 220.0]],
        )

    def test_manual_corner_order_handles_diamond_shape(self):
        report = calibration_web.build_manual_geometry_report(
            {
                "outer_box": {"corners_px": [[200, 20], [350, 150], [200, 280], [50, 150]]},
                "boxes": [],
            }
        )

        outer = report["outer_box"]
        self.assertEqual(len({tuple(point) for point in outer["corners_px"]}), 4)
        self.assertEqual(len({tuple(point) for point in outer["transform_corners_px"]}), 4)
        self.assertEqual(set(map(tuple, outer["corners_px"])), set(map(tuple, outer["transform_corners_px"])))
        self.assertGreater(outer["area"], 0.0)

    def test_manual_report_rejects_invalid_outer_geometry(self):
        with self.assertRaises(ValueError):
            calibration_web.build_manual_geometry_report(
                {
                    "outer_box": {"corners_px": [[10, 10], [20, 20], [30, 30], [40, 40]]},
                    "boxes": [],
                }
            )

        with self.assertRaises(ValueError):
            calibration_web.build_manual_geometry_report(
                {
                    "outer_box": {"corners_px": [[10, 10], [20, 20], [30, 30]]},
                    "boxes": [],
                }
            )


if __name__ == "__main__":
    unittest.main()





