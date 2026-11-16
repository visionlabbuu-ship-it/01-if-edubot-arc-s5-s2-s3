"""Standalone S2 vision and multi-robot coordination boundary."""

from .adapter import (
    S2PlanResult,
    S2ValidationError,
    S2VisionCoordinator,
    plan_to_arc_assignment_requests,
    validate_scan_payload,
)
from .contract import Assignment, CoordinationPlan, VisionObject, VisionSnapshot
from .planner import build_coordination_plan
from .standalone import S2StandaloneController
from .verification import VerificationReport, verify_assignments

__all__ = [
    "Assignment",
    "CoordinationPlan",
    "S2PlanResult",
    "S2ValidationError",
    "S2VisionCoordinator",
    "S2StandaloneController",
    "VisionObject",
    "VisionSnapshot",
    "VerificationReport",
    "build_coordination_plan",
    "plan_to_arc_assignment_requests",
    "validate_scan_payload",
    "verify_assignments",
]
