import unittest
from unittest.mock import patch

from arc_runtime.tool_context import build_tool_context


class ToolContextTest(unittest.TestCase):
    @patch("arc_runtime.tool_context.load_storage")
    @patch("arc_runtime.tool_context.get_calibrated_workspace_model")
    @patch("arc_runtime.tool_context.active_robot_ids", return_value=["Arm1", "Arm2"])
    def test_context_is_compact_and_uses_per_arm_motion_contract(
        self, active_robot_ids, workspace_model, load_storage
    ):
        workspace_model.return_value = {
            "readiness": {"workspace_ready": True, "storage_regions_ready": True},
            "errors": [],
            "arms": {
                arm_id: {
                    "geometry_ready": True,
                    "profile": {
                        "min_reach_mm": 130,
                        "max_reach_mm": 440,
                        "workspace_to_robot_transform": {
                            "enabled": True,
                            "arm_id": arm_id,
                        },
                    },
                }
                for arm_id in ("Arm1", "Arm2")
            },
        }
        load_storage.return_value = {
            "expected_count": 16,
            "updated_at": "2026-08-09T00:00:00+00:00",
            "summary": {
                "empty": 13,
                "occupied": 3,
                "unknown": 0,
                "by_group": {"LB": {"total": 4, "empty": 2, "occupied": 2, "occupied_by_color": {"blue": 2}}},
            },
            "slots": [{"name": f"LT{i}"} for i in range(1, 17)],
        }

        result = build_tool_context(online_arms=["Arm1"])

        self.assertTrue(result["ok"])
        self.assertEqual(result["source_of_truth"]["motion_xy"], "per_arm_robot_plane[arm_id]")
        self.assertEqual(result["scan_contract"]["camera_scan"], "objects and per-arm robot coordinates")
        self.assertEqual(result["storage"]["slot_count"], 16)
        self.assertIn("color_locations", result["scan_contract"]["scan_storage_slots"])
        self.assertEqual(result["storage"]["groups"]["LB"]["occupied_by_color"], {"blue": 2})
        self.assertTrue(result["arms"]["Arm1"]["online"])
        self.assertFalse(result["arms"]["Arm2"]["online"])


if __name__ == "__main__":
    unittest.main()
