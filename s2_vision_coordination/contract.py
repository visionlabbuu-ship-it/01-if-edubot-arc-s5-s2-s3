"""Data contracts for S2.

S2 owns perception records and arm assignment proposals. It does not own
language understanding, robot connections, motion execution, or recovery.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def _point(value: Mapping[str, Any] | None, label: str) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object with x and y")
    if "x" not in value or "y" not in value:
        raise ValueError(f"{label} must contain x and y")
    try:
        point = {"x": float(value["x"]), "y": float(value["y"])}
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain numeric x and y") from exc
    if not all(math.isfinite(coordinate) for coordinate in point.values()):
        raise ValueError(f"{label} must contain finite x and y")
    return point


@dataclass(frozen=True)
class VisionObject:
    object_id: str
    color: str
    center_px: dict[str, float] | None
    workspace_xy: dict[str, float] | None
    per_arm_robot_plane: dict[str, dict[str, float]]
    reachable_by: tuple[str, ...]
    preferred_arm: str | None = None
    zone: str | None = None
    block: str | None = None

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "VisionObject":
        if not isinstance(record, Mapping):
            raise ValueError("vision object must be an object")
        object_id = str(record.get("id") or "").strip()
        if not object_id:
            raise ValueError("vision object is missing id")

        center = _point(record.get("center_px"), "center_px")

        raw_planes = record.get("per_arm_robot_plane")
        if raw_planes is None:
            raw_planes = {}
        if not isinstance(raw_planes, Mapping):
            raise ValueError(f"{object_id}.per_arm_robot_plane must be an object")
        planes: dict[str, dict[str, float]] = {}
        for arm_id, pose in raw_planes.items():
            point = _point(pose, f"per_arm_robot_plane[{arm_id}]")
            if point is not None:
                planes[str(arm_id)] = point

        raw_reachable = record.get("reachable_by") or []
        if isinstance(raw_reachable, str):
            raw_reachable = [raw_reachable]
        if isinstance(raw_reachable, (bytes, bytearray)) or not isinstance(raw_reachable, Sequence):
            raise ValueError(f"{object_id}.reachable_by must be a list")
        reachable = tuple(
            arm_id
            for arm_id in (str(value).strip() for value in raw_reachable)
            if arm_id
        )
        zone_value = record.get("zone")
        if isinstance(zone_value, Mapping):
            zone_value = zone_value.get("name") or zone_value.get("id")

        return cls(
            object_id=object_id,
            color=str(record.get("color") or "unknown"),
            center_px=center,
            workspace_xy=_point(record.get("workspace_xy"), "workspace_xy"),
            per_arm_robot_plane=planes,
            reachable_by=reachable,
            preferred_arm=(str(record["preferred_arm"]) if record.get("preferred_arm") else None),
            zone=(str(zone_value) if zone_value else None),
            block=(str(record["block"]) if record.get("block") else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.object_id,
            "color": self.color,
            "center_px": dict(self.center_px) if self.center_px else None,
            "workspace_xy": dict(self.workspace_xy) if self.workspace_xy else None,
            "per_arm_robot_plane": {
                arm_id: dict(point) for arm_id, point in self.per_arm_robot_plane.items()
            },
            "reachable_by": list(self.reachable_by),
            "preferred_arm": self.preferred_arm,
            "zone": self.zone,
            "block": self.block,
        }


@dataclass(frozen=True)
class VisionSnapshot:
    camera_id: str
    captured_at: str | None
    calibration_id: str | None
    objects: tuple[VisionObject, ...]

    @classmethod
    def from_scan(
        cls,
        payload: Mapping[str, Any],
        *,
        require_pixel_coordinates: bool = False,
    ) -> "VisionSnapshot":
        if not isinstance(payload, Mapping):
            raise ValueError("scan payload must be an object")
        if not payload.get("ok"):
            raise ValueError(str(payload.get("error") or "vision scan failed"))
        raw_objects = payload.get("objects") or []
        if isinstance(raw_objects, (str, bytes, bytearray)) or not isinstance(raw_objects, Sequence):
            raise ValueError("scan objects must be a list")
        parsed_objects = []
        seen_ids: set[str] = set()
        for item in raw_objects:
            parsed = VisionObject.from_record(item)
            if require_pixel_coordinates and parsed.center_px is None:
                raise ValueError(f"{parsed.object_id} is missing center_px")
            if parsed.object_id in seen_ids:
                raise ValueError(f"duplicate vision object id: {parsed.object_id}")
            seen_ids.add(parsed.object_id)
            parsed_objects.append(parsed)
        objects = tuple(parsed_objects)
        workspace = payload.get("workspace") or {}
        if not isinstance(workspace, Mapping):
            raise ValueError("scan workspace must be an object")
        calibration_id = workspace.get("calibration_id") or payload.get("calibration_id")
        calibration_id = (
            calibration_id
            or workspace.get("calibration_path")
            or payload.get("calibration_path")
        )
        return cls(
            camera_id=str(payload.get("camera_id") or "Cam1"),
            captured_at=(
                str(payload.get("timestamp") or payload.get("captured_at"))
                if payload.get("timestamp") or payload.get("captured_at")
                else None
            ),
            calibration_id=(str(calibration_id) if calibration_id else None),
            objects=objects,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "captured_at": self.captured_at,
            "calibration_id": self.calibration_id,
            "object_count": len(self.objects),
            "pixel_coordinate_count": sum(
                item.center_px is not None for item in self.objects
            ),
            "objects": [item.to_dict() for item in self.objects],
        }


@dataclass(frozen=True)
class Assignment:
    object_id: str
    arm_id: str
    color: str
    robot_xy: dict[str, float]
    workspace_xy: dict[str, float] | None
    zone: str | None
    block: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "arm_id": self.arm_id,
            "color": self.color,
            "robot_xy": dict(self.robot_xy),
            "workspace_xy": dict(self.workspace_xy) if self.workspace_xy else None,
            "zone": self.zone,
            "block": self.block,
        }


@dataclass(frozen=True)
class CoordinationPlan:
    camera_id: str
    assignments: tuple[Assignment, ...]
    unassigned: tuple[dict[str, Any], ...]
    requested_mode: str
    execution_mode: str
    parallel_admissible: bool
    requires_arc_validation: bool = True
    robot_control: bool = False
    active_arm_ids: tuple[str, ...] = ()
    calibration_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.unassigned,
            "camera_id": self.camera_id,
            "calibration_id": self.calibration_id,
            "active_arm_ids": list(self.active_arm_ids),
            "object_count": len(self.assignments) + len(self.unassigned),
            "assigned_count": len(self.assignments),
            "unassigned_count": len(self.unassigned),
            "assignments": [item.to_dict() for item in self.assignments],
            "unassigned": [dict(item) for item in self.unassigned],
            "requested_mode": self.requested_mode,
            "execution_mode": self.execution_mode,
            "parallel_admissible": self.parallel_admissible,
            "requires_arc_validation": self.requires_arc_validation,
            "robot_control": self.robot_control,
        }
