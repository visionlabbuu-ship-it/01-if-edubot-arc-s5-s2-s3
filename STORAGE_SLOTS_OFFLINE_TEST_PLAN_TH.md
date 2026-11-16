# แผนทดสอบ ARC Storage Slots แบบไม่ใช้ฮาร์ดแวร์

เอกสารนี้ใช้ทดสอบระบบหลังปรับ Storage Slot โดยยังไม่ต้องเชื่อมต่อกล้องจริงและไม่สั่งแขนจริง

## เป้าหมาย

- ทุกช่องเป็น Storage และไม่มีความหมาย Drop
- Registry เดิม migrate เป็นกลุ่ม LT, LB, RT, RB
- เรียก Slot ได้ด้วยชื่อเต็ม ชื่อกลุ่ม และภาษาธรรมชาติ
- policy สีทำงานถูกต้อง
- ปฏิเสธ Slot ที่เต็ม, unknown หรือแขนเอื้อมไม่ถึง
- occupancy update ไม่ทำลายพิกัด Calibration
- API และ AI tools expose ข้อมูลชุดเดียวกัน
- Regression ของ Calibration เดิมยังผ่าน

## สิ่งที่ไม่ทำ

- ไม่เปิดกล้อง MVS
- ไม่เรียก scan จากกล้องจริง
- ไม่เรียก CameraController.connect()
- ไม่เรียก FleetController.connect_all()
- ไม่เรียก move, grip, pick หรือ place กับแขนจริง
- ไม่ตัดสินความถูกต้องของพิกัดจากภาพจริง

## คำสั่งทดสอบหลัก

รันจาก project root:

    .\.venv\Scripts\python.exe -m py_compile arc_runtime/storage_service.py arc_runtime/capacity_slots.py arc_runtime/fleet_controller.py arc_runtime/ai_fleet.py arc_runtime/service.py
    .\.venv\Scripts\python.exe -m unittest test_storage_service.py test_workspace_calibration.py test_object_calibration.py

ผลล่าสุด: 35 tests passed และ py_compile ผ่าน

## Scenario

### A. Registry Migration

ตรวจ runtime_state/capacity_slots.json:

- version = 2
- purpose = named_storage_slots
- groups มี LT, LB, RT, RB
- ทุก Slot มี role = storage
- ไม่มี group หรือ role เป็น drop
- จำนวนรวม 16 ช่อง
- ค่าเริ่มต้น: empty 16, occupied 0, unknown 0

### B. Slot Reference Parsing

ต้อง resolve ได้เหมือนกัน:

- LT3
- lt-3
- slot 3 of LT
- ช่องที่ 3 ของ LT

ผลต้องเป็น slot_name = LT3 และ group = LT

### C. Group Selection

- LT เลือก Empty Slot ลำดับแรกใน LT
- RB เลือก Empty Slot ลำดับแรกใน RB
- ไม่ระบุกลุ่มและไม่มี policy ต้องได้ needs_storage_policy
- ระบุชื่อเต็มต้องไม่ถูกเปลี่ยนไปกลุ่มอื่น

### D. Color Policy

ใช้ Registry ชั่วคราว:

    {
      "color_groups": {
        "red": ["RB"],
        "green": ["LB"],
        "blue": ["RT"]
      }
    }

ตรวจ resolve(color="red") ต้องเลือก RB เท่านั้น หลังทดสอบต้องไม่เปลี่ยน policy runtime โดยไม่ตั้งใจ

### E. Capacity Errors

สร้าง Registry ชั่วคราว:

- occupied -> capacity_full
- unknown -> slot_unavailable
- กลุ่มไม่มี Empty Slot -> capacity_full
- ไม่มี transform หรือ reachable=false สำหรับ Arm1 -> slot_unreachable
- สีไม่มี mapping -> needs_storage_policy

### F. Occupancy Update

ทดสอบ update_occupancy() ด้วยไฟล์ชั่วคราว:

- เปลี่ยน Slot เป็น occupied
- บันทึก held_object.color และ object_id
- บันทึก occupancy_source และ verification
- workspace_xy, per_arm_robot_plane, center_px ต้องคงเดิม
- เปลี่ยนกลับเป็น empty ต้องล้าง held_object

### G. API Function Test แบบไม่เปิด Server

เรียก function โดยตรง:

    from arc_runtime import service
    service.storage_slots(group="LT")
    service.storage_resolve(service.StorageResolveRequest(reference="LT3"))
    service.get_storage_policy()

ต้องได้ Registry และ Slot ชุดเดียวกับ storage_service

### H. AI Tool Contract

ตรวจ OllamaFleetManager.get_api_tools() และ get_available_functions() ว่ามี:

- list_storage_slots
- resolve_storage_slot
- set_storage_policy

ยังไม่เรียก function ที่สั่งแขน

### I. Calibration Regression

    .\.venv\Scripts\python.exe -m unittest test_workspace_calibration.py test_object_calibration.py

ต้องผ่านโดยไม่เปลี่ยน Transform หรือ Session เดิม

## ผลทดสอบล่าสุด

- 35 tests passed
- py_compile ผ่าน storage_service, capacity_slots, fleet_controller, ai_fleet, service
- Registry มี 16 Storage Slots ครบ 4 กลุ่ม
- LT3, lt-3, slot 3 of LT, ช่องที่ 3 ของ LT resolve เป็น LT3
- unknown -> slot_unavailable
- unreachable -> slot_unreachable
- occupancy update บันทึก held object และคงพิกัดเดิม
- ยังไม่มีการทดสอบกล้องหรือแขนจริง

## ลำดับเมื่อพร้อม Hardware

1. ตรวจ service, camera และ arm health
2. ทดสอบ read-only API และ Registry
3. ทดสอบ camera scan และตำแหน่ง
4. ทดสอบวาง 1 วัตถุที่ Slot เดียว
5. park แขนแล้ว post-place scan
6. ยืนยันสีและตำแหน่ง
7. ทดสอบหยิบออกและ post-pick scan
8. ค่อยทดสอบ policy หลายสีและสองแขน

ถ้าพิกัดไม่ตรง, Slot ถูกเลือกผิด, กล้องยืนยันไม่ได้ หรือแขน offline ให้หยุดทันที
