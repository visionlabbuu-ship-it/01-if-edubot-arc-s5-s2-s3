import math
import time
import re

from .camera_controller import CameraController
from . import config
from .config import (
    ARM_FRAME_TRANSFORMS,
    ARM_NAMED_TARGETS,
    ARM_PICK_CONFIG,
    ARM_REACH_ZONES,
    CAMERA_CONFIG,
    ROBOT_SAFETY_CONFIG,
    active_config,
    active_robot_ids,
)
from .incident_store import IncidentStore
from .robot_guardrails import RobotSafetyGuardrail
from .robot_arm import MG400Robot
from .sim_robot import SimRobot
from .sim_world import WorldModel
from .surface_model import phase_z_from_pick_config, surface_z_for_named_target, surface_z_for_pose
from .storage_service import find_slot as find_storage_slot, load as load_storage, resolve as resolve_storage, scan as scan_storage, update_occupancy, reserve_slot, release_reservation, set_verification_pending


class FleetController:
    IMMEDIATE_FAULT_DISTANCE_THRESHOLD_MM = 5.0

    def __init__(self, fleet_config):
        self.incidents = IncidentStore()
        self.active_arm_ids = active_robot_ids(fleet_config)
        active_reach_zones = {
            arm_id: zone
            for arm_id, zone in ARM_REACH_ZONES.items()
            if arm_id in self.active_arm_ids
        }
        self.camera = CameraController(
            CAMERA_CONFIG,
            active_reach_zones,
            active_config(ARM_FRAME_TRANSFORMS),
            active_arm_ids=self.active_arm_ids,
        )
        self.guardrails = RobotSafetyGuardrail(
            ROBOT_SAFETY_CONFIG,
            active_config(ARM_FRAME_TRANSFORMS),
            active_arm_ids=self.active_arm_ids,
        )
        active_fleet_config = active_config(fleet_config)
        self.camera_sim = None
        if config.SIM_MODE:
            self.world = WorldModel()
            # world เนเธซเธฅเธ”เนเธ __init__ เนเธฅเนเธง (singleton)
            # เนเธซเนเธเธฅเนเธญเธเธญเนเธฒเธเธเธฒเธ world model โ’ scan_workspace เนเธเน simulator
            self.camera.world_model = self.world
            self.camera_sim = None  # set by caller if needed
            self.robots = {
                robot_id: SimRobot(
                    robot_id,
                    cfg["ip"],
                    cfg["port"],
                    start_pose=cfg.get("shared_home_pose"),
                    world=self.world,
                )
                for robot_id, cfg in active_fleet_config.items()
            }
        else:
            self.world = None
            self.robots = {
                robot_id: MG400Robot(robot_id, cfg["ip"], cfg["port"])
                for robot_id, cfg in active_fleet_config.items()
            }

    def _guardrail_block(self, robot_id: str, operation: str, result: dict):
        return self.guardrails.block_result(robot_id, operation, result)

    # ---- scene management (sim mode only) ----
    def _require_world(self):
        """เธเธทเธ WorldModel เธ–เนเธฒเธญเธขเธนเนเนเธ sim mode เธกเธดเธเธฐเธเธฑเนเธเธเธทเธ error dict"""
        if not config.SIM_MODE or self.world is None:
            return None, {"ok": False, "error": "Scene editing is only available in simulation mode"}
        return self.world, None

    def scene_state(self):
        """เธชเธ–เธฒเธเธฐเธเธฒเธเธเธฑเธเธเธธเธเธฑเธเธชเธณเธซเธฃเธฑเธ UI (objects, held, zones, arms)"""
        world, err = self._require_world()
        if err:
            return err
        # เนเธซเธฅเธ”เธเธฒเธ disk เน€เธเธเธฒเธฐเน€เธกเธทเนเธญเนเธเธฅเนเน€เธเธฅเธตเนเธขเธ (CLI/subprocess)
        # เธซเนเธฒเธก force _load เธ—เธธเธ poll โ€” เธ–เนเธฒเนเธเธฅเนเธเธฑเธเธเธฃเธถเนเธเธ—เธฒเธเธเธฐเธฃเธตเน€เธเนเธ•เธเธฒเธเธเธฅเธฑเธ default
        if hasattr(world, "reload_if_changed"):
            world.reload_if_changed()
        state = world.scene_state()
        state["sim_mode"] = True
        arms_payload = self.arms_motion_state()
        state["arms"] = arms_payload.get("arms", [])
        state["server_now_ms"] = arms_payload.get("server_now_ms")
        state["motion_profile"] = arms_payload.get("motion_profile")
        # held เธเธฒเธ arms_payload เน€เธเนเธเธเธงเธฒเธกเธเธฃเธดเธเธฅเนเธฒเธชเธธเธ” (เธซเธฅเธฑเธ pick เนเธ memory)
        if "held" in arms_payload:
            state["held"] = arms_payload.get("held")
            state["held_by"] = arms_payload.get("held_by")
        return state

    def arms_motion_state(self):
        """Lightweight arms + trajectory เธชเธณเธซเธฃเธฑเธ poll เน€เธฃเนเธง (โฅ25 FPS UI)

        เธฃเธงเธก held/held_by เธ—เธธเธเธเธฃเธฑเนเธ โ€” เนเธกเนเธเธฑเนเธ poll เน€เธฃเนเธง (80ms) เธเธฐเธ—เธฑเธ holding เน€เธเนเธ
        unknown เธเธฒเธ scene cache เน€เธเนเธฒ โ’ เธงเธฑเธ•เธ–เธธเธ—เธตเนเธ–เธทเธญเธซเธฒเธขเธเธฒเธเธเธฅเธฒเธขเนเธเธ
        """
        import time as _time
        from . import config as _cfg

        arms = []
        profile = None
        for robot_id, robot in self.robots.items():
            if hasattr(robot, "get_motion_public"):
                arm = robot.get_motion_public()
            else:
                pose_payload = self.get_arm_pose(robot_id)
                if not pose_payload.get("ok"):
                    continue
                pose = pose_payload.get("pose") or {}
                arm = {
                    "arm_id": robot_id,
                    "pose": {
                        "x": float(pose.get("x", 0.0)),
                        "y": float(pose.get("y", 0.0)),
                        "z": float(pose.get("z", 0.0)),
                        "r": float(pose.get("r", 0.0)),
                    },
                    "robot_mode_name": pose_payload.get("robot_mode_name"),
                    "enable_status": pose_payload.get("enable_status"),
                    "running_status": pose_payload.get("running_status"),
                    "error_status": pose_payload.get("error_status"),
                    "motion": None,
                }
            arms.append(arm)
            if profile is None and hasattr(robot, "motion_profile"):
                try:
                    profile = robot.motion_profile()
                except Exception:
                    profile = None

        held = None
        held_by = None
        if self.world is not None:
            try:
                # memory เน€เธ”เธตเธขเธงเธเธฑเธ + lock โ€” เธซเนเธฒเธก _load เธ—เธธเธ 80ms (เน€เธเธขเธ—เธณเนเธซเน held เธซเธฒเธข)
                if hasattr(self.world, "held_snapshot"):
                    held, held_by = self.world.held_snapshot()
                else:
                    held = dict(self.world._held) if getattr(self.world, "_held", None) else None
                    held_by = self.world._held_by if held else None
            except Exception:
                held, held_by = None, None

        # เธ•เธดเธ” holding เธฅเธเนเธ•เนเธฅเธฐเนเธเธ โ€” UI เธญเนเธฒเธเธ•เธฃเธเธเธฒเธ arm entry เนเธกเนเธเธถเนเธ merge เธเธฑเนเธ client
        held_id = held.get("id") if isinstance(held, dict) else None
        held_color = held.get("color") if isinstance(held, dict) else None
        for arm in arms:
            aid = arm.get("arm_id")
            if held_id and held_by and aid == held_by:
                arm["holding"] = held_id
                arm["holding_color"] = held_color or "green"
            else:
                arm["holding"] = "unknown"
                arm["holding_color"] = None

        return {
            "ok": True,
            "arms": arms,
            "held": held,
            "held_by": held_by,
            "server_now_ms": _time.time() * 1000.0,
            "motion_profile": profile
            or {
                "speed_mm_s": float(getattr(_cfg, "SIM_MOTION_SPEED_MM_S", 100.0)),
                "fps": float(getattr(_cfg, "SIM_MOTION_FPS", 50.0)),
                "easing": str(getattr(_cfg, "SIM_MOTION_EASING", "linear")),
            },
            "any_running": any(bool(a.get("running_status")) for a in arms),
        }

    def spawn_object(self, color: str = "green", x: float = 250.0, y: float = -50.0):
        world, err = self._require_world()
        if err:
            return err
        return world.spawn_object(color=color, x=x, y=y)

    def remove_object(self, obj_id: str):
        world, err = self._require_world()
        if err:
            return err
        return world.remove_object(obj_id)

    def move_object(self, obj_id: str, x: float, y: float):
        world, err = self._require_world()
        if err:
            return err
        return world.move_object(obj_id, x, y)

    def reset_scene(self):
        world, err = self._require_world()
        if err:
            return err
        world.reset()
        return {"ok": True, "scene": world.scene_state()}

    def _other_arm_pose_context(self, robot_id: str):
        poses = {}
        unavailable = {}
        for other_id in self.robots.keys():
            if other_id == robot_id:
                continue
            state = self.get_arm_pose(other_id)
            if state.get("ok"):
                poses[other_id] = state["pose"]
            else:
                unavailable[other_id] = state
        return {"poses": poses, "unavailable": unavailable}

    def _other_arm_poses(self, robot_id: str):
        return self._other_arm_pose_context(robot_id)["poses"]

    def _unknown_other_pose_block(self, robot_id, operation, unavailable):
        return {
            "ok": False,
            "robot_id": robot_id,
            "operation": operation,
            "guardrail_blocked": True,
            "error_code": "other_arm_pose_unknown",
            "error": "Collision preflight requires a current pose for every other active arm",
            "unavailable_arms": sorted(unavailable),
            "pose_errors": unavailable,
            "safety_geometry": self.guardrails.safety_geometry(),
        }

    def _preflight_pose(self, robot_id: str, pose: dict, operation: str, check_collision: bool = True):
        context = self._other_arm_pose_context(robot_id) if check_collision else {"poses": {}, "unavailable": {}}
        if context["unavailable"]:
            return self._unknown_other_pose_block(robot_id, operation, context["unavailable"])
        result = self.guardrails.check_pose(
            robot_id,
            pose,
            other_poses=context["poses"],
            operation=operation,
            check_collision=check_collision,
        )
        if not result.get("ok"):
            return self._guardrail_block(robot_id, operation, result)
        return None

    def preflight_pose(self, robot_id: str, pose: dict, operation: str = "preflight_pose", check_collision: bool = True):
        robot = self.get_robot(robot_id)
        resolved_robot_id = robot.robot_id if robot else str(robot_id)
        if not robot:
            return {"ok": False, "robot_id": resolved_robot_id, "error": f"Robot '{robot_id}' not found"}

        context = self._other_arm_pose_context(resolved_robot_id) if check_collision else {"poses": {}, "unavailable": {}}
        if context["unavailable"]:
            return self._unknown_other_pose_block(resolved_robot_id, operation, context["unavailable"])
        result = self.guardrails.check_pose(
            resolved_robot_id,
            pose,
            other_poses=context["poses"],
            operation=operation,
            check_collision=check_collision,
        )
        if not result.get("ok"):
            blocked = self._guardrail_block(resolved_robot_id, operation, result)
            blocked["target_pose"] = {
                "x": float(pose["x"]),
                "y": float(pose["y"]),
                "z": float(pose["z"]),
                "r": float(pose.get("r", 0.0)),
            }
            return blocked
        return {
            "ok": True,
            "robot_id": resolved_robot_id,
            "operation": operation,
            "guardrail_passed": True,
            "target_pose": result.get("target_pose"),
            "guardrail": result,
            "safety_geometry": self.guardrails.safety_geometry(),
        }

    def _current_pose(self, robot_id: str):
        state = self.get_arm_pose(robot_id)
        if not state.get("ok"):
            return None, state
        return state["pose"], None

    def _relative_target_pose(self, robot_id: str, dx: float, dy: float, dz: float, dr: float = 0.0):
        current_pose, error = self._current_pose(robot_id)
        if error:
            return None, error
        return {
            "x": float(current_pose["x"]) + float(dx),
            "y": float(current_pose["y"]) + float(dy),
            "z": float(current_pose["z"]) + float(dz),
            "r": float(current_pose.get("r", 0.0)) + float(dr),
        }, None

    def _native_motion_poses(self, command: str):
        cmd = str(command or "").strip()
        match = re.match(r"^(MovL|MovJ)\(([-0-9.,\s]+)\)$", cmd)
        if match:
            values = [part.strip() for part in match.group(2).split(",")]
            if len(values) == 4:
                return [
                    {
                        "x": float(values[0]),
                        "y": float(values[1]),
                        "z": float(values[2]),
                        "r": float(values[3]),
                    }
                ]
        match = re.match(r"^Arc\(([-0-9.,\s]+)\)$", cmd)
        if match:
            values = [part.strip() for part in match.group(1).split(",")]
            if len(values) == 8:
                return [
                    {
                        "x": float(values[0]),
                        "y": float(values[1]),
                        "z": float(values[2]),
                        "r": float(values[3]),
                    },
                    {
                        "x": float(values[4]),
                        "y": float(values[5]),
                        "z": float(values[6]),
                        "r": float(values[7]),
                    },
                ]
        return None

    def preflight_parallel_targets(self, arm_targets, path="task"):
        conflict = self.guardrails.parallel_shared_zone_conflict(arm_targets, path=path)
        if conflict.get("ok"):
            return None
        return {
            "ok": False,
            "path": path,
            "guardrail_blocked": True,
            "fault_type": "guardrail_blocked",
            "recommended_action": conflict.get("recommended_action", "sequence_or_replan"),
            "guardrail": conflict,
            "error": conflict.get("message", "Parallel task blocked by ARC safety guardrail."),
        }

    def _save_motion_incident_if_needed(self, robot_id: str, operation: str, result):
        if not isinstance(result, dict) or result.get("ok", True):
            return

        incident = {
            "robot_id": robot_id,
            "operation": operation,
            "status": result.get("status"),
            "fault_type": result.get("fault_type"),
            "recommended_action": result.get("recommended_action"),
            "saw_running_motion": result.get("saw_running_motion"),
            "max_deviation_mm": result.get("max_deviation_mm"),
            "start_state": result.get("start_state"),
            "final_state": result.get("final_state"),
            "command": result.get("command"),
            "command_response": result.get("command_response"),
        }
        self.incidents.save(robot_id, incident)

    def connect_all(self):
        for robot in self.robots.values():
            robot.connect()

    def connect_robot(self, robot_id: str):
        robot = self.get_robot(robot_id)
        if not robot:
            return False
        return robot.connect()

    def disconnect_all(self):
        for robot in self.robots.values():
            robot.disconnect()

    def get_robot(self, robot_id: str):
        rid = str(robot_id).strip()
        if rid in ["Arm1", "1", "arm1"]:
            return self.robots.get("Arm1")
        if rid in ["Arm2", "2", "arm2"]:
            return self.robots.get("Arm2")
        return self.robots.get(rid)

    def list_arms(self):
        arms = []
        connected_count = 0

        for robot in self.robots.values():
            status = robot.get_status()
            arms.append(status)
            if status["connected"]:
                connected_count += 1

        return {
            "total_arms": len(self.robots),
            "connected_arms": connected_count,
            "arms": arms,
        }

    def get_arm_status(self, robot_id: str):
        robot = self.get_robot(robot_id)
        if not robot:
            return {
                "ok": False,
                "error": f"Robot '{robot_id}' not found",
            }

        if not robot.is_connected:
            robot.connect()

        status = robot.get_status()
        status["ok"] = status["connected"]
        return status

    def get_arm_state(self, robot_id: str):
        robot = self.get_robot(robot_id)
        if not robot:
            return {
                "ok": False,
                "error": f"Robot '{robot_id}' not found",
            }

        feedback = robot.get_feedback_snapshot()
        if not feedback.get("ok"):
            return {
                "ok": False,
                "robot_id": robot.robot_id,
                "error": feedback["error"],
            }

        return {
            "ok": True,
            "robot_id": robot.robot_id,
            "state": feedback,
        }

    def get_arm_pose(self, robot_id: str):
        state = self.get_arm_state(robot_id)
        if not state.get("ok"):
            return state

        return {
            "ok": True,
            "robot_id": state["robot_id"],
            "pose": state["state"]["pose"],
            "joints": state["state"]["joints"],
            "robot_mode": state["state"]["robot_mode"],
            "robot_mode_name": state["state"]["robot_mode_name"],
            "enable_status": state["state"]["enable_status"],
            "running_status": state["state"]["running_status"],
            "error_status": state["state"]["error_status"],
        }

    def _get_pick_config(self, robot_id: str):
        return ARM_PICK_CONFIG.get(robot_id, ARM_PICK_CONFIG.get("Arm1", {}))

    def _get_named_target(self, robot_id: str, destination: str):
        destination_key = str(destination or "").strip().lower()
        arm_targets = ARM_NAMED_TARGETS.get(robot_id, {})
        return arm_targets.get(destination_key)

    def _phase_z_from_pick_config(self, robot_id: str, phase: str):
        return phase_z_from_pick_config(robot_id, phase)

    def _phase_z_for_work_pose(self, robot_id: str, phase: str, x: float, y: float):
        return phase_z_from_pick_config(robot_id, phase, x, y)

    def _phase_z_from_named_target(self, target: dict, phase: str):
        phase_key = str(phase or "").strip().lower()
        if phase_key == "hover":
            return float(target["hover_z"])
        if phase_key == "surface":
            return surface_z_for_named_target(target)
        return None

    def _distance_xy(self, first: dict, second: dict):
        dx = float(first["x"]) - float(second["x"])
        dy = float(first["y"]) - float(second["y"])
        return math.sqrt(dx * dx + dy * dy)

    def _verify_post_pick(self, camera_id: str, color: str, source_pose: dict, robot_id: str):
        cfg = self._get_pick_config(robot_id)
        delay_s = float(cfg.get("post_pick_rescan_delay_s", 1.0))
        tolerance_mm = float(cfg.get("post_pick_position_tolerance_mm", 35.0))
        if delay_s > 0:
            time.sleep(delay_s)

        rescan = self.camera.scan_workspace(
            camera_id=camera_id,
            color=color,
            selection="first",
            require_saved_workspace=True,
        )
        if not rescan.get("ok"):
            return {
                "ok": False,
                "verified": False,
                "error": "Post-pick rescan failed",
                "rescan": rescan,
            }

        nearby_matches = []
        for obj in rescan.get("objects", []):
            arm_plane = (obj.get("per_arm_robot_plane") or {}).get(robot_id)
            if not arm_plane:
                continue
            distance_mm = self._distance_xy(arm_plane, source_pose)
            if distance_mm <= tolerance_mm:
                nearby_matches.append(
                    {
                        "object_id": obj["id"],
                        "robot_plane": arm_plane,
                        "distance_mm": distance_mm,
                    }
                )

        verified = len(nearby_matches) == 0
        return {
            "ok": True,
            "verified": verified,
            "tolerance_mm": tolerance_mm,
            "rescan": rescan,
            "nearby_matches": nearby_matches,
            "error": None if verified else "Detected a same-color object still near the source pose after pick/place",
        }

    def set_tool_state(self, robot_id: str, enable: bool):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}
        if not robot.is_connected:
            robot.connect()

        cfg = self._get_pick_config(robot.robot_id)
        response = robot.set_tool_output(cfg.get("tool_do_index", 1), enable)
        ok = str(response).startswith("0,{},")
        settle_s = float(cfg.get("tool_settle_s", 0.5))
        if enable:
            settle_s += float(cfg.get("post_suction_on_delay_s", 0.0))
        if ok:
            time.sleep(settle_s)
        return {
            "ok": ok,
            "robot_id": robot.robot_id,
            "tool_enabled": bool(enable),
            "settle_s": settle_s,
            "result": response,
            "error": None if ok else "Tool output command failed",
        }

    def move_to_work_pose(self, robot_id: str, x: float, y: float, phase: str, r: float = 0.0):
        """Atomic single motion to an explicit workspace x/y at a configured Z phase.

        This intentionally does not toggle suction and does not perform approach/descend/lift
        sequences. Agents compose those steps explicitly for handoff and teaching flows.
        """
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}
        if not robot.is_connected:
            robot.connect()

        phase_key = str(phase or "").strip().lower()
        z = self._phase_z_for_work_pose(robot.robot_id, phase_key, x, y)
        if z is None:
            return {
                "ok": False,
                "robot_id": robot.robot_id,
                "error": "phase must be 'hover' or 'surface'",
            }

        target_pose = {"x": float(x), "y": float(y), "z": z, "r": float(r)}
        blocked = self._preflight_pose(robot.robot_id, target_pose, f"move_to_work_pose:{phase_key}")
        if blocked:
            blocked["phase"] = phase_key
            blocked["target_pose"] = target_pose
            return blocked

        response = robot.move_joint_to_pose(
            target_pose["x"],
            target_pose["y"],
            target_pose["z"],
            target_pose["r"],
        )
        ok = response.get("ok", False) if isinstance(response, dict) else not str(response).startswith("Error:")
        payload = {
            "ok": ok,
            "robot_id": robot.robot_id,
            "operation": "move_to_work_pose",
            "phase": phase_key,
            "atomic_motion": True,
            "motion_type": "MovJ",
            "target_pose": target_pose,
            "result": response,
            "error": None if ok else "Atomic work-pose motion failed",
        }
        self._save_motion_incident_if_needed(robot.robot_id, f"move_to_work_pose:{phase_key}", response)
        return payload

    def move_to_named_pose(self, robot_id: str, destination: str, phase: str):
        """Atomic single motion to a named target at hover or surface Z.

        Use this for explicit handoff-zone and drop-zone sequences where suction state
        changes must remain visible to the agent as separate tool calls.
        """
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}
        if not robot.is_connected:
            robot.connect()

        target = self._get_named_target(robot.robot_id, destination)
        if not target:
            return {
                "ok": False,
                "robot_id": robot.robot_id,
                "error": f"Named target '{destination}' not found for {robot.robot_id}",
            }

        phase_key = str(phase or "").strip().lower()
        z = self._phase_z_from_named_target(target, phase_key)
        if z is None:
            return {
                "ok": False,
                "robot_id": robot.robot_id,
                "destination": destination,
                "error": "phase must be 'hover' or 'surface'",
            }

        target_pose = {
            "x": float(target["x"]),
            "y": float(target["y"]),
            "z": z,
            "r": float(target.get("r", 0.0)),
        }
        blocked = self._preflight_pose(
            robot.robot_id,
            target_pose,
            f"move_to_named_pose:{destination}:{phase_key}",
        )
        if blocked:
            blocked["destination"] = destination
            blocked["phase"] = phase_key
            blocked["target_pose"] = target_pose
            return blocked

        response = robot.move_joint_to_pose(
            target_pose["x"],
            target_pose["y"],
            target_pose["z"],
            target_pose["r"],
        )
        ok = response.get("ok", False) if isinstance(response, dict) else not str(response).startswith("Error:")
        payload = {
            "ok": ok,
            "robot_id": robot.robot_id,
            "operation": "move_to_named_pose",
            "destination": destination,
            "phase": phase_key,
            "atomic_motion": True,
            "motion_type": "MovJ",
            "target_pose": target_pose,
            "result": response,
            "error": None if ok else f"Atomic named-pose motion to '{destination}' failed",
        }
        self._save_motion_incident_if_needed(robot.robot_id, f"move_to_named_pose:{destination}:{phase_key}", response)
        return payload

    def _resolve_pick_xy(self, robot_id: str, x: float, y: float):
        """เนเธเธฅเธเธเธดเธเธฑเธ”เธซเธขเธดเธ โ’ arm-local เธ—เธตเน MG400 เนเธเน

        เธฃเธฑเธเนเธ”เนเธ—เธฑเนเธ:
          - arm-local เธญเธขเธนเนเนเธฅเนเธง (เธเธฒเธ per_arm_robot_plane[arm])
          - workspace (เธเธฒเธ robot_plane / LLM เธชเนเธ workspace เธ•เธฃเธเน)

        เนเธ sim: เธ–เนเธฒเน€เธเธญ object เนเธเธฅเน workspace (x,y) โ’ เนเธเน per_arm local เธเธญเธเนเธเธเธเธฑเนเธ
        เธ–เนเธฒเน€เธเธญเนเธเธฅเน local เธญเธขเธนเนเนเธฅเนเธง โ’ เนเธเนเธเนเธฒเธเธฑเนเธ
        """
        lx, ly = float(x), float(y)
        if config.SIM_MODE and self.world is not None:
            from .sim_world import workspace_to_arm_local, arm_local_to_workspace

            # 1) เธฅเธญเธ workspace เธ•เธฃเธเน
            obj = self.world.find_nearby({"x": lx, "y": ly}, tolerance_mm=50.0)
            if obj is not None:
                per = (obj.get("per_arm_robot_plane") or {}).get(robot_id)
                if isinstance(per, dict):
                    return float(per["x"]), float(per["y"]), obj
                ws = obj.get("robot_plane") or {}
                local = workspace_to_arm_local(robot_id, float(ws.get("x", lx)), float(ws.get("y", ly)))
                return float(local["x"]), float(local["y"]), obj

            # 2) เธฅเธญเธเน€เธเนเธ arm-local
            obj = self.world.find_nearby({"x": lx, "y": ly}, tolerance_mm=50.0, arm_id=robot_id)
            if obj is not None:
                return lx, ly, obj

            # 3) เนเธกเนเน€เธเธญ object โ€” เธ–เนเธฒเธเธธเธ”เธญเธขเธนเนเธเธญเธเธเนเธงเธ local เธเธเธ•เธด เนเธเธฅเธ workspaceโ’local
            # local x เธเธญเธ MG400 เธกเธฑเธ 100โ€“450; workspace x 0โ€“600
            if 0 <= lx <= 600 and -250 <= ly <= 450:
                # เธญเธฒเธเน€เธเนเธ workspace: เนเธเธฅเธ
                local = workspace_to_arm_local(robot_id, lx, ly)
                return float(local["x"]), float(local["y"]), None
        return lx, ly, None

    def _resolve_place_xy(self, robot_id: str, x: float, y: float):
        """เนเธเธฅเธเธเธดเธเธฑเธ”เธงเธฒเธ โ’ arm-local เธ—เธตเน MG400 เนเธเน

        เนเธ SIM เธเธฑเนเธ chat/LLM/UI เธกเธฑเธเธชเนเธ workspace (robot_plane) เธกเธฒ โ€” เธ•เนเธญเธเนเธเธฅเธ
        เน€เธซเธกเธทเธญเธ pick เธกเธดเธเธฐเธเธฑเนเธเนเธเธเนเธเธเธดเธ”เธเธธเธ” + place เนเธเธฅเธ localโ’workspace เธเนเธณ = เธงเธฑเธ•เธ–เธธเธเธฃเธฐเนเธ”เธ”
        Named targets เนเธเนเธเนเธฒ local เธเธฒเธ config เธญเธขเธนเนเนเธฅเนเธง (place_to_named_target)
        """
        lx, ly = float(x), float(y)
        if not config.SIM_MODE:
            return lx, ly
        from .sim_world import workspace_to_arm_local

        # local เธเธฑเธ”: y เธเธญเธเธเนเธงเธ workspace เธเธเธ•เธด (เน€เธเนเธ storage local yโ-311) เธซเธฃเธทเธญ x เธขเธฒเธงเน€เธเธดเธ board
        # workspace เธ—เธฑเนเธงเนเธ: xโ[0,600], yโ[-250,450]
        looks_like_workspace = (0.0 <= lx <= 600.0) and (-250.0 <= ly <= 450.0)
        # local MG400 เธกเธฑเธ xโ~80โ€“450; เธ–เนเธฒ y เธ•เธดเธ”เธฅเธเธกเธฒเธ (< -250) เนเธเธฅเธงเนเธฒ local เนเธฅเนเธง
        looks_like_local = (80.0 <= lx <= 480.0) and (ly < -250.0 or ly > 450.0)
        if looks_like_local:
            return lx, ly
        if looks_like_workspace:
            local = workspace_to_arm_local(robot_id, lx, ly)
            return float(local["x"]), float(local["y"])
        # เธเนเธฒเธญเธทเนเธ (เน€เธเนเธ local y เธเธฒเธเธเธฅเธฒเธ) โ€” เธ–เนเธฒ x เธญเธขเธนเนเนเธเธเนเธงเธ reach เนเธซเนเธ–เธทเธญเธงเนเธฒ local
        if 80.0 <= lx <= 480.0:
            return lx, ly
        local = workspace_to_arm_local(robot_id, lx, ly)
        return float(local["x"]), float(local["y"])

    def _sim_place_at_tip(self, robot):
        """SIM: เธงเธฒเธ held เธ•เธฃเธเธเธฅเธฒเธขเนเธเธเธเธฃเธดเธเธซเธฅเธฑเธ descend (arm-local โ’ workspace)

        เนเธเน pose เธเธฑเธเธเธธเธเธฑเธเธเธญเธเธซเธธเนเธ โ€” เนเธกเนเนเธเนเธเธดเธเธฑเธ”เธเธณเธชเธฑเนเธเธ”เธดเธ โ€” เธเธฑเธ object เธซเธฅเธธเธ”เธเธฒเธเธเธธเธ”เธงเธฒเธ
        """
        pose, err = self._current_pose(robot.robot_id)
        if pose is None:
            return {"ok": False, "error": (err or {}).get("error", "No tip pose for place")}
        px = float(pose["x"])
        py = float(pose["y"])
        pz = float(pose.get("z", 0.0))
        if hasattr(robot, "place_at"):
            return robot.place_at(px, py, pz)
        if self.world is not None:
            return self.world.place_at({"x": px, "y": py, "z": pz}, arm_id=robot.robot_id)
        return {"ok": False, "error": "No sim place path"}

    def pick_at(self, robot_id: str, x: float, y: float, r: float = 0.0):
        """pick: approach(hover) โ’ descend(surface) โ’ suction โ’ lift(hover).

        เนเธเธเนเธเธซเธฒเธงเธฑเธ•เธ–เธธ (เนเธเธฅเธ workspaceโ’arm-local เนเธซเน) โ€” เนเธกเนเน€เธฅเธทเนเธญเธเธงเธฑเธ•เธ–เธธเธกเธฒเธซเธฒเนเธเธ
        SIM เธงเธดเนเธ motion เน€เธ•เนเธก + world.pick เธ•เธญเธเธเธฅเธฒเธขเนเธ•เธฐ
        """
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}
        if not robot.is_connected:
            robot.connect()

        lx, ly, matched = self._resolve_pick_xy(robot.robot_id, x, y)

        cfg = self._get_pick_config(robot.robot_id)
        hover_z = float(cfg["hover_z"])
        pick_z = surface_z_for_pose(robot.robot_id, lx, ly)

        # sim: เนเธกเน sleep settle เธขเธฒเธง โ€” motion เน€เธญเธเธกเธต duration เธญเธขเธนเนเนเธฅเนเธง
        settle_s = 0.0 if config.SIM_MODE else float(cfg.get("motion_settle_s", 1.0))
        steps = []
        if matched is not None:
            steps.append({
                "step": "resolve_target",
                "result": {
                    "object_id": matched.get("id"),
                    "workspace": matched.get("robot_plane"),
                    "arm_local": {"x": lx, "y": ly},
                },
            })

        def run_step(step_name, func, *args):
            target_pose = {"x": args[0], "y": args[1], "z": args[2], "r": args[3] if len(args) > 3 else 0.0}
            blocked = self._preflight_pose(robot.robot_id, target_pose, f"pick_at:{step_name}")
            if blocked:
                steps.append({"step": step_name, "result": blocked})
                return False
            result = func(*args)
            ok = result.get("ok", False) if isinstance(result, dict) else not str(result).startswith("Error:")
            steps.append({"step": step_name, "result": result})
            if ok and settle_s > 0:
                time.sleep(settle_s)
            return ok

        if not run_step("approach", robot.move_joint_to_pose, lx, ly, hover_z, r):
            return {"ok": False, "robot_id": robot.robot_id, "steps": steps, "error": "Approach failed"}
        if not run_step("descend", robot.move_joint_to_pose, lx, ly, pick_z, r):
            return {"ok": False, "robot_id": robot.robot_id, "steps": steps, "error": "Descend failed"}

        # เธ”เธนเธ”: real = DO, sim = เธเธนเธ object เนเธ world เธ•เธญเธเธเธฅเธฒเธขเนเธ•เธฐเธเธทเนเธ
        if config.SIM_MODE and hasattr(robot, "pick_at"):
            world_pick = robot.pick_at(lx, ly, pick_z)
            steps.append({"step": "world_pick", "result": world_pick})
            if not world_pick.get("ok", False):
                return {
                    "ok": False,
                    "robot_id": robot.robot_id,
                    "steps": steps,
                    "error": world_pick.get("error", "World pick failed"),
                }
            tool_on = {"ok": True, "sim": True, "tool_enabled": True}
        else:
            tool_on = self.set_tool_state(robot.robot_id, True)
        steps.append({"step": "tool_on", "result": tool_on})
        if not tool_on.get("ok"):
            return {"ok": False, "robot_id": robot.robot_id, "steps": steps, "error": "Tool-on failed"}

        if not run_step("lift", robot.move_joint_to_pose, lx, ly, hover_z, r):
            return {"ok": False, "robot_id": robot.robot_id, "steps": steps, "error": "Lift failed"}

        return {
            "ok": True,
            "robot_id": robot.robot_id,
            "source_pose": {"x": lx, "y": ly, "hover_z": hover_z, "pick_z": pick_z, "r": r},
            "steps": steps,
        }

    def place_at(self, robot_id: str, x: float, y: float, r: float = 0.0):
        """Primitive place at explicit (x,y). Uses the arm's pick_z as the drop height
        (same height as suction during pick โ€” objects sit on the workspace surface).
        approach (hover) โ’ descend (pick_z) โ’ suction off โ’ lift (hover).
        SIM เธงเธดเนเธ motion เน€เธ•เนเธก + world.place_at เธ•เธญเธเธเธฅเนเธญเธข โ€” เนเธกเนเธงเธฒเธ.
        เธเธดเธเธฑเธ” x,y เนเธ SIM เธกเธฑเธเน€เธเนเธ workspace โ’ เนเธเธฅเธเน€เธเนเธ arm-local เธเนเธญเธ motion
        เนเธฅเนเธงเธงเธฒเธเธฅเธ world เธ•เธฃเธเธเธฅเธฒเธขเนเธเธเธเธฃเธดเธ (เนเธกเนเนเธเนเธเธดเธเธฑเธ”เธ”เธดเธ)."""
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}
        if not robot.is_connected:
            robot.connect()

        lx, ly = self._resolve_place_xy(robot.robot_id, x, y)

        cfg = self._get_pick_config(robot.robot_id)
        hover_z = float(cfg["hover_z"])
        place_z = surface_z_for_pose(robot.robot_id, lx, ly)

        settle_s = 0.0 if config.SIM_MODE else float(cfg.get("motion_settle_s", 1.0))
        steps = []
        if (lx, ly) != (float(x), float(y)):
            steps.append({
                "step": "resolve_place_xy",
                "result": {
                    "input": {"x": float(x), "y": float(y)},
                    "arm_local": {"x": lx, "y": ly},
                },
            })

        def run_step(step_name, func, *args):
            target_pose = {"x": args[0], "y": args[1], "z": args[2], "r": args[3] if len(args) > 3 else 0.0}
            blocked = self._preflight_pose(robot.robot_id, target_pose, f"place_at:{step_name}")
            if blocked:
                steps.append({"step": step_name, "result": blocked})
                return False
            result = func(*args)
            ok = result.get("ok", False) if isinstance(result, dict) else not str(result).startswith("Error:")
            steps.append({"step": step_name, "result": result})
            if ok and settle_s > 0:
                time.sleep(settle_s)
            return ok

        if not run_step("approach_place", robot.move_joint_to_pose, lx, ly, hover_z, r):
            return {"ok": False, "robot_id": robot.robot_id, "steps": steps, "error": "Place approach failed"}
        if not run_step("descend_place", robot.move_joint_to_pose, lx, ly, place_z, r):
            return {"ok": False, "robot_id": robot.robot_id, "steps": steps, "error": "Place descend failed"}

        if config.SIM_MODE and (hasattr(robot, "place_at") or self.world is not None):
            # เธงเธฒเธเธ•เธฃเธเธเธฅเธฒเธขเธเธฃเธดเธ โ€” เนเธกเนเนเธเน (x,y) เธ”เธดเธ (เธเธฑเธเน€เธเธฃเธกเธเธดเธ” / drift)
            world_place = self._sim_place_at_tip(robot)
            steps.append({"step": "world_place", "result": world_place})
            if not world_place.get("ok", False):
                return {
                    "ok": False,
                    "robot_id": robot.robot_id,
                    "steps": steps,
                    "error": world_place.get("error", "World place failed"),
                }
            tool_off = {"ok": True, "sim": True, "tool_enabled": False}
        else:
            tool_off = self.set_tool_state(robot.robot_id, False)
        steps.append({"step": "tool_off", "result": tool_off})
        if not tool_off.get("ok"):
            return {"ok": False, "robot_id": robot.robot_id, "steps": steps, "error": "Tool-off failed"}

        if not run_step("lift_place", robot.move_joint_to_pose, lx, ly, hover_z, r):
            return {"ok": False, "robot_id": robot.robot_id, "steps": steps, "error": "Place lift failed"}

        return {
            "ok": True,
            "robot_id": robot.robot_id,
            "target_pose": {"x": lx, "y": ly, "hover_z": hover_z, "place_z": place_z, "r": r},
            "steps": steps,
        }

    def _verify_storage_slot_after_place(self, slot, color=None, camera_id="Cam1"):
        """Verify placement from a fresh capacity scan, never from object scan."""
        slot_name = slot.get("name")
        verification = {
            "slot_name": slot_name,
            "expected_color": color,
            "verified": False,
            "source": "capacity_slot_scan",
        }
        cfg = self._get_pick_config((slot.get("reservation") or {}).get("robot_id") or "Arm1")
        delay_s = float(cfg.get("post_place_rescan_delay_s", cfg.get("post_pick_rescan_delay_s", 1.0)))
        if delay_s > 0:
            time.sleep(delay_s)

        scan = scan_storage()
        if not scan.get("ok"):
            verification.update({"error": "post_place_capacity_scan_failed", "scan": scan})
            update_occupancy(slot_name, "unknown", source="post_place_capacity_scan", verification=verification)
            return {"ok": False, "verified": False, "error_code": "capacity_unknown", "verification": verification}

        registry = scan.get("registry") or load_storage()
        observed = find_storage_slot(registry, slot_name)
        if not observed:
            verification.update({"error": "slot_missing_after_scan", "scan_timestamp": (registry.get("source_scan") or {}).get("timestamp")})
            update_occupancy(slot_name, "unknown", source="post_place_capacity_scan", verification=verification)
            return {"ok": False, "verified": False, "error_code": "capacity_unknown", "verification": verification}

        held = observed.get("held_object") or {}
        observed_color = observed.get("occupancy_color") or observed.get("object_color") or held.get("color")
        occupied = str(observed.get("occupancy") or "unknown").lower() == "occupied"
        color_matches = not color or str(observed_color or "").lower() == str(color).lower()
        verified = occupied and color_matches
        verification.update({
            "verified": verified,
            "observed_occupancy": observed.get("occupancy"),
            "observed_color": observed_color,
            "scan_timestamp": (registry.get("source_scan") or {}).get("timestamp"),
        })
        if not verified:
            verification["error"] = "slot_or_color_not_confirmed"

        update_occupancy(
            slot_name,
            "occupied" if verified else "unknown",
            held_object={"color": observed_color or color} if verified and (observed_color or color) else None,
            source="post_place_capacity_scan",
            verification=verification,
        )
        return {
            "ok": verified,
            "verified": verified,
            "error_code": None if verified else "capacity_unknown",
            "verification": verification,
            "slot": observed,
        }
    def place_to_storage_slot(self, robot_id: str, reference=None, color=None):
        if robot_id not in self.active_arm_ids:
            return {"ok": False, "error_code": "arm_inactive", "arm_id": robot_id}
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error_code": "arm_inactive", "arm_id": robot_id}
        if not robot.is_connected:
            robot.connect()
        online_arms = [arm_id for arm_id, item in self.robots.items() if item.is_connected]
        resolved = resolve_storage(
            reference=reference,
            color=color,
            arm_id=robot_id,
            active_arms=self.active_arm_ids,
            online_arms=online_arms,
        )
        if not resolved.get("ok"):
            return resolved
        slot = resolved["slot"]
        reservation = reserve_slot(slot.get("name"), robot_id=robot_id)
        if not reservation.get("ok"):
            return reservation
        slot = reservation["slot"]
        target = (slot.get("per_arm_robot_plane") or {}).get(robot_id)
        if not target:
            release_reservation(slot.get("name"), reason="transform_not_ready")
            return {"ok": False, "error_code": "transform_not_ready", "slot_name": slot.get("name"), "arm_id": robot_id}
        result = self.place_at(robot_id, float(target["x"]), float(target["y"]))
        result["storage_slot"] = slot.get("name")
        result["storage_group"] = slot.get("group")
        if not result.get("ok"):
            release_reservation(slot.get("name"), reason="placement_failed")
            return result
        set_verification_pending(slot.get("name"), {"status": "pending", "robot_id": robot_id})
        park = self.move_to_named_target(robot_id, "home")
        result["park"] = park
        if not park.get("ok"):
            update_occupancy(slot.get("name"), "unknown", source="post_place_park_failed", verification={"verified": False, "error": "park_failed"})
            result.update({"ok": False, "occupancy": "unknown", "verification_required": True, "error_code": "post_place_park_failed"})
            return result
        verification = self._verify_storage_slot_after_place(slot, color=color)
        result["verification"] = verification
        result["ok"] = bool(verification.get("verified"))
        result["occupancy"] = "occupied" if result["ok"] else "unknown"
        result["verification_required"] = False
        result["error_code"] = verification.get("error_code")
        return result
    def place_to_named_target(self, robot_id: str, destination: str):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}
        if not robot.is_connected:
            robot.connect()

        target = self._get_named_target(robot.robot_id, destination)
        if not target:
            return {
                "ok": False,
                "robot_id": robot.robot_id,
                "error": f"Named target '{destination}' not found for {robot.robot_id}",
            }

        settle_s = 0.0 if config.SIM_MODE else float(self._get_pick_config(robot.robot_id).get("motion_settle_s", 1.0))
        x = float(target["x"])
        y = float(target["y"])
        hover_z = float(target["hover_z"])
        place_z = surface_z_for_named_target(target)
        r = float(target.get("r", 0.0))
        steps = []

        def run_step(step_name, func, *args):
            target_pose = {"x": args[0], "y": args[1], "z": args[2], "r": args[3] if len(args) > 3 else 0.0}
            blocked = self._preflight_pose(robot.robot_id, target_pose, f"place_to_named_target:{destination}:{step_name}")
            if blocked:
                steps.append({"step": step_name, "result": blocked})
                return False
            result = func(*args)
            ok = result.get("ok", False) if isinstance(result, dict) else not str(result).startswith("Error:")
            steps.append({"step": step_name, "result": result})
            if ok and settle_s > 0:
                time.sleep(settle_s)
            return ok

        if not run_step("approach_destination", robot.move_joint_to_pose, x, y, hover_z, r):
            return {"ok": False, "robot_id": robot.robot_id, "steps": steps, "error": "Destination approach failed"}
        if place_z != hover_z:
            if not run_step("descend_destination", robot.move_joint_to_pose, x, y, place_z, r):
                return {"ok": False, "robot_id": robot.robot_id, "steps": steps, "error": "Destination descend failed"}
        # SIM: เธงเธฒเธเธฅเธ world เธ•เธฃเธเธเธฅเธฒเธขเนเธเธเธเธฃเธดเธ (named x/y = arm-local เนเธเน motion เนเธฅเนเธง)
        # เน€เธเธขเน€เธเธตเธขเธ local เน€เธเนเธ workspace เธ•เธฃเธเน โ’ เธงเธฑเธ•เธ–เธธเน€เธฅเธทเนเธญเธเธญเธญเธเธเธญเธเธเธธเธ”เธงเธฒเธ
        if config.SIM_MODE and (hasattr(robot, "place_at") or self.world is not None):
            world_result = self._sim_place_at_tip(robot)
            steps.append({"step": "world_place", "result": world_result})
            if not world_result.get("ok", False):
                return {
                    "ok": False,
                    "robot_id": robot.robot_id,
                    "steps": steps,
                    "error": world_result.get("error", "World place failed"),
                }
            tool_off = {"ok": True, "sim": True, "tool_enabled": False}
        else:
            tool_off = self.set_tool_state(robot.robot_id, False)
        steps.append({"step": "tool_off", "result": tool_off})
        if not tool_off.get("ok"):
            return {"ok": False, "robot_id": robot.robot_id, "steps": steps, "error": "Tool-off failed"}

        if place_z != hover_z:
            if not run_step("depart_destination", robot.move_joint_to_pose, x, y, hover_z, r):
                return {"ok": False, "robot_id": robot.robot_id, "steps": steps, "error": "Destination depart failed"}

        return {
            "ok": True,
            "robot_id": robot.robot_id,
            "destination": destination,
            "target_pose": {"x": x, "y": y, "hover_z": hover_z, "place_z": place_z, "r": r},
            "steps": steps,
        }

    def move_to_named_target(self, robot_id: str, destination: str):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}
        if not robot.is_connected:
            robot.connect()

        target = self._get_named_target(robot.robot_id, destination)
        if not target:
            return {
                "ok": False,
                "robot_id": robot.robot_id,
                "error": f"Named target '{destination}' not found for {robot.robot_id}",
            }

        pose = {
            "x": float(target["x"]),
            "y": float(target["y"]),
            "z": float(target["hover_z"]),
            "r": float(target.get("r", 0.0)),
        }
        blocked = self._preflight_pose(
            robot.robot_id,
            pose,
            f"move_to_named_target:{destination}",
        )
        if blocked:
            blocked["destination"] = destination
            blocked["named_target_pose"] = pose
            return blocked

        response = robot.move_joint_to_pose(
            float(target["x"]),
            float(target["y"]),
            float(target["hover_z"]),
            float(target.get("r", 0.0)),
        )
        ok = response.get("ok", False) if isinstance(response, dict) else not str(response).startswith("Error:")
        payload = {
            "ok": ok,
            "robot_id": robot.robot_id,
            "destination": destination,
            "named_target_pose": {
                "x": float(target["x"]),
                "y": float(target["y"]),
                "z": float(target["hover_z"]),
                "r": float(target.get("r", 0.0)),
            },
            "result": response,
            "error": None if ok else f"Move to named target '{destination}' failed",
        }
        self._save_motion_incident_if_needed(robot.robot_id, f"move_to_named_target:{destination}", response)
        return payload

    def pick_detected_and_place(self, robot_id: str, color: str, destination: str, camera_id: str = "Cam1", block: str = None):
        assignment_request = {"arm_id": robot_id, "color": color}
        if block:
            assignment_request["block"] = block
        assignment = self.camera.assign_targets(
            [assignment_request],
            camera_id=camera_id,
            require_saved_workspace=True,
        )
        if not assignment.get("ok"):
            return {
                "ok": False,
                "robot_id": robot_id,
                "camera_id": camera_id,
                "assignment": assignment,
                "error": assignment.get("error") or "No reachable detected target for requested arm/color",
            }

        target = assignment["targets"][0]
        source = target["robot_plane"]
        verification_source = target.get("calibrated_robot_plane", source)
        pick_result = self.pick_at(robot_id, source["x"], source["y"])
        if not pick_result.get("ok"):
            return {
                "ok": False,
                "robot_id": robot_id,
                "camera_id": camera_id,
                "assignment": assignment,
                "pick": pick_result,
                "error": "Pick step failed",
            }

        place_result = self.place_to_named_target(robot_id, destination)
        if not place_result.get("ok"):
            return {
                "ok": False,
                "robot_id": robot_id,
                "camera_id": camera_id,
                "assignment": assignment,
                "pick": pick_result,
                "place": place_result,
                "error": "Place step failed",
            }

        verification = self._verify_post_pick(camera_id, color, verification_source, robot_id)
        overall_ok = verification.get("ok") and verification.get("verified")

        return {
            "ok": overall_ok,
            "robot_id": robot_id,
            "camera_id": camera_id,
            "assignment": assignment,
            "pick": pick_result,
            "place": place_result,
            "post_pick_verification": verification,
            "destination": destination,
            "block": target.get("block"),
            "error": None if overall_ok else verification.get("error", "Post-pick verification failed"),
        }

    def pick_all_detected_and_place(self, robot_id: str, destination: str, color: str = "any", camera_id: str = "Cam1", max_picks: int = 10):
        picks = []
        seen_failure = None
        for iteration in range(max(1, int(max_picks))):
            scan = self.camera.scan_workspace(
                camera_id=camera_id,
                color=color,
                selection="first",
                require_saved_workspace=True,
            )
            if not scan.get("ok"):
                seen_failure = {"stage": "scan", "result": scan}
                break

            reachable_targets = [
                obj for obj in scan.get("objects", [])
                if robot_id in obj.get("reachable_by", [])
                and (color == "any" or obj.get("color") == color)
            ]
            if not reachable_targets:
                break

            target_obj = reachable_targets[0]
            arm_plane = (target_obj.get("per_arm_robot_plane") or {}).get(robot_id) or target_obj["robot_plane"]
            verification_source = target_obj["robot_plane"]

            pick_result = self.pick_at(robot_id, float(arm_plane["x"]), float(arm_plane["y"]))
            if not pick_result.get("ok"):
                seen_failure = {"stage": "pick", "object_id": target_obj["id"], "result": pick_result}
                picks.append({"object_id": target_obj["id"], "block": target_obj.get("block"), "color": target_obj.get("color"), "ok": False, "stage": "pick", "result": pick_result})
                break

            place_result = self.place_to_named_target(robot_id, destination)
            if not place_result.get("ok"):
                seen_failure = {"stage": "place", "object_id": target_obj["id"], "result": place_result}
                picks.append({"object_id": target_obj["id"], "block": target_obj.get("block"), "color": target_obj.get("color"), "ok": False, "stage": "place", "result": place_result})
                break

            verification = self._verify_post_pick(camera_id, target_obj.get("color", "any"), verification_source, robot_id)
            picks.append({
                "object_id": target_obj["id"],
                "block": target_obj.get("block"),
                "color": target_obj.get("color"),
                "ok": bool(verification.get("ok") and verification.get("verified")),
                "verification": verification,
            })

        return {
            "ok": seen_failure is None,
            "robot_id": robot_id,
            "camera_id": camera_id,
            "destination": destination,
            "color_filter": color,
            "picks": picks,
            "picked_count": sum(1 for p in picks if p.get("ok")),
            "failure": seen_failure,
            "error": None if seen_failure is None else (
                (seen_failure.get("result") or {}).get("error")
                or f"{seen_failure['stage']} step failed"
            ),
        }

    def recover_arm(self, robot_id: str):
        robot = self.get_robot(robot_id)
        if not robot:
            return {
                "ok": False,
                "error": f"Robot '{robot_id}' not found",
            }

        if not robot.is_connected:
            robot.connect()

        result = robot.recover()
        ok = (
            str(result["clear_error"]).startswith("0,{},")
            and str(result["enable_robot"]).startswith("0,{},")
        )

        return {
            "ok": ok,
            "robot_id": robot.robot_id,
            "result": result,
        }

    def recover_and_home_arm(self, robot_id: str):
        initial_state = self.get_arm_state(robot_id)
        if not initial_state.get("ok"):
            return initial_state

        steps = [{"step": "initial_state", "result": initial_state["state"]}]

        current_state = initial_state["state"]
        if current_state["error_status"] or current_state["robot_mode_name"] not in {"enabled", "running"}:
            recover_result = self.recover_arm(robot_id)
            steps.append({"step": "recover", "result": recover_result})
            if not recover_result.get("ok"):
                return {
                    "ok": False,
                    "robot_id": robot_id,
                    "steps": steps,
                    "error": "Recover failed",
                }

            followup_state = self.get_arm_state(robot_id)
            steps.append({"step": "post_recover_state", "result": followup_state.get("state", followup_state)})
            if not followup_state.get("ok"):
                return {
                    "ok": False,
                    "robot_id": robot_id,
                    "steps": steps,
                    "error": "State check after recover failed",
                }
            current_state = followup_state["state"]

        if current_state["error_status"] or current_state["robot_mode_name"] != "enabled":
            return {
                "ok": False,
                "robot_id": robot_id,
                "steps": steps,
                "error": f"Robot not ready for home: mode={current_state['robot_mode_name']}",
            }

        home_result = self.home_arm(robot_id)
        steps.append({"step": "home", "result": home_result})
        if home_result.get("ok"):
            self.incidents.clear(robot_id)
        return {
            "ok": home_result.get("ok", False),
            "robot_id": robot_id,
            "steps": steps,
        }

    def classify_fault(self, start_pose, current_pose):
        if not start_pose or not current_pose:
            return {
                "fault_type": "unknown_fault",
                "travel_distance_mm": None,
                "recommended_action": "await_operator_decision",
            }

        dx = float(current_pose["x"]) - float(start_pose["x"])
        dy = float(current_pose["y"]) - float(start_pose["y"])
        dz = float(current_pose["z"]) - float(start_pose["z"])
        travel_distance = math.sqrt(dx * dx + dy * dy + dz * dz)

        if travel_distance < self.IMMEDIATE_FAULT_DISTANCE_THRESHOLD_MM:
            return {
                "fault_type": "immediate_fault",
                "travel_distance_mm": travel_distance,
                "recommended_action": "auto_recover_allowed",
            }

        return {
            "fault_type": "runtime_fault",
            "travel_distance_mm": travel_distance,
            "recommended_action": "await_operator_decision",
        }

    def home_arm(self, robot_id: str):
        return self.move_to_named_target(robot_id, "home")

    def move_arm(self, robot_id: str, action: str, step: float):
        robot = self.get_robot(robot_id)
        if not robot:
            return {
                "ok": False,
                "error": f"Robot '{robot_id}' not found",
            }

        if not robot.is_connected:
            robot.connect()

        if action == "HOME":
            target_pose = {"x": 250.0, "y": 0.0, "z": 50.0, "r": 0.0}
        else:
            deltas = {
                "UP": (0.0, 0.0, step, 0.0),
                "DOWN": (0.0, 0.0, -step, 0.0),
                "LEFT": (0.0, step, 0.0, 0.0),
                "RIGHT": (0.0, -step, 0.0, 0.0),
                "FORWARD": (step, 0.0, 0.0, 0.0),
                "BACK": (-step, 0.0, 0.0, 0.0),
            }
            delta = deltas.get(action)
            if not delta:
                return {"ok": False, "robot_id": robot.robot_id, "error": f"Invalid move action '{action}'"}
            target_pose, pose_error = self._relative_target_pose(robot.robot_id, *delta)
            if pose_error:
                return pose_error

        blocked = self._preflight_pose(robot.robot_id, target_pose, f"move:{action}")
        if blocked:
            blocked["action"] = action
            blocked["step"] = step
            return blocked

        response = robot.move_direction(action, step)
        ok = (
            response.get("ok", False)
            if isinstance(response, dict)
            else not str(response).startswith("Error:")
        )

        payload = {
            "ok": ok,
            "robot_id": robot.robot_id,
            "action": action,
            "step": step,
            "result": response,
            "error": None if ok else "Motion failed",
        }
        self._save_motion_incident_if_needed(robot.robot_id, f"move:{action}", response)
        return payload

    def move_relative_arm(self, robot_id: str, dx: float, dy: float, dz: float, dr: float = 0.0):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}

        if not robot.is_connected:
            robot.connect()

        target_pose, pose_error = self._relative_target_pose(robot.robot_id, dx, dy, dz, dr)
        if pose_error:
            return pose_error
        blocked = self._preflight_pose(robot.robot_id, target_pose, "move_relative")
        if blocked:
            blocked.update({"dx": dx, "dy": dy, "dz": dz, "dr": dr, "target_pose": target_pose})
            return blocked

        response = robot.move_relative(dx, dy, dz, dr)
        ok = response.get("ok", False) if isinstance(response, dict) else not str(response).startswith("Error:")
        payload = {
            "ok": ok,
            "robot_id": robot.robot_id,
            "dx": dx,
            "dy": dy,
            "dz": dz,
            "dr": dr,
            "result": response,
            "error": None if ok else "Relative motion failed",
        }
        self._save_motion_incident_if_needed(robot.robot_id, "move_relative", response)
        return payload

    def move_to_pose_arm(self, robot_id: str, x: float, y: float, z: float, r: float = 0.0):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}

        if not robot.is_connected:
            robot.connect()

        target_pose = {"x": x, "y": y, "z": z, "r": r}
        blocked = self._preflight_pose(robot.robot_id, target_pose, "move_to_pose")
        if blocked:
            blocked["target_pose"] = target_pose
            return blocked

        response = robot.move_to_pose(x, y, z, r)
        ok = response.get("ok", False) if isinstance(response, dict) else not str(response).startswith("Error:")
        payload = {
            "ok": ok,
            "robot_id": robot.robot_id,
            "target_pose": {"x": x, "y": y, "z": z, "r": r},
            "result": response,
            "error": None if ok else "Move-to-pose failed",
        }
        self._save_motion_incident_if_needed(robot.robot_id, "move_to_pose", response)
        return payload

    def move_joint_to_pose_arm(self, robot_id: str, x: float, y: float, z: float, r: float = 0.0):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}

        if not robot.is_connected:
            robot.connect()

        target_pose = {"x": x, "y": y, "z": z, "r": r}
        blocked = self._preflight_pose(robot.robot_id, target_pose, "move_joint_to_pose")
        if blocked:
            blocked["target_pose"] = target_pose
            blocked["motion_type"] = "MovJ"
            return blocked

        response = robot.move_joint_to_pose(x, y, z, r)
        ok = response.get("ok", False) if isinstance(response, dict) else not str(response).startswith("Error:")
        payload = {
            "ok": ok,
            "robot_id": robot.robot_id,
            "target_pose": {"x": x, "y": y, "z": z, "r": r},
            "motion_type": "MovJ",
            "result": response,
            "error": None if ok else "Joint move-to-pose failed",
        }
        self._save_motion_incident_if_needed(robot.robot_id, "move_joint_to_pose", response)
        return payload

    def move_relative_joint_arm(self, robot_id: str, dx: float, dy: float, dz: float, dr: float = 0.0):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}

        if not robot.is_connected:
            robot.connect()

        target_pose, pose_error = self._relative_target_pose(robot.robot_id, dx, dy, dz, dr)
        if pose_error:
            return pose_error
        blocked = self._preflight_pose(robot.robot_id, target_pose, "move_relative_joint")
        if blocked:
            blocked.update({"dx": dx, "dy": dy, "dz": dz, "dr": dr, "target_pose": target_pose, "motion_type": "RelMovJ"})
            return blocked

        response = robot.move_relative_joint(dx, dy, dz, dr)
        ok = response.get("ok", False) if isinstance(response, dict) else not str(response).startswith("Error:")
        payload = {
            "ok": ok,
            "robot_id": robot.robot_id,
            "dx": dx,
            "dy": dy,
            "dz": dz,
            "dr": dr,
            "motion_type": "RelMovJ",
            "result": response,
            "error": None if ok else "Relative joint motion failed",
        }
        self._save_motion_incident_if_needed(robot.robot_id, "move_relative_joint", response)
        return payload

    def set_motion_profile(self, robot_id: str, speed_l=None, acc_l=None, speed_j=None, acc_j=None):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}

        if not robot.is_connected:
            robot.connect()

        results = []
        ok = True
        if speed_l is not None:
            res = robot.set_speed_l(float(speed_l))
            results.append({"setting": "SpeedL", "value": speed_l, "result": res})
            ok = ok and str(res).startswith("0,{},")
        if acc_l is not None:
            res = robot.set_acc_l(float(acc_l))
            results.append({"setting": "AccL", "value": acc_l, "result": res})
            ok = ok and str(res).startswith("0,{},")
        if speed_j is not None:
            res = robot.set_speed_j(float(speed_j))
            results.append({"setting": "SpeedJ", "value": speed_j, "result": res})
            ok = ok and str(res).startswith("0,{},")
        if acc_j is not None:
            res = robot.set_acc_j(float(acc_j))
            results.append({"setting": "AccJ", "value": acc_j, "result": res})
            ok = ok and str(res).startswith("0,{},")

        return {
            "ok": ok,
            "robot_id": robot.robot_id,
            "results": results,
            "error": None if ok else "One or more motion profile settings failed",
        }

    def continue_motion_queue(self, robot_id: str):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}
        if not robot.is_connected:
            robot.connect()

        response = robot.continue_queue()
        return {
            "ok": str(response).startswith("0,{},"),
            "robot_id": robot.robot_id,
            "result": response,
            "error": None if str(response).startswith("0,{},") else "Continue queue failed",
        }

    def arc_arm(self, robot_id: str, through_pose: dict, target_pose: dict):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}
        if not robot.is_connected:
            robot.connect()

        for label, pose in (("through_pose", through_pose), ("target_pose", target_pose)):
            blocked = self._preflight_pose(robot.robot_id, pose, f"arc:{label}")
            if blocked:
                blocked["motion_type"] = "Arc"
                blocked["through_pose"] = through_pose
                blocked["target_pose"] = target_pose
                return blocked

        response = robot.arc_to_points(through_pose, target_pose)
        ok = response.get("ok", False) if isinstance(response, dict) else not str(response).startswith("Error:")
        payload = {
            "ok": ok,
            "robot_id": robot.robot_id,
            "motion_type": "Arc",
            "through_pose": through_pose,
            "target_pose": target_pose,
            "result": response,
            "error": None if ok else "Arc motion failed",
        }
        self._save_motion_incident_if_needed(robot.robot_id, "arc", response)
        return payload

    def circle_arm(self, robot_id: str, through_pose: dict, target_pose: dict, count: int = 1):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}
        if not robot.is_connected:
            robot.connect()

        for label, pose in (("through_pose", through_pose), ("target_pose", target_pose)):
            blocked = self._preflight_pose(robot.robot_id, pose, f"circle:{label}")
            if blocked:
                blocked["motion_type"] = "Circle"
                blocked["through_pose"] = through_pose
                blocked["target_pose"] = target_pose
                blocked["count"] = count
                return blocked

        response = robot.circle_to_points(through_pose, target_pose, count)
        ok = response.get("ok", False) if isinstance(response, dict) else not str(response).startswith("Error:")
        payload = {
            "ok": ok,
            "robot_id": robot.robot_id,
            "motion_type": "Circle",
            "through_pose": through_pose,
            "target_pose": target_pose,
            "count": count,
            "result": response,
            "error": None if ok else "Circle motion failed",
        }
        self._save_motion_incident_if_needed(robot.robot_id, "circle", response)
        return payload

    def circle_arm_via_arcs(self, robot_id: str, through_pose_1: dict, target_pose_1: dict, through_pose_2: dict, target_pose_2: dict, count: int = 1):
        results = []
        for lap in range(count):
            first = self.arc_arm(robot_id, through_pose_1, target_pose_1)
            results.append({"lap": lap + 1, "segment": 1, "result": first})
            if not first.get("ok"):
                return {
                    "ok": False,
                    "robot_id": robot_id,
                    "motion_type": "CircleViaArcs",
                    "count": count,
                    "results": results,
                    "error": "First arc segment failed",
                }

            second = self.arc_arm(robot_id, through_pose_2, target_pose_2)
            results.append({"lap": lap + 1, "segment": 2, "result": second})
            if not second.get("ok"):
                return {
                    "ok": False,
                    "robot_id": robot_id,
                    "motion_type": "CircleViaArcs",
                    "count": count,
                    "results": results,
                    "error": "Second arc segment failed",
                }

        return {
            "ok": True,
            "robot_id": robot_id,
            "motion_type": "CircleViaArcs",
            "count": count,
            "results": results,
        }

    def send_native_motion_arm(self, robot_id: str, command: str):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}

        if not robot.is_connected:
            robot.connect()

        poses = self._native_motion_poses(command)
        if poses is None:
            return {
                "ok": False,
                "robot_id": robot.robot_id,
                "motion_type": "native",
                "command": command,
                "guardrail_blocked": True,
                "fault_type": "guardrail_blocked",
                "recommended_action": "use_structured_motion_tool",
                "error": "Native motion command is not guardrail-parseable. Use a structured motion tool instead.",
            }
        for index, pose in enumerate(poses):
            blocked = self._preflight_pose(robot.robot_id, pose, f"native_motion:{index}")
            if blocked:
                blocked["motion_type"] = "native"
                blocked["command"] = command
                return blocked

        response = robot.send_native_motion_command(command)
        ok = response.get("ok", False) if isinstance(response, dict) else not str(response).startswith("Error:")
        payload = {
            "ok": ok,
            "robot_id": robot.robot_id,
            "motion_type": "native",
            "command": command,
            "result": response,
            "error": None if ok else "Native motion command failed",
        }
        self._save_motion_incident_if_needed(robot.robot_id, f"native:{command}", response)
        return payload

    def stop_arm(self, robot_id: str):
        robot = self.get_robot(robot_id)
        if not robot:
            return {
                "ok": False,
                "error": f"Robot '{robot_id}' not found",
            }

        if not robot.is_connected:
            robot.connect()

        response = robot.stop()
        ok = not str(response).startswith("Error:")

        return {
            "ok": ok,
            "robot_id": robot.robot_id,
            "result": response,
        }

    def get_latest_incident(self, robot_id: str):
        incident = self.incidents.load(robot_id)
        if not incident:
            return {
                "ok": False,
                "robot_id": robot_id,
                "error": "No stored incident for this robot",
            }
        return {
            "ok": True,
            "robot_id": robot_id,
            "incident": incident,
        }

    def acknowledge_incident(self, robot_id: str, action: str):
        incident = self.incidents.load(robot_id)
        if not incident:
            return {
                "ok": False,
                "robot_id": robot_id,
                "error": "No stored incident to acknowledge",
            }

        if action == "abort":
            self.incidents.clear(robot_id)
            return {
                "ok": True,
                "robot_id": robot_id,
                "action": action,
                "incident": incident,
                "result": "Incident acknowledged and task aborted",
            }

        if action == "recover":
            result = self.recover_arm(robot_id)
            if result.get("ok"):
                self.incidents.clear(robot_id)
            return {
                "ok": result.get("ok", False),
                "robot_id": robot_id,
                "action": action,
                "incident": incident,
                "result": result,
            }

        if action == "recover_home":
            result = self.recover_and_home_arm(robot_id)
            if result.get("ok"):
                self.incidents.clear(robot_id)
            return {
                "ok": result.get("ok", False),
                "robot_id": robot_id,
                "action": action,
                "incident": incident,
                "result": result,
            }

        if action == "retry_last":
            operation = incident.get("operation", "")
            if operation.startswith("move:"):
                last_action = operation.split(":", 1)[1]
                last_result = incident.get("final_state") or {}
                return {
                    "ok": False,
                    "robot_id": robot_id,
                    "action": action,
                    "incident": incident,
                    "error": f"Retry for {last_action} is not yet supported without task runner context",
                    "last_state": last_result,
                }
            return {
                "ok": False,
                "robot_id": robot_id,
                "action": action,
                "incident": incident,
                "error": "Retry is not yet supported for this incident type",
            }

        return {
            "ok": False,
            "robot_id": robot_id,
            "incident": incident,
            "error": f"Unknown incident action '{action}'",
        }

    def trace_square(self, robot_id: str, step: float):
        robot = self.get_robot(robot_id)
        if not robot:
            return {
                "ok": False,
                "error": f"Robot '{robot_id}' not found",
            }

        if not robot.is_connected:
            robot.connect()

        sequence = ["FORWARD", "LEFT", "BACK", "RIGHT"]
        results = []
        ok = True
        error = None

        for action in sequence:
            response = self.move_arm(robot_id, action, step)
            results.append({
                "action": action,
                "result": response,
            })
            if not response.get("ok", False):
                ok = False
                error = f"Square motion failed on {action}"
                break

        return {
            "ok": ok,
            "robot_id": robot.robot_id,
            "pattern": "square",
            "step": step,
            "results": results,
            "error": None if ok else error,
        }

    def run_path(self, robot_id: str, path_type: str, **kwargs):
        robot = self.get_robot(robot_id)
        if not robot:
            return {"ok": False, "error": f"Robot '{robot_id}' not found"}

        if path_type == "rectangle":
            width = float(kwargs.get("width", 0))
            height = float(kwargs.get("height", 0))
            if width <= 0 or height <= 0:
                return {"ok": False, "robot_id": robot_id, "error": "Rectangle path requires positive width and height"}
            sequence = [
                ("FORWARD", width),
                ("LEFT", height),
                ("BACK", width),
                ("RIGHT", height),
            ]
            results = []
            for action, step in sequence:
                step_result = self.move_arm(robot_id, action, step)
                results.append(step_result)
                if not step_result.get("ok"):
                    return {
                        "ok": False,
                        "robot_id": robot_id,
                        "path_type": path_type,
                        "results": results,
                        "error": f"Rectangle path failed on {action}",
                    }
            return {
                "ok": True,
                "robot_id": robot_id,
                "path_type": path_type,
                "results": results,
            }

        if path_type == "waypoints":
            waypoints = kwargs.get("waypoints") or []
            if not isinstance(waypoints, list) or not waypoints:
                return {"ok": False, "robot_id": robot_id, "error": "Waypoints path requires a non-empty waypoint list"}

            results = []
            for idx, waypoint in enumerate(waypoints):
                step_result = self.move_to_pose_arm(
                    robot_id,
                    float(waypoint["x"]),
                    float(waypoint["y"]),
                    float(waypoint["z"]),
                    float(waypoint.get("r", 0.0)),
                )
                results.append({"index": idx, "waypoint": waypoint, "result": step_result})
                if not step_result.get("ok"):
                    return {
                        "ok": False,
                        "robot_id": robot_id,
                        "path_type": path_type,
                        "results": results,
                        "error": f"Waypoint path failed at index {idx}",
                    }
            return {
                "ok": True,
                "robot_id": robot_id,
                "path_type": path_type,
                "results": results,
            }

        if path_type == "circle":
            radius = float(kwargs.get("radius", 0))
            segments = int(kwargs.get("segments", 12))
            count = int(kwargs.get("count", 1))
            if radius <= 0:
                return {"ok": False, "robot_id": robot_id, "error": "Circle path requires positive radius"}
            if segments < 6:
                return {"ok": False, "robot_id": robot_id, "error": "Circle path requires at least 6 segments"}

            state = self.get_arm_state(robot_id)
            if not state.get("ok"):
                return state

            pose = state["state"]["pose"]
            start_x = float(pose["x"])
            start_y = float(pose["y"])
            z_value = kwargs.get("z")
            r_value = kwargs.get("r")
            z = float(pose["z"] if z_value is None else z_value)
            r = float(pose["r"] if r_value is None else r_value)
            clockwise = bool(kwargs.get("clockwise", False))
            prefer_native = kwargs.get("prefer_native", True)

            center_x = start_x - radius
            center_y = start_y
            if clockwise:
                through_pose = {"x": center_x, "y": center_y - radius, "z": z, "r": r}
                target_pose = {"x": center_x - radius, "y": center_y, "z": z, "r": r}
            else:
                through_pose = {"x": center_x, "y": center_y + radius, "z": z, "r": r}
                target_pose = {"x": center_x - radius, "y": center_y, "z": z, "r": r}

            if clockwise:
                through_pose_1 = {"x": center_x, "y": center_y - radius, "z": z, "r": r}
                target_pose_1 = {"x": center_x - radius, "y": center_y, "z": z, "r": r}
                through_pose_2 = {"x": center_x, "y": center_y + radius, "z": z, "r": r}
                target_pose_2 = {"x": start_x, "y": start_y, "z": z, "r": r}
            else:
                through_pose_1 = {"x": center_x, "y": center_y + radius, "z": z, "r": r}
                target_pose_1 = {"x": center_x - radius, "y": center_y, "z": z, "r": r}
                through_pose_2 = {"x": center_x, "y": center_y - radius, "z": z, "r": r}
                target_pose_2 = {"x": start_x, "y": start_y, "z": z, "r": r}

            if prefer_native:
                native_result = self.circle_arm_via_arcs(
                    robot_id,
                    through_pose_1,
                    target_pose_1,
                    through_pose_2,
                    target_pose_2,
                    count=count,
                )
                if native_result.get("ok"):
                    native_result["path_type"] = path_type
                    native_result["native"] = True
                    native_result["native_mode"] = "arc_pair"
                    native_result["path_geometry"] = {
                        "start_pose": {"x": start_x, "y": start_y, "z": z, "r": r},
                        "center": {"x": center_x, "y": center_y},
                        "radius": radius,
                        "clockwise": clockwise,
                    }
                    return native_result
                native_result["path_type"] = path_type
                native_result["native"] = True
                native_result["native_mode"] = "arc_pair"
                native_result["fallback_blocked"] = True
                return native_result

            angles = [2 * math.pi * i / segments for i in range(segments + 1)]
            if clockwise:
                angles = [-a for a in angles]

            waypoints = []
            for angle in angles:
                waypoints.append(
                    {
                        "x": center_x + radius * math.cos(angle),
                        "y": center_y + radius * math.sin(angle),
                        "z": z,
                        "r": r,
                    }
                )
            fallback_result = self.run_path(robot_id, "waypoints", waypoints=waypoints)
            fallback_result["native"] = False
            fallback_result["fallback_reason"] = "native_circle_failed_or_unavailable"
            return fallback_result

        if path_type == "arc":
            through_pose = kwargs.get("through_pose")
            target_pose = kwargs.get("target_pose")
            if not through_pose or not target_pose:
                return {"ok": False, "robot_id": robot_id, "error": "Arc path requires through_pose and target_pose"}
            result = self.arc_arm(robot_id, through_pose, target_pose)
            result["path_type"] = path_type
            return result

        return {
            "ok": False,
            "robot_id": robot_id,
            "error": f"Unsupported path_type '{path_type}'",
        }

    def run_motion_step(self, robot_id: str, action: str, step: float):
        return self.move_arm(robot_id, action, step)





