# ARC / MG400 Implementation Status

Last updated: 2026-06-21

## 1. Scope of this document

This file records the actual implementation status of the current codebase.

It is intentionally different from `ARC_CLI_Spec_v1.md`:

- `ARC_CLI_Spec_v1.md` describes the target architecture.
- This file describes what is already built, tested, and how it behaves on the real robots.

## 2. Current architecture

Current system shape:

```text
CLI / Interactive LLM
        |
        v
  FleetController / OllamaFleetManager
        |
        v
      MG400Robot
        |
        +--> dashboard port 29999
        +--> motion port 30003
        +--> feedback port 30004
```

Current implementation is still single-process and Python-based.

Current runtime mode:

- default active arms: `Arm1` only
- `Arm2` calibration/config remains in `config.py`, but runtime controllers, camera reachability, workspace layout/state, and guardrail collision checks filter it out by default
- set `ARC_ACTIVE_ROBOT_IDS=all` before starting ARC to restore two-arm behavior for legacy demos/tests
- single-arm mode keeps reach/Z guardrails for `Arm1`, but does not block on missing `Arm2` pose or shared-zone occupancy

There is not yet:

- `arcd` daemon
- lease manager
- scheduler
- formal audit log

## 3. Main files

- `arc.py`
  - current CLI entrypoint
- `fleet_controller.py`
  - thin control layer used by CLI and test scripts
- `robot_arm.py`
  - low-level MG400 control and feedback access
- `ai_fleet.py`
  - interactive LLM + tool-call path
- `main.py`
  - interactive prompt loop for LLM control
- `monitor_fault_test.py`
  - runtime fault monitoring test script
- `dual_arm_stop_test.py`
  - dual-arm loop and stop test script
- `config.py`
  - model name and arm endpoint config

## 4. CLI commands currently implemented

Command root:

```bash
python arc.py arm ...
```

### 4.1 Fleet / arm discovery

- `python arc.py arm list`
- `python arc.py arm --json list`
- `python arc.py arm status Arm1`
- `python arc.py arm status Arm2`
- `python arc.py arm state Arm1`
- `python arc.py arm pose Arm1`
- `python arc.py arm --json pose Arm1`

What these do:

- `list`
  - connect to all configured arms
  - report total configured arms
  - report connected arms
- `status`
  - basic connection status
- `state`
  - live feedback snapshot from feed port `30004`
  - includes mode, error/running/enable flags, pose, joints
- `pose`
  - pose + joints oriented output from same feedback path

### 4.2 Motion commands

- `python arc.py arm home Arm1`
- `python arc.py arm move Arm1 FORWARD --step 25`
- `python arc.py arm move Arm1 LEFT --step 25`
- `python arc.py arm move-joint-pose Arm1 --x 250 --y 0 --z 50 --r 0`
- `python arc.py arm move-relative-joint Arm1 --dx 10 --dy 0 --dz 0 --dr 0`
- `python arc.py arm native-motion Arm1 --command "MovJ(250,0,50,0)"`
- `python arc.py arm arc Arm1 --through-x 230 --through-y 20 --through-z 50 --target-x 210 --target-y 0 --target-z 50`
- `python arc.py arm circle Arm1 --radius 20 --count 1`
- `python arc.py arm motion-profile Arm1 --speed-l 70 --acc-l 70`
- `python arc.py arm square Arm1 --step 25`

Supported direction actions:

- `HOME`
- `UP`
- `DOWN`
- `LEFT`
- `RIGHT`
- `FORWARD`
- `BACK`

### 4.3 Stop / recovery commands

- `python arc.py arm stop Arm1`
- `python arc.py arm recover Arm1`
- `python arc.py arm recover-home Arm1`

Behavior:

- `stop`
  - tries `Stop()`
  - if controller does not accept it as success, falls back to `ResetRobot()`
- `recover`
  - sends `ClearError()`
  - sends `EnableRobot()`
- `recover-home`
  - reads state first
  - if error or mode not ready, runs recover
  - re-checks state
  - homes only when mode is `enabled` and no error remains

## 5. Feedback/state features currently implemented

The system can now read live robot feedback from port `30004`.

Current feedback fields exposed:

- `robot_mode`
- `robot_mode_name`
- `enable_status`
- `running_status`
- `error_status`
- `brake_status`
- `pose`
  - `x`
  - `y`
  - `z`
  - `r`
  - `rx`
  - `ry`
- `joints`
  - `j1` to `j6`

Current mode mapping in code:

- `1`: `init`
- `2`: `brake_open`
- `3`: `power_off`
- `4`: `disabled`
- `5`: `enabled`
- `6`: `backdrive`
- `7`: `running`
- `8`: `recording`
- `9`: `error`
- `10`: `pause`
- `11`: `jog`

## 6. LLM tool integration already added

The following robot tools are now available from `ai_fleet.py`:

- `control_robot`
- `move_to_coords`
- `tool_control`
- `get_robot_state`
- `get_robot_pose`
- `recover_robot`
- `recover_and_home_robot`
- `execute_parallel`

This means the LLM can now:

- read current pose/state before motion
- read current error/running/mode flags
- recover a fault explicitly
- recover and home when state is suitable

## 7. Task system currently implemented

The codebase now has a first `TaskRunner v1` layer in [task_system.py](PATH_TO_IF_EDUBOT_ARC\task_system.py:1).

Current task features:

- in-process task execution
- background task start
- task status files under `runtime_state/tasks`
- task document validation for non-empty `steps`
- sequential steps
- parallel step groups
- `delay` steps
- per-arm concurrent execution through worker threads
- task event history
- cancellation request flag
- derived task telemetry: `step_count`, `event_count`, `duration_ms`

Current task CLI:

- `python arc.py task --json run --file <task.json>`
- `python arc.py task --json start --file <task.json>`
- `python arc.py task --json status <task_id>`
- `python arc.py task --json list`
- `python arc.py task --json cancel <task_id>`

Current task step types:

- `motion_profile`
- `run_path`
- `move_relative`
- `move_to_pose`
- `move_joint_pose`
- `recover_robot`
- `recover_home_robot`
- `stop_robot`
- `delay`

Example capability now verified:

- `Arm1` can draw a circle
- `Arm2` can draw a square
- both can run under one task
- `Arm2` can be delayed by `2000 ms` relative to `Arm1`
- two arms can be given the same motion distance but different motion profiles

## 8. Real hardware tests completed

All items below were tested against the real MG400 setup in this repo config.

### 7.1 Connectivity

Verified:

- both `Arm1` and `Arm2` connect successfully from CLI

### 7.2 Basic motion

Verified:

- `home` works
- relative directional move works
- `square` pattern works

Example confirmed motion response:

- `RelMovL(25.0,0,0,0);`
- `MovL(250,0,50,0);`
- `MovJ(250.0,0.0,50.0,0.0);`

Current motion channel behavior:

- dashboard/status/control commands go to `29999`
- motion commands now go to native motion port `30003`
- feedback/state remains on `30004`
- `run_path(circle)` now prefers a native `Arc + Arc` execution path instead of waypoint stepping

### 7.3 Stop behavior

Verified behavior on this setup:

- `Stop()` returned non-success style response: `-10000,{},Stop();`
- `ResetRobot()` returned success style response: `0,{},ResetRobot();`

Practical conclusion:

- `Stop()` is not currently reliable as the sole stop path on this controller/firmware
- fallback to `ResetRobot()` is required in current code

### 7.4 Feedback reading

Verified:

- live pose/joint/state data can be read from port `30004`
- pose values update during motion
- mode changes between `enabled` and `running` are visible

### 7.5 Immediate fault scenario

Test performed:

- large move request such as `FORWARD 440`

Observed behavior:

- controller accepted command string
- robot entered `error`
- actual pose changed very little

Conclusion:

- this is treated as `immediate_fault`
- this class of fault is usually consistent with invalid motion / out-of-range / safety-block-before-motion

### 7.6 Recovery after immediate fault

Verified:

- `ClearError()` + `EnableRobot()` can clear the tested immediate fault
- robot returned to `enabled` state after recovery

### 7.7 Runtime fault monitoring

Test performed:

- run stepwise square loop
- monitor state continuously
- induce fault during active movement

Observed behavior:

- monitor detected transition to `error`
- incident report was produced
- runtime fault was classified and reported instead of auto-recovering

### 7.8 Multi-arm task execution

Verified:

- inline task execution using `parallel` + `delay`
- background task execution using `task start` + `task status`
- real hardware execution of:
  - `Arm1`: native circle via `Arc + Arc`
  - `Arm2`: rectangle path
  - delayed start for the second arm

Practical conclusion:

- the system can now execute two-arm coordinated tasks
- task state can be read back after launch
- empty or malformed task payloads no longer complete silently as fake-success runs
- this is the current recommended path for coordinated multi-arm work

### 7.9 Speed versus distance semantics

Current intended semantics:

- geometric distance belongs in:
  - `move_relative` deltas
  - `move_to_pose` pose coordinates
  - `run_path` geometry parameters such as `radius`, `width`, `height`
- speed and acceleration belong in:
  - `motion_profile` task steps
  - `python arc.py arm --json motion-profile ...`

This separation matters for ARC behavior:

- "move both arms the same distance with different speeds" must be expressed as:
  - one `motion_profile` per arm
  - one geometry/motion step per arm
- this must not be inferred from `run_path` or `move_relative` parameters alone

## 9. Fault handling policy currently intended

The desired policy is now:

### 8.1 Immediate fault

Definition:

- command sent
- robot does not meaningfully progress
- error happens immediately or near-immediately
- pose delta from intended start context stays small

Recommended handling:

- auto-recover may be allowed
- system can propose or execute recovery if policy permits

### 8.2 Runtime fault

Definition:

- robot already started moving
- pose changed materially before fault
- error happens during execution

Recommended handling:

- stop issuing new commands
- report incident
- wait for explicit decision from operator or higher-level logic

### 8.3 Important design rule

Detection and recovery must remain separate concerns.

Runtime fault monitoring should:

- detect
- classify
- report
- freeze task progression

It should not automatically:

- recover
- home
- retry

unless explicit policy says so.

## 10. Test scripts and their role

### 9.1 `monitor_fault_test.py`

Purpose:

- test runtime fault monitoring
- run one arm in a stepwise square loop
- poll `state` continuously
- produce incident report on fault

Current behavior:

- no intentional auto-recover
- no intentional auto-home
- reports:
  - `fault_type`
  - `last_command`
  - `start_pose`
  - `fault_pose`
  - `max_deviation_mm`
  - `recommended_action`

This script is for testing policy and monitoring behavior.
It is not yet a production task runner.

### 9.2 `dual_arm_stop_test.py`

Purpose:

- test dual-arm loop execution
- test stop behavior while more than one arm is active

Current behavior:

- useful for ad hoc concurrency testing
- not a scheduler
- not a durable task executor

## 11. Current known limitations

These limitations are important for operator expectations and for future ARC work.

### 11.1 Task system is v1, not a daemonized scheduler

The current task layer is real and tested, but it is still:

- single-process per task
- file-backed rather than daemon-backed
- not a queue manager
- not a lease manager
- not a multi-tenant scheduler

Practical effect:

- coordinated two-arm jobs now work
- but there is still no central `arcd` control plane
- overlapping background tasks can still conflict if the operator launches them carelessly

### 11.2 No durable wait-for-condition primitives yet

Current task system supports:

- `delay`

It does not yet support structured:

- `wait_for_state`
- `wait_for_task`
- `wait_for_incident_clear`
- `wait_until_timestamp`

Practical effect:

- timed delays are supported now
- richer conditional orchestration still needs to be added

### 11.3 Circle support depends on the working native fallback

Current status on this controller:

- native `Arc(...)` works
- direct `Circle3(...)` did not execute reliably in testing
- full circle is currently implemented as `2` native arcs

Practical effect:

- circle motion is much better than waypoint stepping
- but the controller-specific circle path still depends on the `arc-pair` implementation, not a proven single native circle primitive

### 11.4 Feedback and connection handling still need hardening

Observed during hardware tests:

- repeated reconnect attempts can occur
- the controller may drop sockets during some command sequences
- feedback snapshots may need multiple packets before they look stable

Practical effect:

- the system works, but connection management is not production-grade yet
- daemonized connection ownership and reconnection policy are still needed

### 11.5 Motion completion semantics are safety-first, not throughput-first

Current design intentionally waits for monitored completion or fault classification.

Practical effect:

- safer behavior for single actions
- lower throughput for dense path sequencing
- without a task runner or queueing model, this can make composite jobs feel slow

### 11.6 No lease or ownership model

There is not yet:

- arm lease
- owner identity
- task ownership
- source-of-command arbitration

Practical effect:

- CLI, ARC, scripts, and future automation do not yet have a deterministic conflict-resolution model

### 11.7 Event history exists, but is not audit-grade yet

Tasks now store step and task events, but there is still not a full audit log for:

- task creation
- task start
- step transitions
- delay steps
- operator approvals
- concurrent arm launches

Practical effect:

- debugging complex multi-arm behavior will become harder as automation grows

## 12. Next architectural priority

The next major system milestone after TaskRunner v1 should be:

- daemonized task control
- lease ownership
- queued task admission
- richer wait conditions
- operator acknowledgments bound to task state
- better inter-task arbitration

This is the minimum needed to support commands like:

- `Arm1 draw a circle while Arm2 draws a square`
- `start Arm2 2 seconds after Arm1`
- `run both arms, but block the whole task if either arm faults`

Purpose:

- test two-arm loop behavior
- test stop timing behavior

This script is also test-only, not production control flow.

## 10. What is integrated vs not integrated

### 10.1 Already integrated into normal CLI/LLM path

- connection status
- live state/pose reads
- directional moves
- home
- stop with `ResetRobot()` fallback
- recover
- recover-home
- LLM access to state/pose/recover

### 10.2 Built but still test-oriented

- `monitor_fault_test.py`
- `dual_arm_stop_test.py`
- runtime fault classification logic as exercised in test script

These are integrated as tools/scripts in the repo, but not yet exposed as a clean production command family.

### 10.3 Not yet integrated into a production command layer

- formal incident lifecycle
- explicit operator decision commands such as:
  - `ack-recover`
  - `ack-recover-home`
  - `ack-abort`
  - `retry-last-step`
- structured persistent fault log
- safe monitored task runner as a normal CLI command
- pre-motion software workspace guardrails
- queueing / leases / scheduler / daemon mode

## 11. Known issues / caveats

### 11.1 Motion path was refactored to avoid hidden recovery

Important change:

- normal motion path should not silently `ClearError()` + `EnableRobot()` before every move

Reason:

- hidden recovery makes fault policy ambiguous
- runtime faults should not appear to self-heal unless explicitly commanded

### 11.2 Stop semantics are controller-dependent

Observed on this setup:

- `Stop()` alone is not dependable
- `ResetRobot()` is the practical stop fallback

This should be treated as current-controller behavior, not a universal MG400 guarantee.

### 11.3 Fault classification still has room to improve

Current runtime fault classification is much better than before because it tracks max deviation and running state.

Still possible future improvements:

- accumulate total traveled distance, not only max deviation from start pose
- keep per-step target and step index in incident detail
- separate collision-like stop from protocol/state errors more explicitly

## 12. Recommended next steps

Highest-value next items:

1. Turn runtime monitoring into a reusable production command, not just a test script
2. Add explicit post-incident decision commands:
   - recover
   - recover-home
   - abort
   - retry-step
3. Add structured JSON incident logging
4. Add pre-motion workspace limits in software
5. Add target-based `safe_move_and_wait`
6. Later: split CLI from daemon/control-plane architecture

## 13. Practical summary

The system is no longer just a raw command sender.

It can now:

- command both MG400 arms from CLI
- read live robot state and pose
- let the LLM inspect state before acting
- detect and classify fault situations
- recover tested immediate faults
- report runtime faults instead of blindly continuing

It is still not a full ARC control plane, but it is now a functional prototype with:

- real motion control
- real feedback
- real fault detection
- basic recovery primitives
- initial fault policy separation
