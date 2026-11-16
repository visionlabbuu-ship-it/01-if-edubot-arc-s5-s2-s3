# S2 Vision And Multi-Robot Coordination

This package is the extracted research boundary for S2. It normalizes camera
scan records and produces deterministic object-to-arm assignment proposals.
It does not connect to MG400, move a robot, call a camera SDK, run an LLM, or
execute a task.

The current ARC runtime remains the source of live evidence and execution:

1. S2VisionCoordinator.scan_and_plan calls a scanner and applies the saved
   workspace/calibration gate.
2. VisionSnapshot.from_scan validates the payload and preserves per-arm
   calibrated coordinates.
3. build_coordination_plan assigns each object to an active reachable arm.
4. S2PlanResult.to_arc_assignment_requests creates evidence-rich requests.
5. ARC camera_assign_targets, guardrails, approval, and FleetController/task
   execution decide whether and how to act.

The planner intentionally never declares parallel motion safe. Camera geometry
and assignment are S2 evidence; live shared-zone, reach, state, and collision
checks remain ARC runtime responsibilities.

## Example

    from arc_runtime.camera_controller import CameraController
    from s2_vision_coordination import S2VisionCoordinator

    coordinator = S2VisionCoordinator(
        camera_controller,
        active_arm_ids=("Arm1", "Arm2"),
    )
    result = coordinator.scan_and_plan(camera_id="Cam1", requested_mode="parallel")
    if result.ok:
        report_payload = result.to_dict()
        arc_requests = result.to_arc_assignment_requests()

parallel is recorded as the requested mode, but the S2 plan remains
sequential and sets parallel_admissible to false. The ARC runtime must
re-scan and run its own calibration, reach, collision, and state guardrails
before any action.

## Standalone Web Planner

Run the local operator surface in simulator mode:

    $env:ARC_SIM_MODE = "1"
    python -m s2_vision_coordination.web

On Windows, `run_s2_web.cmd` starts the same simulator-first server.

Open `http://127.0.0.1:8788`. The web workflow supports snapshot/live polling,
object selection, arm selection, named or custom destinations, step ordering,
confirmation, sequential execution, and post-step verification. Execution
re-scans before every step and delegates pick/place motion to ARC's existing
`FleetController` and guardrails.

Hardware execution remains locked unless `S2_ALLOW_HARDWARE_EXECUTE=1` is set
explicitly after reviewing the plan and runtime safety state.
