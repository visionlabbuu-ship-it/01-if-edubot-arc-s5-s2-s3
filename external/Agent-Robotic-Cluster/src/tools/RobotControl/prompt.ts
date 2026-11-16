export const ROBOT_CONTROL_DESCRIPTION =
  `Control or inspect MG400 robot arms through ARC RobotControl.`

export const ROBOT_CONTROL_SHARED_GUIDANCE = `RobotControl shared guidance:

Non-negotiable Storage routing:
- Never describe or execute Storage work as generic list/status/move/drop/stop steps. Storage means named Slot routing, not a generic destination.
- If the operator says only "Storage", "เก็บ", or "ใส่ช่อง" without LT, LB, RT, RB, a named Slot, or configured color policy, ask which Storage group to use. Do not invent a group or create an executable plan yet.
- "พร้อมกัน", "at the same time", and similar wording always means a sequential two-arm plan. Never claim concurrent arm motion is available.
- Once a group is known, the only valid workflow is: get_runtime_context as needed -> scan loose objects -> plan_task -> validate_plan -> execute_plan. The plan itself must include scan_storage_slots, pick, and atomic place_to_storage_slot for each object. Never substitute generic move/drop for Storage placement.
- Required Thai response pattern for an ambiguous request such as 'เก็บวัตถุสีเขียวทั้งหมดลง Storage พร้อมกัน': first say 'ต้องการให้เก็บในกลุ่มไหน: LT, LB, RT หรือ RB?' Then say 'ระบบจะใช้สองแขนแบบทีละขั้นตอน ไม่เคลื่อนพร้อมกัน.' Do not mention generic list/status/move/place/stop as the execution plan.
- Required response pattern when the group is known, for example LB: say 'ต้องผ่าน plan_task -> validate_plan -> execute_plan.' Then state that the plan contains scan_storage_slots -> pick -> place_to_storage_slot, and generic drop is forbidden.
- Prefer the lean default surface: list_robots, get_runtime_context, get_workspace_layout, list_workspace_zones, list_storage_groups, scan, scan_storage_slots, list_storage_slots, resolve_storage_slot, place_to_storage_slot, plan_calibrated_intent, get_state, move, grip, pick, drop, plan_task, validate_plan, execute_plan, get_plan_status, stop, cancel_plan, recover, and ack_incident.

Default tool surface:
- scan: inspect loose camera objects, color, reachability, and per-arm coordinates. It does not scan Slot occupancy.
- A successful scan is self-contained: answer questions such as "what objects were found?" directly from its objects list. Do not repeat the scan, read a saved output file, or invoke stop, cancel_plan, TaskStop, or any task-stopping tool merely to inspect scan results.
- scan_storage_slots: fresh-scan named Storage slots and return a compact slots list plus color_locations mapping each color to its occupied slots and groups. It never moves hardware. Consume this result directly; do not repeat the scan or probe groups with list_storage_slots when the answer is already present.
- list_storage_slots: read the current Slot Registry without taking a new image.
- resolve_storage_slot: choose an empty Slot for a group/color/arm. It does not reserve or move.
- place_to_storage_slot: resolve and reserve a Slot, place, park, rescan, and succeed only after verification.
- get_runtime_context: read compact arm/workspace/transform readiness and Slot summary without scanning or motion.
- get_state: read robot/workspace state, shared-zone occupancy, parking classification, and holding estimate.
- move: the only normal motion primitive. It requires phase=hover, surface, park, or home and does not change suction.
- grip: the only suction/tool-state primitive. state=on grips; state=off releases. It never moves hardware.
- pick: standard sequence only: move hover -> move surface -> grip on -> move hover.
- drop: standard non-Storage sequence only: move hover -> move surface -> grip off -> move hover. Never release at hover.
- plan_task: build or repair a semantic plan from structured intent. It does not execute.
- validate_plan: reject missing/unsafe steps with structured repair feedback, including incomplete handoffs. It does not execute.
- execute_plan: execute a validated semantic plan step by step with logs, interrupt/status support, and semantic scan verification when present.
- get_plan_status: inspect semantic plan status and events.
- stop, cancel_plan, recover, ack_incident: recovery/safety tools, not normal pick/drop decision tools.

Lean workflow contract:
1. For object work, scan first when object coordinates are not already known.
2. Before every real Storage placement task, use scan_storage_slots once for a fresh capacity view before selecting the first Slot. Read slot identity, occupancy, color, and color_locations directly from that result. list_storage_slots is cached state and must not repeat or replace a successful fresh scan.
3. Use camera object per_arm_robot_plane[arm_id].x/y for the arm that will physically touch the object.
4. Treat per_arm_robot_plane[arm_id].x/y as planar XY only; y is not vertical Z. Let ARC compute surface Z from the runtime surface model.
5. Use get_state before entering a shared zone, after interruption, or before resuming a plan.
6. Separate motion from suction. A correct manual workflow is move hover, move surface, grip, move hover.
7. Prefer pick for pickup and place_to_storage_slot for Storage placement. Generic drop must never target Storage groups, slots, storage_zone, or drop_zone.
8. Always use plan_task -> validate_plan -> execute_plan for every non-trivial workflow, including Storage. Storage plans must use scan_storage_slots and the atomic place_to_storage_slot action; execution must never expand Storage placement into generic move/drop.
9. If validate_plan rejects a plan, explain the specific error and repair it instead of forcing execution.
10. For "all objects" requests whose destination is not Storage, scan first, then call plan_task with intent=plan_all_matching_objects, color, destination, camera_id, and the full scan result or full scan.objects entries. Preserve reachable_by and per_arm_robot_plane; do not rebuild object summaries from memory.
11. Do not claim a multi-object task is complete until every Storage placement was individually verified and a final object scan confirms no requested objects remain.

Calibrated workspace semantics:
- outer_box is the shared pixel-to-millimeter reference frame; it is not a placement destination.
- Named polygons such as A1-A8 are work zones. Use source_zone to constrain which detected objects may be selected.
- Storage Box polygons are physical storage regions. LT, LB, RT, and RB are their canonical routing groups; LT1-RB4 are individual slots.
- Storage labels are metadata. Legacy storage_zone and drop_zone are compatibility aliases only and must not define new Drop semantics.
- Slot occupancy follows empty -> reserved -> verification_pending -> occupied or unknown. A successful low-level release is not verified Storage success.
- Read get_runtime_context before Storage work. Do not proceed when Storage mapping or the selected arm transform is not ready.
- Use plan_calibrated_intent for source-zone, Storage group/slot, arm selection, or handoff decisions. It is read-only and must precede execution of nontrivial calibrated work.
- New motion coordinates must come from per_arm_robot_plane[arm_id]. Never fall back to legacy robot_plane.
- Handoff is a planner capability, not a normal destination. It is allowed only when a calibrated handoff station is ready and the source arm retreats before the receiver enters.- Baseline Storage ownership is fixed for this installation: Arm1 owns LB and RB; Arm2 owns LT and RT. Use this routing before calling any resolve tool. Do not probe the other arm or unrelated groups. Calibration/reachability is a second-stage confirmation; if it contradicts this routing, report a calibration mismatch instead of searching all groups.
- For a handoff into Storage, call plan_task once with intent=handoff, intent_arm=the receiving arm, support_arm=the picking arm, object_id, and the complete matching object from scan under object (or scan_result.objects), plus destination=LT/LB/RT/RB or a named Slot such as RB4. Never copy workspace_xy into object_x/object_y and omit those fields: Python derives the support-arm local XY from per_arm_robot_plane[support_arm]. Do not compose primitive retreat, home, get_state, or drop actions; the semantic planner inserts the verified transfer and atomic Storage placement.
Natural-language routing rules:
- Map explicit quadrants exactly: left-top/ซ้ายบน = LT, left-bottom/ซ้ายล่าง = LB, right-top/ขวาบน = RT, right-bottom/ขวาล่าง = RB.
- Bare left/ซ้าย or right/ขวา is ambiguous because each side has top and bottom groups. Never guess LT versus LB or RT versus RB. Ask the operator, unless an explicit configured color policy resolves the group.
- When the operator asks to store objects with existing objects of the same color, run one fresh scan_storage_slots and read color_locations[color].groups. If exactly one group contains that color, use that group without asking or probing LT/LB/RT/RB individually. If multiple groups contain it, ask which existing group to extend unless configured policy selects one.
- Any request to store, keep, put into a free slot, or place into LT/LB/RT/RB must use place_to_storage_slot after pickup. Never describe or call generic drop for Storage, even when the operator does not say the word Storage.
- place_to_storage_slot is atomic from a held object: it performs resolve, reserve, placement, park, fresh Slot scan, and verification. Do not add manual move or drop steps around it.
- Thai requests such as เก็บของบนโต๊ะ, เก็บให้หมด, or เอาไปใส่ช่อง mean a Storage workflow unless the operator names a different non-Storage destination. Ask for a Storage group or configured policy when none is supplied.
- Storage workflows must enter the semantic planner. A valid Storage plan contains a fresh scan_storage_slots action before the first placement, a successful pick/holding flow, and one atomic place_to_storage_slot action per object.
- validate_plan must reject missing Slot scans, placement without a held object, generic drop to Storage, inactive/offline/transform-not-ready arms, unreachable Slots, and insufficient/unknown capacity.
- execute_plan runs Storage actions sequentially. place_to_storage_slot owns resolve, reserve, placement, park, fresh Slot scan, and verification; stop immediately on any reservation, motion, guardrail, or verification failure.
- The current runtime has no general simultaneous dual-arm collision scheduler. Words such as together, at the same time, พร้อมกัน, or simultaneously do not authorize concurrent arm motion. Use both arms sequentially with only one arm moving at a time, unless a future runtime tool returns an explicitly validated collision-safe concurrent plan.
Safety rules:
- Do not infer that an arm is holding an object from narration or pose alone. Base holding/transfer claims on successful grip and motion results.
- If a result includes guardrail_blocked=true or fault_type=guardrail_blocked, treat it as a planned safety stop. Do not retry the same motion or bypass with low-level motion.
- Runtime faults and guardrail blocks freeze task progression. Report the failed step and wait for explicit operator direction.
- stop, recover, and ack_incident are operator-level actions. Use them only when the user asks or when a safety/recovery workflow requires it.

Developer/lesson mode:
- Legacy low-level tools such as move_to_pose, move_relative, run_task, pick_at_pose, place_at_named, pick_detected_place, and RobotArmTool are hidden from the default surface. They are available only when developer tools are explicitly enabled.`

export const ROBOT_CONTROL_DEFAULT_TOOL_NAMES = [
  'list_robots',
  'get_runtime_context',
  'get_workspace_layout',
  'list_workspace_zones',
  'list_storage_groups',
  'scan',
  'scan_storage_slots',
  'list_storage_slots',
  'resolve_storage_slot',
  'place_to_storage_slot',
  'plan_calibrated_intent',
  'get_state',
  'move',
  'grip',
  'pick',
  'drop',
  'plan_task',
  'validate_plan',
  'execute_plan',
  'get_plan_status',
  'stop',
  'cancel_plan',
  'recover',
  'ack_incident',
] as const

export const LIST_ROBOTS_PROMPT =
  'List configured robot arms and connection status.'
export const SCAN_PROMPT =
  'Inspect loose camera objects, colors, reachability, and per-arm coordinates. This does not scan Storage occupancy.'
export const SCAN_STORAGE_SLOTS_PROMPT =
  'Fresh-scan named Storage slots and return compact slot states plus color_locations mapping colors to occupied slots/groups. Consume this one result directly; never repeat the scan or call list_storage_slots group-by-group when it already answers the question. Never moves hardware and does not return loose objects.'
export const LIST_STORAGE_SLOTS_PROMPT =
  'Read a compact cached Slot Registry without scanning or moving hardware. Do not call after a successful fresh scan merely to rediscover color locations already present in scan_storage_slots.'
export const RESOLVE_STORAGE_SLOT_PROMPT =
  'Dry-resolve an empty reachable Storage Slot. Does not reserve or move hardware.'
export const PLACE_TO_STORAGE_SLOT_PROMPT =
  'Exclusive Storage placement tool for every group or named Slot: from a held object it resolves, reserves, places, parks, fresh-scans, and verifies. Do not add move or drop around it. Success requires verified occupied state.'
export const GET_RUNTIME_CONTEXT_PROMPT =
  'Read compact calibrated readiness and Slot summary. Does not scan or move hardware.'
export const GET_STATE_PROMPT =
  'Read robot and workspace state before motion, recovery, or resume.'
export const MOVE_PROMPT =
  'Move one arm to hover, surface, park, or home. Does not change suction.'
export const GRIP_PROMPT = 'Set suction on or off. Never moves hardware.'
export const PICK_PROMPT =
  'Run hover -> surface -> grip on -> hover for one object.'
export const DROP_PROMPT =
  'Non-Storage release only. Never use for requests mentioning a Storage group, named Slot, free storage slot, or spatial Storage quadrant.'
export const PLAN_TASK_PROMPT =
  'Build or repair a semantic robot plan. Storage plans must include scan_storage_slots before place_to_storage_slot and never use generic drop. Does not execute.'
export const VALIDATE_PLAN_PROMPT =
  'Validate a semantic robot plan, including Storage scan, holding, reachability, capacity, and transactional placement ordering. Does not execute.'
export const EXECUTE_PLAN_PROMPT =
  'Execute a validated semantic plan sequentially. Storage succeeds only after place_to_storage_slot reports verified occupied state.'
export const GET_PLAN_STATUS_PROMPT =
  'Read background plan status and events.'
export const CANCEL_PLAN_PROMPT =
  'Cancel a running background plan. Safety/recovery tool; use only when needed or requested.'
export const STOP_PROMPT =
  'Stop robot motion. Safety/recovery tool; use only when needed or requested.'
export const RECOVER_PROMPT =
  'Recover a robot after an operator decision. Safety/recovery tool; use only when needed or requested.'
export const ACK_INCIDENT_PROMPT =
  'Apply an operator decision to a stored incident. Safety/recovery tool; use only when needed or requested.'
