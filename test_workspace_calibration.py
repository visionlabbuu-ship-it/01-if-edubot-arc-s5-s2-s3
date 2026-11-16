import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workspace_calibration import (
    STALE_WORKSPACE_CALIBRATION_ERROR,
    build_default_calibration_from_report,
    enrich_calibration,
    load_json,
    load_workspace_calibration,
    resolve_workspace_calibration,
    robot_center_workspace_xy,
    save_workspace_calibration,
    workspace_to_robot_local,
)
from workspace_model import build_workspace_layout


REPORT_PATH = Path("runtime_state/calibration_grid_outer_test/live_report.json")


class WorkspaceCalibrationTest(unittest.TestCase):
    def _calibration(self):
        report = load_json(REPORT_PATH)
        calibration = build_default_calibration_from_report(
            report,
            outer_width_mm=600.0,
            outer_height_mm=400.0,
            robot_id="Arm1",
            robot_edge="bottom",
            robot_center_to_outer_center_mm=350.0,
            min_reach_mm=150.0,
            max_reach_mm=440.0,
        )
        for zone in calibration["zones"]:
            zone["width_mm"] = 200.0
            zone["height_mm"] = 200.0
        return calibration

    def test_enrich_maps_outer_center_to_workspace_center(self):
        enriched = enrich_calibration(self._calibration())

        self.assertTrue(enriched["validation"]["ok"])
        self.assertAlmostEqual(enriched["outer_box"]["center_mm"]["x"], 300.0, delta=3.0)
        self.assertAlmostEqual(enriched["outer_box"]["center_mm"]["y"], 200.0, delta=3.0)

    def test_enrich_uses_non_crossing_polygon_corner_order(self):
        enriched = enrich_calibration(self._calibration())

        corners = enriched["outer_box"]["corners_px"]
        self.assertLess(corners[0][1], corners[2][1])
        self.assertLess(corners[1][1], corners[3][1])
        self.assertLess(corners[0][0], corners[1][0])
        self.assertLess(corners[3][0], corners[2][0])

    def test_default_report_derives_outer_quad_from_child_corners(self):
        report = load_json(REPORT_PATH)
        report["outer_box"] = {
            "source": "outer_from_children",
            "bbox": {"x": 918, "y": 52, "w": 1086, "h": 1454},
            "center_px": {"x": 1461.0, "y": 779.0},
            "corners_px": [
                [918.0, 52.0],
                [2004.0, 52.0],
                [918.0, 1506.0],
                [2004.0, 1506.0],
            ],
        }
        calibration = build_default_calibration_from_report(
            report,
            outer_width_mm=600.0,
            outer_height_mm=400.0,
            robot_id="Arm1",
            robot_edge="bottom",
            robot_center_to_outer_center_mm=350.0,
            min_reach_mm=150.0,
            max_reach_mm=440.0,
        )

        self.assertEqual(calibration["outer_box"]["source"], "outer_quad_from_children")
        self.assertEqual(calibration["outer_box"]["quad_source"], "derived_from_child_corners")
        self.assertNotEqual(
            calibration["outer_box"]["corners_px"],
            [
                [918.0, 52.0],
                [2004.0, 52.0],
                [918.0, 1506.0],
                [2004.0, 1506.0],
            ],
        )

    def test_robot_center_by_edge_uses_outer_center_distance(self):
        robot = {"edge": "left", "robot_center_to_outer_center_mm": 350.0}

        self.assertEqual(
            robot_center_workspace_xy(robot, 600.0, 400.0),
            {"x": -50.0, "y": 200.0},
        )

    def test_workspace_to_robot_local_bottom_edge_faces_inward(self):
        robot = {"edge": "bottom", "robot_center_to_outer_center_mm": 350.0}

        local = workspace_to_robot_local({"x": 300.0, "y": 200.0}, robot, 600.0, 400.0)

        self.assertAlmostEqual(local["x"], 350.0)
        self.assertAlmostEqual(local["y"], 0.0)

    def test_invalid_calibration_is_not_written(self):
        calibration = self._calibration()
        calibration["outer_box"]["width_mm"] = 0.0

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "workspace_calibration.json"
            result = save_workspace_calibration(calibration, path)

            self.assertFalse(result["validation"]["ok"])
            self.assertFalse(path.exists())

    def test_saved_calibration_loads_enriched(self):
        calibration = self._calibration()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "workspace_calibration.json"
            save_workspace_calibration(calibration, path)
            loaded = load_workspace_calibration(path)

            self.assertTrue(loaded["validation"]["ok"])
            self.assertIn("robot_center_workspace_xy_mm", loaded["robot"])
            self.assertIn("robot_center_to_outer_center_mm", loaded["robot"])
            self.assertNotIn("base_workspace_xy_mm", loaded["robot"])
            self.assertNotIn("base_to_outer_center_mm", loaded["robot"])
            self.assertEqual(len(loaded["zones"]), len(calibration["zones"]))

    def test_legacy_base_distance_field_is_normalized_to_robot_center(self):
        calibration = self._calibration()
        calibration["robot"].pop("robot_center_to_outer_center_mm", None)
        calibration["robot"]["base_to_outer_center_mm"] = 350.0

        enriched = enrich_calibration(calibration)

        self.assertTrue(enriched["validation"]["ok"])
        self.assertEqual(enriched["robot"]["robot_center_to_outer_center_mm"], 350.0)
        self.assertNotIn("base_to_outer_center_mm", enriched["robot"])

    def test_resolver_rejects_required_saved_workspace_when_report_is_newer(self):
        report = load_json(REPORT_PATH)
        stale = self._calibration()
        stale["source_report"]["timestamp"] = "old-report"

        with tempfile.TemporaryDirectory() as temp_dir:
            calibration_path = Path(temp_dir) / "workspace_calibration.json"
            report_path = Path(temp_dir) / "live_report.json"
            report_path.write_text(REPORT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            save_workspace_calibration(stale, calibration_path)

            result = resolve_workspace_calibration(
                calibration_path,
                report_path,
                require_saved=True,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], STALE_WORKSPACE_CALIBRATION_ERROR)
        self.assertTrue(result["stale_saved"])
        self.assertEqual(result["report_timestamp"], report["timestamp"])
        self.assertEqual(result["saved_report_timestamp"], "old-report")

    def test_resolver_allows_required_saved_workspace_when_timestamps_match(self):
        report = load_json(REPORT_PATH)
        saved = self._calibration()

        with tempfile.TemporaryDirectory() as temp_dir:
            calibration_path = Path(temp_dir) / "workspace_calibration.json"
            report_path = Path(temp_dir) / "live_report.json"
            report_path.write_text(REPORT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            save_workspace_calibration(saved, calibration_path)

            result = resolve_workspace_calibration(
                calibration_path,
                report_path,
                require_saved=True,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["saved"])
        self.assertFalse(result["stale_saved"])
        self.assertEqual(result["source"], "workspace_calibration")
        self.assertEqual(result["report_timestamp"], report["timestamp"])

    def test_workspace_layout_reports_stale_latest_report_metadata(self):
        stale = self._calibration()
        stale["source_report"]["timestamp"] = "old-report"

        with tempfile.TemporaryDirectory() as temp_dir:
            calibration_path = Path(temp_dir) / "workspace_calibration.json"
            report_path = Path(temp_dir) / "live_report.json"
            report_path.write_text(REPORT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            save_workspace_calibration(stale, calibration_path)

            with patch("workspace_model.DEFAULT_CALIBRATION_PATH", calibration_path), patch(
                "workspace_model.DEFAULT_DETECTION_REPORT_PATH",
                report_path,
            ):
                layout = build_workspace_layout()

        status = layout["source"]["workspace_calibration"]
        self.assertTrue(layout["ok"])
        self.assertFalse(status["using_saved"])
        self.assertTrue(status["stale_saved"])
        self.assertEqual(status["source"], "detection_report_latest")
        self.assertEqual(layout["workspace"]["calibrated"]["source"], "detection_report_latest")
        self.assertFalse(layout["workspace"]["calibrated"]["saved"])


if __name__ == "__main__":
    unittest.main()
