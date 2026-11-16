// Mock data — สร้างตาม contract เป๊ะ เพื่อ render UI ก่อนต่อ hardware จริง
// ภายหลังต่อ SSE: แทนที่ getSnapshot() ด้วยข้อมูลจริงจาก bridge.tool_result

import { WORKSPACE } from "./geometry"
import type { RobotState, ScanResult, WorkspaceSnapshot } from "./types"

const TIMESTAMP = "2026-06-26T00:00:00Z"

const baseWorkspace = {
  width_mm: WORKSPACE.width_mm,
  height_mm: WORKSPACE.height_mm,
  zones: [
    {
      name: "shared",
      role: "shared",
      bounds_mm: { x_min: 200, x_max: 400, y_min: 0, y_max: 400 },
    },
    {
      name: "storage",
      role: "storage",
      bounds_mm: { x_min: 0, x_max: 200, y_min: -200, y_max: 0 },
    },
    {
      name: "drop",
      role: "drop",
      bounds_mm: { x_min: 400, x_max: 600, y_min: -200, y_max: 0 },
    },
  ],
}

/** scan ว่าง ไม่มีวัตถุ */
const emptyScan: ScanResult = {
  ok: true,
  camera_id: "Cam1",
  timestamp: TIMESTAMP,
  workspace: baseWorkspace,
  object_count: 0,
  objects: [],
}

/** scan มี 3 วัตถุ R/G/B กระจายในบล็อก */
const threeObjectScan: ScanResult = {
  ok: true,
  camera_id: "Cam1",
  timestamp: TIMESTAMP,
  workspace: baseWorkspace,
  object_count: 3,
  objects: [
    {
      id: "obj-red",
      color: "red",
      robot_plane: { x: 500, y: 300 },
      block: "1,3",
      reachable_by: ["Arm2"],
      preferred_arm: "Arm2",
      radius_mm: 18,
    },
    {
      id: "obj-green",
      color: "green",
      robot_plane: { x: 100, y: 120 },
      block: "2,1",
      reachable_by: ["Arm1"],
      preferred_arm: "Arm1",
      radius_mm: 16,
    },
    {
      id: "obj-blue",
      color: "blue",
      robot_plane: { x: 300, y: 200 },
      block: "2,2",
      reachable_by: ["Arm1", "Arm2"],
      preferred_arm: "Arm1",
      radius_mm: 20,
    },
  ],
}

/** ทั้งสองแขนอยู่ home, shared zone โล่ง */
const homeState: RobotState = {
  ok: true,
  arms: [
    {
      arm_id: "Arm1",
      pose: { x: 350, y: 0, z: 0, r: 0 },
      zone: "own",
      occupies_shared: false,
      is_active: true,
      is_at_standard_park: true,
      holding: "unknown",
    },
    {
      arm_id: "Arm2",
      pose: { x: 350, y: 0, z: 0, r: 0 },
      zone: "own",
      occupies_shared: false,
      is_active: true,
      is_at_standard_park: true,
      holding: "unknown",
    },
  ],
  shared_zone_occupancy: { occupied_by: [], is_clear: true },
}

/** Arm1 ยื่นเข้า shared zone ไปหยิบวัตถุกลาง */
const reachingState: RobotState = {
  ok: true,
  arms: [
    {
      arm_id: "Arm1",
      // local x ยื่นไกลขึ้น เอื้อมไปบล็อกกลาง (workspace ~300,200)
      pose: { x: 395, y: 0, z: -40, r: 15 },
      zone: "shared",
      occupies_shared: true,
      is_active: true,
      is_at_standard_park: false,
      holding: "unknown",
    },
    {
      arm_id: "Arm2",
      pose: { x: 350, y: 0, z: 0, r: 0 },
      zone: "own",
      occupies_shared: false,
      is_active: true,
      is_at_standard_park: true,
      holding: "unknown",
    },
  ],
  shared_zone_occupancy: { occupied_by: ["Arm1"], is_clear: false },
}

export interface MockScenario {
  id: string
  label: string
  snapshot: WorkspaceSnapshot
}

export const MOCK_SCENARIOS: MockScenario[] = [
  {
    id: "home",
    label: "พร้อมทำงาน (home)",
    snapshot: { scan: emptyScan, state: homeState },
  },
  {
    id: "three-objects",
    label: "สแกนเจอ 3 วัตถุ",
    snapshot: { scan: threeObjectScan, state: homeState },
  },
  {
    id: "reaching",
    label: "Arm1 เข้า shared zone",
    snapshot: { scan: threeObjectScan, state: reachingState },
  },
]

export function getScenario(id: string): MockScenario {
  return MOCK_SCENARIOS.find(s => s.id === id) ?? MOCK_SCENARIOS[0]
}
