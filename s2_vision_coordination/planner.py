"""Deterministic, motion-free S2 assignment planner."""

from __future__ import annotations

from typing import Sequence

from .contract import Assignment, CoordinationPlan, VisionSnapshot


def build_coordination_plan(
    snapshot: VisionSnapshot,
    active_arm_ids: Sequence[str],
    requested_mode: str = "sequential",
) -> CoordinationPlan:
    """Assign detected objects to reachable arms without moving hardware.

    Preferred arms are respected when reachable. Remaining choices are
    balanced by current assignment count and then arm ID. Parallel execution
    is never declared safe here; ARC must run its live guardrails and task
    preflight against the final plan.
    """

    active = tuple(
        dict.fromkeys(
            arm_id
            for arm_id in (str(value).strip() for value in (active_arm_ids or ()))
            if arm_id
        )
    )
    requested = str(requested_mode or "sequential").strip().lower()
    if requested not in {"sequential", "parallel"}:
        raise ValueError("requested_mode must be sequential or parallel")

    counts = {arm_id: 0 for arm_id in active}
    assignments: list[Assignment] = []
    unassigned: list[dict] = []

    for obj in snapshot.objects:
        candidates = [arm_id for arm_id in obj.reachable_by if arm_id in counts]
        candidates = [arm_id for arm_id in candidates if arm_id in obj.per_arm_robot_plane]
        if not candidates:
            unassigned.append(
                {
                    "object_id": obj.object_id,
                    "reason": "no_active_reachable_arm",
                    "reachable_by": list(obj.reachable_by),
                    "available_transforms": sorted(obj.per_arm_robot_plane),
                }
            )
            continue

        preferred = obj.preferred_arm if obj.preferred_arm in candidates else None
        if preferred:
            arm_id = preferred
        else:
            arm_id = min(candidates, key=lambda candidate: (counts[candidate], candidate))

        counts[arm_id] += 1
        assignments.append(
            Assignment(
                object_id=obj.object_id,
                arm_id=arm_id,
                color=obj.color,
                robot_xy=dict(obj.per_arm_robot_plane[arm_id]),
                workspace_xy=dict(obj.workspace_xy) if obj.workspace_xy else None,
                zone=obj.zone,
                block=obj.block,
            )
        )

    return CoordinationPlan(
        camera_id=snapshot.camera_id,
        assignments=tuple(assignments),
        unassigned=tuple(unassigned),
        requested_mode=requested,
        execution_mode="sequential",
        parallel_admissible=False,
        active_arm_ids=active,
        calibration_id=snapshot.calibration_id,
    )
