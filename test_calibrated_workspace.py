import unittest
from unittest.mock import patch

from arc_runtime.calibrated_workspace import (
    build_calibrated_workspace,
    filter_objects,
    plan_calibrated_intent,
    resolve_work_zone,
)
from workspace_model import build_workspace_layout


def arm(arm_id, tx=0.0):
    return {
        "robot_id": arm_id,
        "configured": True,
        "edge": "left" if arm_id == "Arm1" else "right",
        "robot_center_to_outer_center_mm": 350.0,
        "min_reach_mm": 0.0,
        "max_reach_mm": 1000.0,
        "workspace_to_robot_transform": {
            "enabled": True,
            "arm_id": arm_id,
            "a": 1.0,
            "b": 0.0,
            "c": 0.0,
            "d": 1.0,
            "tx": tx,
            "ty": 0.0,
        },
    }


def calibration(handoff=False):
    payload = {
        "outer_box": {"width_mm": 600.0, "height_mm": 400.0},
        "arms": {"Arm1": arm("Arm1"), "Arm2": arm("Arm2", tx=100.0)},
        "robot": arm("Arm1"),
        "zones": [
            {
                "name": "A1",
                "label": "Input A1",
                "role": "custom",
                "polygon_mm": [
                    {"x": 0.0, "y": 0.0},
                    {"x": 100.0, "y": 0.0},
                    {"x": 100.0, "y": 100.0},
                    {"x": 0.0, "y": 100.0},
                ],
            },
            {
                "name": "A2",
                "role": "work_zone",
                "polygon_mm": [
                    {"x": 100.0, "y": 0.0},
                    {"x": 200.0, "y": 0.0},
                    {"x": 200.0, "y": 100.0},
                    {"x": 100.0, "y": 100.0},
                ],
            },
        ],
        "storage_regions": [
            {"region_id": f"region-{group}", "group": group, "label": f"Box {group}"}
            for group in ("LT", "LB", "RT", "RB")
        ],
    }
    if handoff:
        payload["handoff_station"] = {"id": "H1", "enabled": True, "workspace_xy": {"x": 300, "y": 200}}
    return payload


def registry():
    return {
        "expected_count": 4,
        "slots": [
            {
                "name": f"{group}1",
                "group": group,
                "storage_region_id": f"region-{group}",
                "occupancy": "empty",
                "index_in_cluster": 1,
                "workspace_xy": {"x": 50.0, "y": 50.0},
                "per_arm_robot_plane": {
                    "Arm1": {"x": 50.0, "y": 50.0, "reachable": True},
                    "Arm2": {"x": 150.0, "y": 50.0, "reachable": True},
                },
            }
            for group in ("LT", "LB", "RT", "RB")
        ],
    }


class CalibratedWorkspaceTest(unittest.TestCase):
    def model(self, handoff=False):
        return build_calibrated_workspace(
            calibration(handoff=handoff),
            registry(),
            active_arms=["Arm1", "Arm2"],
            online_arms=["Arm1", "Arm2"],
        )

    def test_canonical_model_separates_work_and_storage_regions(self):
        model = self.model()

        self.assertEqual(set(model["work_zones"]), {"A1", "A2"})
        self.assertEqual(model["work_zones"]["A1"]["role"], "work_zone")
        self.assertEqual(set(model["storage_regions"]), {"LT", "LB", "RT", "RB"})
        self.assertEqual(len(model["storage_slots"]), 4)
        self.assertTrue(model["readiness"]["storage_regions_ready"])
        self.assertTrue(model["readiness"]["storage_registry_ready"])

    def test_zone_filter_uses_calibrated_polygon(self):
        objects = [
            {"id": "inside", "color": "red", "workspace_xy": {"x": 25, "y": 25}},
            {"id": "other", "color": "red", "workspace_xy": {"x": 125, "y": 25}},
        ]

        result = filter_objects(self.model(), objects, zone_reference="Input A1", color="red")

        self.assertTrue(result["ok"])
        self.assertEqual([item["id"] for item in result["objects"]], ["inside"])
        self.assertEqual(result["objects"][0]["source_zone_id"], "A1")
        self.assertTrue(resolve_work_zone(self.model(), "A2")["ok"])

    def test_direct_plan_selects_arm_that_reaches_both_ends(self):
        obj = {
            "id": "obj-1",
            "color": "red",
            "workspace_xy": {"x": 25, "y": 25},
            "reachable_by": ["Arm1"],
            "per_arm_robot_plane": {"Arm1": {"x": 25, "y": 25}},
        }
        model = self.model()
        model["storage_slots_by_name"]["LT1"]["reachable_by"] = ["Arm1", "Arm2"]

        result = plan_calibrated_intent(model, [obj], "LT1", source_zone="A1")

        self.assertTrue(result["ok"])
        self.assertEqual(result["plan_type"], "direct")
        self.assertEqual(result["selected_arm"], "Arm1")
        self.assertFalse(result["requires_handoff"])

    def test_split_reach_requires_calibrated_handoff(self):
        obj = {
            "id": "obj-1",
            "color": "blue",
            "workspace_xy": {"x": 25, "y": 25},
            "reachable_by": ["Arm1"],
            "per_arm_robot_plane": {"Arm1": {"x": 25, "y": 25}},
        }
        without_handoff = self.model()
        without_handoff["storage_slots_by_name"]["RT1"]["reachable_by"] = ["Arm2"]
        blocked = plan_calibrated_intent(without_handoff, [obj], "RT1", source_zone="A1")
        self.assertEqual(blocked["error_code"], "handoff_unavailable")

        with_handoff = self.model(handoff=True)
        with_handoff["storage_slots_by_name"]["RT1"]["reachable_by"] = ["Arm2"]
        result = plan_calibrated_intent(with_handoff, [obj], "RT1", source_zone="A1")
        self.assertTrue(result["ok"])
        self.assertTrue(result["requires_handoff"])
        self.assertEqual(result["support_arm"], "Arm1")
        self.assertEqual(result["selected_arm"], "Arm2")
        self.assertLess(
            [step["action"] for step in result["steps"]].index("retreat"),
            [step["action"] for step in result["steps"]].index("pick_handoff"),
        )

    def test_applied_transform_overrides_stale_unconfigured_draft_flag(self):
        payload = calibration()
        payload["arms"]["Arm2"]["configured"] = False

        model = build_calibrated_workspace(payload, registry(), active_arms=["Arm1", "Arm2"])

        self.assertTrue(model["readiness"]["arms"]["Arm2"]["geometry_ready"])
        self.assertTrue(model["readiness"]["arms"]["Arm2"]["transform_ready"])
    def test_missing_transform_is_not_unreachable(self):
        payload = calibration()
        payload["arms"]["Arm2"]["workspace_to_robot_transform"]["enabled"] = False
        data = registry()
        for slot in data["slots"]:
            slot["per_arm_robot_plane"].pop("Arm2")

        model = build_calibrated_workspace(payload, data, active_arms=["Arm1", "Arm2"])

        self.assertEqual(model["storage_slots"][0]["reachability_status"]["Arm2"], "transform_not_ready")
        self.assertNotIn("Arm2", model["storage_slots"][0]["reachable_by"])

    def test_workspace_layout_exposes_real_slots_not_auto_slots(self):
        resolution = {"calibration": calibration(), "source": "test", "saved": True}
        with patch("workspace_model._runtime_workspace_resolution", return_value=resolution), patch(
            "workspace_model.load_storage_registry", return_value=registry()
        ), patch("workspace_model._active_arms", return_value=["Arm1", "Arm2"]):
            layout = build_workspace_layout()

        self.assertEqual(set(layout["workspace"]["zones"]), {"A1", "A2"})
        self.assertEqual(len(layout["workspace"]["storage_slots"]), 4)
        self.assertFalse(any("placement_slots" in zone for zone in layout["workspace"]["zones"].values()))


if __name__ == "__main__":
    unittest.main()
