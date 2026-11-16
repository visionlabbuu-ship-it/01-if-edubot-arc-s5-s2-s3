import json
import tempfile
import unittest
from pathlib import Path

from arc_runtime import storage_service


class StorageServiceTest(unittest.TestCase):
    def registry(self):
        return {
            "slots": [
                {"name": "LT1", "group": "storage", "occupancy": "empty", "index_in_cluster": 1},
                {"name": "LT2", "group": "storage", "occupancy": "occupied", "index_in_cluster": 2},
                {"name": "RT1", "group": "drop", "occupancy": "empty", "index_in_cluster": 1},
            ]
        }

    def test_legacy_groups_migrate_to_four_storage_groups(self):
        result = storage_service.normalize(self.registry())
        self.assertEqual(set(result["groups"]), {"LT", "LB", "RT", "RB"})
        self.assertEqual({slot["role"] for slot in result["slots"]}, {"storage"})
        self.assertEqual(result["summary"]["by_group"]["LT"]["total"], 2)
        self.assertEqual(result["summary"]["by_group"]["RT"]["total"], 1)

    def test_resolve_explicit_and_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slots.json"
            storage_service.save(self.registry(), path)
            self.assertEqual(storage_service.resolve("LT1", path=path)["slot_name"], "LT1")
            self.assertEqual(storage_service.resolve("slot 1 of LT", path=path)["slot_name"], "LT1")
            self.assertEqual(storage_service.resolve("เธเนเธญเธเธ—เธตเน 1 เธเธญเธ LT", path=path)["slot_name"], "LT1")
            self.assertEqual(storage_service.resolve("LT", path=path)["slot_name"], "LT1")

    def test_full_slot_and_missing_policy_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slots.json"
            storage_service.save({"slots": [{"name": "LT1", "group": "LT", "occupancy": "occupied"}]}, path)
            self.assertEqual(storage_service.resolve("LT1", path=path)["error_code"], "capacity_full")
            self.assertEqual(storage_service.resolve(color="red", path=path)["error_code"], "needs_storage_policy")

    def test_policy_resolves_color(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slots.json"
            storage_service.save({"slots": [{"name": "RB1", "group": "RB", "occupancy": "empty"}]}, path)
            storage_service.set_policy({"color_groups": {"red": ["RB"]}}, path)
            result = storage_service.resolve(color="red", path=path)
            self.assertEqual(result["slot_name"], "RB1")
            blocked = storage_service.resolve(color="red", arm_id="Arm2", active_arms=["Arm1", "Arm2"], path=path)
            self.assertEqual(blocked["reason"], "group_arm_routing_policy")
    def test_reservation_is_atomic_and_releasable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slots.json"
            storage_service.save({"slots": [{"name": "LT1", "group": "LT", "occupancy": "empty"}]}, path)
            first = storage_service.reserve_slot("LT1", robot_id="Arm1", path=path)
            second = storage_service.reserve_slot("LT1", robot_id="Arm2", path=path)
            self.assertTrue(first["ok"])
            self.assertEqual(second["error_code"], "slot_unavailable" if second.get("error_code") else "capacity_unknown")
            storage_service.release_reservation("LT1", path=path)
            self.assertEqual(storage_service.resolve("LT1", path=path)["slot_name"], "LT1")

    def test_terminal_occupancy_clears_stale_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slots.json"
            storage_service.save({"slots": [{"name": "LT1", "group": "LT", "occupancy": "reserved", "reservation": {"robot_id": "Arm1"}}]}, path)
            payload = storage_service.update_occupancy("LT1", "occupied", held_object={"color": "blue"}, path=path)
            self.assertNotIn("reservation", payload["slots"][0])

    def test_unknown_capacity_is_distinct_from_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slots.json"
            storage_service.save({"slots": [{"name": "LT1", "group": "LT", "occupancy": "unknown"}]}, path)
            self.assertEqual(storage_service.resolve("LT1", path=path)["error_code"], "capacity_unknown")

    def test_group_arm_routing_avoids_cross_arm_slot_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slots.json"
            storage_service.save({"slots": [
                {"name": "LB1", "group": "LB", "occupancy": "empty", "per_arm_robot_plane": {"Arm1": {"reachable": True}}},
                {"name": "LT1", "group": "LT", "occupancy": "empty", "per_arm_robot_plane": {"Arm2": {"reachable": True}}},
            ]}, path)
            self.assertEqual(storage_service.resolve("LB1", arm_id="Arm1", active_arms=["Arm1", "Arm2"], path=path)["recommended_arm"], "Arm1")
            blocked = storage_service.resolve("LT1", arm_id="Arm1", active_arms=["Arm1", "Arm2"], path=path)
            self.assertEqual(blocked["error_code"], "slot_unreachable")
            self.assertEqual(blocked["reason"], "group_arm_routing_policy")
            self.assertEqual(storage_service.load(path)["groups"]["RB"]["allowed_arms"], ["Arm1"])

    def test_summary_preserves_occupied_colors_by_group(self):
        slots = [
            {"name": "LB1", "group": "LB", "occupancy": "occupied", "occupancy_color": "blue"},
            {"name": "LB2", "group": "LB", "occupancy": "occupied", "held_object": {"color": "blue"}},
            {"name": "RT1", "group": "RT", "occupancy": "occupied", "object_color": "red"},
            {"name": "RT2", "group": "RT", "occupancy": "empty"},
        ]

        summary = storage_service.summarize(slots)

        self.assertEqual(summary["occupied_by_color"], {"blue": 2, "red": 1})
        self.assertEqual(summary["by_group"]["LB"]["occupied_by_color"], {"blue": 2})
        self.assertEqual(summary["by_group"]["RT"]["occupied_by_color"], {"red": 1})

    def test_compact_view_maps_color_to_slots_and_groups(self):
        view = storage_service.compact_storage_view([
            {
                "name": "LB1",
                "group": "LB",
                "occupancy": "occupied",
                "occupancy_color": "blue",
                "reachable_by": ["Arm1"],
                "per_arm_robot_plane": {"Arm1": {"x": 288, "y": 236}},
            },
            {"name": "LB2", "group": "LB", "occupancy": "empty", "reachable_by": ["Arm1"]},
            {"name": "RT1", "group": "RT", "occupancy": "occupied", "held_object": {"color": "red"}},
        ])

        self.assertEqual(
            view["color_locations"],
            {
                "blue": {"count": 1, "slots": ["LB1"], "groups": ["LB"]},
                "red": {"count": 1, "slots": ["RT1"], "groups": ["RT"]},
            },
        )
        self.assertEqual(view["empty_slots_by_group"]["LB"], ["LB2"])
        self.assertNotIn("per_arm_robot_plane", view["slots"][0])
        self.assertEqual(view["slots"][0]["allowed_arms"], ["Arm1"])

if __name__ == "__main__":
    unittest.main()



