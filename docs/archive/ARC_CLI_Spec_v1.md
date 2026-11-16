# ARC CLI Spec v1

## 1. Purpose

This document defines the first implementation specification for `ARC` (Agent Robotic Cluster), a multi-arm robotic control system designed to be operated through a CLI and orchestrated by an agent runtime such as OpenClaude.

The design goal is to support:

- multiple robot arms
- multiple hardware backends
- safe task-level control
- operator-assisted and autonomous workflows
- future TUI expansion without replacing the core architecture

This spec is intentionally biased toward practical implementation on a machine directly connected to robotic hardware.

## 2. Design Principles

### 2.1 Core Principles

1. LLMs issue intent, not raw hardware writes.
2. All robot actions pass through a deterministic control layer.
3. Every command is validated against safety policy before execution.
4. Every arm is addressable by explicit identity.
5. Multi-arm scheduling is a first-class design concern.
6. All operator-facing and agent-facing commands should support machine-readable JSON output.

### 2.2 Non-Goals for v1

- hard real-time servo loop control inside the agent
- autonomous collision-free motion planning for arbitrary 3D scenes
- vendor-agnostic full robotics abstraction for every possible robot class
- replacing existing motion runtimes like ROS2 controllers or vendor SDKs

## 3. System Overview

ARC should be split into four layers:

```text
OpenClaude / Agent
        |
        v
     arc CLI
        |
        v
  arcd control daemon
        |
        +--> scheduler
        +--> safety policy engine
        +--> state store
        +--> lease manager
        |
        v
   per-arm drivers / adapters
        |
        v
  hardware controller / SDK / ROS / serial / TCP
```

## 4. Components

### 4.1 `arc` CLI

The CLI is the public command surface used by:

- operators
- scripts
- OpenClaude tools
- future TUI actions

Responsibilities:

- send structured requests to `arcd`
- render human output
- emit JSON output
- avoid direct hardware coupling

The CLI should be stateless.

### 4.2 `arcd` Control Daemon

`arcd` is the control plane.

Responsibilities:

- load arm registry
- maintain fleet state
- route commands to correct drivers
- enforce safety policies
- manage task queue
- manage arm leases and ownership
- aggregate faults and health
- expose a local API for CLI and TUI

Recommended transport for v1:

- local HTTP API on `127.0.0.1`
or
- Unix domain socket

HTTP is easier to inspect and debug during early development.

### 4.3 Drivers

Each robot backend is implemented as a driver adapter.

Examples:

- serial-based arm
- vendor SDK arm
- ROS2-backed manipulator
- TCP/IP industrial controller

Each driver must expose a common interface defined in Section 10.

### 4.4 State Store

The state store can start simple.

Recommended v1:

- SQLite for persistent metadata and task history
- in-memory cache inside `arcd` for live runtime state

Stored data:

- arm registry
- arm capability metadata
- task records
- lease ownership
- fault history
- operator approvals

### 4.5 Scheduler

The scheduler selects the best arm for a task when no exact arm is forced.

Responsibilities:

- capability matching
- availability checks
- health checks
- workspace reachability checks
- priority handling
- load balancing
- fallback arm selection

### 4.6 Safety Engine

The safety engine must be deterministic.

Responsibilities:

- validate target pose against workspace limits
- validate joints against hard limits
- validate speed and acceleration caps
- deny commands into restricted zones
- enforce shared-zone locking for nearby arms
- deny commands if arm health is degraded
- enforce approval requirements for dangerous tasks

## 5. Control Model

### 5.1 Control Hierarchy

ARC uses three abstraction layers:

#### Layer 1: Primitive Motion

- move joints
- move pose
- gripper open/close
- home
- stop
- recover

#### Layer 2: Skill Actions

- pick
- place
- pick-place
- inspect
- handoff
- press-button

#### Layer 3: Mission Actions

- sort objects
- assemble component
- clear tray
- move batch to station

LLM-driven logic should primarily operate at Layer 2 and Layer 3.

### 5.2 Execution Model

The execution path should be:

1. Agent/operator issues command
2. CLI sends request to `arcd`
3. `arcd` validates request
4. `arcd` acquires lease or checks ownership
5. `arcd` routes command to driver
6. Driver executes deterministic motion/runtime logic
7. State and logs are updated
8. Result is returned as structured output

## 6. Multi-Arm Design

### 6.1 Arm Identity

Every arm must have:

- `arm_id`
- `driver`
- `endpoint`
- `capabilities`
- `workspace`
- `joint_limits`
- `tooling`
- `status`

No automation should rely on an implicit default arm.

### 6.2 Shared Workspace

If multiple arms operate in overlapping physical space, the system must support:

- workspace zones
- shared exclusion zones
- zone locks
- task serialization where necessary

### 6.3 Lease Ownership

An arm may be leased by:

- operator
- planner
- automation job
- calibration process

This prevents conflicting commands from multiple sources.

Lease fields:

- `arm_id`
- `owner`
- `lease_id`
- `issued_at`
- `expires_at`
- `mode`

### 6.4 Flexible Allocation

Tasks may target:

- an exact arm
- any arm with required capabilities
- any arm inside a named cluster

Examples:

- exact target: `arm-a`
- capability target: `requires grip + move_pose`
- cluster target: `packing-cell-1`

## 7. Modes of Operation

ARC should support these operational modes:

### 7.1 Manual

Operator directly commands arms.

### 7.2 Assisted

Agent suggests actions; operator approves before execution.

### 7.3 Auto

Agent dispatches approved classes of tasks automatically.

### 7.4 Maintenance

Restricted mode for calibration, diagnostics, recovery, and service actions.

## 8. CLI Specification

Command root:

```text
arc
```

Subcommand groups:

- `arc arm`
- `arc task`
- `arc lease`
- `arc queue`
- `arc safety`
- `arc fault`
- `arc cluster`
- `arc system`
- `arc estop`

All subcommands should support:

- `--json`
- `--timeout`
- `--request-id` (optional)

### 8.1 Arm Commands

#### List arms

```bash
arc arm list
arc arm list --json
```

#### Get arm details

```bash
arc arm get arm-a
```

#### Get health

```bash
arc arm health arm-a
```

#### Get status

```bash
arc arm status arm-a
```

#### Home

```bash
arc arm home arm-a
```

#### Stop

```bash
arc arm stop arm-a
```

#### Move joints

```bash
arc arm move-joints arm-a --j1 10 --j2 20 --speed 30
```

#### Move pose

```bash
arc arm move-pose arm-a --x 120 --y 40 --z 30 --speed 25
```

#### Gripper control

```bash
arc arm grip arm-a --open
arc arm grip arm-a --close
```

#### Wait until idle

```bash
arc arm wait arm-a --timeout 20
```

#### Recover

```bash
arc arm recover arm-a
```

### 8.2 Task Commands

#### Submit task

```bash
arc task submit pick-place --arm arm-a --pick-x 10 --pick-y 20 --pick-z 5 --place-x 30 --place-y 40 --place-z 5
```

#### Submit without exact arm

```bash
arc task submit pick-place --cluster packing-cell-1 --requires grip --pick-x 10 --pick-y 20 --place-x 30 --place-y 40
```

#### List tasks

```bash
arc task list
```

#### Get task

```bash
arc task get task-001
```

#### Cancel task

```bash
arc task cancel task-001
```

#### Approve task

```bash
arc task approve task-001
```

#### Retry task

```bash
arc task retry task-001
```

### 8.3 Lease Commands

#### Acquire

```bash
arc lease acquire arm-a --owner planner
```

#### Release

```bash
arc lease release arm-a --owner planner
```

#### Show lease

```bash
arc lease get arm-a
```

### 8.4 Queue Commands

```bash
arc queue list
arc queue list arm-a
arc queue pause arm-a
arc queue resume arm-a
arc queue clear arm-a
```

### 8.5 Safety Commands

```bash
arc safety check move-pose --arm arm-a --x 100 --y 0 --z 20
arc safety zones
arc safety zone-locks
arc safety policy show
```

### 8.6 Fault Commands

```bash
arc fault list
arc fault get fault-001
arc fault acknowledge fault-001
```

### 8.7 Cluster Commands

```bash
arc cluster list
arc cluster get packing-cell-1
arc cluster arms packing-cell-1
```

### 8.8 System Commands

```bash
arc system health
arc system version
arc system drivers
```

### 8.9 Emergency Stop

```bash
arc estop arm-a
arc estop all
```

## 9. Output Schema

All commands should support structured JSON output.

### 9.1 Success Envelope

```json
{
  "ok": true,
  "request_id": "req-123",
  "timestamp": "2026-05-06T11:20:00Z",
  "data": {}
}
```

### 9.2 Error Envelope

```json
{
  "ok": false,
  "request_id": "req-123",
  "timestamp": "2026-05-06T11:20:00Z",
  "error": {
    "code": "OUT_OF_BOUNDS",
    "message": "Requested pose exceeds workspace limits",
    "details": {
      "arm_id": "arm-a",
      "axis": "x",
      "requested": 440,
      "limit_max": 400
    }
  }
}
```

### 9.3 Standard Error Codes

- `ARM_NOT_FOUND`
- `ARM_UNAVAILABLE`
- `LEASE_CONFLICT`
- `OUT_OF_BOUNDS`
- `JOINT_LIMIT_EXCEEDED`
- `RESTRICTED_ZONE`
- `UNSUPPORTED_CAPABILITY`
- `SAFETY_POLICY_BLOCKED`
- `ESTOP_ACTIVE`
- `TASK_NOT_FOUND`
- `TASK_APPROVAL_REQUIRED`
- `DRIVER_TIMEOUT`
- `DRIVER_ERROR`
- `SYSTEM_UNHEALTHY`

## 10. Driver Interface

Every driver must implement the following interface contract.

### 10.1 Driver Methods

- `connect()`
- `disconnect()`
- `health()`
- `get_state()`
- `home()`
- `stop()`
- `estop()`
- `recover()`
- `move_joints(joints, speed)`
- `move_pose(pose, speed)`
- `grip(mode)`
- `wait_until_idle(timeout)`
- `validate_capability(capability)`

### 10.2 Driver State Model

Each driver should return a normalized state:

```json
{
  "arm_id": "arm-a",
  "status": "idle",
  "mode": "auto",
  "pose": {"x": 120, "y": 30, "z": 10},
  "joints": {"j1": 0, "j2": 20, "j3": -10},
  "gripper": {"state": "open"},
  "fault": null,
  "busy": false,
  "estop": false
}
```

### 10.3 Driver Status Values

- `idle`
- `busy`
- `paused`
- `fault`
- `offline`
- `estop`
- `maintenance`

## 11. Arm Registry Format

Recommended file:

```text
config/arms.json
```

Example:

```json
{
  "arms": [
    {
      "id": "arm-a",
      "name": "Left Packing Arm",
      "driver": "serial_uarm",
      "endpoint": "/dev/ttyUSB0",
      "cluster": "packing-cell-1",
      "tool": "parallel_gripper",
      "capabilities": ["move_joints", "move_pose", "grip", "pick_place"],
      "workspace": {
        "x": [0, 400],
        "y": [-180, 180],
        "z": [0, 260]
      },
      "joint_limits": {
        "j1": [-180, 180],
        "j2": [-90, 90],
        "j3": [-120, 120]
      },
      "speed_limits": {
        "default": 30,
        "max": 60
      },
      "restricted_zones": ["human-access-zone"],
      "enabled": true
    }
  ]
}
```

## 12. Task Model

Recommended task schema:

```json
{
  "task_id": "task-001",
  "type": "pick_place",
  "target": {
    "arm_id": "arm-a",
    "cluster": null,
    "requirements": ["grip"]
  },
  "params": {
    "pick": {"x": 120, "y": 50, "z": 10},
    "place": {"x": 220, "y": -10, "z": 15},
    "speed": 20
  },
  "priority": 5,
  "approval_required": false,
  "status": "queued",
  "created_at": "2026-05-06T11:20:00Z",
  "owner": "planner"
}
```

### 12.1 Task Status Values

- `queued`
- `scheduled`
- `awaiting_approval`
- `running`
- `succeeded`
- `failed`
- `canceled`
- `blocked`

## 13. Scheduler Rules

When a task does not explicitly name an arm, selection should follow this order:

1. filter arms by `enabled`
2. filter by required capabilities
3. filter by health status
4. filter by current mode
5. filter by workspace reachability
6. filter by lease availability
7. score by current load
8. score by cluster preference
9. select highest score

### 13.1 Suggested Scoring Fields

- idle state bonus
- same-cluster bonus
- lower queue depth bonus
- closer last-known pose bonus
- tool match bonus

### 13.2 Required Scheduler Guarantees

- never assign one exclusive task to two arms
- never assign two exclusive tasks to one arm simultaneously
- never assign into restricted or locked zones
- always respect estop state

## 14. Safety Policy Matrix

### 14.1 Safety Levels

- `low`: ordinary motion within known safe workspace
- `medium`: motion with object handling or non-default speed
- `high`: motion near restricted zone or shared workspace boundary
- `critical`: operator-confirmed action, maintenance motion, or unsafe recovery path

### 14.2 Approval Policy

| Action | Approval Needed | Notes |
|---|---|---|
| `status` | no | read-only |
| `home` | optional | depends on workspace risk |
| `move-joints` | no/optional | optional if outside normal safe envelope |
| `move-pose` | no/optional | optional for shared-zone boundary |
| `grip` | no | unless tool hazard exists |
| `recover` | yes | recovery is inherently risky |
| `estop reset` | yes | must be explicit |
| maintenance calibration | yes | operator-controlled |

### 14.3 Mandatory Safety Checks Before Motion

- arm exists
- arm enabled
- arm not in estop
- arm healthy enough for requested command
- lease valid
- requested capability supported
- target within workspace
- target joints within limits
- requested speed within cap
- shared zones available
- command mode allowed in current system mode

## 15. Logging and Audit

Every command should create an audit record.

Required fields:

- `request_id`
- `timestamp`
- `source` (`operator`, `agent`, `scheduler`, `system`)
- `owner`
- `arm_id`
- `task_id` if applicable
- `action`
- `arguments`
- `result`
- `safety_decision`

Recommended storage:

- SQLite table
- newline-delimited JSON logs for easy grep and ingestion

## 16. TUI Readiness

The future TUI should consume `arcd`, not bypass it.

Recommended panels:

1. Fleet overview
2. Selected arm detail
3. Task queue
4. Fault stream
5. Lease ownership
6. Safety alerts
7. Command input
8. Estop controls

Hotkeys worth planning for:

- `h`: home selected arm
- `s`: stop selected arm
- `e`: estop selected arm
- `E`: estop all
- `a`: approve selected task
- `r`: recover selected arm

## 17. Suggested API Between CLI and `arcd`

Recommended local HTTP API:

- `GET /v1/arms`
- `GET /v1/arms/{arm_id}`
- `GET /v1/arms/{arm_id}/status`
- `POST /v1/arms/{arm_id}/home`
- `POST /v1/arms/{arm_id}/stop`
- `POST /v1/arms/{arm_id}/move-joints`
- `POST /v1/arms/{arm_id}/move-pose`
- `POST /v1/arms/{arm_id}/grip`
- `POST /v1/tasks`
- `GET /v1/tasks`
- `GET /v1/tasks/{task_id}`
- `POST /v1/tasks/{task_id}/approve`
- `POST /v1/tasks/{task_id}/cancel`
- `POST /v1/leases/acquire`
- `POST /v1/leases/release`
- `GET /v1/faults`
- `POST /v1/estop/{arm_id}`
- `POST /v1/estop/all`

## 18. Implementation Roadmap

### Phase 1: Core Single-Arm Foundation

- implement `arcd`
- implement one hardware driver
- implement `arc arm status`
- implement `arc arm home`
- implement `arc arm stop`
- implement `arc arm move-joints`
- implement `arc arm move-pose`
- implement JSON output

### Phase 2: Multi-Arm Support

- add arm registry
- add per-arm state
- add lease management
- add queue per arm
- add cluster grouping

### Phase 3: Safety and Scheduling

- add workspace validation
- add restricted zones
- add approval workflow
- add automatic arm selection
- add fault and recovery flow

### Phase 4: Agent Integration

- expose commands suitable for OpenClaude tools
- add task-level commands
- add audit trails for agent-originated actions
- add assisted mode

### Phase 5: TUI

- live fleet dashboard
- task queue visualization
- operator approvals
- estop surface

## 19. Recommended v1 Technology Choices

For a practical first build:

- language: Python or Go
- daemon API: FastAPI or lightweight Go HTTP server
- store: SQLite
- config: JSON
- logs: JSONL
- TUI later: Textual (Python) or Bubble Tea (Go)

If hardware integration is Python-heavy already, Python is the shortest path.

## 20. Final Guidance

The key architectural rule is simple:

OpenClaude should decide what to do.
ARC should decide whether it is allowed and how it is executed.

That separation is what makes the system flexible enough for multiple arms and safe enough to run around real hardware.
