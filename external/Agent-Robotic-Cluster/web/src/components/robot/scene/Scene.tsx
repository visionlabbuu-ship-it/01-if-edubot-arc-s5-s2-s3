// 3D scene หลัก: Canvas + กล้อง + แสง + พื้น + แขน + วัตถุ + trash zone
// แขนอ่าน motion จาก armMotionBus ใน useFrame — ไม่ re-render ตอน poll pose
import { useRef, useState } from "react"
import { Canvas } from "@react-three/fiber"
import { OrbitControls, Grid } from "@react-three/drei"
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib"
import { WorkspaceFloor } from "./WorkspaceFloor"
import { RobotArm } from "./RobotArm"
import { SceneObject } from "./SceneObject"
import { TrashZone } from "./TrashZone"
import { moveObject, removeObject } from "@/lib/sim_api"
import { ARMS } from "../geometry"
import type { WorkspaceSnapshot, DetectedObject } from "../types"

interface SceneProps {
  snapshot: WorkspaceSnapshot
  draggingId?: string | null
  onObjectDragStart?: (id: string) => void
  onObjectDragEnd?: () => void
}

export function Scene({ snapshot, draggingId, onObjectDragStart, onObjectDragEnd }: SceneProps) {
  // วัตถุบนพื้นจาก scan (held ไม่อยู่ใน list — วาดติดปลายแขนผ่าน bus)
  const objects = snapshot.scan?.objects ?? []
  const orbitRef = useRef<OrbitControlsImpl>(null)
  const [overTrash, setOverTrash] = useState(false)

  const handleDragStart = (object: DetectedObject) => {
    if (orbitRef.current) orbitRef.current.enabled = false
    onObjectDragStart?.(object.id)
  }

  const handleDragEnd = async (object: DetectedObject, newWorldPos: { x: number; y: number }) => {
    if (orbitRef.current) orbitRef.current.enabled = true

    if (overTrash) {
      await removeObject(object.id)
    } else {
      await moveObject(object.id, newWorldPos.x, newWorldPos.y)
    }
    setOverTrash(false)
    onObjectDragEnd?.()
  }

  return (
    <Canvas
      shadows
      camera={{ position: [0, 6, 7], fov: 45 }}
      dpr={[1, 2]}
      // ล็อก frameloop ปกติ — อนิเมชันแขนต้องการทุกเฟรม
      frameloop="always"
      gl={{ antialias: true, powerPreference: "high-performance" }}
    >
      <color attach="background" args={["#0f172a"]} />

      <ambientLight intensity={0.6} />
      <directionalLight position={[5, 8, 5]} intensity={1.1} castShadow shadow-mapSize={[1024, 1024]} />
      <directionalLight position={[-5, 5, -3]} intensity={0.4} />

      <Grid
        position={[0, -0.01, 0]}
        args={[20, 20]}
        cellSize={0.5}
        cellThickness={0.5}
        cellColor="#1e293b"
        sectionSize={2}
        sectionThickness={1}
        sectionColor="#334155"
        fadeDistance={25}
        infiniteGrid
      />

      <WorkspaceFloor />

      {/* แขน + HeldObject (อ่าน bus) */}
      {ARMS.map(a => (
        <RobotArm key={a.id} armId={a.id} />
      ))}

      {/* วัตถุบนพื้นเท่านั้น (held ถูกลบจาก list แล้วติดปลายแขน) */}
      {objects.map(obj => (
        <SceneObject
          key={obj.id}
          object={obj}
          draggable={true}
          dimmed={overTrash && draggingId === obj.id}
          onDragStart={handleDragStart}
          onDragEnd={(o, pos) => void handleDragEnd(o, pos)}
        />
      ))}

      <TrashZone
        active={draggingId != null}
        highlighted={overTrash}
        onHoverChange={setOverTrash}
      />

      <OrbitControls
        ref={orbitRef}
        makeDefault
        minDistance={3}
        maxDistance={20}
        maxPolarAngle={Math.PI / 2.1}
        target={[0, 0, 0]}
      />
    </Canvas>
  )
}
