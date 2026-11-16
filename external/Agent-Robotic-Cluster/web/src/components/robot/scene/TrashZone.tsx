// Trash zone — มุมขวาล่างของ workspace (mapped coordinate)
// แสดงไอคอนถังขยะ, highlight เมื่อลากวัตถุมาเหนือ, เรียก onHoverChange
import { Text } from "@react-three/drei"
import { planeToScene, mm } from "../coords"

// workspace bottom-right corner: x=600, y=0 (ล่างขวา)
// วาง trash zone ที่ x=500..600, y=-200..0 → center (550, -100)
const TRASH_CENTER_X = 550
const TRASH_CENTER_Y = -100
const TRASH_W_MM = 80
const TRASH_H_MM = 80

interface TrashZoneProps {
  /** true เมื่อกำลังลากวัตถุอยู่ */
  active: boolean
  /** true เมื่อ drag อยู่เหนือ trash zone */
  highlighted: boolean
  onHoverChange: (over: boolean) => void
}

export function TrashZone({ active, highlighted, onHoverChange }: TrashZoneProps) {
  const [sx, sz] = planeToScene(TRASH_CENTER_X, TRASH_CENTER_Y)
  const w = mm(TRASH_W_MM)
  const d = mm(TRASH_H_MM)

  const baseColor = highlighted ? "#ef4444" : active ? "#f97316" : "#475569"
  const opacity = active ? (highlighted ? 0.85 : 0.55) : 0.25

  return (
    <group position={[sx, 0.002, sz]}>
      {/* พื้นสี trash zone */}
      <mesh rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[w, d]} />
        <meshStandardMaterial color={baseColor} transparent opacity={opacity} />
      </mesh>

      {/* ไอคอนถังขยะ (unicode 🗑 → ใช้ emoji text) */}
      <Text
        position={[0, 0.05, 0]}
        rotation={[-Math.PI / 2, 0, 0]}
        fontSize={highlighted ? 0.22 : 0.18}
        color={highlighted ? "#fff" : "#94a3b8"}
        anchorX="center"
        anchorY="middle"
      >
        🗑
      </Text>

      {/* invisible hit plane — รับ pointer events ขณะลาก */}
      {active && (
        <mesh
          rotation={[-Math.PI / 2, 0, 0]}
          onPointerEnter={() => onHoverChange(true)}
          onPointerLeave={() => onHoverChange(false)}
        >
          <planeGeometry args={[w, d]} />
          <meshStandardMaterial transparent opacity={0} />
        </mesh>
      )}
    </group>
  )
}
