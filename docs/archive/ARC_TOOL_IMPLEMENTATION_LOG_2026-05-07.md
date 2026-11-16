# ARC Tool Implementation Log

Date: 2026-05-07

## Implemented today

- Added new Python CLI surface in [arc.py](PATH_TO_IF_EDUBOT_ARC\arc.py:1):
  - `move-relative`
  - `move-to-pose`
  - `run-path`
  - `incident`
  - `ack-incident`
- Extended [fleet_controller.py](PATH_TO_IF_EDUBOT_ARC\fleet_controller.py:1):
  - monitored relative motion wrapper
  - monitored move-to-pose wrapper
  - parameterized `run_path`
  - incident lookup and acknowledgment
- Added ARC tool family in [external/Agent-Robotic-Cluster/src/tools/RobotControl](PATH_TO_IF_EDUBOT_ARC\external\Agent-Robotic-Cluster\src\tools\RobotControl:1):
  - `list_robots`
  - `get_robot_state`
  - `stop_robot`
  - `recover_robot`
  - `recover_home_robot`
  - `move_relative`
  - `move_to_pose`
  - `run_path`
  - `get_robot_incident`
  - `ack_incident`
- Added task tools:
  - `run_task`
  - `get_task_status`
  - `cancel_task`
- Added `set_motion_profile` tool so ARC can control speed/acceleration independently from geometric distance.
- Kept legacy [RobotArmTool.ts](PATH_TO_IF_EDUBOT_ARC\external\Agent-Robotic-Cluster\src\tools\RobotArmTool\RobotArmTool.ts:1) exposed for compatibility during transition.
- Updated [tools.ts](PATH_TO_IF_EDUBOT_ARC\external\Agent-Robotic-Cluster\src\tools.ts:1) so ARC now advertises the new robot capability tools.

## Runtime behavior added

- Tool results now return structured command output plus parsed JSON when available.
- ARC now receives safety hints from tool results:
  - `fault_type`
  - `recommended_action`
  - `saw_running_motion`
  - `incident_detected`
- This is intended to make runtime faults visible to the model instead of collapsing them into generic command failures.

## Task execution update

- Added [task_system.py](PATH_TO_IF_EDUBOT_ARC\task_system.py:1) with TaskRunner v1.
- Added `arc task` CLI commands for inline and background task execution.
- Added task document validation on both the Python runtime and ARC tool side so empty `steps` are rejected.
- Added task telemetry fields `step_count`, `event_count`, and `duration_ms` to make real execution visible.
- Added [task_examples/same_distance_different_speeds.json](PATH_TO_IF_EDUBOT_ARC\task_examples\same_distance_different_speeds.json:1) as the canonical example for "same distance, different speed".
- Verified real hardware execution of a staggered two-arm task:
  - `Arm1` circle
  - `Arm2` rectangle
  - delayed second-arm start

## Known gaps

- `ack_incident retry_last` is still not implemented without a task runner.
- There is still no daemonized or leased task execution layer yet.
- Hardware validation still needs live testing after rebuild.

## Schema correction

- The first `run_path` schema used a root discriminated union, which serialized to root `anyOf`.
- Defaulted optional fields such as `dr`, `r`, `segments`, and `clockwise` also serialized as required in JSON schema.
- These shapes are fragile for OpenAI-compatible function calling, especially with Qwen.
- The tool schemas were flattened to plain objects and cross-field requirements were moved into `validateInput`.
- `run_task` as a raw JSON string was also too permissive. The model could send empty `steps`, which completed in about 16 ms and looked like a scheduler bug. That case is now rejected explicitly.

## Next recommended step

- Test `arc` interactively with:
  - `list_robots`
  - `get_robot_state`
  - `run_path` with `rectangle` and `circle`
  - fault injection during motion
  - `get_robot_incident`
  - `ack_incident`
