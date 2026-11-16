// Loads the single-body MG400 STL and mounts it under the arm base / yaw group.
// Mesh has no joint hierarchy — only follows base yaw. IK sticks + tip stay for motion.
//
// Pipeline: load → scale/rotate in geometry → optional upright flip → snap minY to 0.
import { useMemo } from "react"
import { useLoader } from "@react-three/fiber"
import { STLLoader } from "three-stdlib"
import type { BufferGeometry } from "three"
import {
  MG400_CAD_COLOR,
  MG400_CAD_METALNESS,
  MG400_CAD_REVISION,
  MG400_CAD_ROUGHNESS,
  MG400_CAD_TRANSFORM,
  MG400_CAD_URL,
} from "../mg400Cad"

function prepareCadGeometry(source: BufferGeometry): BufferGeometry {
  const g = source.clone()
  const { rotation, scale, forceFlipY } = MG400_CAD_TRANSFORM

  const s = typeof scale === "number" ? scale : 1
  // Match Three.js Object3D matrix order: scale, then Euler XYZ rotations.
  g.scale(s, s, s)
  g.rotateX(rotation[0])
  g.rotateY(rotation[1])
  g.rotateZ(rotation[2])

  g.computeBoundingBox()
  let bb = g.boundingBox
  if (bb) {
    const centerY = (bb.min.y + bb.max.y) * 0.5
    const shouldFlip =
      forceFlipY === true || (forceFlipY == null && centerY < -0.02)
    if (shouldFlip) {
      // 180° about X: swap up/down, keep +X forward
      g.rotateX(Math.PI)
      g.computeBoundingBox()
      bb = g.boundingBox
    }
  }

  // Always sit the lowest vertex on the floor (y = 0)
  if (bb) {
    g.translate(0, -bb.min.y, 0)
  }

  g.computeVertexNormals()
  g.computeBoundingBox()
  return g
}

export function Mg400CadMesh({ color }: { color?: string }) {
  const geometry = useLoader(STLLoader, MG400_CAD_URL) as BufferGeometry

  const prepared = useMemo(
    () => prepareCadGeometry(geometry),
    // revision forces re-bake when calibration changes (HMR-safe)
    [geometry, MG400_CAD_REVISION],
  )

  const [px, py, pz] = MG400_CAD_TRANSFORM.position

  return (
    <mesh
      key={`mg400-cad-r${MG400_CAD_REVISION}`}
      geometry={prepared}
      position={[px, py, pz]}
      castShadow
      receiveShadow
    >
      <meshStandardMaterial
        color={color ?? MG400_CAD_COLOR}
        metalness={MG400_CAD_METALNESS}
        roughness={MG400_CAD_ROUGHNESS}
      />
    </mesh>
  )
}
