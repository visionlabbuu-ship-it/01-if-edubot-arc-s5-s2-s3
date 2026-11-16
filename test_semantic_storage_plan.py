import unittest
from unittest.mock import patch

import config
from arc_runtime.semantic_plan import plan_task, validate_semantic_plan, SemanticPlanRunner


class StorageSemanticPlanTest(unittest.TestCase):
    def _resolved(self, **kwargs):
        return {"ok": True, "slot_name": "LB1", "slot": {"name": "LB1", "group": "LB", "occupancy": "empty"}}

    def _valid_actions(self):
        return [
            {"action": "scan_storage_slots"},
            {"action": "pick", "arm_id": "Arm1", "target": {"type": "pose", "x": 250.0, "y": 0.0}},
            {"action": "place_to_storage_slot", "robot_id": "Arm1", "reference": "LB", "color": "green"},
        ]

    def test_storage_requires_fresh_slot_scan(self):
        with patch("arc_runtime.semantic_plan.resolve_storage", side_effect=self._resolved):
            result = validate_semantic_plan({"actions": [
                {"action": "pick", "arm_id": "Arm1", "target": {"type": "pose", "x": 250.0, "y": 0.0}},
                {"action": "place_to_storage_slot", "robot_id": "Arm1", "reference": "LB", "color": "green"},
            ]})
        self.assertFalse(result["ok"])
        self.assertIn("missing_storage_scan", {item["code"] for item in result["errors"]})

    def test_storage_requires_pick_before_transaction(self):
        with patch("arc_runtime.semantic_plan.resolve_storage", side_effect=self._resolved):
            result = validate_semantic_plan({"actions": [
                {"action": "scan_storage_slots"},
                {"action": "place_to_storage_slot", "robot_id": "Arm1", "reference": "LB", "color": "green"},
            ]})
        self.assertFalse(result["ok"])
        self.assertIn("storage_place_without_pick", {item["code"] for item in result["errors"]})

    def test_generic_drop_to_storage_is_rejected(self):
        result = validate_semantic_plan({"actions": [
            {"action": "drop", "arm_id": "Arm1", "target": {"type": "named", "name": "LB"}},
        ]})
        self.assertFalse(result["ok"])
        self.assertIn("generic_storage_drop_forbidden", {item["code"] for item in result["errors"]})

    def test_valid_storage_plan_keeps_atomic_action(self):
        with patch("arc_runtime.semantic_plan.resolve_storage", side_effect=self._resolved):
            result = validate_semantic_plan({"actions": self._valid_actions()})
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["expanded_actions"][-1]["action"], "place_to_storage_slot")

    def test_plan_all_storage_contains_one_transaction_per_object(self):
        request = {
            "intent": "plan_all_matching_objects",
            "color": "green",
            "destination": "LB",
            "objects": [
                {"id": "o1", "color": "green", "reachable_by": ["Arm1"], "per_arm_robot_plane": {"Arm1": {"x": 250.0, "y": 0.0}}},
                {"id": "o2", "color": "green", "reachable_by": ["Arm1"], "per_arm_robot_plane": {"Arm1": {"x": 260.0, "y": 0.0}}},
            ],
        }
        with patch("arc_runtime.semantic_plan.resolve_storage", side_effect=self._resolved):
            result = plan_task(request)
        self.assertTrue(result["ok"], result)
        actions = result["plan"]["actions"]
        self.assertEqual(sum(action.get("action") == "place_to_storage_slot" for action in actions), 2)
        self.assertEqual(sum(action.get("action") == "drop" for action in actions), 0)

    def test_multi_object_storage_uses_handoff_for_non_owner_pick_arm(self):
        request = {
            "intent": "plan_all_matching_objects",
            "color": "blue",
            "destination": "RT",
            # A6 is intentionally omitted because the operator asked to leave it.
            "objects": [
                {
                    "id": "obj-a3",
                    "color": "blue",
                    "block": "A3",
                    "reachable_by": ["Arm2"],
                    "per_arm_robot_plane": {"Arm2": {"x": 250.0, "y": 0.0}},
                },
                {
                    "id": "obj-a5",
                    "color": "blue",
                    "block": "A5",
                    "reachable_by": ["Arm1"],
                    "per_arm_robot_plane": {"Arm1": {"x": 250.0, "y": 0.0}},
                },
            ],
        }
        resolved = {
            "ok": True,
            "slot_name": "RT2",
            "slot": {"name": "RT2", "group": "RT", "occupancy": "empty"},
        }

        with patch.object(config, "ACTIVE_ROBOT_IDS", ["Arm1", "Arm2"]), patch(
            "arc_runtime.semantic_plan.resolve_storage", return_value=resolved
        ):
            result = plan_task(request)

        self.assertTrue(result["ok"], result)
        actions = result["plan"]["actions"]
        direct = next(action for action in actions if action.get("action") == "pick")
        handoff = next(action for action in actions if action.get("action") == "handoff")
        self.assertEqual(direct["arm_id"], "Arm2")
        self.assertEqual(direct["object_id"], "obj-a3")
        self.assertEqual(handoff["support_arm"], "Arm1")
        self.assertEqual(handoff["intent_arm"], "Arm2")
        self.assertEqual(handoff["destination"], "RT")
        self.assertEqual(
            {item["object_id"] for item in result["plan"]["matching_object_plan"]["objects"]},
            {"obj-a3", "obj-a5"},
        )
        expanded = result["validation"]["expanded_actions"]
        self.assertEqual(
            sum(action.get("action") == "place_to_storage_slot" for action in expanded),
            2,
        )
    def test_executor_requires_verified_occupied_response(self):
        runner = SemanticPlanRunner.__new__(SemanticPlanRunner)
        class Controller:
            def place_to_storage_slot(self, **kwargs):
                return {"ok": True, "occupancy": "unknown", "verification": {"verified": False}}
        runner.controller = Controller()
        result = runner._execute_storage_place({"robot_id": "Arm1", "reference": "LB", "color": "green"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["verification_required"])

    def test_handoff_to_slot_uses_transactional_storage_placement(self):
        resolved = {
            "ok": True,
            "slot_name": "RB4",
            "slot": {"name": "RB4", "group": "RB", "occupancy": "empty"},
        }
        request = {
            "intent": "handoff",
            "intent_arm": "Arm1",
            "support_arm": "Arm2",
            "object": {
                "id": "obj-2",
                "color": "red",
                "reachable_by": ["Arm2"],
                "per_arm_robot_plane": {"Arm2": {"x": 250.0, "y": 0.0, "reachable": True}},
            },            "destination": "RB4",
            "object_id": "obj-2",
            "color": "red",
        }
        with patch.object(config, "ACTIVE_ROBOT_IDS", ["Arm1", "Arm2"]), patch(
            "arc_runtime.semantic_plan.resolve_storage", return_value=resolved
        ):
            result = plan_task(request)

        self.assertTrue(result["ok"], result.get("validation"))
        expanded = result["validation"]["expanded_actions"]
        self.assertEqual(expanded[0]["action"], "scan_storage_slots")
        self.assertEqual(expanded[-1]["action"], "place_to_storage_slot")
        self.assertEqual(expanded[-1]["robot_id"], "Arm1")
        self.assertEqual(expanded[-1]["reference"], "RB4")
        self.assertFalse(any(action.get("action") == "drop" for action in expanded))
        self.assertLess(
            next(index for index, action in enumerate(expanded) if action.get("arm_id") == "Arm2" and (action.get("target") or {}).get("name") == "home"),
            next(index for index, action in enumerate(expanded) if action.get("arm_id") == "Arm1" and (action.get("target") or {}).get("name") == "handoff_zone"),
        )
        state_gates = [action for action in expanded if action.get("action") == "get_state"]
        self.assertEqual(state_gates[-1]["must_be_clear_of"], ["Arm2"])

    def test_handoff_state_gate_blocks_when_support_is_not_clear(self):
        runner = SemanticPlanRunner.__new__(SemanticPlanRunner)
        runner.controller = object()
        state = {
            "ok": True,
            "arms": {"Arm2": {"ok": True, "in_shared_zone": True, "is_at_standard_park": False}},
        }
        with patch("arc_runtime.semantic_plan.build_workspace_state", return_value=state):
            result = runner._execute_action({"action": "get_state", "must_be_clear_of": ["Arm2"]})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "handoff_shared_zone_not_clear")


    def test_named_handoff_rejects_unverified_manual_coordinates(self):
        request = {
            "intent": "handoff",
            "intent_arm": "Arm1",
            "support_arm": "Arm2",
            "object_id": "obj-2",
            "object_x": 507.0,
            "object_y": 143.0,
            "destination": "LB4",
        }

        result = plan_task(request)

        self.assertFalse(result["ok"])
        self.assertIn("matching scanned object", result["error"])

if __name__ == "__main__":
    unittest.main()