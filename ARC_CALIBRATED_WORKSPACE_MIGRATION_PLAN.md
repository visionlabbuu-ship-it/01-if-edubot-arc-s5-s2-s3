# ARC Calibrated Workspace Migration Plan

Date: 2026-08-04  
Status: Implementation specification

## Current State

ARC still plans around three static names in `workspace_model.py`: `storage_zone`,
`drop_zone`, and `handoff_zone`. Static workspace blocks and synthetic `:auto`
slots are exposed even though Calibration already records an `outer_box`, multiple
calibrated zones, four Storage regions, and the Storage Registry has real slots.

This produces four mismatches:

- calibrated A1-A8 polygons cannot reliably constrain object selection;
- Storage groups LT/LB/RT/RB and slots LT1-RB4 are not the planner's source of truth;
- arm selection is inferred from legacy zone names instead of per-arm reachability;
- handoff is treated as a fixed destination instead of a conditional plan step.

## Target State

The runtime uses one canonical calibrated model:

- `outer_box` defines the pixel-to-workspace reference frame;
- `work_zones` are named polygons such as A1-A8 and may be used as command sources
  or destinations;
- `storage_regions` are calibrated physical polygons assigned to LT/LB/RT/RB;
- `storage_slots` come from `capacity_slots.json` and carry `workspace_xy`,
  `storage_region_id`, per-arm coordinates, reachability, occupancy, and reservation;
- Home/park targets stay separate from Storage placement destinations;
- `storage_zone` and `drop_zone` remain compatibility aliases only;
- a direct plan is preferred when one arm can reach source and destination;
- a station-mediated handoff is planned only when different arms are required.

New motion must use `per_arm_robot_plane[arm_id]`. Legacy `robot_plane` is
reporting compatibility only.

## Implementation Work

### Canonical model and calibration

- Normalize legacy `custom` calibrated zones to `work_zone` without changing their
  IDs, labels, or polygons.
- Split work zones from Storage regions and validate unique LT/LB/RT/RB assignment.
- Load real Registry slots; do not synthesize zone slots.
- Publish readiness independently for workspace, work zones, Storage regions,
  Registry, each arm transform, and handoff station.
- Preserve the other arm profile during calibration updates and preserve slot
  identity across rescans.

### Perception and routing

- Enrich detected objects with `workspace_xy`, containing work-zone IDs,
  `per_arm_robot_plane`, `reachable_by`, and transform status.
- Resolve source filters before motion so a request for A1 cannot select an object
  from A2 or outside the outer box.
- Resolve Storage labels, groups, and slots through the Registry. Selection inside
  a group is deterministic and validates occupancy, reservation, transform, and
  reachability.

### Planning and execution

- Parse intent into source, object filter, destination, optional robot constraint,
  and required result.
- Choose an arm from active/online state, source and destination reachability,
  guardrail safety, distance, and step count. Ties are deterministic.
- Build handoff only when no safe direct arm exists. The source arm must release and
  retreat before the receiving arm enters the shared station.
- Reserve a Storage slot atomically before motion. Placement follows
  `empty -> reserved -> verification_pending -> occupied`; inconclusive verification
  becomes `unknown` and placement is not reported successful.
- Invalidate stale guardrail pose data after reconnect/recovery and fail closed when
  current pose is unavailable.

### ARC contracts

Expose consistent runtime and AI contracts for listing/resolving work zones,
Storage groups and slots, planning intent, placing to a slot, and verifying a slot.
Tool descriptions must define LT/LB/RT/RB as Storage groups and must not describe
`drop_zone` as a new physical Drop area.

## Test Conditions

### Offline model tests

- Outer box, A1-A8, four Storage regions, and 16 Registry slots load from fixtures.
- Polygon containment filters objects correctly, including boundary and outside cases.
- Rescan preserves LT/LB/RT/RB and slot identities.
- Per-arm coordinates differ by transform; missing transform is not reported as
  unreachable.
- Compatibility aliases resolve into the new model without reintroducing fake slots.

### Planner tests

- One capable arm produces a direct plan.
- Split source/destination reachability produces an ordered handoff plan.
- Missing handoff calibration returns `handoff_unavailable` without motion.
- Explicit unreachable arm, offline/inactive arm, full/unknown capacity, and guardrail
  blocks return distinct structured errors.
- Equal candidate plans produce the same arm and slot on repeated runs.

### Storage and safety tests

- Concurrent tasks cannot reserve the same slot.
- A successful motion alone never marks a slot occupied.
- Position/color verification commits occupied; inconclusive verification commits
  unknown; motion failure releases reservation according to task policy.
- Recovery invalidates stale pose cache, unknown pose fails closed, and the receiving
  arm cannot enter handoff before the source arm retreats.

### Contract and integration tests

- Python runtime, service endpoints, CLI, AI schema, and TypeScript ARC tools expose
  matching names and parameters.
- Planner dry-run performs no robot motion.
- Hardware rollout is sequential: Arm1 smoke test, Arm2 smoke test, one verified
  Storage placement, one verified retrieval, handoff test, then dual-arm operation.

## Definition of Done

ARC can accept commands referencing calibrated work zones and Storage groups/slots,
select or coordinate arms from calibrated capability, execute only guardrail-valid
plans, and verify Storage state after physical action. Static three-zone behavior is
limited to compatibility aliases and no longer drives new planning or motion.
