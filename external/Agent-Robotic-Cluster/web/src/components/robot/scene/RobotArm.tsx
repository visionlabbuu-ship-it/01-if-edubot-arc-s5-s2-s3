// แขน MG400 — อนิเมชันสมูท + วัตถุที่ถือติดปลาย
//
// หลักการ:
// 1) อ่าน pose/trajectory จาก armMotionBus (ไม่พึ่ง React props ที่เปลี่ยนทุก poll)
// 2) เล่น path ด้วย local clock (ไม่ใช้ server clock ทุกเฟรม → ไม่ jitter)
// 3) damp มุมข้อ (exponential smooth) ทุก useFrame ~60–120fps
// 4) ห้ามผูก rotation กับ props — กัน re-render รีเซ็ตมุม
// 5) held object ติดปลาย gripper (child ของ elbow group)
// 6) CAD STL (optional shell) หมุนตาม base yaw — ยังไม่มี joint split
import { Suspense, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import { Text } from "@react-three/drei"
import type { Group, MeshStandardMaterial } from "three"
import { ARMS, MG400, armLocalToShared, type ArmGeometry } from "../geometry"
import { mm, planeToScene } from "../coords"
import { solveArmIK } from "../kinematics"
import { MG400_CAD_MODE } from "../mg400Cad"
import {
  getBusArm,
  sampleBusPose,
  type BusPose,
} from "../armMotionBus"
import { HeldObject } from "./HeldObject"
import { Mg400CadMesh } from "./Mg400CadMesh"

const ZONE_TINT: Record<string, string> = {
  own: "#38bdf8",
  shared: "#f59e0b",
  other: "#a78bfa",
  unknown: "#94a3b8",
}

/**
 * ความเร็วตามมุมเป้า (1/s)
 * สูง = ตามทันเร็ว แต่ยัง damp ทุกเฟรม → ลื่น ไม่กระตุก
 * ~25–35 ให้สมูทมากที่ 60fps
 */
const JOINT_FOLLOW = 28

/** โชว์ CAD shell แทนกล่องฐาน + ทำ IK links บางลง */
const USE_CAD_SHELL = MG400_CAD_MODE === "shell"

function findGeometry(armId: string): ArmGeometry | undefined {
  return ARMS.find(a => a.id === armId)
}

function damp(current: number, target: number, lambda: number, dt: number): number {
  const t = 1 - Math.exp(-lambda * dt)
  return current + (target - current) * t
}

function dampAngle(current: number, target: number, lambda: number, dt: number): number {
  let diff = target - current
  while (diff > Math.PI) diff -= Math.PI * 2
  while (diff < -Math.PI) diff += Math.PI * 2
  const t = 1 - Math.exp(-lambda * dt)
  return current + diff * t
}

export function RobotArm({ armId }: { armId: string }) {
  const geo = findGeometry(armId)

  const joints = useRef({ baseYaw: 0, shoulder: 0, elbow: 0, clamped: false })
  const seeded = useRef(false)
  const lastPose = useRef<BusPose>({ x: 350, y: 0, z: 0, r: 0 })

  const yawGroup = useRef<Group>(null)
  const shoulderGroup = useRef<Group>(null)
  const elbowGroup = useRef<Group>(null)
  const upperMat = useRef<MeshStandardMaterial>(null)
  const labelTint = useRef(ZONE_TINT.own)

  useFrame((_, delta) => {
    if (!geo) return
    const dt = Math.min(Math.max(delta, 1 / 240), 0.04)

    const bus = getBusArm(armId)
    const pose = bus ? sampleBusPose(bus) : lastPose.current
    lastPose.current = pose

    const zone = bus?.zone ?? "unknown"
    labelTint.current = ZONE_TINT[zone] ?? ZONE_TINT.unknown

    const ik = solveArmIK(pose.x, pose.y, pose.z, pose.r)
    // --- Yaw จากตำแหน่งจริงบนฉาก (ไม่เดา ±ik.base) ---
    // 1) local pose → workspace เดียวกับ object.robot_plane
    // 2) workspace → scene XZ
    // 3) มุมจากฐานไปเป้า: R_y(θ)·(L,0,0)=(L cosθ, 0, −L sinθ)
    //    ⇒ θ = atan2(−ΔsceneZ, ΔsceneX)
    // ตรวจ FK แล้ว err=0 ทั้ง Arm1/Arm2 (สูตร π−ik.base ทำ Arm2 กลับแกน Y ~200mm)
    // TIP_AIM_STAMP=v5-force-reload
    const shared = armLocalToShared(geo, pose.x, pose.y)
    const [tipSx, tipSz] = planeToScene(shared.x, shared.y)
    const [baseSx, baseSz] = planeToScene(geo.base.x, geo.base.y)
    const targetYaw = Math.atan2(-(tipSz - baseSz), tipSx - baseSx)
    const targetShoulder = ik.shoulder
    const targetElbow = ik.elbow

    if (!seeded.current) {
      joints.current = {
        baseYaw: targetYaw,
        shoulder: targetShoulder,
        elbow: targetElbow,
        clamped: ik.clamped,
      }
      seeded.current = true
    } else {
      joints.current.baseYaw = dampAngle(joints.current.baseYaw, targetYaw, JOINT_FOLLOW, dt)
      joints.current.shoulder = damp(joints.current.shoulder, targetShoulder, JOINT_FOLLOW, dt)
      joints.current.elbow = damp(joints.current.elbow, targetElbow, JOINT_FOLLOW, dt)
      joints.current.clamped = ik.clamped
    }

    const j = joints.current
    if (yawGroup.current) yawGroup.current.rotation.y = j.baseYaw
    if (shoulderGroup.current) shoulderGroup.current.rotation.z = j.shoulder
    if (elbowGroup.current) elbowGroup.current.rotation.z = j.elbow
    if (upperMat.current) {
      upperMat.current.color.set(j.clamped ? "#ef4444" : labelTint.current)
    }
  })

  if (!geo) return null

  const [bx, bz] = planeToScene(geo.base.x, geo.base.y)
  const L1 = mm(MG400.upper_arm_mm)
  const L2 = mm(MG400.forearm_mm)
  const baseH = mm(MG400.base_height_mm)
  const tint = ZONE_TINT.own
  // CAD shell: thinner kinematic ghost so CAD silhouette reads as the body
  const linkRadius = USE_CAD_SHELL ? 0.012 : 0.03
  const linkOpacity = USE_CAD_SHELL ? 0.35 : 1

  return (
    <group position={[bx, 0, bz]}>
      {/* Static footprint / label — CAD sits in yaw group so body faces TCP */}
      {!USE_CAD_SHELL && (
        <mesh position={[0, baseH / 2, 0]} castShadow>
          <boxGeometry args={[mm(MG400.base_size_mm), baseH, mm(MG400.base_size_mm)]} />
          <meshStandardMaterial color="#475569" />
        </mesh>
      )}

      <Text position={[0, baseH + 0.55, 0]} fontSize={0.16} color={tint} anchorX="center" anchorY="bottom">
        {armId}
      </Text>

      <group ref={yawGroup} position={[0, 0, 0]}>
        {USE_CAD_SHELL && (
          <Suspense fallback={null}>
            <Mg400CadMesh />
          </Suspense>
        )}

        {/* IK chain origin at base shoulder height (same as before) */}
        <group position={[0, baseH, 0]}>
          <group ref={shoulderGroup}>
            <mesh position={[L1 / 2, 0, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
              <cylinderGeometry args={[linkRadius, linkRadius, L1, 12]} />
              <meshStandardMaterial
                ref={upperMat}
                color={tint}
                transparent={USE_CAD_SHELL}
                opacity={linkOpacity}
                depthWrite={!USE_CAD_SHELL}
              />
            </mesh>

            <group ref={elbowGroup} position={[L1, 0, 0]}>
              <mesh position={[L2 / 2, 0, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
                <cylinderGeometry args={[linkRadius, linkRadius, L2, 12]} />
                <meshStandardMaterial
                  color="#cbd5e1"
                  transparent={USE_CAD_SHELL}
                  opacity={linkOpacity}
                  depthWrite={!USE_CAD_SHELL}
                />
              </mesh>

              <mesh position={[L2, 0, 0]} castShadow>
                <boxGeometry args={[0.08, 0.08, 0.08]} />
                <meshStandardMaterial color="#f1f5f9" />
              </mesh>

              {/* วัตถุที่ถือ — อ่าน bus เอง แสดงทันทีที่ pick สำเร็จ */}
              <HeldObject armId={armId} />
            </group>
          </group>
        </group>
      </group>
    </group>
  )
}
