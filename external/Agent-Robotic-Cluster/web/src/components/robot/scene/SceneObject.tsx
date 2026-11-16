// วัตถุบนพื้น — robot_plane = shared workspace mm (ไม่ใช่ arm-local)
// ลากวางแล้วอยู่จุด drop ทันที (optimistic) — ไม่ snap กลับ props เก่า
import { useEffect, useRef, useState } from "react"
import { Text } from "@react-three/drei"
import { useThree } from "@react-three/fiber"
import type { ThreeEvent } from "@react-three/fiber"
import * as THREE from "three"
import { DEFAULT_OBJECT_RADIUS_MM, OBJECT_COLORS } from "../geometry"
import { mm, planeToScene, sceneToPlane } from "../coords"
import type { DetectedObject } from "../types"

// Ground plane Y=0 ใช้ raycaster คำนวณตำแหน่ง drop
const GROUND_PLANE = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)

/** ระยะ mm ที่ถือว่า server ตรงกับจุด drop แล้ว */
const SERVER_MATCH_MM = 2

export interface SceneObjectProps {
  object: DetectedObject
  draggable?: boolean
  onDragStart?: (object: DetectedObject) => void
  onDragEnd?: (object: DetectedObject, newWorldPos: { x: number; y: number }) => void
  /** หรี่ความสว่างวัตถุนี้ (เช่น ขณะลากเหนือ trash zone) */
  dimmed?: boolean
}

export function SceneObject({
  object,
  draggable = false,
  onDragStart,
  onDragEnd,
  dimmed = false,
}: SceneObjectProps) {
  const radius = mm(object.radius_mm ?? DEFAULT_OBJECT_RADIUS_MM)
  const height = radius * 1.6
  const color = OBJECT_COLORS[object.color] ?? OBJECT_COLORS.unknown

  const [hovered, setHovered] = useState(false)
  const [dragging, setDragging] = useState(false)
  const { camera, gl } = useThree()

  // committed = จุดที่แสดงบนพื้น (optimistic หลัง drop / หรือจาก server)
  const [committedWs, setCommittedWs] = useState(() => ({
    x: object.robot_plane.x,
    y: object.robot_plane.y,
  }))
  // ระหว่างรอ server หลัง drop — อย่าให้ props เก่าดึงกลับ
  const pendingDropRef = useRef<{ x: number; y: number } | null>(null)
  const dragPosRef = useRef<[number, number, number]>([0, height / 2, 0])
  const [dragPos, setDragPos] = useState<[number, number, number]>([0, height / 2, 0])

  // sync จาก server: reset / spawn / ย้ายจากที่อื่น
  useEffect(() => {
    const sx = object.robot_plane.x
    const sy = object.robot_plane.y
    const pending = pendingDropRef.current
    if (pending) {
      const dx = sx - pending.x
      const dy = sy - pending.y
      if (Math.hypot(dx, dy) <= SERVER_MATCH_MM) {
        // server ตรงจุด drop แล้ว
        pendingDropRef.current = null
        setCommittedWs({ x: sx, y: sy })
      }
      // ยังไม่ตรง — คง committed ที่ drop ไว้ (ไม่ snap กลับ)
      return
    }
    setCommittedWs({ x: sx, y: sy })
  }, [object.id, object.robot_plane.x, object.robot_plane.y])

  const [sx, sz] = planeToScene(committedWs.x, committedWs.y)
  const basePos: [number, number, number] = [sx, height / 2, sz]

  const handlePointerDown = (e: ThreeEvent<PointerEvent>) => {
    if (!draggable) return
    e.stopPropagation()
    gl.domElement.setPointerCapture(e.pointerId)
    setDragging(true)
    dragPosRef.current = basePos
    setDragPos(basePos)
    onDragStart?.(object)
  }

  const handlePointerMove = (e: ThreeEvent<PointerEvent>) => {
    if (!dragging) {
      setHovered(true)
      return
    }
    e.stopPropagation()
    const rect = gl.domElement.getBoundingClientRect()
    const ndcX = ((e.clientX - rect.left) / rect.width) * 2 - 1
    const ndcY = -((e.clientY - rect.top) / rect.height) * 2 + 1
    const raycaster = new THREE.Raycaster()
    raycaster.setFromCamera(new THREE.Vector2(ndcX, ndcY), camera)
    const target = new THREE.Vector3()
    if (raycaster.ray.intersectPlane(GROUND_PLANE, target)) {
      const newPos: [number, number, number] = [target.x, height / 2, target.z]
      dragPosRef.current = newPos
      setDragPos(newPos)
    }
  }

  const handlePointerUp = (e: ThreeEvent<PointerEvent>) => {
    if (!dragging) return
    e.stopPropagation()
    gl.domElement.releasePointerCapture(e.pointerId)
    setDragging(false)

    const [wx, , wz] = dragPosRef.current
    const shared = sceneToPlane(wx, wz)

    // sticky: แสดงจุด drop ทันที ก่อน server ตอบ
    pendingDropRef.current = { x: shared.x, y: shared.y }
    setCommittedWs({ x: shared.x, y: shared.y })

    onDragEnd?.(object, { x: shared.x, y: shared.y })
  }

  const handlePointerLeave = () => {
    if (!dragging) setHovered(false)
  }

  const currentPos = dragging ? dragPos : basePos
  const emissiveIntensity = dragging ? 0.5 : hovered ? 0.3 : 0

  return (
    <group position={currentPos}>
      <mesh
        castShadow
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerLeave}
      >
        <cylinderGeometry args={[radius, radius, height, 24]} />
        <meshStandardMaterial
          color={color}
          opacity={dimmed ? 0.4 : 1}
          transparent={dimmed}
          emissive={color}
          emissiveIntensity={emissiveIntensity}
        />
      </mesh>
      <Text
        position={[0, height, 0]}
        fontSize={0.1}
        color="#e2e8f0"
        anchorX="center"
        anchorY="bottom"
      >
        {object.id}
      </Text>
    </group>
  )
}
