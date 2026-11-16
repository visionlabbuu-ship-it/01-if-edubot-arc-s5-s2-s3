// Geometry constants — source of truth = workspace_layout_reference.md
// shared workspace frame: x=0..600 (ซ้าย→ขวา), y=0..400 (ล่าง→บน), หน่วย mm
// แกน 3D ที่ใช้วาด: X = workspace x, Z = workspace y (ลึก), Y = ขึ้นบน (สูง)

export const WORKSPACE = {
  width_mm: 600, // แกน x
  height_mm: 400, // แกน y (depth)
} as const

/** block 200x200mm, 3 คอลัมน์ x 2 แถว */
export const BLOCK_SIZE_MM = 200

export interface BlockDef {
  name: string
  x_min: number
  x_max: number
  y_min: number
  y_max: number
}

export const BLOCKS: BlockDef[] = [
  { name: "1,1", x_min: 0, x_max: 200, y_min: 200, y_max: 400 },
  { name: "1,2", x_min: 200, x_max: 400, y_min: 200, y_max: 400 },
  { name: "1,3", x_min: 400, x_max: 600, y_min: 200, y_max: 400 },
  { name: "2,1", x_min: 0, x_max: 200, y_min: 0, y_max: 200 },
  { name: "2,2", x_min: 200, x_max: 400, y_min: 0, y_max: 200 },
  { name: "2,3", x_min: 400, x_max: 600, y_min: 0, y_max: 200 },
]

export interface ArmGeometry {
  id: string
  /** ฐานใน shared frame (mm) */
  base: { x: number; y: number }
  /** home ใน shared frame (mm) */
  home: { x: number; y: number }
  /** ทิศที่แขนยื่นเข้า workspace: +1 = ไปทางขวา, -1 = ไปทางซ้าย */
  facing: 1 | -1
}

export const ARMS: ArmGeometry[] = [
  { id: "Arm1", base: { x: -95, y: 200 }, home: { x: 255, y: 200 }, facing: 1 },
  { id: "Arm2", base: { x: 695, y: 200 }, home: { x: 345, y: 200 }, facing: -1 },
]

/** zones นอก/ใน workspace สำหรับวาดพื้นสี */
export interface ZoneDef {
  name: string
  role: string
  x_min: number
  x_max: number
  y_min: number
  y_max: number
  color: string
}

export const ZONES: ZoneDef[] = [
  // shared zone = block กลางทั้งสองแถว (1,2 + 2,2)
  { name: "shared", role: "shared", x_min: 200, x_max: 400, y_min: 0, y_max: 400, color: "#f59e0b" },
  // storage ใต้ block 2,1 (นอก workspace ด้าน y ติดลบ)
  { name: "storage", role: "storage", x_min: 0, x_max: 200, y_min: -200, y_max: 0, color: "#3b82f6" },
  // drop ใต้ block 2,3
  { name: "drop", role: "drop", x_min: 400, x_max: 600, y_min: -200, y_max: 0, color: "#22c55e" },
]

/** MG400 spec */
export const MG400 = {
  reach_mm: 440, // working radius ในระนาบ
  base_size_mm: 190,
  /** ความยาวลิงก์โดยประมาณสำหรับวาด (แบ่ง reach เป็น 2 ลิงก์) */
  upper_arm_mm: 230, // J2 link
  forearm_mm: 210, // J3 link
  base_height_mm: 120, // ความสูงฐานถึงข้อ J2
} as const

/** สีของวัตถุตาม contract */
export const OBJECT_COLORS: Record<string, string> = {
  red: "#ef4444",
  green: "#22c55e",
  blue: "#3b82f6",
  unknown: "#9ca3af",
}

export const DEFAULT_OBJECT_RADIUS_MM = 15

/**
 * แปลงพิกัด local frame ของแขน → shared workspace frame (เฉพาะแกน x)
 * Arm1: workspace_x = -95 + local_x
 * Arm2: workspace_x = 695 - local_x
 * (อ้างอิง workspace_layout_reference.md ข้อ 6)
 */
export function armLocalToShared(arm: ArmGeometry, localX: number, localY: number): { x: number; y: number } {
  const x = arm.base.x + arm.facing * localX
  // แกน y: ฐานอยู่ที่ y=200, local y เลื่อนตามแนวขวางของ workspace
  const y = arm.base.y + arm.facing * localY
  return { x, y }
}

/** shared workspace → arm-local (กลับจาก armLocalToShared — ใช้ตอน drag-drop บันทึก) */
export function armSharedToLocal(arm: ArmGeometry, sharedX: number, sharedY: number): { x: number; y: number } {
  const x = arm.facing * (sharedX - arm.base.x)
  const y = arm.facing * (sharedY - arm.base.y)
  return { x, y }
}
