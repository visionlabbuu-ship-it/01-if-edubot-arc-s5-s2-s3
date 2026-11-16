# ARC Canonical System Definition

Updated: 2026-06-05

This is the canonical document for the current ARC direction. Older documents may still be useful as history, but this file is the source of truth for product direction, runtime architecture, implemented safety behavior, agent behavior, and the current learning UI status.

## Current Snapshot

ARC is now a working prototype, not only a concept document. The current system has three active layers:

- root Python robot runtime in `PATH_TO_IF_EDUBOT_ARC`
- ARC TypeScript agent and robot tool layer in `external/Agent-Robotic-Cluster`
- React/Vite web learning chat in `external/Agent-Robotic-Cluster/web`

What is currently working:

- Python runtime owns robot hardware access, camera mapping, safety guardrails, task execution, semantic planning, incidents, and surface-Z compensation.
- TypeScript ARC agent prompt/persona has been refocused around a concise robotics lab assistant, Teaching Mode, plan-before-execute behavior, and rejection handling.
- Default robot tool surface is now lean workflow tooling, not the full low-level tool set.
- Web chat exists as a learning-oriented UI with Thai copy, suggested topic cards, approval/choice cards, streaming responses, and a prompt input sized to the chat width.
- Web typecheck passes with `bun run typecheck` as of this update.

What is partially working:

- The web UI has a reasoning component and can route explicit `<think>...</think>` content into a collapsible reasoning area.
- Reasoning stream hygiene is not solved end to end. If the model streams unmarked reasoning or preamble as ordinary assistant text, the current frontend can still show it. The correct fix is a stream contract and buffer gate that withholds user-visible answer text until a final-answer marker is detected, not late visual trimming.
- Thai learning copy exists in the web UI, but the UI still needs real browser testing against actual streamed model output.

Immediate active product risk:

- The learning chat must not expose hidden reasoning as the final answer. This is the next high-priority web/bridge fix before judging the teaching UX.

## Positioning

ARC means Agent Robotics Cluster.

ARC is an agentic AI layer for turning existing educational robot labs into interactive robotics learning systems. The first target environment is a two-arm Dobot MG400 cluster, but the architecture is intended to expand through robot profiles rather than being locked to one robot model.

ARC is not just a chatbot and not just a robot controller. It is the layer that turns natural language into a plan, checks robot constraints, translates the plan into robot-specific commands, executes through the runtime, and explains what happened.

Canonical flow:

```text
Natural Language Command
-> Intent Understanding
-> Task Planning
-> Safety / Constraint Check
-> Robot Profile Translation
-> Robot Execution
-> Feedback / Explanation
```

## Product Focus

The primary customer segment is education:

- schools, universities, STEM labs, robotics labs, automation labs, and smart factory learning labs
- institutions that already own robot arms but do not use them to full learning value
- teachers who need a safer, more explainable way to demonstrate robotics concepts
- students who should learn workspace, reachability, task decomposition, sequencing, and multi-robot cooperation from real robot behavior

The product thesis is a top-up software layer: ARC adds planning, safety checks, and explanation to robots already in the lab.

## Current Runtime Boundary

The current system is split across three working areas:

- `PATH_TO_IF_EDUBOT_ARC`: Python robot runtime, hardware access, guardrails, camera mapping, semantic plans, task execution, and incident handling
- `external/Agent-Robotic-Cluster`: TypeScript ARC agent, prompt/persona, CLI/tool registration, and RobotControl tool wrappers
- `external/Agent-Robotic-Cluster/web`: React/Vite learning chat UI connected to a local bridge

The TypeScript robot tools call the Python CLI with `ARC_ROBOT_WORKSPACE` or workspace discovery. The Python runtime remains the source of truth for hardware behavior and guardrails.

The web app uses `VITE_ARC_BRIDGE_URL`, defaulting to `http://127.0.0.1:9797`, and sends streaming chat requests to `/v1/chat/completions`. Tool approvals are returned through `/v1/bridge/approvals/:id`.

## Geometry Source Of Truth

Runtime geometry must follow the calibrated code in the machine, not the early example constants.

The canonical source for global arm geometry is:

- `ARM_FRAME_TRANSFORMS` in root `config.py`

Current active transform:

```python
ARM_FRAME_TRANSFORMS = {
    "Arm1": {"a": 1.0, "b": 0.0, "tx": 0.0, "c": 0.0, "d": 1.0, "ty": 0.0},
    "Arm2": {"a": -1.0, "b": 0.0, "tx": 790.0, "c": 0.0, "d": -1.0, "ty": 0.0, "verified": False},
}
```

Safety config still uses MG400 reach limits:

- max arm length: `440 mm`
- minimum reach: `130 mm`

The shared zone is derived from runtime transforms by default. With the current `Arm2.tx = 790.0`, the shared X zone is:

```text
start_x = 790 - 440 = 350
end_x = 440
shared zone = X 350..440
```

The earlier 600 mm spacing example produced `160..440`; that is now treated as concept math only, not current machine geometry.

## Camera Calibration Canon

The current camera calibration is arm-specific. The runtime source of truth is:

- `CAMERA_CONFIG["Cam1"]` in root `config.py`
- `camera_controller.py`
- calibration session: `runtime_state/calibration_session_20260526_141910.json`
- calibration report: `runtime_state/calibration_session_20260526_141910_report.json`

`CAMERA_CONFIG["Cam1"]["calibration"]` remains the primary Arm1 calibration for backward compatibility. New runtime object targeting must use `per_arm_robot_plane[arm_id]`, not the top-level `robot_plane` field.

Current Arm1 camera-to-robot calibration:

```python
camera_points = [
    [1156.0, 529.0],
    [2222.0, 446.0],
    [2200.0, 820.0],
    [1672.0, 998.0],
]
robot_points = [
    [255.087890625, -158.87002563476562],
    [226.88121032714844, 168.60813903808594],
    [343.013427734375, 161.34739685058594],
    [396.6991882324219, 3.6865625381469727],
]
```

Current Arm2 camera-to-robot calibration:

```python
camera_points = [
    [1122.0, 1552.0],
    [1133.0, 1161.0],
    [2217.0, 1519.0],
    [1672.0, 998.0],
]
robot_points = [
    [222.18836975097656, 175.17343139648438],
    [341.513671875, 170.22409057617188],
    [230.45314025878906, -164.18984985351562],
    [390.9952392578125, 2.0249946117401123],
]
```

Runtime behavior:

- `camera_scan` returns `robot_plane` as the primary Arm1-compatible plane.
- `camera_scan` returns `per_arm_robot_plane["Arm1"]` and `per_arm_robot_plane["Arm2"]` from each arm's own calibrated matrix.
- The old Arm2 frame-transform conversion is only a fallback if `arm_calibrations` is unavailable.
- `per_arm_robot_plane[arm_id]["calibration"] == "arm_specific"` is the expected marker after a healthy calibrated scan.
- `per_arm_robot_plane[arm_id].x/y` are planar robot-frame XY coordinates only. The `.y` value is not vertical Z; a value like `-154.8` in `.y` is lateral position, not pickup height.

Calibration result from the May 26 session:

- old Arm1 config error: mean `6.08 mm`, max `7.27 mm`
- old Arm2 transformed error: mean `8.61 mm`, max `10.34 mm`
- new four-point per-arm calibration fit error at taught points: `0.0 mm`

## Object Surface Z Canon

Object pickup/drop surface Z for camera-detected object poses is not a single global constant for both arms.

Arm1 currently uses a constant object surface:

```python
ARM_PICK_CONFIG["Arm1"]["pick_z"] = -152.0
```

For Arm1, every camera-detected object pose that reaches `phase="surface"` without an explicit Z uses this constant `-152.0` pickup/drop surface. Do not copy `per_arm_robot_plane[arm_id].y` into Z.

Arm2 uses an XY surface plane fitted from taught contact poses:

```python
ARM_PICK_CONFIG["Arm2"]["pick_z"] = -152.48  # fallback only
ARM_PICK_CONFIG["Arm2"]["surface_z_plane"] = {
    "a": 0.00979012,
    "b": 0.00109043,
    "c": -154.17717827,
    "offset": 0.0,
    "source": "calibration_session_20260526_141910",
}
```

The canonical Arm2 object surface equation is:

```text
surface_z = 0.00979012*x + 0.00109043*y - 154.17717827
```

No extra offset is currently applied. If future lab testing shows the suction cup needs a softer or firmer contact, change `offset`, not the fitted coefficients.

The helper source of truth is `surface_model.py`:

- `surface_z_for_pose(arm_id, x, y)`
- `phase_z_from_pick_config(arm_id, phase, x=None, y=None)`

Runtime consumers that must stay aligned with this model:

- `fleet_controller.py`: `move_to_work_pose(... phase="surface")`, `pick_at`, `place_at`
- `workspace_model.py`: pick/place and handoff static reach checks
- `semantic_plan.py`: semantic move validation for pose targets at `phase="surface"`

Named target surfaces are separate from camera-detected object surfaces:

- `move_to_named_pose(... phase="surface")` uses the named target's `place_z`
- `handoff_zone`, `drop_zone`, and `storage_zone` are configured named targets, not object surface plane samples
- hover Z remains configured per arm and does not use the surface plane

Reasoning from taught contact data:

- Arm1 taught object contact Z span: about `0.63 mm`
- Arm2 taught object contact Z span: about `1.93 mm`
- Arm2 therefore uses an XY plane for object surface contact, while Arm1 currently remains constant

## Implemented Safety Guardrails

Implemented in the Python runtime:

- `robot_guardrails.py`
- `fleet_controller.py`
- `task_system.py`
- `semantic_plan.py`
- `arc.py`

Guardrail behavior:

- Dynamic Z reach: rejects poses where Z depth reduces safe horizontal reach below the target radius.
- Minimum reach: rejects poses too close to the robot base.
- Shared-zone collision: rejects a target entering the shared zone if another robot is already in the shared zone.
- Conservative unavailable-pose behavior: runtime collision checks block if a target enters the shared zone and the other arm pose cannot be read.
- Parallel shared-zone preflight: rejects parallel task groups that would place multiple arms in the shared zone at the same time.
- Native motion restriction: unparseable native motion commands are blocked because they cannot be safety-checked.
- Semantic plan validation rejects incomplete or unsafe plan structure before execution.

Guardrail block payloads use:

```json
{
  "ok": false,
  "guardrail_blocked": true,
  "fault_type": "guardrail_blocked",
  "recommended_action": "wait_or_replan"
}
```

Agents must treat `guardrail_blocked` as a planned safety stop, not as a normal runtime failure.

## Agent Persona And Planning Logic

ARC's current agent behavior is intentionally narrower than a generic assistant.

Default persona:

- concise robotics lab assistant
- teaches from the live task, plan, validation result, or robot output
- avoids lecture-style background unless the user asks
- avoids repeated questions and repeated permission requests
- never invents robot capabilities, motion success, object ownership, or recovery success

Core operational flow:

```text
Intent
-> inspect state or camera when needed
-> choose arm and target
-> plan or select the smallest safe workflow
-> validate or rely on runtime guardrails
-> execute only through implemented robot tools
-> explain the result from tool output
```

Teaching Mode flow:

```text
Learning Objective
-> Demonstration Design
-> Prediction or Choice
-> Safe Robot Plan
-> Execution or Stop
-> Explanation from Result
```

Teaching rules:

- Separate the Teaching Plan from the Robot Task Plan.
- Teach one main concept per round.
- Ask at most one learner-facing question, and only when it has teaching value or affects safety.
- If a plan cannot run, explain the robotics concept behind the stop, such as reachability, shared-zone timing, robot readiness, or a guardrail.
- Do not turn every answer into a lesson. Add teaching only when the user is learning or the current robot behavior needs explanation.

Rejection/approval behavior:

- If the user rejects a tool call or approval request, do not retry or repackage the same request.
- Do not ask a broad "why did you reject this?" question.
- If a clear safe alternative exists, take it.
- Otherwise acknowledge the block once in one short sentence and wait for the user's next instruction.

## Robot Tool Contract Surface

The current default agent-facing robot surface is the lean `RobotControl` workflow set. Low-level tools still exist but are hidden from the default surface unless developer tools are enabled.

Default lean tools:

- `list_robots`: list available robots
- `scan`: inspect camera objects, color, block, reachability, and per-arm coordinates; never moves hardware
- `get_state`: read robot/workspace state, shared-zone occupancy, parking classification, and holding estimate
- `move`: normal motion primitive; requires `phase=hover`, `surface`, `park`, or `home`; does not change suction
- `grip`: suction/tool-state primitive; never moves hardware
- `pick`: standard sequence only, `move hover -> move surface -> grip on -> move hover`
- `drop`: standard sequence only, `move hover -> move surface -> grip off -> move hover`
- `plan_task`: build or repair a semantic plan from structured intent; does not execute
- `validate_plan`: reject missing or unsafe steps with structured repair feedback; does not execute
- `execute_plan`: execute a validated semantic plan step by step with logs, interrupt/status support, and semantic scan verification when present
- `get_plan_status`: inspect semantic plan status and events
- `stop`, `cancel_plan`, `recover`, `ack_incident`: safety/recovery tools, not normal decision tools

Lean workflow contract:

- For object work, scan first when object coordinates are not already known.
- Use camera object `per_arm_robot_plane[arm_id].x/y` for the arm that will physically touch the object.
- Treat `per_arm_robot_plane[arm_id].x/y` as planar XY only; `.y` is not vertical Z. Let ARC compute surface Z from the runtime surface model.
- Use `get_state` before entering a shared zone, after interruption, or before resuming a plan.
- Separate motion from suction. A correct manual workflow is `move hover`, `move surface`, `grip`, `move hover`.
- Prefer `pick` and `drop` for simple one-arm workflows because they expand to the correct sequence.
- Use `plan_task -> validate_plan -> execute_plan` for multi-step, zone, or handoff workflows.
- If `validate_plan` rejects a plan, explain the specific error and repair the plan instead of forcing execution.
- For "all objects" requests, scan first, then call `plan_task` with `intent=plan_all_matching_objects`, color, destination, camera id, and the full scan result or full scan object entries. Preserve `reachable_by` and `per_arm_robot_plane`; do not rebuild object summaries from memory.
- Do not claim a multi-object planned task is complete unless the final scan-absence verification in `execute_plan` passed.

Developer/lesson mode tools:

`ARC_ROBOT_TOOL_MODE=developer` or `ARC_ROBOT_DEVELOPER_TOOLS` exposes the broader tool set:

- workspace contract: `get_workspace_layout`, `get_workspace_state`, `plan_handoff`
- explicit pose motion: `move_to_pose`, `move_joint_to_pose`, `move_relative`, `move_to_named_target`
- atomic station motion: `move_to_work_pose`, `move_to_named_pose`, `set_tool_state`
- read-only safety checks: `preflight_pose`, `preflight_task`
- perception: `camera_status`, `camera_scan`, `camera_assign_targets`
- low-level grasp workflows: `pick_detected_place`, `pick_all_detected_place`, `pick_at_pose`, `place_at_named`, `place_at_pose`
- paths and tasks: `run_path`, `run_task`, `get_task_status`, `cancel_task`
- incidents and operator actions: `get_robot_incident`, `stop_robot`, `recover_robot`, `recover_home_robot`
- legacy fallback: `RobotArmTool`

Developer tool limits:

- `run_task` supports motion/task primitives, not camera scans or grasp actions inside `task_json`.
- `run_path` supports `rectangle`, `circle`, `arc`, and `waypoints`.
- `preflight_task` is static. It checks reach and planned parallel shared-zone conflicts, but live execution still performs mandatory guardrails before each hardware motion.
- `preflight_pose` only checks live shared-zone occupancy when `check_collision=true`.
- `RobotArmTool` is a legacy fallback for basic list/status/state/pose/home/move/square/stop/recover actions only.

## Preflight Interfaces

Python CLI:

```powershell
python arc.py arm --json preflight-pose Arm1 --x 250 --y 0 --z 0 --r 0 --skip-collision
python arc.py task --json preflight --task-json "{...}"
```

Agent tools:

- `preflight_pose`
- `preflight_task`

Current interpretation:

- These preflight tools still exist and remain useful for developer/lesson inspection.
- The default lean workflow should usually use `validate_plan` for semantic plans and rely on Python runtime guardrails during execution.
- `preflight_task` is static and does not move hardware.
- `preflight_pose` defaults to static reach-only checks from the agent tool. It can request live collision checking when `check_collision=true`.

## Handoff Contract

Handoff is station-mediated through `handoff_zone`; direct gripper-to-gripper transfer is not supported.

Default handoff path:

```text
scan
-> plan_task with handoff intent or requested-arm workflow
-> validate_plan
-> execute_plan
-> get_plan_status / scan verification when needed
```

Developer/lesson inspection can use `plan_handoff` directly. It is read-only and returns an ordered atomic execution contract. It does not move hardware.

Canonical station-mediated handoff flow:

```text
User requests an intent arm, e.g. Arm2
-> scan object
-> if target is not reachable by requested arm, choose reachable support arm
-> plan handoff with intent arm, support arm, and object pose in support-arm local frame
-> park receiver/intent arm outside shared zone
-> support arm picks object with explicit safe sequence
-> support arm moves to handoff_zone
-> support arm lowers to surface and releases
-> support arm retreats out of handoff_zone
-> receiver/intent arm enters handoff_zone
-> receiver picks from handoff_zone
-> receiver completes original goal, such as placing at drop_zone
```

Standard non-shared park poses for handoff are:

- `Arm1 -> storage_zone`
- `Arm2 -> drop_zone`

Do not use `home` as a handoff park pose in the current setup because configured home is in the shared zone.

During developer-level handoff, these tools are forbidden because they hide stacked motion and suction behavior:

- `pick_detected_place`
- `pick_all_detected_place`
- `pick_at_pose`
- `place_at_named`
- `place_at_pose`

The allowed developer-level handoff execution tools are:

- `move_to_work_pose`
- `move_to_named_pose`
- `set_tool_state`
- `get_workspace_state`

The agent must not claim that an arm is holding or has transferred an object from narration or pose alone. That claim must be grounded in successful tool-state and motion/checkpoint steps.

## Web Learning UI Status

Current web stack:

- Vite + React 19 + TypeScript
- Tailwind CSS 4
- shadcn-style AI components
- `streamdown` for markdown streaming
- local bridge default: `http://127.0.0.1:9797`

Current implemented UI behavior:

- Thai-first learning copy in header, empty state, placeholder, topic cards, approval labels, and status labels.
- Two suggested topic banners appear in the empty chat state.
- Prompt input is full width relative to the chat container.
- Low-value metadata such as bridge/model/session identifiers is hidden from the learning surface.
- Conversation area is scrollable with a scroll-to-bottom button.
- Tool approvals and choice questions are rendered as structured cards.
- A shadcn-style `Reasoning` component exists at `web/src/components/ai/reasoning.tsx`.

Known web UI issue:

- Current stream routing only reliably hides reasoning when the model emits explicit `<think>...</think>` tags.
- If the model emits reasoning without tags, the UI cannot know which streamed text is reasoning and which text is final answer.
- The needed fix is a buffer gate: collect assistant stream text until a final-answer marker such as `คำตอบสุดท้าย:` or `Final:` appears, route text before the marker to hidden reasoning/internal buffer, and stream only text after the marker to the visible answer.
- A finalize step should sanitize fallback output if the model ignores the final-answer contract.

## Implemented ARC Capabilities

Current implemented capabilities:

- robot listing, status, state, and pose inspection
- canonical workspace layout inspection in developer mode
- live workspace/shared-zone state classification
- lean robot workflow tools for scan, move, grip, pick, drop, plan, validate, execute, status, stop, cancel, recover, and incident acknowledgement
- semantic plan builder and validator for pick/drop, handoff, move, and `plan_all_matching_objects`
- semantic plan execution with step logs and plan status
- first-class read-only handoff planning in developer/lesson mode
- explicit motion: named target, absolute pose, relative move, joint pose
- camera scan and per-arm robot-plane mapping
- arm-specific camera calibration for `per_arm_robot_plane`
- Arm2 XY-plane object surface Z compensation for camera-detected object poses
- color/object assignment for detected objects
- composite pick/place flows
- primitive pick/place flows for explicit control
- parameterized paths and waypoint paths
- multi-step task runner with sequence and parallel control nodes
- task state persistence and background worker support
- incidents and acknowledgement flow
- safety middleware before robot motion
- read-only safety preflight tools for pose and task checks
- handoff contract that preserves requested intent arm identity and decomposes handoff into visible atomic steps
- agent prompt rules for Teaching Mode, plan-before-execute, rejection behavior, guardrail behavior, and concise explanation
- agent-facing tool contracts aligned with the current Python runtime for JSON output, explicit MovJ pose motion, arc path execution, and semantic plans
- web learning chat shell with Thai copy, topic suggestions, approval/choice UI, and partial reasoning UI

## Current Learning Value

ARC can already demonstrate these learning concepts:

- robot workspace and coordinate frames
- per-arm reachability
- why Z depth changes horizontal reach
- why shared workspaces need sequencing
- task decomposition into plan steps
- difference between motion-only requests and grasp workflows
- why motion and suction should be separate primitives
- how camera perception maps to robot-local coordinates
- why a planned safety stop is different from a hardware fault
- how an unreachable requested-arm goal can be decomposed into support-arm pickup, station handoff, receiver pickup, and final placement
- how `validate_plan` turns a vague or unsafe request into a repairable learning moment

## Current Verification Status

Verified in this update:

- `external/Agent-Robotic-Cluster/web`: `bun run typecheck` passes.

Known but not revalidated in this update:

- Python runtime unit tests exist for guardrails, workspace model, and semantic plan behavior.
- Latest hardware end-to-end behavior still needs controlled lab validation.
- Full inherited TypeScript CLI repo typecheck is not treated as clean unless separately re-run and fixed.

Manual tests still needed:

- Web browser test of Thai layout on desktop and mobile widths.
- Web streaming test with explicit `<think>` tags.
- Web streaming test with unmarked reasoning text to confirm the future buffer gate hides it.
- Bridge approval/rejection test to ensure rejected actions are not retried.
- Teaching-mode prompt tests for concise answer shape and one-question behavior.

## Missing Or Incomplete

The system is not finished. Important gaps:

- web reasoning stream gate: current UI may still show unmarked reasoning during streaming
- trajectory sweep collision: current checks focus on target poses and planned waypoints, not the full swept path between poses
- shared-zone reservation/lock: there is no runtime lease system that reserves the shared zone while one arm is using it
- authoritative object/holding state: ARC exposes pose-based workspace state and holding estimates, but it still does not maintain a fully authoritative inventory of which object is attached to which gripper
- bulk workflows: `plan_all_matching_objects` exists, but robust bulk handoff execution, retry, verification, and lab validation are still incomplete
- curriculum modules: educational lessons, exercises, teacher scripts, and assessment content are not implemented as durable modules
- explanation reports: tool outputs are structured, but learner-facing explanation reports are still mostly prompt-driven
- robot profile abstraction: MG400 works first; a clean profile package for other robots is still future work
- full hardware end-to-end validation: the newest semantic plan, guardrail, and web-bridge changes still need controlled lab validation
- web UX polish: the learning UI exists, but streaming behavior, visual rhythm, mobile layout, and final-answer cleanliness still need testing and iteration
- documentation cleanup: old inherited docs still exist; this file is canonical, but historical docs have not all been pruned

## Agent Operating Rules

When operating robots through ARC:

- plan before execute
- use the lean RobotControl tools by default
- prefer `scan`, `get_state`, `plan_task`, `validate_plan`, and `execute_plan` for multi-step workflows
- use developer tools only when developer/lesson mode is explicitly enabled or the task requires low-level inspection
- never bypass a guardrail block with native motion
- do not retry the same blocked motion without changing the plan
- do not recover, home, continue, or acknowledge incidents unless the operator explicitly asks or the safety workflow clearly requires it
- keep geometry and speed separate
- use camera `per_arm_robot_plane[arm_id]` when moving an arm to a camera-detected object
- never target Arm2 from top-level `robot_plane`; use `per_arm_robot_plane["Arm2"]`
- for camera-detected object surface moves, let ARC compute surface Z from the runtime surface model instead of supplying a stale constant Z
- treat `ARM_PICK_CONFIG["Arm2"]["pick_z"]` as fallback; the canonical Arm2 object surface is `surface_z_plane`
- report guardrail reasons and recommended actions clearly
- if the user rejects an action, do not ask repeatedly; wait or choose a genuinely different safe plan

When teaching through ARC:

- separate Teaching Plan from Robot Task Plan
- use the flow `Learning Objective -> Demonstration Design -> Prediction or Choice -> Safe Robot Plan -> Execution or Stop -> Explanation from Result`
- ask at most one focused learner question
- keep answers concise and grounded in the current task, robot state, validation result, or tool output
- do not expose hidden reasoning or internal scratchpad as user-facing teaching text

## Status Summary Against ARC_Concept.md

Achieved:

- natural language agent layer connected to robot tools
- MG400 two-arm runtime integration
- camera-aware object detection and per-arm coordinate mapping
- sequence/parallel task planning substrate
- semantic planning, validation, and execution surface
- safety/constraint middleware before robot execution
- dynamic reach and shared-zone guardrails
- read-only preflight checks
- structured incident and safety feedback
- educational positioning reflected in prompts, output style, and canonical docs
- Teaching Mode planning logic added to the system prompt
- rejection behavior added to reduce repeated permission asks
- lean RobotControl default tool surface
- web learning chat shell with Thai copy and suggested topics
- partial reasoning UI component

Still needed:

- robust hidden-reasoning stream gate for the web UI
- robust persistent multi-robot world state with object ownership and gripper attachment tracking
- curriculum and teacher/student-facing learning flows
- richer explanation layer grounded in task plans and guardrail math
- trajectory-level collision checking
- shared-zone reservation during execution
- automatic bulk handoff execution with verification after each item
- additional robot profiles beyond the current MG400 runtime
- lab validation and demo scripts for the latest guardrails, semantic plans, and web bridge behavior

## Calibrated Storage Slot Tool Contract (2026-08-09)

Storage planning uses calibrated groups LT, LB, RT, and RB and named Slots such as LT1. storage_zone and drop_zone remain compatibility aliases only; they are not new Storage groups.

Canonical perception and execution:

1. scan detects loose workspace objects.
2. scan_storage_slots refreshes Slot occupancy and colors.
3. resolve_storage_slot performs read-only selection and reachability validation.
4. place_to_storage_slot owns reservation, placement, park, fresh capacity verification, and final Registry state.
5. Placement success requires verified occupied; inconclusive results become unknown and fail.
6. Motion XY comes only from per_arm_robot_plane[robot_id], never legacy top-level robot_plane.
7. Generic drop must reject Storage groups, Slots, and legacy Storage aliases.

The Python runtime is the source of truth for Slot coordinates, reachability, reservation, and verification. TypeScript tools must not recompute these values.