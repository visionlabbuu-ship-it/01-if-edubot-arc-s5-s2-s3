// แปลงพิกัด workspace (mm) → three.js scene units
// scene unit = mm / 100 (workspace 600x400mm → 6x4 units) ใช้เลขเล็กจัดการง่าย
// แกน: sceneX = workspace x, sceneZ = -workspace y (y มาก= ลึกเข้าไป), sceneY = ความสูง z
// จัด center ของ workspace (300,200) ให้อยู่ที่ origin

export const SCENE_SCALE = 1 / 100

const CENTER_X = 300
const CENTER_Y = 200

/** พิกัดพื้น (workspace mm) → [x, z] ใน scene (y=0) */
export function planeToScene(x: number, y: number): [number, number] {
  return [(x - CENTER_X) * SCENE_SCALE, -(y - CENTER_Y) * SCENE_SCALE]
}

/** scene [x, z] → workspace mm (กลับจาก planeToScene — ใช้ตอน drag-drop) */
export function sceneToPlane(sx: number, sz: number): { x: number; y: number } {
  return {
    x: sx / SCENE_SCALE + CENTER_X,
    y: -(sz / SCENE_SCALE) + CENTER_Y,
  }
}

/** พิกัด 3 มิติเต็ม (workspace mm + ความสูง) → [x, y, z] ใน scene */
export function pointToScene(x: number, y: number, height: number): [number, number, number] {
  const [sx, sz] = planeToScene(x, y)
  return [sx, height * SCENE_SCALE, sz]
}

/** ความยาว mm → scene units */
export function mm(value: number): number {
  return value * SCENE_SCALE
}
