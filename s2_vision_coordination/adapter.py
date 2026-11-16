"""Camera-to-S2 adapter and report-friendly planning result.

The adapter depends on a scanner protocol instead of importing ARC runtime
classes. A live camera controller or a simulator can therefore provide the
scan, while S2 remains a read-only perception and assignment boundary.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .contract import CoordinationPlan, VisionSnapshot
from .planner import build_coordination_plan
from .verification import VerificationReport, verify_assignments


class S2ValidationError(ValueError):
    """A scan cannot be used as S2 evidence."""

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class WorkspaceScanner(Protocol):
    def scan_workspace(
        self,
        camera_id: str = "Cam1",
        color: str = "any",
        selection: str = "first",
        require_saved_workspace: bool = False,
    ) -> Mapping[str, Any]:
        ...


def validate_scan_payload(
    payload: Mapping[str, Any],
    *,
    require_saved_workspace: bool = False,
) -> None:
    """Validate scan-level readiness before converting it into S2 evidence."""

    if not isinstance(payload, Mapping):
        raise S2ValidationError("invalid_payload", "scan payload must be an object")
    if not payload.get("ok"):
        raise S2ValidationError(
            "scan_failed",
            str(payload.get("error") or "camera scan failed"),
            {"camera_id": payload.get("camera_id")},
        )
    objects = payload.get("objects")
    if isinstance(objects, (str, bytes, bytearray)) or not isinstance(objects or [], Sequence):
        raise S2ValidationError("invalid_objects", "scan objects must be a list")

    workspace = payload.get("workspace")
    if workspace is not None and not isinstance(workspace, Mapping):
        raise S2ValidationError("invalid_workspace", "scan workspace must be an object")
    if not require_saved_workspace:
        return

    if not workspace:
        raise S2ValidationError(
            "saved_workspace_required",
            "S2 requires workspace calibration metadata",
        )
    if workspace.get("stale_saved"):
        raise S2ValidationError(
            "stale_calibration",
            "saved workspace calibration is stale",
            {"saved_report_timestamp": workspace.get("saved_report_timestamp")},
        )
    if workspace.get("detection_ready") is False:
        raise S2ValidationError(
            "workspace_not_ready",
            "workspace calibration is not ready",
            {"detection_error": workspace.get("detection_error")},
        )
    if workspace.get("saved_exists") is False:
        raise S2ValidationError(
            "saved_workspace_missing",
            "saved workspace calibration does not exist",
        )
    source = str(workspace.get("source") or "").strip().lower()
    if workspace.get("saved") is False and source not in {"saved", "workspace_calibration"}:
        raise S2ValidationError(
            "saved_workspace_required",
            "scan did not use a saved workspace calibration",
            {"source": workspace.get("source")},
        )


@dataclass(frozen=True)
class S2PlanResult:
    """Stable result object for live scans and offline report evidence."""

    ok: bool
    snapshot: VisionSnapshot | None = None
    plan: CoordinationPlan | None = None
    scan_payload: Mapping[str, Any] | None = None
    error: dict[str, Any] | None = None

    def to_arc_assignment_requests(self) -> list[dict[str, Any]]:
        if not self.ok or self.plan is None:
            return []
        return plan_to_arc_assignment_requests(self.plan)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok, "scope": "S2", "robot_control": False}
        if self.scan_payload is not None:
            result["scan"] = dict(self.scan_payload)
        if self.snapshot is not None:
            result["snapshot"] = self.snapshot.to_dict()
        if self.plan is not None:
            result["plan"] = self.plan.to_dict()
            result["arc_assignment_requests"] = self.to_arc_assignment_requests()
        if self.error is not None:
            result["error"] = dict(self.error)
        return result

def plan_to_arc_assignment_requests(plan: CoordinationPlan) -> list[dict[str, Any]]:
    """Convert S2 assignments to the existing ARC assignment request shape.

    ARC still performs the live re-scan, calibration, reachability, and
    guardrail checks. The object ID and coordinates are retained as evidence
    so a report can correlate the proposal with the source scan.
    """

    return [
        {
            "arm_id": assignment.arm_id,
            "color": assignment.color,
            "block": assignment.block,
            "object_id": assignment.object_id,
            "robot_plane": dict(assignment.robot_xy),
            "workspace_xy": (
                dict(assignment.workspace_xy) if assignment.workspace_xy else None
            ),
        }
        for assignment in plan.assignments
    ]


class S2VisionCoordinator:
    """Run a scan through the S2 boundary without executing motion."""

    def __init__(
        self,
        scanner: WorkspaceScanner | Any,
        active_arm_ids: Sequence[str],
        *,
        require_saved_workspace: bool = True,
        require_pixel_coordinates: bool = True,
    ) -> None:
        self.scanner = scanner
        self.active_arm_ids = tuple(
            dict.fromkeys(
                arm_id
                for arm_id in (str(value).strip() for value in active_arm_ids)
                if arm_id
            )
        )
        self.require_saved_workspace = bool(require_saved_workspace)
        self.require_pixel_coordinates = bool(require_pixel_coordinates)

    def plan_from_payload(
        self,
        payload: Mapping[str, Any],
        *,
        requested_mode: str = "sequential",
    ) -> S2PlanResult:
        try:
            validate_scan_payload(
                payload,
                require_saved_workspace=self.require_saved_workspace,
            )
            snapshot = VisionSnapshot.from_scan(
                payload,
                require_pixel_coordinates=self.require_pixel_coordinates,
            )
            plan = build_coordination_plan(
                snapshot,
                self.active_arm_ids,
                requested_mode=requested_mode,
            )
            return S2PlanResult(
                ok=True,
                snapshot=snapshot,
                plan=plan,
                scan_payload=dict(payload),
            )
        except S2ValidationError as exc:
            return S2PlanResult(
                ok=False,
                scan_payload=dict(payload) if isinstance(payload, Mapping) else None,
                error=exc.to_dict(),
            )
        except (TypeError, ValueError) as exc:
            return S2PlanResult(
                ok=False,
                scan_payload=dict(payload) if isinstance(payload, Mapping) else None,
                error={
                    "code": "invalid_s2_evidence",
                    "message": str(exc),
                    "details": {},
                },
            )

    def scan_and_plan(
        self,
        *,
        camera_id: str = "Cam1",
        color: str = "any",
        selection: str = "first",
        requested_mode: str = "sequential",
    ) -> S2PlanResult:
        try:
            method = getattr(self.scanner, "scan_workspace", None)
            if method is None:
                method = getattr(self.scanner, "scan", None)
            if method is None and callable(self.scanner):
                method = self.scanner
            if method is None:
                raise S2ValidationError(
                    "scanner_unavailable",
                    "scanner must provide scan_workspace, scan, or be callable",
                )

            kwargs: dict[str, Any] = {
                "camera_id": camera_id,
                "color": color,
                "selection": selection,
            }
            try:
                parameters = inspect.signature(method).parameters
                accepts_kwargs = any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters.values()
                )
                if "require_saved_workspace" in parameters or accepts_kwargs:
                    kwargs["require_saved_workspace"] = self.require_saved_workspace
            except (TypeError, ValueError):
                pass
            payload = method(**kwargs)
            return self.plan_from_payload(payload, requested_mode=requested_mode)
        except S2ValidationError as exc:
            return S2PlanResult(ok=False, error=exc.to_dict())
        except Exception as exc:  # scanner errors are returned as report evidence
            return S2PlanResult(
                ok=False,
                error={
                    "code": "scanner_error",
                    "message": str(exc),
                    "details": {"exception": type(exc).__name__},
                },
            )

    def verify_after_scan(
        self,
        before_result: S2PlanResult,
        after_payload: Mapping[str, Any],
        assignments: Sequence[Any] | None = None,
    ) -> VerificationReport:
        if not before_result.ok or before_result.snapshot is None or before_result.plan is None:
            return VerificationReport.failure(
                "plan_not_available",
                "a successful S2 plan is required before verification",
            )
        try:
            validate_scan_payload(
                after_payload,
                require_saved_workspace=self.require_saved_workspace,
            )
            after_snapshot = VisionSnapshot.from_scan(
                after_payload,
                require_pixel_coordinates=self.require_pixel_coordinates,
            )
        except S2ValidationError as exc:
            return VerificationReport.failure(exc.code, exc.message)
        except (TypeError, ValueError) as exc:
            return VerificationReport.failure("invalid_verification_evidence", str(exc))
        return verify_assignments(
            before_result.snapshot,
            after_snapshot,
            assignments if assignments is not None else before_result.plan.assignments,
        )
