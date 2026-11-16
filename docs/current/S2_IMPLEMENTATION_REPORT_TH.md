# รายงานสถานะระบบ S2: AI Vision และการประสานงานแขนกลหลายตัว

ปรับปรุงล่าสุด: 19 ตุลาคม 2026

## สรุปผลการพัฒนา

ระบบ S2 ถูกแยกเป็นขอบเขตที่ตรวจสอบได้ในแพ็กเกจ
s2_vision_coordination/ โดยรับผลจากการสแกนกล้องของ ARC แล้วทำหน้าที่
ตรวจรูปแบบข้อมูล เก็บหลักฐานพิกัด และสร้างข้อเสนอการจัดสรรวัตถุให้แขนกล
แบบ deterministic ระบบ S2 ไม่มีการเชื่อมต่อ MG400 ไม่มีคำสั่งเคลื่อนที่ และ
ไม่ตัดสินใจแทน safety guardrail ของ ARC

สถานะที่ยืนยันได้:

- รับข้อมูล camera_scan ของ ARC และแปลงเป็น VisionSnapshot
- เก็บ center_px, workspace_xy, per_arm_robot_plane,
  reachable_by, preferred_arm, zone และ block
- ตรวจรหัสวัตถุซ้ำ ข้อมูลพิกัดผิดรูป ค่าไม่ใช่ตัวเลข และพิกัดไม่ finite
- ตรวจ saved workspace, stale calibration และ calibration readiness
- จัดสรรวัตถุให้เฉพาะ active arm ที่อยู่ใน reachable_by และมี transform
- เคารพ preferred_arm เมื่อแขนนั้นใช้งานได้
- กระจายงานที่เหลือด้วยจำนวนงานปัจจุบันและรหัสแขนเป็น tie-breaker
- รายงานวัตถุที่จัดสรรไม่ได้พร้อมเหตุผลและ transform ที่พบ
- แปลงผลเป็นคำขอรูปแบบ ARC ได้โดยไม่ execute motion
- ส่งต่อ `object_id` ไปยัง ARC assignment path เพื่อไม่เลือกวัตถุผิดตัว
  เมื่อมีวัตถุสีเดียวกันหลายชิ้น
- มี standalone web planner สำหรับ snapshot/live scan, การเลือกวัตถุ แขน
  จุดวาง การเรียงลำดับ step, confirm และ execute แบบ sequential
- ตรวจผลหลังปฏิบัติการในระดับ S2 ว่าวัตถุต้นทางหายจาก scan หรือยัง
- บันทึกว่า parallel เป็นเพียง requested mode; S2 ไม่ประกาศว่า parallel ปลอดภัย

## โครงสร้างโมดูล

| โมดูล | หน้าที่ |
| --- | --- |
| contract.py | สัญญาข้อมูล snapshot, object, assignment และ plan |
| planner.py | การจัดสรร object-to-arm แบบ deterministic |
| adapter.py | เชื่อม scanner, ตรวจ calibration, สร้างผลลัพธ์สำหรับรายงาน |
| verification.py | ตรวจ object removal จาก scan ก่อน/หลัง |
| standalone.py | workflow plan, confirm, re-scan และ execute ผ่าน ARC |
| web.py + web/ | standalone operator UI และ HTTP API |
| test_s2_vision_coordination.py | unit และ simulator integration tests |

จุดเรียกหลัก:

    from s2_vision_coordination import S2VisionCoordinator

    coordinator = S2VisionCoordinator(
        camera_controller,
        active_arm_ids=("Arm1", "Arm2"),
    )
    result = coordinator.scan_and_plan(camera_id="Cam1")

เมื่อ result.ok เป็นจริง จะอ่านข้อมูลรายงานได้จาก result.to_dict() และสร้าง
คำขอสำหรับ ARC ได้จาก result.to_arc_assignment_requests() การเรียกหลัง
ปฏิบัติการใช้ coordinator.verify_after_scan(before_result, after_payload).

## ผลการทดสอบ

คำสั่ง:

    python -m unittest -v test_s2_vision_coordination.py
    python -m compileall -q s2_vision_coordination

ผลล่าสุด: S2 tests ผ่าน 10/10 และ compile ผ่าน รวม integration test ที่ยืนยัน
การส่งต่อ object_id ของวัตถุสีเดียวกันไปยัง ARC โดยไม่เลือกวัตถุผิดตัว

Standalone workflow ใน simulator ผ่านลำดับ scan, create plan, confirm,
pick/place ผ่าน FleetController และ post-step verification โดยใช้ world state
ชั่วคราว ส่วนเว็บ smoke test ผ่าน health, scan, options, plan, confirm และ reset
ที่ `http://127.0.0.1:8788`

การทดสอบ integration ใช้ CameraController กับ WorldModel จำลอง ตรวจว่า
ระบบสร้างแผนได้โดยไม่เรียกคำสั่งเคลื่อนที่ และตรวจการหายของ object หลัง
simulated pick ได้สำเร็จ

Regression ที่เกี่ยวข้อง:

- test_object_calibration.py: ผ่านทั้งหมด
- test_workspace_calibration.py: ผ่านทั้งหมด
- ชุด test_robot_guardrails.py พบ failure 8 เคสและ error 1 เคสจาก
  logic semantic/motion ที่อยู่นอกขอบเขตไฟล์ S2; ไม่ได้แก้หรือย้าย source
  เดิมในรอบนี้

## ขอบเขตการอ้างผลในรายงาน

หลักฐานปัจจุบันรองรับการอ้างว่า S2 มี prototype ที่ทำงานได้ในด้าน
การรับผลตรวจจับ การแปลงพิกัดต่อแขน การคัดกรอง reachability การจัดสรรงาน
และการตรวจผลเชิง visibility หลังปฏิบัติการ

ยังไม่ควรอ้างเป็นค่าความแม่นยำของ AI Vision หรือประสิทธิภาพการป้องกันการชน
โดยไม่มีชุดภาพทดสอบจริงและข้อมูลการทดลองที่บันทึกไว้ การตรวจหลังปฏิบัติการ
ยืนยันได้เพียงว่า object ต้นทางไม่ปรากฏใน scan หลังงาน ไม่ได้ยืนยันตำแหน่ง
ปลายทางหรือความสำเร็จของ motion ซึ่งเป็นความรับผิดชอบของ ARC execution และ
guardrail

## ตัวชี้วัดที่ควรเก็บเพิ่ม

1. precision, recall และ object-count accuracy จากชุดภาพที่มี ground truth
2. pixel-to-workspace และ workspace-to-arm coordinate error
3. reachable-arm classification accuracy
4. assignment success rate และ load balance ระหว่างแขน
5. latency ของ scan, planning และ verification
6. จำนวน stale-calibration rejection, unassigned object และ scan failure
7. collision/near-miss และผลการวางจริงจากหลักฐาน ARC แยกจากคะแนน S2
