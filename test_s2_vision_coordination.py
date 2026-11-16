import unittest

from arc_runtime.camera_controller import CameraController
from arc_runtime.sim_world import WorldModel
from s2_vision_coordination.standalone import _attach_display_coordinates
from s2_vision_coordination import (
    S2VisionCoordinator,
    VisionSnapshot,
    build_coordination_plan,
)


class S2VisionCoordinationTests(unittest.TestCase):
    def test_web_overlay_prefers_arc_display_coordinate(self):
        response = {
            "scan": {
                "frame": {"width": 640, "height": 480},
                "display_frame": {"width": 2592, "height": 1944},
                "objects": [
                    {
                        "id": "obj-1",
                        "center_px": {"x": 100, "y": 200},
                        "display_center_px": {"x": 1356, "y": 482},
                        "display_coordinate_source": "arc_simulation_image_detection",
                    }
                ],
            }
        }

        _attach_display_coordinates(response)

        object_record = response["scan"]["objects"][0]
        self.assertEqual(object_record["display_position"]["x"], 1356 / 2592)
        self.assertEqual(object_record["display_position"]["y"], 482 / 1944)
        self.assertEqual(
            object_record["display_position"]["source"],
            "arc_simulation_image_detection",
        )

    def _snapshot(self):
        return VisionSnapshot.from_scan(
            {
                "ok": True,
                "camera_id": "Cam1",
                "timestamp": "2026-10-16T00:00:00+00:00",
                "objects": [
                    {
                        "id": "obj-1",
                        "color": "green",
                        "center_px": {"x": 100, "y": 200},
                        "workspace_xy": {"x": 220, "y": 30},
                        "per_arm_robot_plane": {
                            "Arm1": {"x": 220, "y": 30},
                            "Arm2": {"x": 300, "y": -30},
                        },
                        "reachable_by": ["Arm1", "Arm2"],
                        "preferred_arm": "Arm2",
                    },
                    {
                        "id": "obj-2",
                        "color": "red",
                        "center_px": {"x": 200, "y": 250},
                        "workspace_xy": {"x": 300, "y": 80},
                        "per_arm_robot_plane": {"Arm1": {"x": 300, "y": 80}},
                        "reachable_by": ["Arm1"],
                    },
                ],
            }
        )

    def test_assigns_only_active_reachable_arms(self):
        result = build_coordination_plan(self._snapshot(), ["Arm1", "Arm2"])
        self.assertEqual([item.arm_id for item in result.assignments], ["Arm2", "Arm1"])
        self.assertEqual(result.unassigned, ())
        self.assertFalse(result.robot_control)

    def test_parallel_is_deferred_to_arc_guardrails(self):
        result = build_coordination_plan(
            self._snapshot(), ["Arm1", "Arm2"], requested_mode="parallel"
        )
        self.assertEqual(result.requested_mode, "parallel")
        self.assertEqual(result.execution_mode, "sequential")
        self.assertFalse(result.parallel_admissible)

    def test_unreachable_object_is_explicit(self):
        snapshot = VisionSnapshot.from_scan(
            {
                "ok": True,
                "camera_id": "Cam1",
                "objects": [
                    {
                        "id": "obj-9",
                        "center_px": {"x": 1, "y": 2},
                        "per_arm_robot_plane": {},
                        "reachable_by": [],
                    }
                ],
            }
        )
        result = build_coordination_plan(snapshot, ["Arm1"])
        self.assertEqual(result.assignments, ())
        self.assertEqual(result.unassigned[0]["reason"], "no_active_reachable_arm")

    def test_preserves_block_and_reports_calibration(self):
        snapshot = VisionSnapshot.from_scan(
            {
                "ok": True,
                "camera_id": "Cam1",
                "calibration_id": "cal-2026-10-16",
                "objects": [
                    {
                        "id": "obj-1",
                        "color": "green",
                        "block": "Block 1,2",
                        "center_px": {"x": 10, "y": 20},
                        "per_arm_robot_plane": {"Arm1": {"x": 1, "y": 2}},
                        "reachable_by": ["Arm1", "Arm2"],
                    }
                ],
            }
        )
        plan = build_coordination_plan(snapshot, ["Arm1"])
        self.assertEqual(plan.calibration_id, "cal-2026-10-16")
        self.assertEqual(plan.assignments[0].block, "Block 1,2")
        self.assertEqual(plan.to_dict()["assigned_count"], 1)

    def test_rejects_duplicate_object_ids(self):
        result = S2VisionCoordinator(
            lambda **_: {
                "ok": True,
                "objects": [
                    {"id": "same", "center_px": {"x": 1, "y": 1}},
                    {"id": "same", "center_px": {"x": 2, "y": 2}},
                ],
            },
            ["Arm1"],
            require_saved_workspace=False,
            require_pixel_coordinates=False,
        ).scan_and_plan()
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "invalid_s2_evidence")

    def test_rejects_malformed_arm_plane_as_reportable_evidence_error(self):
        result = S2VisionCoordinator(
            lambda **_: {
                "ok": True,
                "objects": [
                    {
                        "id": "bad",
                        "center_px": {"x": 1, "y": 1},
                        "per_arm_robot_plane": [],
                    }
                ],
            },
            ["Arm1"],
            require_saved_workspace=False,
        ).scan_and_plan()
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "invalid_s2_evidence")

    def test_stale_saved_workspace_is_blocked(self):
        result = S2VisionCoordinator(
            lambda **_: {
                "ok": True,
                "workspace": {
                    "saved": True,
                    "saved_exists": True,
                    "stale_saved": True,
                },
                "objects": [],
            },
            ["Arm1"],
        ).scan_and_plan()
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "stale_calibration")

    def test_camera_controller_simulator_integration_is_motion_free(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            world = WorldModel(Path(directory) / "world.json")
            camera = CameraController(
                active_arm_ids=["Arm1", "Arm2"],
                world_model=world,
            )
            result = S2VisionCoordinator(
                camera,
                ["Arm1", "Arm2"],
                require_saved_workspace=True,
                require_pixel_coordinates=False,
            ).scan_and_plan(requested_mode="parallel")

        self.assertTrue(result.ok)
        self.assertEqual(result.plan.execution_mode, "sequential")
        self.assertFalse(result.plan.parallel_admissible)
        self.assertFalse(result.plan.robot_control)
        self.assertGreaterEqual(result.plan.to_dict()["assigned_count"], 1)
        report_payload = result.to_dict()
        self.assertEqual(report_payload["scope"], "S2")
        self.assertEqual(report_payload["snapshot"]["pixel_coordinate_count"], 0)
        self.assertTrue(
            all("object_id" in request for request in result.to_arc_assignment_requests())
        )

    def test_simulator_scan_can_verify_removed_assignment(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            world = WorldModel(Path(directory) / "world.json")
            camera = CameraController(
                active_arm_ids=["Arm1", "Arm2"],
                world_model=world,
            )
            coordinator = S2VisionCoordinator(
                camera,
                ["Arm1", "Arm2"],
                require_saved_workspace=True,
                require_pixel_coordinates=False,
            )
            before = coordinator.scan_and_plan()
            target = before.plan.assignments[0]
            self.assertTrue(world.pick_at(target.robot_xy, arm_id=target.arm_id)["ok"])
            after = camera.scan_workspace()
            verification = coordinator.verify_after_scan(before, after, assignments=[target])

        self.assertTrue(verification.ok)
        self.assertEqual(verification.checked_count, 1)
        self.assertEqual(verification.verified_count, 1)

    def test_arc_assignment_request_preserves_object_identity(self):
        payload = {
            "ok": True,
            "camera_id": "Cam1",
            "timestamp": "2026-10-16T00:00:00+00:00",
            "objects": [
                {
                    "id": "green-first",
                    "color": "green",
                    "center_px": {"x": 10, "y": 10},
                    "per_arm_robot_plane": {"Arm1": {"x": 10, "y": 10}},
                    "reachable_by": ["Arm1"],
                },
                {
                    "id": "green-second",
                    "color": "green",
                    "center_px": {"x": 20, "y": 20},
                    "per_arm_robot_plane": {"Arm1": {"x": 20, "y": 20}},
                    "reachable_by": ["Arm1"],
                },
            ],
        }
        camera = CameraController(active_arm_ids=["Arm1"])
        camera.scan_workspace = lambda **_: payload
        coordinator = S2VisionCoordinator(
            camera,
            ["Arm1"],
            require_saved_workspace=False,
        )
        result = coordinator.plan_from_payload(payload)
        request = result.to_arc_assignment_requests()[1]
        assigned = camera.assign_targets([request])

        self.assertTrue(assigned["ok"])
        self.assertEqual(assigned["targets"][0]["object_id"], "green-second")


if __name__ == "__main__":
    unittest.main()
