"""Standalone S2 planning and sequential execution workflow.

The workflow owns the operator-facing plan state. ARC remains the execution
backend: every step is re-scanned, assigned by object identity, and passed
through the existing FleetController guardrails before motion.
"""

from __future__ import annotations

import math
import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .adapter import S2VisionCoordinator
from .verification import verify_assignments


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _attach_display_coordinates(response: dict[str, Any]) -> None:
    """Normalize ARC-provided pixel coordinates for the web overlay."""

    scan = response.get("scan")
    if not isinstance(scan, dict):
        return
    scan = deepcopy(scan)
    response["scan"] = scan
    frame = scan.get("display_frame") or scan.get("frame") or {}
    frame_width = int(frame.get("width") or 0)
    frame_height = int(frame.get("height") or 0)
    if frame_width <= 0 or frame_height <= 0:
        return
    scan["display_frame"] = {"width": frame_width, "height": frame_height}

    for object_record in scan.get("objects") or []:
        if not isinstance(object_record, dict):
            continue
        # `center_px` is the execution/evidence coordinate.  The web overlay
        # must use ARC's coordinate in the actual displayed camera frame when
        # one is available (the simulator exposes it as display_center_px).
        display_center = object_record.get("display_center_px")
        center = display_center if isinstance(display_center, Mapping) else object_record.get("center_px")
        if not isinstance(center, Mapping):
            continue
        try:
            x = float(center["x"]) / float(frame_width)
            y = float(center["y"]) / float(frame_height)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            source = str(
                object_record.get("display_coordinate_source") or "arc_display_center_px"
                if isinstance(display_center, Mapping)
                else "arc_center_px"
            )
            object_record["display_position"] = {"x": x, "y": y, "source": source}


class S2StandaloneController:
    """Coordinate S2 scans, editable plans, confirmation, and execution.

    A FleetController may be injected by tests or an embedding application.
    When omitted, the ARC runtime is created lazily for the local standalone
    server. Hardware execution is explicitly gated by ``S2_ALLOW_HARDWARE_EXECUTE``.
    """

    def __init__(self, camera=None, fleet=None, active_arm_ids: Sequence[str] | None = None):
        if fleet is None:
            from arc_runtime.config import ROBOT_FLEET_CONFIG
            from arc_runtime.fleet_controller import FleetController

            fleet = FleetController(ROBOT_FLEET_CONFIG)
        self.fleet = fleet
        self.camera = camera or getattr(fleet, "camera", None)
        if self.camera is None:
            raise ValueError("S2 standalone controller requires a camera")

        configured = active_arm_ids or getattr(fleet, "active_arm_ids", ())
        self.active_arm_ids = tuple(
            dict.fromkeys(
                arm_id
                for arm_id in (str(value).strip() for value in configured)
                if arm_id
            )
        )
        self.coordinator = S2VisionCoordinator(
            self.camera,
            self.active_arm_ids,
            require_saved_workspace=True,
            require_pixel_coordinates=False,
        )
        self.last_scan: dict[str, Any] | None = None
        self.last_plan: dict[str, Any] | None = None

    @property
    def simulation_mode(self) -> bool:
        from arc_runtime import config

        return bool(config.SIM_MODE)

    def scan(self, camera_id: str = "Cam1", mode: str = "snapshot") -> dict[str, Any]:
        """Capture a current snapshot and return the normalized S2 result."""

        from arc_runtime.state_paths import runtime_state_path

        image_path = runtime_state_path("s2_web", "latest_scan.jpg")
        try:
            payload = self.camera.scan_workspace(
                camera_id=camera_id,
                color="any",
                selection="first",
                output_image_path=str(image_path),
                require_saved_workspace=True,
            )
        except TypeError as exc:
            if "output_image_path" not in str(exc):
                raise
            payload = self.camera.scan_workspace(
                camera_id=camera_id,
                color="any",
                selection="first",
                require_saved_workspace=True,
            )
        result = self.coordinator.plan_from_payload(payload)
        if result.ok:
            self.last_scan = dict(payload)
        response = result.to_dict()
        response["capture_mode"] = str(mode or "snapshot").lower()
        response["active_arm_ids"] = list(self.active_arm_ids)
        response["execution_backend"] = "ARC FleetController"
        response["simulation_mode"] = self.simulation_mode
        _attach_display_coordinates(response)
        return response

    def options(self, arm_id: str | None = None) -> dict[str, Any]:
        """Return destinations that are safe to expose in the plan editor."""

        from arc_runtime.config import ARM_NAMED_TARGETS
        from arc_runtime.sim_world import WORKSPACE_ZONES

        arm_ids = (arm_id,) if arm_id else self.active_arm_ids
        named: list[dict[str, Any]] = []
        seen: set[str] = set()
        for current_arm in arm_ids:
            for name, target in (ARM_NAMED_TARGETS.get(current_arm) or {}).items():
                if name == "home" or name in seen:
                    continue
                named.append(
                    {
                        "kind": "named",
                        "name": name,
                        "label": name.replace("_", " ").title(),
                        "arm_ids": [
                            candidate
                            for candidate in self.active_arm_ids
                            if name in (ARM_NAMED_TARGETS.get(candidate) or {})
                        ],
                        "pose": {
                            key: value
                            for key, value in target.items()
                            if key in {"x", "y", "hover_z", "place_z", "r"}
                        },
                    }
                )
                seen.add(name)

        zones = []
        for zone in WORKSPACE_ZONES:
            bounds = zone.get("bounds_mm") or {}
            try:
                center = {
                    "x": (float(bounds["x_min"]) + float(bounds["x_max"])) / 2.0,
                    "y": (float(bounds["y_min"]) + float(bounds["y_max"])) / 2.0,
                }
            except (KeyError, TypeError, ValueError):
                continue
            zones.append(
                {
                    "kind": "point",
                    "name": str(zone.get("name") or "zone"),
                    "label": f"{str(zone.get('name') or 'zone').title()} center",
                    "frame": "workspace",
                    "point": center,
                    "role": zone.get("role"),
                }
            )
        return {"ok": True, "arms": list(self.active_arm_ids), "named": named, "zones": zones}

    def _destination(self, raw: Any, arm_id: str) -> dict[str, Any]:
        if isinstance(raw, str):
            raw = {"kind": "named", "name": raw}
        if not isinstance(raw, Mapping):
            raise ValueError("destination must be an object or named target")

        kind = str(raw.get("kind") or raw.get("type") or "named").strip().lower()
        if kind == "named":
            name = str(raw.get("name") or raw.get("id") or "").strip()
            if not name:
                raise ValueError("destination name is required")
            from arc_runtime.config import ARM_NAMED_TARGETS

            if name not in (ARM_NAMED_TARGETS.get(arm_id) or {}):
                raise ValueError(f"destination '{name}' is not available for {arm_id}")
            if name == "home":
                raise ValueError("home is not a placement destination")
            return {"kind": "named", "name": name, "frame": "arm"}

        if kind not in {"point", "custom", "workspace"}:
            raise ValueError("destination kind must be named or point")
        point = raw.get("point") if isinstance(raw.get("point"), Mapping) else raw
        return {
            "kind": "point",
            "name": str(raw.get("name") or "custom point"),
            "frame": str(raw.get("frame") or "workspace").lower(),
            "x": _finite(point.get("x"), "destination.x"),
            "y": _finite(point.get("y"), "destination.y"),
        }

    def create_plan(self, steps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Validate user-selected steps against the latest S2 scan."""

        if self.last_scan is None:
            return {"ok": False, "error_code": "scan_required", "error": "Scan before creating a plan"}
        if isinstance(steps, (str, bytes, bytearray)) or not isinstance(steps, Sequence):
            return {"ok": False, "error_code": "invalid_steps", "error": "steps must be a list"}

        objects = {
            str(item.get("id")): item
            for item in (self.last_scan.get("objects") or [])
            if isinstance(item, Mapping) and item.get("id")
        }
        built: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        used_objects: set[str] = set()

        for index, raw in enumerate(steps, start=1):
            try:
                if not isinstance(raw, Mapping):
                    raise ValueError("step must be an object")
                object_id = str(raw.get("object_id") or "").strip()
                arm_id = str(raw.get("arm_id") or "").strip()
                if not object_id:
                    raise ValueError("object_id is required")
                if object_id in used_objects:
                    raise ValueError(f"object '{object_id}' is already in the plan")
                if object_id not in objects:
                    raise ValueError(f"object '{object_id}' is not in the latest scan")
                if arm_id not in self.active_arm_ids:
                    raise ValueError(f"arm '{arm_id}' is not active")
                obj = objects[object_id]
                reachable = {str(value) for value in (obj.get("reachable_by") or [])}
                planes = obj.get("per_arm_robot_plane") or {}
                if arm_id not in reachable or not isinstance(planes.get(arm_id), Mapping):
                    raise ValueError(f"{arm_id} cannot reach object '{object_id}'")
                source = {
                    "x": _finite(planes[arm_id].get("x"), "source.x"),
                    "y": _finite(planes[arm_id].get("y"), "source.y"),
                }
                destination = self._destination(raw.get("destination"), arm_id)
                used_objects.add(object_id)
                built.append(
                    {
                        "step_id": f"step-{index}",
                        "order": index,
                        "status": "pending",
                        "object_id": object_id,
                        "color": str(obj.get("color") or "unknown"),
                        "block": obj.get("block"),
                        "arm_id": arm_id,
                        "source": source,
                        "destination": destination,
                        "preferred_arm": obj.get("preferred_arm"),
                    }
                )
            except (TypeError, ValueError) as exc:
                errors.append({"order": index, "error": str(exc)})

        if errors or not built:
            return {
                "ok": False,
                "error_code": "invalid_plan" if errors else "empty_plan",
                "error": "Plan contains invalid steps" if errors else "Add at least one step",
                "errors": errors,
            }

        self.last_plan = {
            "ok": True,
            "scope": "S2",
            "status": "draft",
            "confirmed": False,
            "created_at": _now(),
            "scan_timestamp": self.last_scan.get("timestamp"),
            "camera_id": self.last_scan.get("camera_id", "Cam1"),
            "execution_mode": "sequential",
            "parallel_admissible": False,
            "requires_arc_validation": True,
            "robot_control": False,
            "steps": built,
        }
        return deepcopy(self.last_plan)

    def confirm_plan(self) -> dict[str, Any]:
        if self.last_plan is None or not self.last_plan.get("ok"):
            return {"ok": False, "error_code": "plan_required", "error": "Create a valid plan first"}
        if self.last_plan.get("status") not in {"draft", "confirmed"}:
            return {"ok": False, "error_code": "plan_not_editable", "error": "Plan is already executing or finished"}
        self.last_plan["confirmed"] = True
        self.last_plan["status"] = "confirmed"
        self.last_plan["confirmed_at"] = _now()
        return deepcopy(self.last_plan)

    def _execution_allowed(self) -> tuple[bool, str | None]:
        if self.simulation_mode:
            return True, None
        if os.getenv("S2_ALLOW_HARDWARE_EXECUTE", "").strip().lower() in {"1", "true", "yes", "on"}:
            return True, None
        return False, "Hardware execution is disabled; set S2_ALLOW_HARDWARE_EXECUTE=1 after review"

    def _execute_step(self, step: dict[str, Any]) -> dict[str, Any]:
        arm_id = step["arm_id"]
        object_id = step["object_id"]
        before = self.camera.scan_workspace(
            camera_id=str(self.last_plan.get("camera_id") or "Cam1"),
            color="any",
            selection="first",
            require_saved_workspace=True,
        )
        if not before.get("ok"):
            return {"ok": False, "stage": "pre_step_scan", "scan": before}

        assignment = self.camera.assign_targets(
            [
                {
                    "arm_id": arm_id,
                    "color": step.get("color", "any"),
                    "block": step.get("block"),
                    "object_id": object_id,
                }
            ],
            camera_id=str(self.last_plan.get("camera_id") or "Cam1"),
            require_saved_workspace=True,
        )
        if not assignment.get("ok") or not assignment.get("targets"):
            return {"ok": False, "stage": "arc_assignment", "assignment": assignment}
        target = assignment["targets"][0]
        source = target["robot_plane"]
        pick = self.fleet.pick_at(arm_id, float(source["x"]), float(source["y"]))
        if not pick.get("ok"):
            return {"ok": False, "stage": "pick", "assignment": assignment, "pick": pick}

        destination = step["destination"]
        if destination["kind"] == "named":
            place = self.fleet.place_to_named_target(arm_id, destination["name"])
        else:
            place = self.fleet.place_at(arm_id, float(destination["x"]), float(destination["y"]))
        if not place.get("ok"):
            return {
                "ok": False,
                "stage": "place",
                "assignment": assignment,
                "pick": pick,
                "place": place,
            }

        after = self.camera.scan_workspace(
            camera_id=str(self.last_plan.get("camera_id") or "Cam1"),
            color="any",
            selection="first",
            require_saved_workspace=True,
        )
        verification = verify_assignments(before, after, [target]) if after.get("ok") else None
        verification_payload = verification.to_dict() if verification else None
        verification_ok = bool(verification and verification.ok)

        # A placement inside the camera workspace keeps the same object_id but
        # changes its workspace position. Treat that as successful movement;
        # the lower-level S2 verifier intentionally remains removal-only.
        if after.get("ok") and verification and not verification.ok:
            before_object = next(
                (item for item in (before.get("objects") or []) if item.get("id") == object_id),
                None,
            )
            after_object = next(
                (item for item in (after.get("objects") or []) if item.get("id") == object_id),
                None,
            )
            before_point = (before_object or {}).get("robot_plane") or {}
            after_point = (after_object or {}).get("robot_plane") or {}
            try:
                moved_distance = math.hypot(
                    float(after_point["x"]) - float(before_point["x"]),
                    float(after_point["y"]) - float(before_point["y"]),
                )
            except (KeyError, TypeError, ValueError):
                moved_distance = 0.0
            if moved_distance >= 25.0:
                verification_ok = True
                verification_payload = {
                    **verification.to_dict(),
                    "ok": True,
                    "verified_count": 1,
                    "failed_count": 0,
                    "results": [
                        {
                            "object_id": object_id,
                            "arm_id": arm_id,
                            "status": "verified_moved",
                            "verified": True,
                            "moved_distance_mm": round(moved_distance, 3),
                        }
                    ],
                }
        ok = bool(after.get("ok")) and verification_ok
        return {
            "ok": ok,
            "stage": "completed" if ok else "verification",
            "assignment": assignment,
            "pick": pick,
            "place": place,
            "verification": verification_payload,
            "after_scan": after,
            "error": None if ok else "Source object was not verified as removed",
        }

    def execute_plan(self) -> dict[str, Any]:
        if self.last_plan is None or not self.last_plan.get("ok"):
            return {"ok": False, "error_code": "plan_required", "error": "Create a valid plan first"}
        if not self.last_plan.get("confirmed"):
            return {"ok": False, "error_code": "confirmation_required", "error": "Confirm the plan before execution"}
        allowed, reason = self._execution_allowed()
        if not allowed:
            return {"ok": False, "error_code": "hardware_execution_locked", "error": reason}

        self.last_plan["status"] = "executing"
        self.last_plan["execution_started_at"] = _now()
        results: list[dict[str, Any]] = []
        for step in self.last_plan["steps"]:
            step["status"] = "executing"
            result = self._execute_step(step)
            results.append({"step_id": step["step_id"], **result})
            step["result"] = result
            if result.get("ok"):
                step["status"] = "completed"
            else:
                step["status"] = "failed"
                self.last_plan["status"] = "failed"
                self.last_plan["execution_finished_at"] = _now()
                self.last_plan["results"] = results
                return {"ok": False, "plan": deepcopy(self.last_plan), "results": results}

        self.last_plan["status"] = "completed"
        self.last_plan["execution_finished_at"] = _now()
        self.last_plan["results"] = results
        return {"ok": True, "plan": deepcopy(self.last_plan), "results": results}

    def reset_plan(self) -> dict[str, Any]:
        self.last_plan = None
        return {"ok": True}

