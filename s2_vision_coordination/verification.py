"""Read-only post-action verification for S2 evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contract import Assignment, VisionSnapshot


def _snapshot(value: VisionSnapshot | Mapping[str, Any]) -> VisionSnapshot:
    if isinstance(value, VisionSnapshot):
        return value
    return VisionSnapshot.from_scan(value)


def _assignment_id(value: Assignment | Mapping[str, Any]) -> tuple[str, str | None]:
    if isinstance(value, Assignment):
        return value.object_id, value.arm_id
    if not isinstance(value, Mapping):
        raise ValueError("assignment must be an Assignment or object")
    object_id = str(value.get("object_id") or value.get("id") or "").strip()
    if not object_id:
        raise ValueError("assignment is missing object_id")
    arm_id = value.get("arm_id")
    return object_id, str(arm_id) if arm_id else None


@dataclass(frozen=True)
class VerificationReport:
    """Evidence that assigned source objects disappeared after an action."""

    ok: bool
    camera_id: str
    checked_count: int
    verified_count: int
    failed_count: int
    results: tuple[dict[str, Any], ...]
    error: dict[str, Any] | None = None

    @classmethod
    def failure(cls, code: str, message: str, camera_id: str = "Cam1") -> "VerificationReport":
        return cls(
            ok=False,
            camera_id=camera_id,
            checked_count=0,
            verified_count=0,
            failed_count=0,
            results=(),
            error={"code": code, "message": message, "details": {}},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "scope": "S2",
            "camera_id": self.camera_id,
            "checked_count": self.checked_count,
            "verified_count": self.verified_count,
            "failed_count": self.failed_count,
            "results": [dict(item) for item in self.results],
            "error": dict(self.error) if self.error else None,
            "robot_control": False,
        }


def verify_assignments(
    before: VisionSnapshot | Mapping[str, Any],
    after: VisionSnapshot | Mapping[str, Any],
    assignments: Sequence[Assignment | Mapping[str, Any]],
) -> VerificationReport:
    """Verify that every assigned source object disappeared from the scan.

    A camera scan does not prove where an object was placed. This function
    verifies only the S2 claim that the assigned source object is no longer
    visible. Placement and robot state remain ARC responsibilities.
    """

    try:
        before_snapshot = _snapshot(before)
        after_snapshot = _snapshot(after)
        before_ids = {item.object_id for item in before_snapshot.objects}
        after_ids = {item.object_id for item in after_snapshot.objects}
        results: list[dict[str, Any]] = []
        for item in assignments:
            object_id, arm_id = _assignment_id(item)
            if object_id not in before_ids:
                results.append(
                    {
                        "object_id": object_id,
                        "arm_id": arm_id,
                        "status": "missing_before",
                        "verified": False,
                    }
                )
            elif object_id in after_ids:
                results.append(
                    {
                        "object_id": object_id,
                        "arm_id": arm_id,
                        "status": "still_visible",
                        "verified": False,
                    }
                )
            else:
                results.append(
                    {
                        "object_id": object_id,
                        "arm_id": arm_id,
                        "status": "verified_removed",
                        "verified": True,
                    }
                )
        verified_count = sum(bool(item["verified"]) for item in results)
        failed_count = len(results) - verified_count
        return VerificationReport(
            ok=failed_count == 0,
            camera_id=after_snapshot.camera_id,
            checked_count=len(results),
            verified_count=verified_count,
            failed_count=failed_count,
            results=tuple(results),
        )
    except (TypeError, ValueError) as exc:
        return VerificationReport.failure("invalid_verification_evidence", str(exc))
