// แปลง scene_state() จาก Python server → WorkspaceSnapshot ที่ Scene component ใช้
// Python scene_state() shape (จาก sim_world.py):
//   { ok, objects[], held, zones[], sim_mode? }
//
// WorkspaceSnapshot shape (จาก types.ts):
//   { scan: ScanResult | null, state: RobotState | null }

import type {
  ArmState,
  DetectedObject,
  ObjectColor,
  RobotState,
  ScanResult,
  WorkspaceSnapshot,
  WorkspaceZone,
} from "@/components/robot/types"
import type { SceneState, SimObject, SimZone } from "./sim_api"
import { WORKSPACE } from "@/components/robot/geometry"

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

function toObjectColor(raw: string): ObjectColor {
  if (raw === "red" || raw === "green" || raw === "blue") return raw
  return "unknown"
}

/** แปลง SimObject → DetectedObject */
function toDetectedObject(obj: SimObject): DetectedObject {
  return {
    id: obj.id,
    color: toObjectColor(obj.color),
    robot_plane: { x: obj.robot_plane.x, y: obj.robot_plane.y },
    per_arm_robot_plane: obj.per_arm_robot_plane
      ? Object.fromEntries(
          Object.entries(obj.per_arm_robot_plane).map(([arm, pt]) => [
            arm,
            { x: pt.x, y: pt.y },
          ]),
        )
      : undefined,
    reachable_by: obj.reachable_by ?? [],
    preferred_arm: obj.preferred_arm,
    block: obj.block ?? undefined,
    radius_mm: obj.radius_mm,
  }
}

/** แปลง SimZone → WorkspaceZone */
function toWorkspaceZone(z: SimZone): WorkspaceZone {
  return {
    name: z.name,
    role: z.role,
    bounds_mm: {
      x_min: z.bounds_mm.x_min,
      x_max: z.bounds_mm.x_max,
      y_min: z.bounds_mm.y_min,
      y_max: z.bounds_mm.y_max,
    },
  }
}

// --------------------------------------------------------------------------
// Main transform
// --------------------------------------------------------------------------

/** arm-local pose → shared workspace (x,y) สำหรับ zone tint */
function localPoseToWorkspace(armId: string, localX: number, localY: number): {
  x: number
  y: number
} {
  if (armId === "Arm2") {
    // base (695,200) facing −x
    return { x: 695 - localX, y: 200 - localY }
  }
  // Arm1 base (−95,200) facing +x
  return { x: -95 + localX, y: 200 + localY }
}

function classifyZone(armId: string, pose: { x: number; y: number }): {
  zone: ArmState["zone"]
  occupies_shared: boolean
  is_at_standard_park: boolean
} {
  const { x: wx, y: wy } = localPoseToWorkspace(armId, pose.x, pose.y)
  // Shared band ≈ workspace x 200–400
  const occupies_shared = wx >= 200 && wx <= 400 && wy >= 0 && wy <= 400
  // Park: storage ซ้าย y<0, drop ขวา y<0
  const is_at_standard_park =
    wy < -50 && (armId === "Arm1" ? wx < 250 : wx > 350)
  let zone: ArmState["zone"] = "own"
  if (occupies_shared) zone = "shared"
  else if (wx < 0 || wx > 600) zone = "other"
  return { zone, occupies_shared, is_at_standard_park }
}

/**
 * แปลง SceneState จาก Python sim_server → WorkspaceSnapshot ที่ Scene ใช้วาด
 *
 * - data.objects[]  → scan.objects[]
 * - data.arms[]     → state.arms[] (live poses — without this 3D never moves)
 * - data.held       → state.arms[].holding  (ใส่ใน Arm1 เป็น default)
 * - data.zones      → scan.workspace.zones
 * - data.sim_mode   → ไม่แปลงโดยตรง (WorkspacePanel ใช้เอง)
 */
export function transformToSnapshot(data: SceneState): WorkspaceSnapshot {
  if (!data.ok) {
    return { scan: null, state: null }
  }

  const objects = (data.objects ?? []).map(toDetectedObject)
  const zones = (data.zones ?? []).map(toWorkspaceZone)

  const scan: ScanResult = {
    ok: true,
    camera_id: "Sim",
    timestamp: new Date().toISOString(),
    workspace: {
      width_mm: WORKSPACE.width_mm,
      height_mm: WORKSPACE.height_mm,
      zones,
    },
    object_count: objects.length,
    objects,
  }

  const heldId = data.held?.id ?? "unknown"
  // แขนที่ถือ: server held_by ก่อน, ไม่งั้น preferred_arm ของ object
  const heldBy =
    data.held_by ||
    data.held?.preferred_arm ||
    (data.held ? "Arm1" : null)
  const liveArms = data.arms ?? []

  const defaultPose = { x: 350, y: 0, z: 0, r: 0 }

  function buildArm(armId: string): ArmState {
    const live = liveArms.find(a => a.arm_id === armId)
    const pose = live?.pose
      ? {
          x: Number(live.pose.x) || 0,
          y: Number(live.pose.y) || 0,
          z: Number(live.pose.z) || 0,
          r: Number(live.pose.r) || 0,
        }
      : { ...defaultPose }
    const flags = classifyZone(armId, pose)
    const motionRaw = live?.motion
    const motion =
      motionRaw && motionRaw.from && motionRaw.to
        ? {
            from: {
              x: Number(motionRaw.from.x) || 0,
              y: Number(motionRaw.from.y) || 0,
              z: Number(motionRaw.from.z) || 0,
              r: Number(motionRaw.from.r) || 0,
            },
            to: {
              x: Number(motionRaw.to.x) || 0,
              y: Number(motionRaw.to.y) || 0,
              z: Number(motionRaw.to.z) || 0,
              r: Number(motionRaw.to.r) || 0,
            },
            t0_ms: Number(motionRaw.t0_ms) || 0,
            duration_ms: Number(motionRaw.duration_ms) || 0,
            easing: motionRaw.easing,
            speed_mm_s: motionRaw.speed_mm_s,
            server_now_ms: motionRaw.server_now_ms ?? data.server_now_ms,
          }
        : null
    const holding =
      data.held && heldBy === armId && heldId !== "unknown" ? heldId : "unknown"
    return {
      arm_id: armId,
      pose,
      zone: flags.zone,
      occupies_shared: flags.occupies_shared,
      is_active: true,
      is_at_standard_park: flags.is_at_standard_park,
      holding,
      motion,
      running_status: Boolean(live?.running_status),
    }
  }

  const arm1 = buildArm("Arm1")
  const arm2 = buildArm("Arm2")
  const occupied_by = [arm1, arm2]
    .filter(a => a.occupies_shared)
    .map(a => a.arm_id)

  const held_meta =
    data.held && heldBy
      ? {
          id: data.held.id,
          color: toObjectColor(data.held.color),
          held_by: heldBy,
        }
      : null

  const state: RobotState = {
    ok: true,
    arms: [arm1, arm2],
    held_meta,
    shared_zone_occupancy: {
      occupied_by,
      is_clear: occupied_by.length === 0,
    },
  }

  return { scan, state }
}
