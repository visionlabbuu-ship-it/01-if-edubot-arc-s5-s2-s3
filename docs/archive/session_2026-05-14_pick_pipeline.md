# Robot Pick Pipeline — Session Summary

Date: 2026-05-14
Scope: Arm1 (Dobot MG400 @ 192.168.2.7) + Hikrobot MVS camera "Cam1"

---

## 1. Problem statement

`main.py` ของกล้อง MVS รายงาน block ผิด และ `pick_matrix_left_arm` พาแขนหุ่นไปไม่ตรงกลางก้อนวัตถุ
จุดที่เพี้ยนหนักที่สุดอยู่ขอบ workspace (ห่างก้อนจริง 6–9 mm)
มีอีก bug ซ่อนคือ `cv2.contourArea` คำนวณผิด เพราะ FIXED corners เรียงแบบ Z (bowtie)
รวมแล้ว pipeline ขาด 3 อย่าง: block layout ที่ถูก, calibration ที่ครอบ workspace, polygon ordering ที่ถูก

---

## 2. Root causes ที่เจอ และวิธีแก้

### 2.1 Block layout วางผิด (workspace 3x2 → ควรเป็น 2x3 ตามมุมมองกล้อง)

- กล้องวาง workspace ตะแคง: pixel-y axis ↔ workspace x (600 mm), pixel-x axis ↔ workspace y (400 mm)
- แก้: `build_workspace_target_points_longside_width()` ใน
  `D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan\main.py` — เปลี่ยน ordering ของ target points
- ค่าคงที่ `WORKSPACE_WIDTH_MM = 600`, `WORKSPACE_HEIGHT_MM = 400` (canonical ตาม
  `PATH_TO_IF_EDUBOT_ARC\workspace_layout_reference.md`) ไม่ต้องสลับ

### 2.2 `FIXED_WORKSPACE_CORNERS_PX` ขอบเลื่อนเข้า 25 px

- ค่าเก่า: `(1042,33) (2344,44) (1025,1920) (2329,1942)` — เลื่อนเข้ามาทำให้ object ติดขอบหลุด mask
- ค่าใหม่: `(1017,27) (2320,22) (1024,1914) (2329,1920)` ตรงกับ contour จริงที่ตรวจเจอ
- แก้ใน: `main.py`, `auto_pick_block.py`, `hover_then_descend.py`, `scan_only.py`, `auto_pick_sequence.py`

### 2.3 `cv2.contourArea` คำนวณผิด เพราะ corner order

- FIXED corners เป็น TL,TR,BL,BR (รูป Z) → cv2 มอง bowtie → area ออกมา ~1848 แทน ~2.47M
- ทำให้เงื่อนไข `area >= ws_area * 0.2` กรอง object 7000+ ทิ้งหมด
- แก้: เพิ่ม `order_polygon()` คืน CCW order (TL→TR→BR→BL) แล้วใช้แทนใน scan
- ใช้กับทุก helper ที่ใช้ `cv2.contourArea` หรือ `cv2.drawContours` กับ workspace contour
- คงไว้ `order_quad()` (TL,TR,BL,BR) สำหรับ `cv2.getPerspectiveTransform` เพราะตรงกับ target points

### 2.4 Calibration เก่าครอบ workspace แค่ 40%

- 4 จุดเดิม pixel y ∈ [127, 901] (774 px) แต่ workspace จริง y ∈ [27, 1914] (1893 px)
- ต้อง extrapolate → ห่างจาก calibration center error เพิ่มแบบ non-linear

**Calibration ใหม่ (เก็บผ่าน GetPose ที่จุดกลางวัตถุจริง 4 จุด):**

```python
PTS_CAMERA_LEFT = np.float32([
    [1356, 481],   # Block 2,1
    [2215, 561],   # Block 1,1
    [1944, 922],   # Block 1,2
    [1300, 912],   # Block 2,2
])
PTS_ROBOT_LEFT = np.float32([
    [244.508,  -92.628],
    [267.690,  170.562],
    [377.581,   88.214],
    [375.471, -110.825],
])
```

Round-trip error: 0.00 mm ทั้ง 4 จุด
หยิบจริง 4 block ผ่านทั้งหมด (1,1 / 1,2 / 2,1 / 2,2)

ครอบเฉพาะ **ครึ่ง workspace ฝั่ง Arm1** (col 1–2, x = 0..400 mm) เพราะ Arm1 เอื้อมไม่ถึงฝั่ง col 3
ฝั่ง col 3 (Block 1,3 / 2,3) ต้อง calibrate Arm2 แยกในอนาคต

---

## 3. ไฟล์ที่แก้

| ไฟล์ | จุดที่แก้ |
|---|---|
| `D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan\main.py` | `build_workspace_target_points_longside_width` ordering, `FIXED_WORKSPACE_CORNERS_PX`, `pts_camera_left` + `pts_robot_left` (calibration ใหม่), parse_intent + active_target_block, workspace_estimate_text แสดง 600x400 canonical, ลบ `not report_once` guard |
| `PATH_TO_IF_EDUBOT_ARC\config.py` | `CAMERA_CONFIG.Cam1.calibration.camera_points` + `robot_points` (calibration ใหม่) |
| `PATH_TO_IF_EDUBOT_ARC\analyze_workspace_frame.py` | block classification + target points ordering ให้ตรงกับ layout |

## 4. Scripts ใหม่ที่สร้าง (อยู่ใต้ `D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan\`)

| ไฟล์ | หน้าที่ |
|---|---|
| `scan_only.py` | จับภาพ 1 frame, list ทุก object candidate พร้อมเหตุผล keep/drop, save overlay + thresh + capture ลง `PATH_TO_IF_EDUBOT_ARC\` |
| `auto_pick_block.py` | one-shot: scan → หา blue ที่ Block 1,1 (fallback 2,1) → pick + drop |
| `hover_then_descend.py` | safe alignment test: hover → ลงระดับ pick ค้าง 8s (ไม่ดูด) → ยกกลับ + log GetPose |
| `get_pose.py` | อ่าน GetPose ของหุ่นปัจจุบัน (ไม่ขยับ) |
| `calibrate_collect.py` | flow scan / getpose / fit สำหรับเก็บ calibration pairs (alternative) |
| `auto_pick_sequence.py` | sequence pick & drop หลายเป้าหมาย — รับ list `(block, color)` แล้ววน scan→pick→drop ทีละจุด |
| `camera_block_diagnostic.py` | (มีอยู่แล้ว) ทดสอบ orientation hypothesis 8 แบบ ช่วย debug ตอนตั้ง workspace ใหม่ |

ไฟล์ output ที่ scripts เขียน (อยู่ใน `PATH_TO_IF_EDUBOT_ARC\`):
- `scan_capture.png`, `scan_overlay.png`, `scan_thresh.png`
- `auto_pick_capture.png`, `hover_diag_overlay.png`, `hover_descend_capture.png`
- `calibration_pairs.json` (raw pairs ที่เก็บ), `new_calibration.json` (matrix ใหม่)

---

## 5. Reference docs

| ไฟล์ | บอกอะไร |
|---|---|
| `PATH_TO_IF_EDUBOT_ARC\workspace_layout_reference.md` | source of truth: 600x400, 3 cols x 2 rows, ตำแหน่ง B1/B2/home/Drop/Storage |
| `PATH_TO_IF_EDUBOT_ARC\config.py` | `CAMERA_CONFIG`, `ROBOT_FLEET_CONFIG`, `ARM_REACH_ZONES`, `ARM_PICK_CONFIG`, `ARM_NAMED_TARGETS` (drop zone อยู่ที่นี่) |
| ไฟล์นี้ | สรุป session การแก้ pipeline |

---

## 6. ค่าคงที่สำคัญ (ใช้กับ Arm1)

```python
ROBOT_IP   = "192.168.2.7"
ROBOT_PORT = 29999
Z_HOVER    = 83.41
Z_PICK     = -154.48
# Drop Zone
DROP_X, DROP_Y, DROP_Z = 187.41, -311.67, -113.61
# Safe envelope สำหรับ commanded xy (ปรับให้พอดี Arm1 reach)
ROBOT_SAFE_X = (100.0, 500.0)
ROBOT_SAFE_Y = (-250.0, 250.0)
```

Camera params (mode RGB):
```
PixelFormat       = 17301512
ExposureTime      = 52000.0
Gain              = 10.0
Gamma             = 0.7
AcquisitionFrameRate = 60.0
Sharpness         = 50
BalanceWhiteAuto  = 2
```

Vision pipeline:
1. Canny(40,150) → dilate 5x5 → findContours → largest quad area > 200,000 px² = workspace
2. ถ้าหา quad ไม่เจอ → `FIXED_WORKSPACE_CORNERS_PX` (ค่าใหม่ section 2.2)
3. มุมทั้ง 4 → `order_polygon` (CCW) ก่อน `contourArea` / `drawContours` / `pointPolygonTest`
4. มุมทั้ง 4 → `order_quad` (TL,TR,BL,BR) ก่อน `getPerspectiveTransform`
5. HSV → saturation channel → mask ด้วย workspace contour → threshold 40 → open 5x5 → close 7x7 → findContours
6. กรอง: area > 1500, area < workspace_area * 0.2, ไม่ติดขอบภาพ 5 px, อยู่ใน workspace contour
7. แต่ละ object: pixel center → `workspace_report_matrix` (block name), → `pick_matrix_left_arm` (robot xy)

---

## 7. Sequence ที่ใช้สั่งหุ่นจริง (Dobot dashboard port 29999)

```
ClearError()           → reset
EnableRobot()          → ปลดล็อกมอเตอร์
SpeedFactor(50)        → 50% ของความเร็วสูงสุด
DO(1, 0)               → suction off (init)

# per pick
MovJ(x, y, Z_HOVER, 0) → ลอยเหนือเป้า
MovJ(x, y, Z_PICK, 0)  → ลงระดับหยิบ
DO(1, 1)               → suction on
MovJ(x, y, Z_HOVER, 0) → ยกขึ้น
MovJ(DROP_X, DROP_Y, Z_HOVER, 0)  → ไป drop zone
MovJ(DROP_X, DROP_Y, DROP_Z, 0)   → ลงระดับวาง
DO(1, 0)               → suction off
MovJ(DROP_X, DROP_Y, Z_HOVER, 0)  → ยกกลับ
```

ระหว่างคำสั่งหลัก: sleep 1.0–1.5 s, หลัง suction toggle: sleep 0.5 s
ใช้ `GetPose()` ดู `{x,y,z,r,…}` กลับมา (parse จาก response `0,{...},GetPose();`)

---

## 8. การทดสอบที่ผ่าน (2026-05-14)

✅ scan_only เจอ 4 ก้อนตรงทุก block
✅ Calibration ใหม่ round-trip error = 0.00 mm ทั้ง 4 pairs
✅ auto_pick_sequence: หยิบครบ 4 ก้อน (1,1 / 1,2 / 2,1 / 2,2) → drop zone

ก้อนทดสอบ: ลูกบอลพลาสติกขนาด ~30 mm ที่ใช้ผิวด้านบนเป็นจุดดูด

---

## 9. ที่ยังไม่ได้ทำ / ต่อใน session ใหม่ (ARC integration)

1. **Arm2 (192.168.2.8)** — ยังไม่มี calibration; ต้องเก็บ 4 pairs ฝั่ง col 2–3 (`Block 1,2 / 1,3 / 2,2 / 2,3`) แล้ว save แยก
2. **Storage Zone** — ใน `workspace_layout_reference.md` มี แต่ยังไม่มี XYZ ในกรอบหุ่น (ต่างจาก Drop Zone ที่มีพร้อมใช้)
3. **Routing สี → zone** — ตามที่คุยไว้: blue → Drop, green → Storage (ยังไม่ implement, ตอนนี้ทุกอย่างไป Drop หมด)
4. **ARC tool wiring** — `PATH_TO_IF_EDUBOT_ARC\camera_controller.py:scan_workspace` ยังไม่ classify block; `fleet_controller.assign_targets` ยังไม่รองรับ "pick from block X,Y"; ยังไม่มี `pick_and_place_to_zone` ที่รวม motion sequence
5. **Robot motion fault detection** — `MG400Robot` มีอยู่แล้ว แต่ scripts ใน session นี้ใช้ raw socket ไม่ผ่าน fault detection layer
6. **ตำแหน่งที่ใช้ในการพัฒนา**: `order_polygon`/`order_quad` ปัจจุบัน duplicate ในหลาย script — ย้ายเป็น util module เดียวก็ดี

---

## 10. สำหรับ session ใหม่: คำสั่งเริ่มต้น

```powershell
# ทดสอบ vision pipeline (ไม่ขยับหุ่น)
python "D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan\scan_only.py"

# ทดสอบ alignment ปลอดภัย (hover + ลงระดับ ไม่ดูด)
python "D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan\hover_then_descend.py"

# ทำงานจริง: pick & drop ก้อนเดียว
python "D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan\auto_pick_block.py"

# sequence หลายก้อน (แก้ PICK_SEQUENCE ใน source)
python "D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan\auto_pick_sequence.py"

# ARC chat interface
python "PATH_TO_IF_EDUBOT_ARC\main.py"
```

ก่อน run ใดๆ ที่สั่งหุ่น: ตรวจ E-stop พร้อม, แขนหุ่นไม่อยู่ในมุมกล้อง, ก้อนวางในเขต Arm1 reach
