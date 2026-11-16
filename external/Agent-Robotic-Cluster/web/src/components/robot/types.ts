// Robot contract → TypeScript types
// อ้างอิงจาก robot_contract.md (ScanResult, RobotState)
// ใช้ร่วมกันทั้ง mock data และข้อมูลจริงจาก SSE ในอนาคต

export type ObjectColor = "red" | "green" | "blue" | "unknown"

export interface Point2D {
  x: number
  y: number
}

export interface DetectedObject {
  id: string
  color: ObjectColor
  /** พิกัดในระนาบหุ่น (shared workspace frame) หน่วย mm */
  robot_plane: Point2D
  /** พิกัดแยกตามแขน (ถ้ามี) */
  per_arm_robot_plane?: Record<string, Point2D>
  /** ชื่อ block เช่น "1,1" "2,3" */
  block?: string
  /** แขนที่เอื้อมถึงวัตถุนี้ได้ */
  reachable_by: string[]
  preferred_arm?: string
  /** รัศมีวัตถุโดยประมาณ (mm) สำหรับวาด ถ้าไม่มีใช้ค่า default */
  radius_mm?: number
}

export interface WorkspaceZone {
  name: string
  role: string
  bounds_mm: {
    x_min: number
    x_max: number
    y_min: number
    y_max: number
  }
}

export interface ScanResult {
  ok: boolean
  camera_id: string
  timestamp: string
  workspace: {
    width_mm: number
    height_mm: number
    zones: WorkspaceZone[]
  }
  object_count: number
  objects: DetectedObject[]
}

/** ท่าของแขนใน local frame ของแขนนั้น หน่วย mm + องศา */
export interface ArmPose {
  x: number
  y: number
  z: number
  r: number
}

/** Trajectory จาก sim server — เล่นอนิเมชันฝั่ง client ≥25 FPS */
export interface ArmMotionSegment {
  from: ArmPose
  to: ArmPose
  t0_ms: number
  duration_ms: number
  easing?: string
  speed_mm_s?: number
  server_now_ms?: number
}

export type ArmZone = "own" | "shared" | "other" | "unknown"

export interface ArmState {
  arm_id: string
  pose: ArmPose
  zone: ArmZone
  occupies_shared: boolean
  is_active: boolean
  is_at_standard_park: boolean
  /** วัตถุที่ถืออยู่ ("unknown" = ไม่ทราบ) */
  holding: string
  /** ถ้ามี → RobotArm เล่น path เองที่ 60fps */
  motion?: ArmMotionSegment | null
  running_status?: boolean
}

/** เมตาของวัตถุที่กำลังถือ (สำหรับวาดติดปลายแขน) */
export interface HeldMeta {
  id: string
  color: ObjectColor
  held_by?: string
}

export interface RobotState {
  ok: boolean
  arms: ArmState[]
  /** วัตถุที่ถือ + สี + แขน (sim) */
  held_meta?: HeldMeta | null
  shared_zone_occupancy: {
    occupied_by: string[]
    is_clear: boolean
  }
}

/** สถานะรวมที่ scene ใช้วาด รวม scan + state เข้าด้วยกัน */
export interface WorkspaceSnapshot {
  scan: ScanResult | null
  state: RobotState | null
}
