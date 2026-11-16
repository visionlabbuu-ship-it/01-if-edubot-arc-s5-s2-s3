// Inverse kinematics สำหรับ MG400 (แขน 4 แกน parallelogram)
// contract ส่งแค่ pose {x,y,z,r} ใน local frame ของแขน
// เราคำนวณมุมข้อต่อย้อนกลับเพื่อวาดแขนให้ขยับตามจริง
//
// MG400 เป็น SCARA-like vertical: J1 ฐานหมุนรอบแกนตั้ง,
// J2/J3 เป็น 2-link ในระนาบดิ่ง (parallelogram ทำให้ปลาย gripper ตั้งฉากพื้นเสมอ),
// J4 (r) หมุน end-effector รอบแกนตั้ง

import { MG400 } from "./geometry"

export interface JointAngles {
  /** ฐานหมุน (rad) รอบแกนตั้ง — หันไปทางเป้าหมายในระนาบบน */
  base: number
  /** ข้อไหล่ J2 (rad) จากแนวนอน */
  shoulder: number
  /** ข้อศอก J3 (rad) เทียบกับ upper arm */
  elbow: number
  /** หมุนปลาย (rad) จากค่า r */
  tool: number
  /** เป้าหมายเกินระยะเอื้อม → clamp แล้ว true */
  clamped: boolean
}

const deg2rad = (d: number) => (d * Math.PI) / 180

/**
 * ระนาบโต๊ะใน tool-frame ของ MG400/ARC (surface_model.SURFACE_Z ≈ -150)
 * home z=0 อยู่เหนือโต๊ะ ~150mm, pick z≈-150 แตะโต๊ะ
 * ถ้าใช้ robot z ดิบเป็นความสูง scene → home อยู่พื้น / pick ลงใต้ดิน
 */
export const ROBOT_SURFACE_Z_MM = -150

/**
 * แปลง robot tool-frame Z → ความสูงเหนือพื้นโต๊ะใน scene (mm)
 * surface → 0, home(0) → 150, hover(~83) → ~233
 */
export function robotZToDisplayHeight(robotZ: number): number {
  return robotZ - ROBOT_SURFACE_Z_MM
}

/**
 * คำนวณมุมข้อต่อจาก local pose
 * @param x ระยะยื่นไปข้างหน้า (mm) ตามแกนที่แขนหันเข้า workspace
 * @param y ระยะด้านข้าง (mm)
 * @param z ความสูง tool-frame (mm) — home≈0, surface≈-150, hover≈+83
 * @param r การหมุนปลาย (deg)
 */
export function solveArmIK(x: number, y: number, z: number, r: number): JointAngles {
  const { upper_arm_mm: L1, forearm_mm: L2, base_height_mm: baseH } = MG400

  // 1) ฐานหมุน: มุมในระนาบบนมองจากด้านบน
  const base = Math.atan2(y, x)

  // 2) ระยะแนวราบจากแกนฐานถึงเป้าหมาย
  const planar = Math.hypot(x, y)

  // 3) แมป tool Z → ความสูงเหนือโต๊ะ แล้วเทียบกับข้อ J2 (สูง baseH จากพื้น)
  //    เดิมใช้ robot z ดิบ → home(0) อยู่ระดับพื้น / pick(-150) ลงใต้ดิน
  const tipHeight = robotZToDisplayHeight(z)
  const dz = tipHeight - baseH
  let dist = Math.hypot(planar, dz)

  let clamped = false
  const maxReach = L1 + L2 - 1 // กันเอกฐานเป็นเส้นตรง
  if (dist > maxReach) {
    dist = maxReach
    clamped = true
  }
  const minReach = Math.abs(L1 - L2) + 1
  if (dist < minReach) {
    dist = minReach
    clamped = true
  }

  // law of cosines — มุมภายในที่ข้อศอก (ระหว่างลิงก์ L1–L2)
  const cosElbow = (L1 * L1 + L2 * L2 - dist * dist) / (2 * L1 * L2)
  const elbowInner = Math.acos(Math.max(-1, Math.min(1, cosElbow)))

  const cosShoulderOffset = (L1 * L1 + dist * dist - L2 * L2) / (2 * L1 * dist)
  const shoulderOffset = Math.acos(Math.max(-1, Math.min(1, cosShoulderOffset)))
  const elevation = Math.atan2(dz, planar)

  // hierarchical chain (RobotArm): tip = R(shoulder)·(L1,0) + R(shoulder+elbow)·(L2,0)
  // shoulder = elevation + offset (elbow “พับลง” ใต้แขนบน)
  // elbow relative ต้องเป็น (elbowInner − π) ไม่ใช่ (π − elbowInner)
  //   เดิมคืน π−inner แล้ว UI ลบ π อีกที → ได้ −inner → tip ต่ำ/จมพื้น (home err ~−74mm)
  const shoulder = elevation + shoulderOffset
  const elbow = elbowInner - Math.PI

  return {
    base,
    shoulder,
    elbow,
    tool: deg2rad(r),
    clamped,
  }
}
