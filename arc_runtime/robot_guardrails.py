import math


def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


class RobotSafetyGuardrail:
    def __init__(self, safety_config=None, arm_frame_transforms=None, active_arm_ids=None):
        self.config = safety_config or {}
        self.active_arm_ids = list(active_arm_ids) if active_arm_ids is not None else None
        transform_source = arm_frame_transforms or {}
        if self.active_arm_ids is None:
            self.arm_frame_transforms = transform_source
        else:
            active_ids = set(self.active_arm_ids)
            self.arm_frame_transforms = {
                arm_id: value
                for arm_id, value in transform_source.items()
                if arm_id in active_ids
            }

    def enabled(self):
        return bool(self.config.get("enabled", True))

    def max_arm_length_mm(self):
        return _as_float(self.config.get("max_arm_length_mm"), 440.0)

    def min_reach_mm(self):
        return _as_float(self.config.get("min_reach_mm"), 150.0)

    def shared_zone(self):
        shared_cfg = self.config.get("shared_zone", {})
        explicit_start = shared_cfg.get("zone_start_mm")
        explicit_end = shared_cfg.get("zone_end_mm")
        max_reach = self.max_arm_length_mm()
        distance = _as_float(
            self.config.get("distance_between_robots_mm"),
            self._fallback_distance_between_robots(max_reach),
        )
        start = _as_float(explicit_start, distance - max_reach)
        end = _as_float(explicit_end, max_reach)
        if start > end:
            start, end = end, start
        return {"start_x_mm": start, "end_x_mm": end}

    def safety_geometry(self):
        return {
            "enabled": self.enabled(),
            "max_arm_length_mm": self.max_arm_length_mm(),
            "min_reach_mm": self.min_reach_mm(),
            "shared_zone": self.shared_zone(),
            "arm_frames": {
                arm_id: self._arm_frame(arm_id)
                for arm_id in self._configured_arm_ids()
            },
            "active_arm_ids": self._configured_arm_ids(),
            "single_arm_mode": len(self._configured_arm_ids()) == 1,
            "source": "ROBOT_SAFETY_CONFIG + ARM_FRAME_TRANSFORMS",
        }

    def _configured_arm_ids(self):
        if self.active_arm_ids is not None:
            return sorted(self.active_arm_ids)

        arm_ids = set(self.arm_frame_transforms.keys())
        arm_ids.update((self.config.get("arm_global_frames") or {}).keys())
        if not arm_ids:
            arm_ids.update(["Arm1", "Arm2"])
        return sorted(arm_ids)

    def _fallback_distance_between_robots(self, default):
        arm2_transform = self.arm_frame_transforms.get("Arm2", {})
        return _as_float(arm2_transform.get("tx"), default)

    def _arm_frame(self, arm_id):
        frames = self.config.get("arm_global_frames", {})
        frame = frames.get(arm_id, {})
        if frame:
            return {
                "base_x_mm": _as_float(frame.get("base_x_mm"), 0.0),
                "forward_sign": _as_float(frame.get("forward_sign"), 1.0),
            }

        transform = self.arm_frame_transforms.get(arm_id)
        if transform:
            return {
                "base_x_mm": _as_float(transform.get("tx"), 0.0),
                "forward_sign": _as_float(transform.get("a"), 1.0),
                "transform": {
                    "a": _as_float(transform.get("a"), 1.0),
                    "b": _as_float(transform.get("b"), 0.0),
                    "tx": _as_float(transform.get("tx"), 0.0),
                    "c": _as_float(transform.get("c"), 0.0),
                    "d": _as_float(transform.get("d"), 1.0),
                    "ty": _as_float(transform.get("ty"), 0.0),
                },
            }

        if arm_id == "Arm2":
            return {
                "base_x_mm": self._fallback_distance_between_robots(
                    self.max_arm_length_mm()
                ),
                "forward_sign": -1.0,
            }
        return {"base_x_mm": 0.0, "forward_sign": 1.0}

    def local_xy_to_global_xy(self, arm_id, x, y):
        frame = self._arm_frame(arm_id)
        transform = frame.get("transform")
        if transform:
            local_x = float(x)
            local_y = float(y)
            return (
                transform["a"] * local_x + transform["b"] * local_y + transform["tx"],
                transform["c"] * local_x + transform["d"] * local_y + transform["ty"],
            )
        return frame["base_x_mm"] + frame["forward_sign"] * float(x), float(y)

    def local_x_to_global_x(self, arm_id, x):
        global_x, _ = self.local_xy_to_global_xy(arm_id, x, 0.0)
        return global_x

    def pose_global_x(self, arm_id, pose):
        global_x, _ = self.local_xy_to_global_xy(
            arm_id,
            pose["x"],
            pose.get("y", 0.0),
        )
        return global_x

    def global_x_in_shared_zone(self, global_x):
        zone = self.shared_zone()
        return zone["start_x_mm"] <= float(global_x) <= zone["end_x_mm"]

    def pose_in_shared_zone(self, arm_id, pose):
        return self.global_x_in_shared_zone(self.pose_global_x(arm_id, pose))

    def check_dynamic_reach(self, pose):
        max_length = self.max_arm_length_mm()
        min_reach = self.min_reach_mm()
        x = float(pose["x"])
        y = float(pose["y"])
        z = float(pose["z"])

        if abs(z) >= max_length:
            return {
                "ok": False,
                "guardrail": "dynamic_z_reach",
                "reason": "z_exceeds_arm_length",
                "message": "Target Z exceeds the configured maximum arm length.",
                "target_pose": dict(pose),
                "max_arm_length_mm": max_length,
                "recommended_action": "choose_shallower_z_or_replan",
            }

        target_r = math.sqrt(x * x + y * y)
        max_safe_r = math.sqrt(max_length * max_length - z * z)
        if target_r < min_reach:
            return {
                "ok": False,
                "guardrail": "dynamic_z_reach",
                "reason": "target_inside_min_reach",
                "message": "Target is too close to the robot base.",
                "target_pose": dict(pose),
                "target_r_mm": target_r,
                "min_reach_mm": min_reach,
                "max_safe_r_mm": max_safe_r,
                "recommended_action": "choose_farther_pose_or_replan",
            }

        if target_r > max_safe_r:
            return {
                "ok": False,
                "guardrail": "dynamic_z_reach",
                "reason": "target_beyond_z_adjusted_reach",
                "message": "Target is beyond the safe horizontal reach at this Z.",
                "target_pose": dict(pose),
                "target_r_mm": target_r,
                "min_reach_mm": min_reach,
                "max_safe_r_mm": max_safe_r,
                "recommended_action": "choose_closer_pose_or_raise_z",
            }

        return {
            "ok": True,
            "guardrail": "dynamic_z_reach",
            "target_pose": dict(pose),
            "target_r_mm": target_r,
            "min_reach_mm": min_reach,
            "max_safe_r_mm": max_safe_r,
        }

    def check_collision(self, arm_id, pose, other_poses):
        collision_cfg = self.config.get("collision", {})
        if not bool(collision_cfg.get("enabled", True)):
            return {"ok": True, "guardrail": "shared_zone_collision"}

        if not bool(self.config.get("shared_zone", {}).get("enabled", True)):
            return {"ok": True, "guardrail": "shared_zone_collision"}

        target_global_x = self.pose_global_x(arm_id, pose)
        zone = self.shared_zone()
        if not self.global_x_in_shared_zone(target_global_x):
            return {
                "ok": True,
                "guardrail": "shared_zone_collision",
                "shared_zone": zone,
                "target_global_x_mm": target_global_x,
                "target_in_shared_zone": False,
            }

        expected_other_arms = [
            other_arm
            for other_arm in self._configured_arm_ids()
            if other_arm != arm_id
        ]
        if not expected_other_arms:
            expected_other_arms = [other_arm for other_arm in other_poses.keys() if other_arm != arm_id]

        block_if_unavailable = bool(
            collision_cfg.get("block_if_other_pose_unavailable", True)
        )
        for other_arm in expected_other_arms:
            other_pose = other_poses.get(other_arm)
            if other_pose is None:
                if block_if_unavailable:
                    return {
                        "ok": False,
                        "guardrail": "shared_zone_collision",
                        "reason": "other_robot_pose_unavailable",
                        "message": "Target enters shared zone, but the other robot pose is unavailable.",
                        "target_pose": dict(pose),
                        "shared_zone": zone,
                        "target_global_x_mm": target_global_x,
                        "blocking_robot_id": other_arm,
                        "recommended_action": "wait_or_replan",
                    }
                continue

            other_global_x = self.pose_global_x(other_arm, other_pose)
            if self.global_x_in_shared_zone(other_global_x):
                return {
                    "ok": False,
                    "guardrail": "shared_zone_collision",
                    "reason": "shared_zone_occupied",
                    "message": "Target enters shared zone while another robot is already in that zone.",
                    "target_pose": dict(pose),
                    "shared_zone": zone,
                    "target_global_x_mm": target_global_x,
                    "blocking_robot_id": other_arm,
                    "blocking_robot_pose": dict(other_pose),
                    "blocking_robot_global_x_mm": other_global_x,
                    "recommended_action": "wait_or_replan",
                }

        return {
            "ok": True,
            "guardrail": "shared_zone_collision",
            "shared_zone": zone,
            "target_global_x_mm": target_global_x,
            "target_in_shared_zone": True,
        }

    def check_pose(self, arm_id, pose, other_poses=None, operation="motion", check_collision=True):
        if not self.enabled():
            return {
                "ok": True,
                "guardrail_passed": True,
                "operation": operation,
                "target_pose": dict(pose),
                "checks": [],
            }

        normalized_pose = {
            "x": float(pose["x"]),
            "y": float(pose["y"]),
            "z": float(pose["z"]),
            "r": float(pose.get("r", 0.0)),
        }

        checks = []
        reach = self.check_dynamic_reach(normalized_pose)
        checks.append(reach)
        if not reach.get("ok"):
            return {
                **reach,
                "ok": False,
                "operation": operation,
                "robot_id": arm_id,
            }

        if check_collision:
            collision = self.check_collision(arm_id, normalized_pose, other_poses or {})
            checks.append(collision)
            if not collision.get("ok"):
                return {
                    **collision,
                    "ok": False,
                    "operation": operation,
                    "robot_id": arm_id,
                }

        return {
            "ok": True,
            "guardrail_passed": True,
            "operation": operation,
            "robot_id": arm_id,
            "target_pose": normalized_pose,
            "checks": checks,
        }

    def block_result(self, arm_id, operation, guardrail_result):
        return {
            "ok": False,
            "robot_id": arm_id,
            "operation": operation,
            "guardrail_blocked": True,
            "fault_type": "guardrail_blocked",
            "recommended_action": guardrail_result.get("recommended_action", "wait_or_replan"),
            "guardrail": guardrail_result,
            "error": guardrail_result.get("message", "Motion blocked by ARC safety guardrail."),
        }

    def parallel_shared_zone_conflict(self, arm_targets, path="task"):
        zone = self.shared_zone()
        occupied = []
        for item in arm_targets:
            arm_id = item.get("arm_id")
            pose = item.get("pose")
            if not arm_id or not pose:
                continue
            global_x = self.pose_global_x(arm_id, pose)
            if self.global_x_in_shared_zone(global_x):
                occupied.append({**item, "global_x_mm": global_x})

        arms_in_zone = sorted({item["arm_id"] for item in occupied})
        if len(arms_in_zone) < 2:
            return {"ok": True}

        return {
            "ok": False,
            "guardrail": "shared_zone_collision",
            "reason": "parallel_shared_zone_conflict",
            "message": "Parallel task would put multiple robots in the shared zone at the same time.",
            "path": path,
            "shared_zone": zone,
            "targets": occupied,
            "recommended_action": "sequence_or_replan",
        }
