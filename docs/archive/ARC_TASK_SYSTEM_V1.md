# ARC Task System V1

Date: 2026-05-07

This document defines the first practical task model for ARC so the system can orchestrate multi-step and multi-arm work instead of issuing one robot command at a time.

## 1. Why this is needed

Current ARC behavior is action-by-action.

That is not enough for real operator requests such as:

- `Arm1 draw a circle, then Arm2 draw a square`
- `Arm1 starts now, Arm2 starts 2 seconds later`
- `run both arms in parallel`
- `if either arm faults, stop the whole task and wait for me`

To support those requests safely, ARC needs a task layer above the motion primitives.

## 2. Design goals

- Support multi-step robot work as a first-class object.
- Support parallel groups of steps across multiple arms.
- Support explicit `delay` and `wait` steps.
- Keep motion execution deterministic and driver-backed.
- Preserve the current fault/incident policy.
- Make task state queryable from CLI and ARC tools.

## 3. Scope for v1

Task System v1 should support:

- single task creation
- sequential steps
- parallel step groups
- per-step arm target
- delay steps
- incident blocking
- cancel and abort
- query current task status

Task System v1 does not need yet:

- optimal scheduling
- cross-task load balancing
- automatic arm selection
- persistent distributed execution
- arbitrary DAG workflow engine

## 4. Core model

### 4.1 Task

A task is the top-level execution object.

Suggested fields:

- `task_id`
- `name`
- `status`
- `created_at`
- `started_at`
- `completed_at`
- `requested_by`
- `request_id`
- `policy`
- `steps`
- `events`
- `incident`

Suggested statuses:

- `pending`
- `ready`
- `running`
- `blocked`
- `completed`
- `failed`
- `cancelled`
- `aborted`

### 4.2 Step

Each task contains steps.

Suggested step fields:

- `step_id`
- `type`
- `arm_id`
- `status`
- `params`
- `delay_ms`
- `started_at`
- `completed_at`
- `result`

Suggested step statuses:

- `pending`
- `queued`
- `running`
- `blocked`
- `completed`
- `failed`
- `skipped`
- `cancelled`

### 4.3 Step types

Minimum step types for v1:

- `move_relative`
- `move_to_pose`
- `move_joint_pose`
- `run_path`
- `stop_robot`
- `recover_robot`
- `recover_home_robot`
- `delay`
- `wait_for_state`
- `wait_for_task`

## 5. Execution semantics

### 5.1 Sequential execution

Default behavior:

- one step starts after the previous step completes successfully

### 5.2 Parallel execution

ARC needs a concept of a parallel group.

Example:

```json
{
  "type": "parallel",
  "steps": [
    {
      "type": "run_path",
      "arm_id": "Arm1",
      "params": { "path_type": "circle", "radius": 20, "count": 1 }
    },
    {
      "type": "run_path",
      "arm_id": "Arm2",
      "params": { "path_type": "rectangle", "width": 25, "height": 25 }
    }
  ]
}
```

Semantics:

- all child steps are released together
- each arm executes independently on its own worker
- the group completes only when all child steps complete
- if one child faults, the group becomes blocked or failed according to policy

### 5.3 Delay execution

Delay must be a real step, not a hack around `time.sleep` in random code paths.

Example:

```json
{
  "type": "delay",
  "delay_ms": 2000
}
```

This enables:

- `Arm1 starts now`
- `wait 2 seconds`
- `Arm2 starts after the delay`

### 5.4 Timed parallel offsets

If the user wants staggered parallel behavior:

- use a parallel group
- put a `delay` step inside the branch that should start later

Example intent:

- `Arm1 draw a circle`
- `Arm2 draw a square 2 seconds later`

Example representation:

```json
{
  "type": "parallel",
  "steps": [
    {
      "type": "sequence",
      "steps": [
        {
          "type": "run_path",
          "arm_id": "Arm1",
          "params": { "path_type": "circle", "radius": 20, "count": 1 }
        }
      ]
    },
    {
      "type": "sequence",
      "steps": [
        {
          "type": "delay",
          "delay_ms": 2000
        },
        {
          "type": "run_path",
          "arm_id": "Arm2",
          "params": { "path_type": "rectangle", "width": 25, "height": 25 }
        }
      ]
    }
  ]
}
```

## 6. Fault and incident model

The existing motion runtime already distinguishes:

- `immediate_fault`
- `runtime_fault`

Task System v1 should preserve that distinction.

### 6.1 Immediate fault

Default behavior:

- block the affected step
- mark task as `blocked`
- allow operator to choose recovery or abort

### 6.2 Runtime fault

Default behavior:

- block the affected step
- block the entire task immediately
- stop releasing future steps
- surface incident context to ARC and operator

### 6.3 Parallel group fault behavior

If one branch faults while another branch is still active:

- task state becomes `blocked`
- no new steps are released
- policy may optionally stop sibling arms

Recommended default for v1:

- if any branch faults during a parallel group, issue `stop_robot` to sibling branches when safe

## 7. Runtime architecture for v1

### 7.1 Task runner

Add a `TaskRunner` component that owns:

- task registry
- per-arm worker state
- step dispatch
- delay timing
- event recording
- incident propagation

### 7.2 Per-arm workers

Each arm should have a worker loop.

Worker responsibilities:

- receive next step for that arm
- execute one deterministic driver-backed command
- report start and completion
- report faults

This is how ARC can finally let two arms move concurrently.

### 7.3 In-memory first, persistent second

V1 can start with:

- in-memory task registry
- JSON event snapshots on disk

Later:

- SQLite or daemon state store

## 8. CLI surface for v1

Suggested new commands:

- `arc task create --file task.json`
- `arc task start <task_id>`
- `arc task status <task_id>`
- `arc task list`
- `arc task cancel <task_id>`
- `arc task abort <task_id>`
- `arc task ack-incident <task_id> --action recover`

Optional convenience command:

- `arc task run --file task.json`

## 9. ARC tool surface for v1

Suggested new tools:

- `start_task`
- `get_task_status`
- `cancel_task`
- `abort_task`
- `ack_task_incident`

Current implemented notes:

- top-level task payloads must now contain a non-empty `steps` array
- empty task payloads are rejected explicitly instead of returning an instant fake success
- task status payloads now expose:
  - `step_count`
  - `event_count`
  - `duration_ms`

The LLM should prefer these tools for multi-step requests instead of micromanaging each robot command one by one.

## 10. Example task payloads

### 10.1 Sequential example

```json
{
  "name": "circle-then-square",
  "steps": [
    {
      "type": "run_path",
      "arm_id": "Arm1",
      "params": { "path_type": "circle", "radius": 20, "count": 1 }
    },
    {
      "type": "run_path",
      "arm_id": "Arm2",
      "params": { "path_type": "rectangle", "width": 25, "height": 25 }
    }
  ]
}
```

### 10.2 Parallel example

```json
{
  "name": "circle-and-square-parallel",
  "steps": [
    {
      "type": "parallel",
      "steps": [
        {
          "type": "run_path",
          "arm_id": "Arm1",
          "params": { "path_type": "circle", "radius": 20, "count": 1 }
        },
        {
          "type": "run_path",
          "arm_id": "Arm2",
          "params": { "path_type": "rectangle", "width": 25, "height": 25 }
        }
      ]
    }
  ]
}
```

### 10.3 Staggered parallel example

```json
{
  "name": "staggered-circle-square",
  "steps": [
    {
      "type": "parallel",
      "steps": [
        {
          "type": "sequence",
          "steps": [
            {
              "type": "run_path",
              "arm_id": "Arm1",
              "params": { "path_type": "circle", "radius": 20, "count": 1 }
            }
          ]
        },
        {
          "type": "sequence",
          "steps": [
            {
              "type": "delay",
              "delay_ms": 2000
            },
            {
              "type": "run_path",
              "arm_id": "Arm2",
              "params": { "path_type": "rectangle", "width": 25, "height": 25 }
            }
          ]
        }
      ]
    }
  ]
}
```

### 10.4 Same distance, different speeds

```json
{
  "name": "same-distance-different-speeds",
  "steps": [
    {
      "type": "parallel",
      "steps": [
        {
          "type": "sequence",
          "steps": [
            {
              "type": "motion_profile",
              "arm_id": "Arm1",
              "params": { "speed_l": 80, "acc_l": 80 }
            },
            {
              "type": "move_relative",
              "arm_id": "Arm1",
              "params": { "dx": 40, "dy": 0, "dz": 0, "dr": 0 }
            }
          ]
        },
        {
          "type": "sequence",
          "steps": [
            {
              "type": "motion_profile",
              "arm_id": "Arm2",
              "params": { "speed_l": 40, "acc_l": 40 }
            },
            {
              "type": "move_relative",
              "arm_id": "Arm2",
              "params": { "dx": 40, "dy": 0, "dz": 0, "dr": 0 }
            }
          ]
        }
      ]
    }
  ]
}
```

This example encodes the intended semantics clearly:

- distance lives in the motion step
- speed lives in the `motion_profile` step
- the two must not be conflated by ARC

## 11. Recommended implementation order

1. Add `TaskRunner` with in-memory task objects.
2. Add `delay` step support.
3. Add per-arm worker threads.
4. Add `parallel` and `sequence` step group handling.
5. Add incident blocking at task level.
6. Expose `arc task ...` CLI commands.
7. Expose ARC task tools.

## 12. What success looks like

After Task System v1 is in place, ARC should be able to handle operator intents like:

- `let Arm1 move in a circle while Arm2 moves in a square`
- `start Arm2 two seconds after Arm1`
- `if either arm faults, stop the mission and wait for me`

without forcing the LLM to manually babysit every low-level command.
