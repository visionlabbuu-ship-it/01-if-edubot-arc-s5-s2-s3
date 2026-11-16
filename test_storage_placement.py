import json
import unittest
from unittest.mock import Mock, patch

from arc_runtime.ai_fleet import OllamaFleetManager
from arc_runtime.fleet_controller import FleetController


class StoragePlacementLifecycleTest(unittest.TestCase):
    def controller(self):
        controller = FleetController.__new__(FleetController)
        controller.active_arm_ids = ["Arm1", "Arm2"]
        controller._get_pick_config = Mock(return_value={"post_place_rescan_delay_s": 0, "post_place_position_tolerance_mm": 20})
        controller.camera = Mock()
        arm1 = Mock(is_connected=True)
        arm2 = Mock(is_connected=True)
        controller.robots = {"Arm1": arm1, "Arm2": arm2}
        controller.get_robot = Mock(side_effect=lambda arm_id: controller.robots.get(arm_id))
        return controller

    @patch("arc_runtime.fleet_controller.update_occupancy")
    @patch("arc_runtime.fleet_controller.scan_storage")
    def test_post_place_scan_commits_occupied_only_for_matching_object(self, scan_storage, update_occupancy):
        controller = self.controller()
        scan_storage.return_value = {
            "ok": True,
            "registry": {
                "source_scan": {"timestamp": "scan-1"},
                "slots": [{
                    "name": "LT1",
                    "group": "LT",
                    "occupancy": "occupied",
                    "occupancy_color": "red",
                    "held_object": {"color": "red"},
                }],
            },
        }
        slot = {"name": "LT1", "workspace_xy": {"x": 100, "y": 100}, "reservation": {"robot_id": "Arm1"}}

        result = controller._verify_storage_slot_after_place(slot, color="red")

        self.assertTrue(result["verified"])
        self.assertEqual(update_occupancy.call_args.args[1], "occupied")
        scan_storage.assert_called_once_with()
    @patch("arc_runtime.fleet_controller.update_occupancy")
    @patch("arc_runtime.fleet_controller.scan_storage")
    def test_inconclusive_post_place_scan_commits_unknown(self, scan_storage, update_occupancy):
        controller = self.controller()
        scan_storage.return_value = {"ok": False, "error": "camera offline"}
        slot = {"name": "LT1", "workspace_xy": {"x": 100, "y": 100}, "reservation": {"robot_id": "Arm1"}}

        result = controller._verify_storage_slot_after_place(slot, color="red")

        self.assertFalse(result["verified"])
        self.assertEqual(result["error_code"], "capacity_unknown")
        self.assertEqual(update_occupancy.call_args.args[1], "unknown")
    @patch("arc_runtime.fleet_controller.set_verification_pending")
    @patch("arc_runtime.fleet_controller.reserve_slot")
    @patch("arc_runtime.fleet_controller.resolve_storage")
    def test_place_result_is_not_success_until_verification_passes(self, resolve, reserve, pending):
        controller = self.controller()
        slot = {
            "name": "LT1",
            "group": "LT",
            "workspace_xy": {"x": 100, "y": 100},
            "per_arm_robot_plane": {"Arm1": {"x": 200, "y": 10}},
        }
        resolve.return_value = {"ok": True, "slot": slot}
        reserve.return_value = {"ok": True, "slot": slot}
        controller.place_at = Mock(return_value={"ok": True})
        controller.move_to_named_target = Mock(return_value={"ok": True})
        controller._verify_storage_slot_after_place = Mock(return_value={"ok": False, "verified": False, "error_code": "capacity_unknown"})

        result = controller.place_to_storage_slot("Arm1", "LT1", "red")

        self.assertFalse(result["ok"])
        self.assertEqual(result["occupancy"], "unknown")
        pending.assert_called_once()
        controller.move_to_named_target.assert_called_once_with("Arm1", "home")


class GuardrailPoseFreshnessTest(unittest.TestCase):
    def test_missing_other_arm_pose_fails_closed(self):
        controller = FleetController.__new__(FleetController)
        controller.robots = {"Arm1": object(), "Arm2": object()}
        controller.guardrails = Mock()
        controller.guardrails.safety_geometry.return_value = {"shared_zone": {}}
        controller.get_arm_pose = Mock(return_value={"ok": False, "error": "offline"})

        result = controller._preflight_pose(
            "Arm1", {"x": 250, "y": 0, "z": 50, "r": 0}, "test_motion"
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["guardrail_blocked"])
        self.assertEqual(result["error_code"], "other_arm_pose_unknown")
        controller.guardrails.check_pose.assert_not_called()

class AIToolContractTest(unittest.TestCase):
    def test_tool_schemas_match_available_functions(self):
        fleet = OllamaFleetManager.__new__(OllamaFleetManager)
        tools = fleet.get_api_tools()
        schema_names = {tool["function"]["name"] for tool in tools}
        functions = fleet.get_available_functions()

        for name in {
            "list_workspace_zones",
            "list_storage_groups",
            "plan_intent",
            "list_storage_slots",
            "scan_storage_slots",
            "resolve_storage_slot",
            "place_to_storage_slot",
            "get_runtime_context",
        }:
            self.assertIn(name, schema_names)
            self.assertIn(name, functions)

        placement = next(tool["function"] for tool in tools if tool["function"]["name"] == "place_to_storage_slot")
        self.assertEqual(placement["parameters"]["required"], ["robot_id"])
        self.assertEqual(set(placement["parameters"]["properties"]), {"robot_id", "reference", "color"})
        resolver = next(tool["function"] for tool in tools if tool["function"]["name"] == "resolve_storage_slot")
        self.assertEqual(set(resolver["parameters"]["properties"]), {"robot_id", "reference", "color"})
        scan = next(tool["function"] for tool in tools if tool["function"]["name"] == "scan_storage_slots")
        self.assertEqual(scan["parameters"]["required"], [])
        self.assertEqual(scan["parameters"]["properties"], {})


    @patch("arc_runtime.ai_fleet.scan_storage")
    def test_scan_storage_slots_uses_canonical_storage_service_scan(self, scan_storage):
        scan_storage.return_value = {
            "ok": True,
            "registry": {"updated_at": "now", "source_scan": {"timestamp": "now"}},
            "slots": [
                {"name": "LB1", "group": "LB", "occupancy": "occupied", "occupancy_color": "blue"},
                {"name": "LB2", "group": "LB", "occupancy": "empty"},
            ],
        }
        fleet = OllamaFleetManager.__new__(OllamaFleetManager)

        result = json.loads(fleet.scan_storage_slots())

        self.assertTrue(result["ok"])
        self.assertTrue(result["fresh_scan"])
        self.assertEqual(
            result["color_locations"]["blue"],
            {"count": 1, "slots": ["LB1"], "groups": ["LB"]},
        )
        self.assertEqual(result["empty_slots_by_group"]["LB"], ["LB2"])
        self.assertNotIn("registry", result)
        scan_storage.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()


