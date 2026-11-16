// Calibration for the single-body MG400 STL used as a visual shell.
// Source mesh: public/models/mg400/mg400_arm.stl
// CAD frame: meters, Z-up. Arm silhouette extends mostly toward -X.
//
// Scene: 1 unit = 100 mm → 1 m = 10 scene units.
// RobotArm yaw group aims links along +X after base yaw.
//
// Mg400CadMesh bakes rotation/scale into geometry, then ground-snaps minY→0.

/** URL served by Vite from web/public — ?v= busts STLLoader cache after fixes */
export const MG400_CAD_URL = "/models/mg400/mg400_arm.stl?v=3"

/** STL is in meters; scene unit = 0.1 m */
export const MG400_CAD_METERS_TO_SCENE = 10

/**
 * How the rigid CAD body is used in RobotArm:
 * - "shell": CAD yaws with base; thin IK sticks + tip still drive motion/held
 * - "off": procedural sticks only (legacy)
 */
export type Mg400CadMode = "shell" | "off"

export const MG400_CAD_MODE: Mg400CadMode = "off"

/**
 * Import orientation (baked into geometry, then minY snapped to 0).
 *
 * Measured on this STL (meters, Z-up, arm toward −X):
 *   rotX = −90° → CAD Z becomes scene +Y (upright, above floor)
 *   rotY = 180° → arm toward scene +X (yaw-group forward)
 *   rotX = +90° alone → body under floor (do not use without flip)
 *
 * Fine offsets after snap (scene units).
 */
export const MG400_CAD_TRANSFORM = {
  position: [0, 0, 0] as [number, number, number],
  rotation: [-Math.PI / 2, Math.PI, 0] as [number, number, number],
  scale: MG400_CAD_METERS_TO_SCENE,
  /** null = auto flip if bbox centerY < 0 after rotation */
  forceFlipY: null as boolean | null,
}

/** Body material */
export const MG400_CAD_COLOR = "#64748b"
export const MG400_CAD_METALNESS = 0.35
export const MG400_CAD_ROUGHNESS = 0.55

/** Bump when transform pipeline changes so React remounts mesh */
export const MG400_CAD_REVISION = 3
