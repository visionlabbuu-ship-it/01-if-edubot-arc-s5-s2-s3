// วัตถุที่แขนถือ — ติดปลาย gripper ทุกเฟรม
// อ่าน holding + สีจาก armMotionBus (ไม่พึ่ง React props ช้า)
import { useRef } from "react"
import { useFrame } from "@react-three/fiber"
import type { Group, MeshStandardMaterial } from "three"
import { DEFAULT_OBJECT_RADIUS_MM, OBJECT_COLORS, MG400 } from "../geometry"
import { mm } from "../coords"
import { getBusArm } from "../armMotionBus"

export function HeldObject({ armId }: { armId: string }) {
  const group = useRef<Group>(null)
  const mat = useRef<MeshStandardMaterial>(null)
  // ใหญ่กว่า object บนพื้นเล็กน้อย + เรืองแสงชัด — กันมองไม่เห็นตอนติดปลาย
  const radius = mm(DEFAULT_OBJECT_RADIUS_MM) * 1.15
  const height = radius * 1.7
  const L2 = mm(MG400.forearm_mm)
  const lastColor = useRef<string>("")
  const lastActive = useRef<boolean>(false)

  useFrame(() => {
    const bus = getBusArm(armId)
    const holding = bus?.holding
    const active = Boolean(holding && holding !== "unknown" && holding.length > 0)

    if (group.current) {
      group.current.visible = active
      // บังคับให้อยู่ปลาย gripper ทุกเฟรม (กัน parent transform แปลก)
      group.current.position.set(L2, -height * 0.5, 0)
    }

    if (active !== lastActive.current) {
      lastActive.current = active
    }
    if (!active) return

    const colorKey = (bus?.holding_color || "green").toLowerCase()
    if (mat.current && colorKey !== lastColor.current) {
      const c = OBJECT_COLORS[colorKey] ?? OBJECT_COLORS.unknown
      mat.current.color.set(c)
      mat.current.emissive.set(c)
      lastColor.current = colorKey
    }
  })

  return (
    <group ref={group} position={[L2, -height * 0.5, 0]} visible={false}>
      <mesh castShadow renderOrder={10}>
        <cylinderGeometry args={[radius, radius, height, 24]} />
        <meshStandardMaterial
          ref={mat}
          color={OBJECT_COLORS.green}
          emissive={OBJECT_COLORS.green}
          emissiveIntensity={0.85}
          depthTest={true}
        />
      </mesh>
      {/* วงแหวนสว่างรอบปลาย — เห็นชัดว่า “ถืออยู่” */}
      <mesh position={[0, height * 0.55, 0]} rotation={[Math.PI / 2, 0, 0]} renderOrder={11}>
        <torusGeometry args={[radius * 1.25, radius * 0.12, 8, 20]} />
        <meshStandardMaterial
          color="#f8fafc"
          emissive="#f8fafc"
          emissiveIntensity={0.6}
        />
      </mesh>
    </group>
  )
}
