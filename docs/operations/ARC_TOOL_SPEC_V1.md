# ARC Tool Spec V1

Date: 2026-05-07

This document defines the first structured robot tool family for ARC. The goal is to stop relying on one overloaded `RobotArm` action switch and instead expose a small set of capability tools that map cleanly to monitored robot control primitives.

## Design goals

- Keep the tool surface small and stable.
- Use parameterized tools instead of one tool per shape or motion pattern.
- Preserve safety semantics from the Python runtime.
- Return fault context to ARC so the model can stop and ask for operator direction.

## Tool set

### `list_robots`
- Purpose: List configured robot arms and current connectivity.
- Backing CLI: `python arc.py arm --json list`
- Type: Read-only

### `get_robot_state`
- Purpose: Read robot mode, enable status, running status, error status, pose, and joints.
- Backing CLI: `python arc.py arm --json state <arm_id>`
- Type: Read-only

### `stop_robot`
- Purpose: Stop motion immediately.
- Backing CLI: `python arc.py arm --json stop <arm_id>`
- Type: Mutating, operator-level

### `recover_robot`
- Purpose: Clear robot error and re-enable the arm.
- Backing CLI: `python arc.py arm --json recover <arm_id>`
- Type: Mutating, operator-level

### `recover_home_robot`
- Purpose: Recover robot state and send the arm home when ready.
- Backing CLI: `python arc.py arm --json recover-home <arm_id>`
- Type: Mutating, operator-level

### `set_motion_profile`
- Purpose: Set linear and/or joint speed and acceleration independently from geometric motion distance.
- Backing CLI: `python arc.py arm --json motion-profile <arm_id> --speed-l ... --acc-l ... --speed-j ... --acc-j ...`
- Type: Mutating

### `move_relative`
- Purpose: Perform a monitored relative move using explicit deltas.
- Backing CLI: `python arc.py arm --json move-relative <arm_id> --dx ... --dy ... --dz ... --dr ...`
- Type: Mutating

### `move_to_pose`
- Purpose: Perform a monitored absolute pose move.
- Backing CLI: `python arc.py arm --json move-to-pose <arm_id> --x ... --y ... --z ... --r ...`
- Type: Mutating

### `run_path`
- Purpose: Run a parameterized monitored path.
- Supported path types:
  - `rectangle`
  - `circle`
  - `waypoints`
- Backing CLI: `python arc.py arm --json run-path <arm_id> --type ...`
- Type: Mutating

### `get_robot_incident`
- Purpose: Read the latest stored incident for a robot.
- Backing CLI: `python arc.py arm --json incident <arm_id>`
- Type: Read-only

### `ack_incident`
- Purpose: Apply an explicit operator decision after a stored incident.
- Supported actions:
  - `recover`
  - `recover_home`
  - `abort`
  - `retry_last`
- Backing CLI: `python arc.py arm --json ack-incident <arm_id> <action>`
- Type: Mutating, operator-level

### `run_task`
- Purpose: Run a coordinated multi-step robot task with `sequence`, `parallel`, and `delay` steps.
- Backing CLI:
  - `python arc.py task --json run --task-json ...`
  - `python arc.py task --json start --task-json ...`
- Type: Mutating, operator-level
- Validation:
  - top-level `steps` must be non-empty
  - empty tasks are rejected instead of silently completing in a few milliseconds
- Output notes:
  - task payload now includes `step_count`, `event_count`, and `duration_ms`
  - use these fields to confirm the task actually executed

### `get_task_status`
- Purpose: Read the current status and event history for a task.
- Backing CLI: `python arc.py task --json status <task_id>`
- Type: Read-only

### `cancel_task`
- Purpose: Request cancellation for a running task.
- Backing CLI: `python arc.py task --json cancel <task_id>`
- Type: Mutating, operator-level

## Safety policy

- ARC should read state before motion when context is unclear.
- ARC should keep geometry separate from speed. Distance belongs in move/path parameters. Speed and acceleration belong in `set_motion_profile` or `motion_profile` task steps.
- Motion is not complete when the controller accepts the command. Motion is complete only when the Python runtime reports completion, timeout, or fault.
- If a motion result indicates `runtime_fault`, `saw_running_motion=true`, or `recommended_action=await_operator_decision`, ARC must treat it as a safety incident.
- After a safety incident, ARC must not auto-recover, auto-home, auto-retry, or continue the path without explicit operator instruction.

## Why this shape

- Avoids tool explosion such as `move_square`, `move_circle`, `move_triangle`, `move_spiral`.
- Keeps geometric variation inside `run_path`.
- Adds a real task layer for multi-arm execution without changing the motion primitives again.
