# Camera Implemented

Date: 2026-05-14

เอกสารนี้สรุปสิ่งที่ได้เพิ่มเข้าระบบ ARC ฝั่งกล้องแล้วใน workspace `PATH_TO_IF_EDUBOT_ARC`

## 1. เป้าหมายที่ทำในรอบนี้

รอบนี้ตั้งใจเพิ่มเฉพาะแกนที่จำเป็นสำหรับให้ AI ใช้กล้องร่วมกับหุ่นยนต์ได้จริง:

- เปิดกล้องและตรวจสถานะได้
- สแกนภาพและหา object ใน workspace ได้
- แปลงพิกัดจาก pixel เป็น `robot_plane`
- ประเมินว่า object ชิ้นนั้นแขนตัวไหนหยิบถึง
- ใช้ผลจากกล้องไปต่อกับ flow `pick + place(destination)`

ยังไม่ได้ทำระบบ vision เต็มรูปแบบ เช่น tracking, multi-camera, auto-calibration, หรือ production monitoring

## 2. แหล่งที่มาของ logic กล้อง

logic หลักถูกดึงมาจากโปรเจกต์:

- `D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan`

ส่วนที่นำมาใช้จริง:

- การเปิดกล้องผ่าน MVS SDK
- การอ่าน frame
- การหา workspace ด้วย edge detection
- การ detect object จาก saturation threshold
- การประเมินสีแบบง่าย `red/green/blue`
- การใช้ perspective transform เพื่อ map `pixel -> robot_plane`

ส่วนที่ยังไม่ได้นำมาเป็นระบบกลาง:

- UI แบบ `cv2.imshow`
- keyboard control
- calibration click tool
- robot socket control ที่ฝังอยู่ในโปรเจกต์กล้องเดิม

## 3. ไฟล์ที่เพิ่ม/แก้ใน ARC Python

### ไฟล์ใหม่

- [camera_controller.py](PATH_TO_IF_EDUBOT_ARC\camera_controller.py:1)
  - controller หลักของระบบกล้อง
  - เปิดกล้อง
  - scan workspace
  - detect object
  - map พิกัด
  - คำนวณ `reachable_by`

### ไฟล์ที่แก้

- [config.py](PATH_TO_IF_EDUBOT_ARC\config.py:1)
  - เพิ่ม `CAMERA_CONFIG`
  - เพิ่ม `ARM_REACH_ZONES`
  - เพิ่ม `ARM_PICK_CONFIG`
  - เพิ่ม `ARM_NAMED_TARGETS`

- [arc.py](PATH_TO_IF_EDUBOT_ARC\arc.py:1)
  - เพิ่มคำสั่ง `camera`
  - เพิ่มคำสั่ง `arm pick-detected-place`

- [fleet_controller.py](PATH_TO_IF_EDUBOT_ARC\fleet_controller.py:1)
  - เพิ่ม orchestration ระหว่างกล้องกับหุ่น
  - เพิ่ม `pick_at`
  - เพิ่ม `place_to_named_target`
  - เพิ่ม `pick_detected_and_place`
  - เพิ่ม `post-pick verification`

- [robot_arm.py](PATH_TO_IF_EDUBOT_ARC\robot_arm.py:1)
  - เพิ่ม `set_tool_output`

- [ai_fleet.py](PATH_TO_IF_EDUBOT_ARC\ai_fleet.py:1)
  - เพิ่ม tools ฝั่ง Python interactive runtime

## 4. Camera capability ที่มีแล้ว

### 4.1 Camera status

รองรับการตรวจ:

- camera project path
- SDK readiness
- calibration readiness
- mode ปัจจุบัน

ผ่านคำสั่ง:

```bash
python arc.py camera --json status
```

### 4.2 Camera scan

รองรับการ:

- เปิดกล้อง
- อ่าน frame จริง
- detect workspace
- detect object
- classify สี
- map เป็น `robot_plane`
- ประเมิน `reachable_by`

ผ่านคำสั่ง:

```bash
python arc.py camera --json scan
```

## 5. Reachability ที่มีแล้ว

ระบบตอนนี้ให้ AI รู้ได้ว่า object ชิ้นไหนแขนตัวไหนหยิบถึง โดยอาศัย:

- `ARM_REACH_ZONES` ใน [config.py](PATH_TO_IF_EDUBOT_ARC\config.py:1)

รูปแบบปัจจุบันเป็น:

- zone-based reachability
- ยังไม่ใช่ inverse kinematics
- ยังไม่ใช่ motion feasibility check จริง

ผลลัพธ์ของ object แต่ละชิ้นจะมี field:

- `reachable_by`
- `preferred_arm`

## 6. Named destination ที่มีแล้ว

แนวคิดที่ใช้คือ:

- ไม่ผูก workflow กับคำว่า `home`
- ใช้ `destination` เป็น named target

ตัวอย่าง destination ปัจจุบัน:

- `home`
- `drop_zone`

เก็บใน:

- [config.py](PATH_TO_IF_EDUBOT_ARC\config.py:1)

## 7. Pick and place flow ที่มีแล้ว

flow ปัจจุบันสำหรับแขนเดี่ยว:

1. กล้องหา object ตามสี
2. ตรวจว่าแขนที่ระบุหยิบถึงไหม
3. approach ที่ `hover_z`
4. descend ไป `pick_z`
5. suction on
6. lift
7. move ไป destination
8. suction off
9. post-pick verification

คำสั่งที่ใช้งานได้:

```bash
python arc.py arm pick-detected-place Arm1 --color blue --destination home
```

## 7.1 Motion-only named target flow

เพิ่มแล้วเพื่อแก้ ambiguity ของคำว่า `home`

แนวคิด:

- `go home` = motion-only
- `pick ... place to home` = grasp workflow

คำสั่งใหม่:

```bash
python arc.py arm move-to-named-target Arm1 --destination home
```

และเพิ่ม low-level suction control:

```bash
python arc.py arm tool-state Arm2 --enable off
```

## 8. Post-pick verification

เพิ่มแล้วใน [fleet_controller.py](PATH_TO_IF_EDUBOT_ARC\fleet_controller.py:1)

แนวคิด:

- หลังวางเสร็จให้ scan ใหม่
- เช็กว่ามี object สีเดียวกันยังอยู่ใกล้ source เดิมหรือไม่
- ถ้ายังอยู่ ถือว่า verify ไม่ผ่าน

ผลลัพธ์จะมี:

- `post_pick_verification`
- `verified`
- `nearby_matches`

## 9. Tools ที่ runtime ใช้ได้แล้ว

### Python runtime

ใน [ai_fleet.py](PATH_TO_IF_EDUBOT_ARC\ai_fleet.py:1) มี:

- `camera_status`
- `camera_scan`
- `camera_assign_targets`
- `pick_detected_place`

### Terminal ARC runtime

ผูกเพิ่มแล้วใน:

- `external\Agent-Robotic-Cluster\src\tools\RobotControl`

tools ที่เพิ่ม:

- `camera_status`
- `camera_scan`
- `camera_assign_targets`
- `move_to_named_target`
- `set_tool_state`
- `pick_detected_place`

## 10. Contract update ล่าสุด

รอบล่าสุดได้แก้ contract สำคัญของระบบแล้ว:

- `home` ไม่ใช่คำพ้องของ `pick and place`
- `home` ในชั้น ARC เป็น named destination
- เพิ่ม `move_to_named_target` สำหรับ motion-only
- `pick_detected_place` ใช้เฉพาะเมื่อ user มี intent เรื่องหยิบจริง

นอกจากนี้ `home` ชั้น ARC ตอนนี้ใช้ค่าจริงชุดเดียวกันทั้งสองแขน:

- `Arm1 home` -> `x=350, y=0, z=0`
- `Arm2 home` -> `x=350, y=0, z=0`

เก็บใน:

- `ROBOT_FLEET_CONFIG[*].shared_home_pose`
- `ARM_NAMED_TARGETS[*]["home"]`

หมายเหตุ:

- แขนยังหันหน้าเข้าหากันตาม layout จริง
- แต่ canonical `home` สำหรับ orchestration ตอนนี้เป็น pose เดียวกันตามที่หน้างานยืนยัน

## 11. Workspace / Calibration status ปัจจุบัน

สถานะล่าสุดของ `camera scan`:

- detect object ได้ `4 ชิ้น`
- แยกสี `green` และ `blue` ได้ถูกกับหน้างานล่าสุด
- แต่ `workspace.detected` ยังเป็น `false`

แปลว่า:

- ระบบยัง scan ได้
- แต่ตอนนี้กำลังใช้ fallback logic มากกว่าการหา workspace contour จากภาพแบบเสถียร

calibration ปัจจุบันใน [config.py](PATH_TO_IF_EDUBOT_ARC\config.py:1) map ไปยัง `robot_points` ชุดนี้:

- `[139.992, -190.566]`
- `[128.977, 199.616]`
- `[368.753, -184.948]`
- `[362.773, 197.274]`

ค่าชุดนี้ถูกยกมาจากโปรเจกต์กล้องเดิมโดยตรง:

- `pts_camera_left` ใน `D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan\main.py`
- `pts_robot_left` ใน `D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan\main.py`

logic หา workspace ปัจจุบันก็อ้างอิง flow เดิมชุดเดียวกัน:

- grayscale
- gaussian blur
- `cv2.Canny(40, 150)`
- dilate kernel `5x5`
- หา contour สี่เหลี่ยมที่ `area > 200000`
- เก็บ contour ล่าสุดไว้แบบ `saved_workspace_contour`

ถ้าดูเป็น bounding box ใน robot-plane จะได้ประมาณ:

- width ≈ `239.776 mm`
- height ≈ `390.182 mm`

ดังนั้น **workspace ที่ระบบใช้อยู่ตอนนี้ยังไม่ใช่สี่เหลี่ยมผืนผ้า 60 x 40**

สิ่งที่ควรจะเป็นตาม requirement ใหม่:

- workspace หลัง calibrate ควรเป็น rectangle
- width = `60`
- height = `40`

ข้อสรุป:

- object detection ใช้งานได้
- contract ใช้งานได้
- แต่ calibration model ของ workspace ยังไม่ตรง requirement `60 x 40`

## 12. สถานะปัจจุบัน

สิ่งที่พร้อม:

- camera integration
- scan object จากกล้องจริง
- mapping pixel -> robot_plane
- reachability
- Arm1 detected pick/place
- post-pick verification

สิ่งที่ยังไม่พร้อมเต็ม:

- calibration tuning ระดับ production
- Arm2 flow ที่เสถียรเท่า Arm1
- multi-arm orchestration
- clear-all loop
- robust recovery policy สำหรับงานต่อเนื่อง

## 13. ข้อสรุป

ระบบกล้องถูกเพิ่มเข้า ARC แล้วในระดับ prototype ที่ใช้งานจริงได้:

- กล้องมองเห็น
- AI อ่านผลได้
- ระบบรู้คร่าวๆ ว่าแขนไหนหยิบถึง
- Arm1 สามารถใช้ flow `pick + place(destination)` ได้

รอบถัดไปควรเน้น:

1. ทำ Arm1 ให้เสถียร
2. จูน calibration และ reachability
3. ค่อยขยายไป Arm2 และ multi-arm
