import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import config
from config import ARM_FRAME_TRANSFORMS, ARM_NAMED_TARGETS, ROBOT_FLEET_CONFIG, ROBOT_SAFETY_CONFIG
from fleet_controller import FleetController
from robot_guardrails import RobotSafetyGuardrail
from semantic_plan import plan_task, validate_semantic_plan
from task_system import preflight_task
from workspace_calibration import STALE_WORKSPACE_CALIBRATION_ERROR
from workspace_model import build_handoff_plan, build_workspace_layout, build_workspace_state


@contextmanager
def two_arm_mode():
    original = list(config.ACTIVE_ROBOT_IDS)
    config.ACTIVE_ROBOT_IDS = ["Arm1", "Arm2"]
    try:
        yield
    finally:
        config.ACTIVE_ROBOT_IDS = original


def make_guardrail():
    return RobotSafetyGuardrail(
        {
            "enabled": True,
            "max_arm_length_mm": 440.0,
            "min_reach_mm": 150.0,
            "distance_between_robots_mm": 600.0,
            "shared_zone": {
                "enabled": True,
                "zone_start_mm": None,
                "zone_end_mm": None,
            },
            "collision": {
                "enabled": True,
                "block_if_other_pose_unavailable": True,
            },
            "arm_global_frames": {
                "Arm1": {"base_x_mm": 0.0, "forward_sign": 1.0},
                "Arm2": {"base_x_mm": 600.0, "forward_sign": -1.0},
            },
        }
    )


class FakeRobot:
    connect_calls = 0
    feedback_calls = 0
    move_to_pose_calls = 0
    move_joint_to_pose_calls = 0
    set_tool_output_calls = 0

    def __init__(self, robot_id, ip, port):
        self.robot_id = robot_id
        self.ip = ip
        self.dashboard_port = port
        self.is_connected = True

    @classmethod
    def reset(cls):
        cls.connect_calls = 0
        cls.feedback_calls = 0
        cls.move_to_pose_calls = 0
        cls.move_joint_to_pose_calls = 0
        cls.set_tool_output_calls = 0

    def connect(self):
        type(self).connect_calls += 1
        self.is_connected = True
        return True

    def disconnect(self):
        self.is_connected = False

    def get_feedback_snapshot(self):
        type(self).feedback_calls += 1
        return {
            "ok": True,
            "robot_mode": 5,
            "robot_mode_name": "enabled",
            "enable_status": True,
            "running_status": False,
            "error_status": False,
            "brake_status": False,
            "pose": {"x": 100.0, "y": 0.0, "z": 0.0, "r": 0.0},
            "joints": [0.0, 0.0, 0.0, 0.0],
        }

    def get_status(self):
        return {
            "robot_id": self.robot_id,
            "ip": self.ip,
            "port": self.dashboard_port,
            "connected": self.is_connected,
        }

    def move_to_pose(self, x, y, z, r=0.0):
        type(self).move_to_pose_calls += 1
        return {"ok": True, "target_pose": {"x": x, "y": y, "z": z, "r": r}}

    def move_joint_to_pose(self, x, y, z, r=0.0):
        type(self).move_joint_to_pose_calls += 1
        return {"ok": True, "target_pose": {"x": x, "y": y, "z": z, "r": r}, "motion_type": "MovJ"}

    def set_tool_output(self, index, enable):
        type(self).set_tool_output_calls += 1
        return "0,{},SetToolDO();"


class RobotSafetyGuardrailTest(unittest.TestCase):
    def setUp(self):
        self._active_robot_ids = list(config.ACTIVE_ROBOT_IDS)
        config.ACTIVE_ROBOT_IDS = ["Arm1"]

    def tearDown(self):
        config.ACTIVE_ROBOT_IDS = self._active_robot_ids

    def test_dynamic_z_reach_allows_safe_pose(self):
        guardrail = make_guardrail()

        result = guardrail.check_pose(
            "Arm1",
            {"x": 250.0, "y": 0.0, "z": -100.0, "r": 0.0},
            other_poses={"Arm2": {"x": 50.0, "y": 0.0, "z": 0.0, "r": 0.0}},
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["guardrail_passed"])

    def test_dynamic_z_reach_blocks_when_z_reduces_horizontal_reach(self):
        guardrail = make_guardrail()

        result = guardrail.check_pose(
            "Arm1",
            {"x": 430.0, "y": 0.0, "z": -150.0, "r": 0.0},
            other_poses={"Arm2": {"x": 50.0, "y": 0.0, "z": 0.0, "r": 0.0}},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["guardrail"], "dynamic_z_reach")
        self.assertEqual(result["reason"], "target_beyond_z_adjusted_reach")

    def test_shared_zone_calculation_uses_robot_spacing_and_reach(self):
        guardrail = make_guardrail()

        self.assertEqual(
            guardrail.shared_zone(),
            {"start_x_mm": 160.0, "end_x_mm": 440.0},
        )

    def test_collision_blocks_when_other_arm_occupies_shared_zone(self):
        guardrail = make_guardrail()

        result = guardrail.check_pose(
            "Arm1",
            {"x": 200.0, "y": 0.0, "z": 0.0, "r": 0.0},
            other_poses={"Arm2": {"x": 350.0, "y": 0.0, "z": 0.0, "r": 0.0}},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["guardrail"], "shared_zone_collision")
        self.assertEqual(result["reason"], "shared_zone_occupied")
        self.assertEqual(result["recommended_action"], "wait_or_replan")

    def test_collision_blocks_when_other_pose_is_unavailable(self):
        guardrail = make_guardrail()

        result = guardrail.check_pose(
            "Arm1",
            {"x": 200.0, "y": 0.0, "z": 0.0, "r": 0.0},
            other_poses={},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["guardrail"], "shared_zone_collision")
        self.assertEqual(result["reason"], "other_robot_pose_unavailable")

    def test_parallel_conflict_blocks_two_arms_entering_shared_zone(self):
        guardrail = make_guardrail()

        result = guardrail.parallel_shared_zone_conflict(
            [
                {
                    "path": "task.steps[0]",
                    "step_type": "move_to_pose",
                    "arm_id": "Arm1",
                    "pose": {"x": 200.0, "y": 0.0, "z": 0.0, "r": 0.0},
                },
                {
                    "path": "task.steps[1]",
                    "step_type": "move_to_pose",
                    "arm_id": "Arm2",
                    "pose": {"x": 350.0, "y": 0.0, "z": 0.0, "r": 0.0},
                },
            ]
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "parallel_shared_zone_conflict")
        self.assertEqual(result["recommended_action"], "sequence_or_replan")

    def test_runtime_config_uses_existing_arm_frame_transform(self):
        guardrail = RobotSafetyGuardrail(
            ROBOT_SAFETY_CONFIG,
            ARM_FRAME_TRANSFORMS,
            active_arm_ids=["Arm1", "Arm2"],
        )

        self.assertEqual(
            guardrail.shared_zone(),
            {"start_x_mm": 350.0, "end_x_mm": 440.0},
        )
        self.assertEqual(guardrail.pose_global_x("Arm2", {"x": 350.0, "y": 0.0}), 440.0)

    def test_runtime_home_targets_are_clear_of_shared_zone_for_both_arms(self):
        guardrail = RobotSafetyGuardrail(
            ROBOT_SAFETY_CONFIG,
            ARM_FRAME_TRANSFORMS,
            active_arm_ids=["Arm1", "Arm2"],
        )
        homes = {}
        for arm_id in ("Arm1", "Arm2"):
            target = ARM_NAMED_TARGETS[arm_id]["home"]
            pose = {
                "x": target["x"],
                "y": target["y"],
                "z": target["hover_z"],
                "r": target["r"],
            }
            self.assertEqual(ROBOT_FLEET_CONFIG[arm_id]["shared_home_pose"], pose)
            self.assertFalse(guardrail.pose_in_shared_zone(arm_id, pose))
            homes[arm_id] = pose

        self.assertTrue(
            guardrail.check_pose(
                "Arm1", homes["Arm1"], other_poses={"Arm2": homes["Arm2"]}
            )["ok"]
        )
        self.assertTrue(
            guardrail.check_pose(
                "Arm2", homes["Arm2"], other_poses={"Arm1": homes["Arm1"]}
            )["ok"]
        )

    def test_single_arm_guardrail_does_not_block_on_missing_inactive_arm_pose(self):
        guardrail = RobotSafetyGuardrail(
            ROBOT_SAFETY_CONFIG,
            ARM_FRAME_TRANSFORMS,
            active_arm_ids=["Arm1"],
        )

        result = guardrail.check_pose(
            "Arm1",
            {"x": 395.0, "y": 0.0, "z": 83.41, "r": 0.0},
            other_poses={},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["checks"][1]["guardrail"], "shared_zone_collision")
        self.assertEqual(result["checks"][1]["target_in_shared_zone"], True)
        self.assertEqual(result["checks"][1].get("blocking_robot_id"), None)
        self.assertTrue(guardrail.safety_geometry()["single_arm_mode"])

    def test_controller_single_arm_runtime_only_instantiates_active_arm(self):
        FakeRobot.reset()

        with patch("fleet_controller.MG400Robot", FakeRobot):
            controller = FleetController(ROBOT_FLEET_CONFIG)
            try:
                payload = controller.list_arms()
                result = controller.move_to_pose_arm(
                    "Arm1",
                    395.0,
                    0.0,
                    83.41,
                    0.0,
                )
            finally:
                controller.disconnect_all()

        self.assertTrue(result["ok"])
        self.assertEqual(payload["total_arms"], 1)
        self.assertEqual(payload["arms"][0]["robot_id"], "Arm1")
        self.assertEqual(FakeRobot.move_to_pose_calls, 1)

    def test_controller_guardrail_block_prevents_motion_call(self):
        FakeRobot.reset()

        with patch("fleet_controller.MG400Robot", FakeRobot):
            controller = FleetController(ROBOT_FLEET_CONFIG)
            try:
                result = controller.move_to_pose_arm(
                    "Arm1",
                    430.0,
                    0.0,
                    -150.0,
                    0.0,
                )
            finally:
                controller.disconnect_all()

        self.assertFalse(result["ok"])
        self.assertTrue(result["guardrail_blocked"])
        self.assertEqual(result["fault_type"], "guardrail_blocked")
        self.assertEqual(FakeRobot.move_to_pose_calls, 0)

    def test_task_preflight_blocks_parallel_shared_zone_without_motion(self):
        FakeRobot.reset()
        task_doc = {
            "name": "parallel-shared-zone-test",
            "steps": [
                {
                    "type": "parallel",
                    "steps": [
                        {
                            "type": "move_to_pose",
                            "arm_id": "Arm1",
                            "params": {"x": 360.0, "y": 0.0, "z": 0.0, "r": 0.0},
                        },
                        {
                            "type": "move_to_pose",
                            "arm_id": "Arm2",
                            "params": {"x": 350.0, "y": 0.0, "z": 0.0, "r": 0.0},
                        },
                    ],
                }
            ],
        }

        with patch("fleet_controller.MG400Robot", FakeRobot):
            result = preflight_task(task_doc)

        self.assertFalse(result["ok"])
        self.assertTrue(result["guardrail_blocked"])
        self.assertEqual(result["details"]["guardrail"]["reason"], "parallel_shared_zone_conflict")
        self.assertEqual(FakeRobot.connect_calls, 0)
        self.assertEqual(FakeRobot.feedback_calls, 0)
        self.assertEqual(FakeRobot.move_to_pose_calls, 0)

    def test_task_preflight_blocks_dynamic_reach_without_motion(self):
        FakeRobot.reset()
        task_doc = {
            "name": "dynamic-reach-test",
            "steps": [
                {
                    "type": "move_to_pose",
                    "arm_id": "Arm1",
                    "params": {"x": 430.0, "y": 0.0, "z": -150.0, "r": 0.0},
                }
            ],
        }

        with patch("fleet_controller.MG400Robot", FakeRobot):
            result = preflight_task(task_doc)

        self.assertFalse(result["ok"])
        self.assertTrue(result["guardrail_blocked"])
        self.assertEqual(result["details"]["guardrail"]["reason"], "target_beyond_z_adjusted_reach")
        self.assertEqual(FakeRobot.connect_calls, 0)
        self.assertEqual(FakeRobot.feedback_calls, 0)
        self.assertEqual(FakeRobot.move_to_pose_calls, 0)

    def test_atomic_named_pose_moves_once_without_tool_toggle(self):
        FakeRobot.reset()

        with patch("fleet_controller.MG400Robot", FakeRobot):
            controller = FleetController(ROBOT_FLEET_CONFIG)
            try:
                result = controller.move_to_named_pose("Arm1", "handoff_zone", "hover")
            finally:
                controller.disconnect_all()

        self.assertTrue(result["ok"])
        self.assertTrue(result["atomic_motion"])
        self.assertEqual(result["operation"], "move_to_named_pose")
        self.assertEqual(result["destination"], "handoff_zone")
        self.assertEqual(result["phase"], "hover")
        self.assertEqual(result["target_pose"]["x"], 395.0)
        self.assertEqual(result["target_pose"]["z"], 83.41)
        self.assertEqual(FakeRobot.move_joint_to_pose_calls, 1)
        self.assertEqual(FakeRobot.set_tool_output_calls, 0)

    def test_atomic_work_pose_surface_moves_once_without_tool_toggle(self):
        FakeRobot.reset()

        with patch("fleet_controller.MG400Robot", FakeRobot):
            controller = FleetController(ROBOT_FLEET_CONFIG)
            try:
                result = controller.move_to_work_pose("Arm1", 250.0, 0.0, "surface")
            finally:
                controller.disconnect_all()

        self.assertTrue(result["ok"])
        self.assertTrue(result["atomic_motion"])
        self.assertEqual(result["operation"], "move_to_work_pose")
        self.assertEqual(result["phase"], "surface")
        self.assertEqual(result["target_pose"]["x"], 250.0)
        self.assertEqual(result["target_pose"]["z"], -151.0)
        self.assertEqual(FakeRobot.move_joint_to_pose_calls, 1)
        self.assertEqual(FakeRobot.set_tool_output_calls, 0)

    def test_surface_phase_is_fixed_for_arm2_work_pose(self):
        FakeRobot.reset()

        with patch("fleet_controller.MG400Robot", FakeRobot):
            with two_arm_mode():
                controller = FleetController(ROBOT_FLEET_CONFIG)
                try:
                    result = controller.move_to_work_pose("Arm2", 250.0, 0.0, "surface")
                finally:
                    controller.disconnect_all()

        self.assertTrue(result["ok"])
        self.assertEqual(result["target_pose"]["z"], -152.0)
        self.assertEqual(FakeRobot.move_joint_to_pose_calls, 1)

    def test_arm2_waits_after_suction_on(self):
        FakeRobot.reset()

        with patch("fleet_controller.MG400Robot", FakeRobot), patch("fleet_controller.time.sleep") as sleep:
            with two_arm_mode():
                controller = FleetController(ROBOT_FLEET_CONFIG)
                try:
                    result = controller.set_tool_state("Arm2", True)
                finally:
                    controller.disconnect_all()

        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["settle_s"], 0.8)
        sleep.assert_called_once_with(0.8)

    def test_named_handoff_surface_clamps_to_safe_surface_z(self):
        FakeRobot.reset()

        with patch("fleet_controller.MG400Robot", FakeRobot):
            controller = FleetController(ROBOT_FLEET_CONFIG)
            try:
                result = controller.move_to_named_pose("Arm1", "handoff_zone", "surface")
            finally:
                controller.disconnect_all()

        self.assertTrue(result["ok"])
        self.assertEqual(result["target_pose"]["z"], -150.0)
        self.assertEqual(FakeRobot.move_joint_to_pose_calls, 1)

    def test_named_storage_surface_uses_storage_drop_z(self):
        FakeRobot.reset()

        with patch("fleet_controller.MG400Robot", FakeRobot):
            controller = FleetController(ROBOT_FLEET_CONFIG)
            try:
                result = controller.move_to_named_pose("Arm1", "storage_zone", "surface")
            finally:
                controller.disconnect_all()

        self.assertTrue(result["ok"])
        self.assertEqual(result["target_pose"]["z"], -90.0)
        self.assertEqual(FakeRobot.move_joint_to_pose_calls, 1)

    def test_pick_detected_place_blocks_stale_workspace_without_motion(self):
        class StaleCamera:
            def __init__(self):
                self.require_saved_workspace = None

            def assign_targets(self, assignments, camera_id="Cam1", require_saved_workspace=False):
                self.require_saved_workspace = require_saved_workspace
                return {
                    "ok": False,
                    "camera_id": camera_id,
                    "error": STALE_WORKSPACE_CALIBRATION_ERROR,
                    "workspace": {
                        "source": "none",
                        "saved": False,
                        "stale_saved": True,
                    },
                }

        FakeRobot.reset()

        with patch("fleet_controller.MG400Robot", FakeRobot):
            controller = FleetController(ROBOT_FLEET_CONFIG)
            camera = StaleCamera()
            controller.camera = camera
            try:
                result = controller.pick_detected_and_place(
                    "Arm1",
                    color="red",
                    destination="storage_zone",
                )
            finally:
                controller.disconnect_all()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], STALE_WORKSPACE_CALIBRATION_ERROR)
        self.assertTrue(camera.require_saved_workspace)
        self.assertEqual(FakeRobot.move_joint_to_pose_calls, 0)
        self.assertEqual(FakeRobot.set_tool_output_calls, 0)

    def test_pick_all_detected_place_blocks_stale_workspace_without_motion(self):
        class StaleCamera:
            def __init__(self):
                self.require_saved_workspace = None

            def scan_workspace(self, camera_id="Cam1", color="any", selection="first", require_saved_workspace=False):
                self.require_saved_workspace = require_saved_workspace
                return {
                    "ok": False,
                    "camera_id": camera_id,
                    "error": STALE_WORKSPACE_CALIBRATION_ERROR,
                    "workspace": {
                        "source": "none",
                        "saved": False,
                        "stale_saved": True,
                    },
                }

        FakeRobot.reset()

        with patch("fleet_controller.MG400Robot", FakeRobot):
            controller = FleetController(ROBOT_FLEET_CONFIG)
            camera = StaleCamera()
            controller.camera = camera
            try:
                result = controller.pick_all_detected_and_place(
                    "Arm1",
                    destination="storage_zone",
                    color="any",
                    max_picks=3,
                )
            finally:
                controller.disconnect_all()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], STALE_WORKSPACE_CALIBRATION_ERROR)
        self.assertTrue(camera.require_saved_workspace)
        self.assertEqual(result["picked_count"], 0)
        self.assertEqual(FakeRobot.move_joint_to_pose_calls, 0)
        self.assertEqual(FakeRobot.set_tool_output_calls, 0)

    def test_workspace_layout_exposes_runtime_parking_and_handoff_contract(self):
        layout = build_workspace_layout()

        self.assertTrue(layout["ok"])
        self.assertEqual(
            layout["safety_geometry"]["shared_zone"],
            {"start_x_mm": 0.0, "end_x_mm": 440.0},
        )
        self.assertEqual(list(layout["arms"].keys()), ["Arm1"])
        self.assertEqual(layout["safety_geometry"]["active_arm_ids"], ["Arm1"])
        self.assertTrue(layout["safety_geometry"]["single_arm_mode"])
        self.assertTrue(layout["standard_parking"]["home_is_clear_workspace"])
        self.assertEqual(layout["standard_parking"]["clear_workspace"]["Arm1"], "home")
        self.assertEqual(
            layout["named_targets"]["Arm1"]["storage_zone"]["phases"]["surface"]["local_pose"]["z"],
            -90.0,
        )
        self.assertNotIn("Arm2", layout["standard_parking"]["clear_workspace"])
        self.assertFalse(layout["handoff_contract"]["enabled"])
        self.assertIn("plan_handoff", layout["handoff_contract"]["planner_tool"])
        self.assertIn("pick_all_detected_place", layout["handoff_contract"]["forbidden_tools_during_handoff"])

    def test_handoff_plan_reports_single_arm_mode(self):
        plan = build_handoff_plan(
            intent_arm="Arm2",
            support_arm="Arm1",
            object_x=284.156,
            object_y=40.7994,
            destination="drop_zone",
            object_id="obj-3",
            color="green",
        )

        self.assertFalse(plan["ok"])
        self.assertEqual(plan["plan_type"], "station_mediated_handoff")
        self.assertEqual(plan["active_arm_ids"], ["Arm1"])
        self.assertEqual(plan["recommended_action"], "use build_pick_place_plan with the active arm or enable two-arm mode")

    def test_workspace_state_classifies_shared_zone_and_park_pose(self):
        poses = {
            "Arm1": {
                "ok": True,
                "pose": {"x": 250.0, "y": 0.0, "z": 50.0, "r": 0.0},
            },
            "Arm2": {
                "ok": True,
                "pose": {"x": 395.0, "y": 0.0, "z": 83.41, "r": 0.0},
            },
        }

        with two_arm_mode():
            state = build_workspace_state(lambda arm_id: poses[arm_id])

        self.assertTrue(state["ok"])
        self.assertTrue(state["arms"]["Arm1"]["is_at_standard_park"])
        self.assertFalse(state["arms"]["Arm1"]["in_shared_zone"])
        self.assertTrue(state["arms"]["Arm2"]["in_shared_zone"])
        self.assertEqual(state["shared_zone_occupancy"]["occupied_by"], ["Arm2"])
        self.assertEqual(state["arms"]["Arm2"]["holding"], "unknown")

    def test_semantic_drop_expands_to_surface_release_sequence(self):
        plan = {
            "actions": [
                {
                    "action": "drop",
                    "arm_id": "Arm2",
                    "target": {"type": "named", "name": "drop_zone"},
                }
            ]
        }

        with two_arm_mode():
            result = validate_semantic_plan(plan)

        self.assertTrue(result["ok"])
        self.assertEqual(
            [(step["action"], step.get("phase") or step.get("state")) for step in result["expanded_actions"]],
            [("move", "hover"), ("move", "surface"), ("grip", "off"), ("move", "hover")],
        )

    def test_semantic_validator_rejects_release_at_hover(self):
        plan = {
            "actions": [
                {
                    "action": "move",
                    "arm_id": "Arm2",
                    "phase": "hover",
                    "target": {"type": "named", "name": "drop_zone"},
                },
                {"action": "grip", "arm_id": "Arm2", "state": "off"},
            ]
        }

        with two_arm_mode():
            result = validate_semantic_plan(plan)

        self.assertFalse(result["ok"])
        error_codes = {error["code"] for error in result["errors"]}
        self.assertIn("release_at_hover", error_codes)

    def test_semantic_validator_rejects_arm1_drop_zone_directly(self):
        plan = {
            "actions": [
                {
                    "action": "drop",
                    "arm_id": "Arm1",
                    "target": {"type": "named", "name": "drop_zone"},
                }
            ]
        }

        result = validate_semantic_plan(plan)

        self.assertFalse(result["ok"])
        error_codes = {error["code"] for error in result["errors"]}
        self.assertIn("deprecated_zone_for_arm", error_codes)

    def test_single_arm_plan_task_rewrites_drop_zone_to_storage_zone(self):
        result = plan_task(
            {
                "intent": "pick_drop",
                "arm_id": "Arm1",
                "object_x": 250.0,
                "object_y": 0.0,
                "destination": "drop_zone",
            }
        )

        self.assertTrue(result["ok"], result.get("validation"))
        self.assertEqual(result["plan"]["destination"], "storage_zone")
        self.assertEqual(result["plan"]["requested_destination"], "drop_zone")
        self.assertEqual(
            [action["arm_id"] for action in result["plan"]["actions"]],
            ["Arm1", "Arm1"],
        )
        self.assertEqual(result["plan"]["actions"][1]["target"]["name"], "storage_zone")
        self.assertTrue(result["validation"]["ok"])

    def test_plan_task_builds_pick_drop_semantic_plan(self):
        with two_arm_mode():
            result = plan_task(
                {
                    "intent": "pick_drop",
                    "arm_id": "Arm2",
                    "object_x": 250.0,
                    "object_y": 0.0,
                    "destination": "drop_zone",
                }
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            [action["action"] for action in result["plan"]["actions"]],
            ["pick", "drop"],
        )
        self.assertTrue(result["validation"]["ok"])

    def test_semantic_handoff_requires_support_retreat_before_receiver_enters(self):
        plan = {
            "actions": [
                {
                    "action": "move",
                    "arm_id": "Arm1",
                    "phase": "hover",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
                {
                    "action": "move",
                    "arm_id": "Arm1",
                    "phase": "surface",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
                {"action": "grip", "arm_id": "Arm1", "state": "off"},
                {
                    "action": "move",
                    "arm_id": "Arm1",
                    "phase": "hover",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
                {
                    "action": "move",
                    "arm_id": "Arm2",
                    "phase": "hover",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
            ]
        }

        with two_arm_mode():
            result = validate_semantic_plan(plan)

        self.assertFalse(result["ok"])
        error_codes = {error["code"] for error in result["errors"]}
        self.assertIn("handoff_receiver_entered_before_support_retreat", error_codes)

    def test_semantic_validator_rejects_unclaimed_handoff_object(self):
        plan = {
            "actions": [
                {
                    "action": "move",
                    "arm_id": "Arm2",
                    "phase": "hover",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
                {
                    "action": "move",
                    "arm_id": "Arm2",
                    "phase": "surface",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
                {"action": "grip", "arm_id": "Arm2", "state": "off"},
                {
                    "action": "move",
                    "arm_id": "Arm2",
                    "phase": "hover",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
                {
                    "action": "move",
                    "arm_id": "Arm2",
                    "phase": "park",
                    "target": {"type": "named", "name": "drop_zone"},
                },
                {"action": "scan", "camera_id": "Cam1", "color": "green"},
            ]
        }

        with two_arm_mode():
            result = validate_semantic_plan(plan)

        self.assertFalse(result["ok"])
        error_codes = {error["code"] for error in result["errors"]}
        self.assertIn("handoff_object_left_unclaimed", error_codes)

    def test_semantic_validator_rejects_receiver_release_back_to_handoff(self):
        plan = {
            "destination": "storage_zone",
            "actions": [
                {
                    "action": "move",
                    "arm_id": "Arm2",
                    "phase": "hover",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
                {
                    "action": "move",
                    "arm_id": "Arm2",
                    "phase": "surface",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
                {"action": "grip", "arm_id": "Arm2", "state": "off"},
                {
                    "action": "move",
                    "arm_id": "Arm2",
                    "phase": "hover",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
                {
                    "action": "move",
                    "arm_id": "Arm2",
                    "phase": "park",
                    "target": {"type": "named", "name": "home"},
                },
                {
                    "action": "move",
                    "arm_id": "Arm1",
                    "phase": "hover",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
                {
                    "action": "move",
                    "arm_id": "Arm1",
                    "phase": "surface",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
                {"action": "grip", "arm_id": "Arm1", "state": "on"},
                {
                    "action": "move",
                    "arm_id": "Arm1",
                    "phase": "hover",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
                {
                    "action": "drop",
                    "arm_id": "Arm1",
                    "target": {"type": "named", "name": "handoff_zone"},
                },
            ],
        }

        with two_arm_mode():
            result = validate_semantic_plan(plan)

        self.assertFalse(result["ok"])
        error_codes = {error["code"] for error in result["errors"]}
        self.assertIn("handoff_receiver_wrong_final_destination", error_codes)

    def test_plan_task_plan_all_matching_green_to_storage_uses_direct_and_handoff(self):
        scan = {
            "ok": True,
            "camera_id": "Cam1",
            "objects": [
                {
                    "id": "obj-4",
                    "color": "green",
                    "per_arm_robot_plane": {
                        "Arm1": {"x": 250.0, "y": 0.0},
                        "Arm2": {"x": 540.0, "y": 0.0},
                    },
                    "reachable_by": ["Arm1"],
                    "preferred_arm": "Arm1",
                },
                {
                    "id": "obj-2",
                    "color": "green",
                    "per_arm_robot_plane": {
                        "Arm1": {"x": 500.0, "y": 40.0},
                        "Arm2": {"x": 250.0, "y": 0.0},
                    },
                    "reachable_by": ["Arm2"],
                    "preferred_arm": "Arm2",
                },
                {
                    "id": "obj-1",
                    "color": "red",
                    "per_arm_robot_plane": {
                        "Arm1": {"x": 250.0, "y": 80.0},
                        "Arm2": {"x": 540.0, "y": -80.0},
                    },
                    "reachable_by": ["Arm1"],
                    "preferred_arm": "Arm1",
                },
            ],
        }

        with two_arm_mode():
            result = plan_task(
                {
                    "intent": "plan_all_matching_objects",
                    "color": "green",
                    "destination": "storage_zone",
                    "scan": scan,
                }
            )

        self.assertTrue(result["ok"], result.get("validation"))
        actions = result["plan"]["actions"]
        self.assertEqual([action["action"] for action in actions], ["pick", "drop", "handoff", "scan"])
        self.assertEqual(actions[0]["arm_id"], "Arm1")
        self.assertEqual(actions[2]["intent_arm"], "Arm1")
        self.assertEqual(actions[2]["support_arm"], "Arm2")
        self.assertEqual(actions[-1]["verification"]["type"], "scan_absence")
        self.assertEqual(result["plan"]["matching_object_plan"]["planned_object_count"], 2)

        expanded = result["validation"]["expanded_actions"]
        support_retreat_index = next(
            index for index, action in enumerate(expanded)
            if action.get("action") == "move"
            and action.get("arm_id") == "Arm2"
            and action.get("phase") in {"hover", "park"}
            and (action.get("target") or {}).get("name") == "home"
        )
        receiver_enter_index = next(
            index for index, action in enumerate(expanded)
            if action.get("action") == "move"
            and action.get("arm_id") == "Arm1"
            and (action.get("target") or {}).get("name") == "handoff_zone"
        )
        self.assertLess(support_retreat_index, receiver_enter_index)

    def test_single_arm_plan_all_matching_drop_zone_uses_arm1_directly(self):
        scan = {
            "ok": True,
            "camera_id": "Cam1",
            "objects": [
                {
                    "id": "obj-1",
                    "color": "green",
                    "per_arm_robot_plane": {
                        "Arm1": {"x": 250.0, "y": 0.0},
                    },
                    "reachable_by": ["Arm1"],
                    "preferred_arm": "Arm1",
                },
                {
                    "id": "obj-2",
                    "color": "green",
                    "per_arm_robot_plane": {
                        "Arm1": {"x": 300.0, "y": 40.0},
                    },
                    "reachable_by": ["Arm1"],
                    "preferred_arm": "Arm1",
                },
            ],
        }

        result = plan_task(
            {
                "intent": "plan_all_matching_objects",
                "color": "green",
                "destination": "drop_zone",
                "scan": scan,
            }
        )

        self.assertTrue(result["ok"], result.get("validation"))
        self.assertEqual(result["plan"]["destination"], "storage_zone")
        self.assertEqual(result["plan"]["requested_destination"], "drop_zone")
        actions = result["plan"]["actions"]
        self.assertEqual([action["action"] for action in actions], ["pick", "drop", "pick", "drop", "scan"])
        self.assertTrue(all(action.get("arm_id") in {None, "Arm1"} for action in actions))
        self.assertEqual(result["plan"]["matching_object_plan"]["planned_object_count"], 2)

    def test_single_arm_plan_all_matching_reports_arm1_reach_limits(self):
        scan = {
            "ok": True,
            "camera_id": "Cam1",
            "objects": [
                {
                    "id": "obj-far",
                    "color": "green",
                    "per_arm_robot_plane": {
                        "Arm1": {
                            "x": 522.0,
                            "y": 24.0,
                            "reachable": False,
                            "reach_radius_mm": 522.6,
                        },
                    },
                    "reachable_by": [],
                    "preferred_arm": None,
                },
            ],
        }

        result = plan_task(
            {
                "intent": "plan_all_matching_objects",
                "color": "green",
                "destination": "drop_zone",
                "scan": scan,
            }
        )

        self.assertFalse(result["ok"])
        error = json.loads(result["error"])
        self.assertEqual(error["active_arm_ids"], ["Arm1"])
        self.assertEqual(error["destination"], "storage_zone")
        self.assertEqual(error["unresolved"][0]["reason"], "Arm1 cannot reach this object with the current reach limits")
        self.assertEqual(error["unresolved"][0]["reach_diagnostic"]["arm_id"], "Arm1")
        self.assertEqual(error["unresolved"][0]["reach_diagnostic"]["reach_radius_mm"], 522.6)

    def test_single_arm_plan_all_matching_derives_reach_from_xy_when_reachable_by_is_stale(self):
        scan = {
            "ok": True,
            "camera_id": "Cam1",
            "objects": [
                {
                    "id": "obj-stale",
                    "color": "green",
                    "per_arm_robot_plane": {
                        "Arm1": {
                            "x": 356.916,
                            "y": 164.297,
                            "reachable": False,
                            "reach_radius_mm": 392.9,
                        },
                    },
                    "reachable_by": [],
                    "preferred_arm": None,
                },
            ],
        }

        result = plan_task(
            {
                "intent": "plan_all_matching_objects",
                "color": "green",
                "destination": "drop_zone",
                "scan": scan,
            }
        )

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["plan"]["destination"], "storage_zone")
        self.assertEqual([action["action"] for action in result["plan"]["actions"]], ["pick", "drop", "scan"])
        self.assertEqual(result["plan"]["actions"][0]["arm_id"], "Arm1")
        self.assertEqual(
            result["plan"]["matching_object_plan"]["objects"][0]["reach_source"],
            "planner_guardrail_xy_check",
        )


if __name__ == "__main__":
    unittest.main()




