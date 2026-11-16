# สถานะระบบ Calibration ล่าสุด

อัปเดตล่าสุด: 2026-08-03

เอกสารนี้สรุปการปรับปรุงล่าสุดเพื่อเตรียม Calibration แบบสองแขน โดยอ้างอิงโค้ดและ Runtime State ที่ตรวจสอบในวันที่ 3 สิงหาคม 2026

## สรุปสถานะ

ระบบพร้อมให้ผู้ใช้ทำ Workspace Calibration และ Object Calibration แยก Arm1/Arm2 ผ่านเว็บได้แล้วในโหมด Simulation/เตรียม Hardware แต่แขนจะยังไม่ถือว่าพร้อมใช้งานจริงจนกว่าจะมี Geometry และ Object Transform ของแขนนั้นครบ

| แขน | Active | Online (Simulation) | Geometry | Object Transform |
|---|---|---|---|---|
| Arm1 | ใช่ | ใช่ | พร้อม | พร้อม |
| Arm2 | ใช่ | ใช่ | ยังไม่กรอก | ยังไม่พร้อม |

Arm1 มี Object Transform เดิม 6 จุด ค่า fit mean error ประมาณ 0.444 mm และ max error ประมาณ 0.876 mm ส่วน Arm2 ยังไม่มี session/transform ที่นำไปใช้งานได้

## สิ่งที่ทำเสร็จแล้ว

### รองรับโปรไฟล์สองแขนแยกกัน

- ใช้ calibration.arms.Arm1 และ calibration.arms.Arm2 เป็น source of truth
- คง calibration.robot ไว้เพื่อ compatibility กับโค้ดเก่าเท่านั้น
- หน้าเว็บแสดง Arm1 และ Arm2 ให้เลือกได้เสมอ แม้แขนยัง offline หรือยังไม่ calibrate
- Geometry ของแขนที่เลือกไม่เขียนทับ Geometry ของอีกแขน
- Object Calibration session แยก path:

~~~text
runtime_state/local_object_calibration/Arm1/
runtime_state/local_object_calibration/Arm2/
~~~

- Compute & Apply ของ Arm2 เขียนเฉพาะ transform ของ Arm2
- Reset session ของ Arm2 ไม่ลบ session ของ Arm1

### สถานะแขนและ Validation

ระบบแยกสถานะ active, online, geometry_ready และ transform_ready ชัดเจน แขน offline ยังดู profile ได้ แต่ Scan/Save Pose/Compute จะถูกปฏิเสธจนกว่าแขนจะ online

ตรวจ Geometry ดังนี้:

- edge ต้องเป็น top, right, bottom หรือ left
- robot_center_to_outer_center_mm ต้องเป็นบวก
- min_reach_mm ต้องไม่ติดลบ
- เมื่อกรอกทั้งสองค่า ต้องมี min_reach_mm < max_reach_mm
- profile ของแขนที่ยังไม่เริ่มกรอกสามารถบันทึกเป็น draft ได้

### Storage Box และ Storage Groups

Storage Box ยังคงอยู่เพราะเป็นขอบเขตจริงที่วาดจากภาพ แต่ routing ใช้กลุ่มมาตรฐาน ไม่ใช้ชื่อที่ผู้ใช้ตั้งเองเป็น source of truth

ต้องวาดและกำหนดให้ครบ 4 กลุ่มแบบไม่ซ้ำ:

- LT = Left Top
- LB = Left Bottom
- RT = Right Top
- RB = Right Bottom

ชื่อหรือ Label ของ Box เป็น metadata สำหรับแสดงผลเท่านั้น ไม่ใช่ความหมาย Drop ใหม่ ระบบเก็บทั้ง storage_boxes และ storage_regions เพื่อ compatibility และกำหนด region_role เป็น storage_region

### Capacity Slot Mapping

- Registry มี 16 ช่อง แบ่ง 4 กลุ่ม กลุ่มละ 4 ช่อง
- ทุกช่องใช้ role เป็น storage
- Scan ใหม่ใช้ polygon ของ Storage Box ที่เลือกกลุ่มเป็นหลัก
- ชื่อมาตรฐาน LT1-LT4, LB1-LB4, RT1-RT4, RB1-RB4
- เก็บ storage_region_id เพื่อระบุ Box ที่ Slot อยู่ภายใน
- การจับคู่ชื่อเดิมหลัง rescan ใช้ตำแหน่งเดิม

### พิกัดและ Reachability

Object และ Slot ต้องเก็บ workspace_xy ร่วม และ per_arm_robot_plane แยกต่อแขน โดย reachable_by รวมเฉพาะแขนที่มี Transform พร้อมและอยู่ใน reach

ห้ามใช้ legacy robot_plane เป็นพิกัดสั่งงานใหม่ หากยังไม่มี Transform ต้องเป็น transform_not_ready ไม่ใช่ slot_unreachable

### Slot Lifecycle และ Placement Verification

~~~text
empty -> reserved -> verification_pending -> occupied
                                      -> unknown
~~~

place_to_storage_slot จะ reserve ก่อนสั่งวาง และจะไม่ mark occupied จากผล motion สำเร็จเพียงอย่างเดียว ต้อง scan หลังวางและตรวจตำแหน่ง/สี หากตรวจไม่ได้ให้เป็น unknown

### Service/API และ Tool Contract

- แก้ constructor ของ FleetController ให้ตรงกับ runtime
- เพิ่ม endpoint POST /storage/slots/{slot_name}/verify
- แยก error สำหรับ arm inactive/offline, transform ไม่พร้อม, Slot เอื้อมไม่ถึง/ไม่ว่าง และ capacity ไม่ทราบ
- Tool schema place_to_storage_slot(robot_id, reference, color) ให้ตรงกับ runtime method

## ผลการตรวจสอบล่าสุด

คำสั่งที่รัน:

~~~powershell
.\\.venv\\Scripts\\python.exe -m py_compile calibration_web.py arc_runtime/workspace_calibration.py
.\\.venv\\Scripts\\python.exe -m unittest test_calibration_web.py test_workspace_calibration.py test_object_calibration.py test_storage_service.py
~~~

ผลลัพธ์: 47 tests passed, 1 skipped

ตรวจ payload จริง:

- Arm1: active=True, online=True, geometry_ready=True, transform_ready=True
- Arm2: active=True, online=True ใน Simulation, geometry_ready=False, transform_ready=False

## ขั้นตอนที่ผู้ใช้ต้องทำต่อ

1. เปิดเว็บด้วย Python ใน .venv
2. ตรวจหรือวาด outer_box และกำหนดขนาดจริงเป็น mm
3. วาด Storage Box ให้ครบ 4 อัน
4. Assign LT, LB, RT, RB ไม่ซ้ำกัน แล้ว Save
5. Rescan Capacity Slots
6. เลือก Arm1 ตรวจ Geometry และทำ Object Calibration
7. เลือก Arm2 กรอก Geometry จริงและทำ Object Calibration แยก session
8. ตรวจ fit error ของแต่ละแขน
9. ตรวจ per_arm_robot_plane และ reachable_by
10. ก่อน Hardware Test ยืนยัน Camera/Arm online, emergency stop/fault state และความสูง Z ของพื้นผิวจริง

## สิ่งที่ยังไม่เสร็จและห้ามสรุปว่าเสร็จแล้ว

- Arm2 ยังไม่มี Geometry และ Object Transform จริง
- Storage Box ต้องให้ผู้ใช้วาดและ assign ในเว็บก่อนใช้ polygon mapping
- Capacity Slot ต้อง rescan หลังบันทึก Storage Box เพื่อเติม storage_region_id
- Z/surface height ยังไม่ใช่ 3D transform เต็มรูปแบบ ต้องตรวจสอบทางกายภาพ
- ยังไม่ได้ทำ pick/place จริงหลัง Calibration ชุดล่าสุด
- ยังไม่ได้ทำ overlap arbitration/collision scheduling เต็มรูปแบบ
- ห้ามเริ่ม dual-arm hardware placement จนกว่า reachability และ reservation test จะผ่าน

## ไฟล์สำคัญ

- calibration_web.py
- arc_runtime/workspace_calibration.py
- arc_runtime/object_calibration.py
- arc_runtime/capacity_slots.py
- arc_runtime/storage_service.py
- arc_runtime/fleet_controller.py
- runtime_state/workspace_calibration.json
- runtime_state/capacity_slots.json
