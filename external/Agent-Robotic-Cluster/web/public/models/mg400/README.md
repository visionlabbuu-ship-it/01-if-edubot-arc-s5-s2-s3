# MG400 CAD mesh (web sim)

Source: `C:\\Users\\kuk_g\\Downloads\\DOBOT MG400.stl` (ASCII, ~51MB, ~172k tris)

Processed by `tools/process_mg400_stl.py`:
- ASCII → binary STL
- vertex-cluster decimation → ~6691 triangles
- units: **meters** (bbox max dim ≈ 0.467 m)

## Files
- `mg400_arm.stl` — single rigid mesh (whole arm at one pose). Used as visual body at each arm base.

## Limits
- Mesh is **one solid** (not split by joints) → cannot articulate J2/J3 links.
- Web `RobotArm` keeps procedural IK links for tip motion + held objects; CAD mesh is the base/body visual (yaw-aligned toward TCP).

## BBox (meters, CAD frame)
- min: [-0.3000013828277588, -0.09131667017936707, 0.002368421060964465]
- max: [0.10413157194852829, 0.09472421556711197, 0.46968454122543335]
- center: [-0.09793490171432495, 0.0017037726938724518, 0.23602648079395294]
- size: [0.4041329622268677, 0.18604087829589844, 0.4673161208629608]
