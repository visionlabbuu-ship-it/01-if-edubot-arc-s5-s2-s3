# S2 Extraction And Scope Boundary

Updated: 2026-10-16

## Correct mapping

The report defines S2 as AI Vision and multi-robot coordination: capture and
detect objects, estimate positions, transform coordinates, check workspace and
reachability, allocate work between arms, and support post-action verification.

The current ARC already implements these capabilities, but it also contains
natural-language agent behavior, storage policy, handoff workflows, direct
motion, task execution, recovery, incidents, UI, and benchmark telemetry. Those
are broader ARC/S1/S5 concerns and should not inflate the S2 research claim.

## Existing implementation evidence

| S2 responsibility | Current source | Boundary decision |
| --- | --- | --- |
| Camera/object detection | `arc_runtime/camera_controller.py` | Keep as ARC runtime adapter; expose normalized S2 records |
| Workspace and per-arm transforms | `workspace_calibration.py`, `object_calibration.py`, `config.py` | S2 input evidence; runtime remains source of truth |
| Reachability and arm candidates | `CameraController` scan output (`reachable_by`) | S2 proposal, rechecked by ARC before motion |
| Assignment | `camera_assign_targets`, workspace planning | Extract deterministic assignment contract in `s2_vision_coordination` |
| Multi-arm sequencing/parallelism | `task_system.py`, `robot_guardrails.py` | ARC execution/guardrail layer; S2 never approves parallel motion |
| Motion, gripper, storage, handoff, recovery | `fleet_controller.py`, `storage_service.py`, `semantic_plan.py` | Outside S2 core |
| LLM/tool selection and UI | `ai_fleet.py`, `sim_llm_chat.py`, TypeScript/web | Outside S2; classify under ARC agent/platform |

## Implemented extraction boundary

`s2_vision_coordination/` now contains:

- `VisionSnapshot` and `VisionObject` contracts;
- preservation of `per_arm_robot_plane`, `reachable_by`, and `preferred_arm`;
- deterministic reachable-arm assignment;
- explicit unassigned reasons;
- a scanner adapter with saved-calibration and stale-calibration gates;
- an ARC assignment-request serializer that does not execute motion;
- read-only post-action verification of source-object removal;
- a `robot_control: false` plan marker;
- a hard `parallel_admissible: false` result until ARC live validation runs.

No original ARC source file was moved or deleted. The package is a stable seam
for later adapter work while the existing runtime continues to operate.

## S2 acceptance and evaluation

Evaluate S2 separately from robot execution:

1. detection precision/recall or object-count accuracy on a fixed image set;
2. pixel-to-workspace and workspace-to-arm coordinate error;
3. reachable-arm classification accuracy;
4. assignment success and load balance across arms;
5. planning latency and stale-calibration rejection;
6. collision/near-miss and post-action verification evidence supplied by ARC;
7. failure cases: occlusion, out-of-workspace objects, missing transforms,
   offline arms, ambiguous assignment, and camera failure.

Do not turn a successful `pick/place` or a successful LLM tool loop into an S2
vision score without recording the S2 evidence and ARC execution evidence
separately.

## Remaining deployment steps

1. Use `S2VisionCoordinator` around the current `camera_scan` source. The
   adapter is complete; ARC's assignment path also accepts optional
   `object_id` values so same-color objects remain correlated to S2 evidence.
2. Pass `to_arc_assignment_requests()` to the existing ARC assignment path only
   after the application decides to execute; ARC still re-scans and validates.
3. Convert assignments into ARC semantic plans only after live guardrail and
   calibration checks.
4. Keep task execution, storage reservation, incidents, and recovery outside
   the S2 package.
5. Record a fixed S2 test manifest and report S2 metrics separately in the
   research results.
