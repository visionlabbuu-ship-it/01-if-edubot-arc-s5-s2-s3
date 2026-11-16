# MG400 STL → Web Sim Visual

วันที่: 2026-07-20

## เป้าหมาย

เอาไฟล์ CAD ที่แปลงเป็น STL (`DOBOT MG400.stl`) มาโชว์ใน 3D scene ของ
`external/Agent-Robotic-Cluster/web` โดย **ไม่แตะ** logic ของ Python sim
(`sim_robot` ยังส่ง pose `x,y,z,r` เหมือนเดิม)

## ข้อจำกัดของไฟล์ต้นทาง

| รายการ | ค่า |
|--------|-----|
| ต้นทาง | ASCII STL ~51 MB, ~172k triangles |
| หน่วย | เมตร (bbox ~0.41×0.19×0.47 m) |
| โครงสร้าง | **solid เดียว** — ไม่มี joint hierarchy |

ดังนั้น **ขยับข้อ J2/J3 ตาม mesh จริงยังทำไม่ได้** ในเฟสนี้

## แผนที่ลงมือแล้ว (Phase 1)

1. **Process asset**
   - สคริปต์: `tools/process_mg400_stl.py`
   - ผลลัพธ์: `web/public/models/mg400/mg400_arm.stl` (binary, ~0.3 MB, ~7k tris)
2. **Calibration constants**
   - `web/src/components/robot/mg400Cad.ts`
   - scale m→scene, rotation CAD→Y-up, mode `shell|off`
3. **Load + mount**
   - `Mg400CadMesh.tsx` ใช้ `STLLoader`
   - `RobotArm.tsx` ใส่ CAD ใน **yaw group** (หันตาม TCP)
   - เก็บ IK cylinder แบบโปร่งบาง + gripper tip + `HeldObject` เหมือนเดิม
4. **Fallback**
   - ตั้ง `MG400_CAD_MODE = "off"` ใน `mg400Cad.ts` กลับไปกล่อง/กระบอกเดิม

## วิธีรัน / ตรวจ

```bash
# สร้าง mesh ใหม่เมื่อได้ STL ชุดใหม่
.venv/bin/python tools/process_mg400_stl.py --src "/path/to/DOBOT MG400.stl"

# web
cd external/Agent-Robotic-Cluster/web
npm run dev
```

เปิดหน้าที่มี `WorkspacePanel` / teacher sim — ควรเห็นตัวถัง MG400 ที่ฐาน Arm1/Arm2
หมุน yaw ตามปลายแขน; ลิงก์ IK โปร่งๆ ยังชี้ไปที่ TCP

## Phase 2 (ยังไม่ทำ — ถ้าต้องการข้อต่อจริง)

1. กลับไป STEP/assembly แยก part ต่อ link
2. export `base.stl`, `j1.stl`, `j2.stl`, `j3.stl`, `tool.stl`
3. ตั้ง joint origin ให้ตรง DH/IK
4. แทน cylinder chain ด้วย mesh ต่อข้อ (ใช้มุมจาก `solveArmIK` ที่มีอยู่)

## ปรับจูนถ้า mesh เอียง/สูงผิด

แก้ที่ `mg400Cad.ts` → `MG400_CAD_TRANSFORM`:

- `rotation` — กลับด้าน / ตั้งตรง
- `position` — เลื่อนฐานให้อยู่กลาง footprint
- `scale` — ถ้าพบว่า STL เป็น mm ไม่ใช่ m ให้ใช้ `0.01` แทน `10`
