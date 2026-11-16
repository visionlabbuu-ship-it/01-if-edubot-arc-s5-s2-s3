// พื้น workspace 600x400mm + เส้นแบ่ง 6 blocks + zones สี
import { useMemo } from "react"
import { Text } from "@react-three/drei"
import { EdgesGeometry, PlaneGeometry } from "three"
import { BLOCKS, WORKSPACE, ZONES, type BlockDef, type ZoneDef } from "../geometry"
import { mm, planeToScene } from "../coords"

function ZonePatch({ zone }: { zone: ZoneDef }) {
  const cx = (zone.x_min + zone.x_max) / 2
  const cy = (zone.y_min + zone.y_max) / 2
  const [sx, sz] = planeToScene(cx, cy)
  const w = mm(zone.x_max - zone.x_min)
  const d = mm(zone.y_max - zone.y_min)
  return (
    <mesh position={[sx, 0.001, sz]} rotation={[-Math.PI / 2, 0, 0]}>
      <planeGeometry args={[w, d]} />
      <meshStandardMaterial color={zone.color} transparent opacity={0.18} />
    </mesh>
  )
}

function BlockOutline({ block }: { block: BlockDef }) {
  const cx = (block.x_min + block.x_max) / 2
  const cy = (block.y_min + block.y_max) / 2
  const [sx, sz] = planeToScene(cx, cy)
  const w = mm(block.x_max - block.x_min)
  const d = mm(block.y_max - block.y_min)
  const edges = useMemo(() => new EdgesGeometry(new PlaneGeometry(w, d)), [w, d])

  return (
    <group position={[sx, 0.003, sz]}>
      <lineSegments rotation={[-Math.PI / 2, 0, 0]} geometry={edges}>
        <lineBasicMaterial color="#94a3b8" />
      </lineSegments>
      <Text
        position={[0, 0.01, 0]}
        rotation={[-Math.PI / 2, 0, 0]}
        fontSize={0.12}
        color="#64748b"
        anchorX="center"
        anchorY="middle"
      >
        {block.name}
      </Text>
    </group>
  )
}

export function WorkspaceFloor() {
  const floorW = mm(WORKSPACE.width_mm)
  const floorD = mm(WORKSPACE.height_mm)
  return (
    <group>
      <mesh position={[0, 0, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[floorW, floorD]} />
        <meshStandardMaterial color="#1e293b" />
      </mesh>

      {ZONES.map(z => (
        <ZonePatch key={z.name} zone={z} />
      ))}

      {BLOCKS.map(b => (
        <BlockOutline key={b.name} block={b} />
      ))}
    </group>
  )
}
