# สรุปพฤติกรรม ARC Runtime และแนวทางสำหรับ Tool Context

วันที่: 2026-08-09  
สถานะ: Current behavior summary  
ขอบเขต: เอกสารสรุปพฤติกรรมและแนวคิดของ runtime ปัจจุบัน ยังไม่ใช่แผนแก้ Tools ของ ARC

เอกสารนี้เป็นฐานความเข้าใจก่อนตรวจ Tools ของ ARC อย่างละเอียดในรอบถัดไป

## 1. ภาพรวมระบบปัจจุบัน

ระบบไม่ได้มองพื้นที่เป็นเพียงสามโซนคงที่อีกต่อไป แต่ใช้ข้อมูลจาก Calibration เป็นโมเดลกลาง:

~~~text
Camera frame
  -> Shared Workspace / Outer Box
  -> Work Zones เช่น A1-A8
  -> Storage Regions: LT, LB, RT, RB
  -> Storage Slots: LT1-RB4
  -> Per-arm coordinates
  -> Reachability + Guardrail
  -> Pick/Place + Verification
~~~

ส่วนสำคัญของระบบมี 4 ชั้น:

1. Perception: อ่านภาพและตรวจวัตถุหรือ marker ของ Slot
2. Registry: เก็บตัวตน Slot, กลุ่ม, occupancy และพิกัด
3. Planning: เลือกวัตถุ แขน และ Slot ตาม capability/policy
4. Execution: สั่งแขน จอง Slot park และตรวจผลหลังการกระทำ

ARC ต้องไม่ข้ามชั้นเหล่านี้ด้วยการคาดเดาพิกัดเอง

## 2. Calibration และ Source of Truth

### Workspace Calibration

Workspace ใช้กล้อง Overhead ร่วมกัน:

- outer_box เป็นกรอบอ้างอิงภาพและ Workspace
- workspace_xy เป็นพิกัดกลางที่แขนทั้งสองอ้างอิงร่วมกัน
- Work Zones เป็น polygon ที่ใช้กรอง source/destination
- Storage Boxes เป็น polygon ของพื้นที่จริง ไม่ใช่ Drop destination ใหม่

Storage Box ต้องมีครบ 4 กลุ่มและห้ามซ้ำ:

- LT = Left Top
- LB = Left Bottom
- RT = Right Top
- RB = Right Bottom

ชื่อที่ผู้ใช้ตั้งหรือ label ของ Box เป็น metadata เพื่อแสดงผล ส่วน routing ใช้ group มาตรฐานเป็นหลัก

### Arm Profiles

Calibration ของแขนแยกกันอย่างสมบูรณ์:

~~~text
calibration.arms.Arm1
calibration.arms.Arm2
~~~

แต่ละ profile มีอย่างน้อย:

- edge
- robot_center_to_outer_center_mm
- min_reach_mm
- max_reach_mm
- workspace_to_robot_transform
- fit result และจำนวนจุด

Object Calibration session แยก path:

~~~text
runtime_state/local_object_calibration/Arm1/
runtime_state/local_object_calibration/Arm2/
~~~

การ Compute/Apply หรือ Reset ของแขนหนึ่งห้ามเขียนทับ profile หรือ session ของอีกแขน

ค่าปัจจุบันของ runtime:

- Arm1 ใช้ edge bottom
- Arm2 ใช้ edge top
- Arm1 และ Arm2 มี transform พร้อมและมี fit error ต่ำ
- Arm1 กับ Arm2 ต้องคงพิกัดของวัตถุเดียวกันเป็นคนละ per_arm_robot_plane

## 3. พิกัดและ Reachability

วัตถุและ Slot ต้องเก็บพิกัดอย่างน้อย 3 ระดับ:

- center_px: ตำแหน่งในภาพ
- workspace_xy: ตำแหน่งจริงใน Workspace กลาง
- per_arm_robot_plane[arm_id]: พิกัด XY ใน frame ของแขนนั้น

robot_plane ระดับบนสุดเป็น legacy/reporting compatibility เท่านั้น ห้ามใช้เป็นพิกัดสั่งงานใหม่เมื่อมี per-arm transform แล้ว

ผล reachability ต้องแยกความหมาย:

- transform_not_ready: ยังแปลงพิกัดของแขนนั้นไม่ได้
- slot_unreachable: แปลงได้แล้ว แต่เกิน reach
- reachable: transform พร้อมและอยู่ใน reach
- reachable_by: รายชื่อแขนที่ใช้จุดนั้นได้จริง

ห้ามเปลี่ยน transform_not_ready เป็น slot_unreachable เพราะเป็นคนละสาเหตุ

## 4. Storage Registry และ Slot

Storage Registry อยู่ที่:

~~~text
runtime_state/capacity_slots.json
~~~

Registry ปัจจุบันมี 16 Slot:

~~~text
LT1-LT4
LB1-LB4
RT1-RT4
RB1-RB4
~~~

ทุก Slot มี role storage และควรมี:

- name
- group
- storage_region_id
- occupancy
- occupancy_color หรือ held_object
- workspace_xy
- per_arm_robot_plane
- reachable_by
- reservation/verification metadata เมื่อกำลังทำงาน

Slot ไม่ใช่เพียงจุดพิกัด แต่เป็น resource ที่มี identity และ lifecycle

### Slot Scan

Slot ถูกออกแบบเป็น marker วงกลมสีดำ:

- marker สีดำที่มองเห็น = Slot ว่าง
- วัตถุสีแดง/เขียว/น้ำเงินที่แทนที่ marker = Slot ไม่ว่างและมีสีนั้น
- ตรวจไม่ได้หรือภาพไม่ชัด = unknown

การ Scan ใหม่ต้องรักษาชื่อ Slot เดิมด้วยตำแหน่งเดิม และต้องไม่ทำให้ LB/RB กลับไปเป็น LT/RT

Slot scan ที่เชื่อถือได้ต้องอ่านจาก capacity scan และเขียน Registry เดียวกับ runtime motion

## 5. Slot Lifecycle

สถานะมาตรฐาน:

~~~text
empty
  -> reserved
  -> verification_pending
  -> occupied

verification_pending
  -> unknown
~~~

หลักการ:

1. Resolve Slot ตาม group/reference/color และ arm
2. Reserve แบบ atomic ก่อนเริ่ม motion
3. สั่งวาง
4. Park แขน
5. Scan หลังวาง
6. ตรวจทั้งตำแหน่งและสี
7. ตรวจผ่านจึงเป็น occupied
8. ตรวจไม่ได้เป็น unknown
9. Motion สำเร็จอย่างเดียวห้ามประกาศว่าวางสำเร็จ

ถ้า motion ล้มเหลวให้ release reservation ตาม task policy และไม่ทำให้ Slot occupied

## 6. Object Scan กับ Slot Scan ไม่ใช่สิ่งเดียวกัน

### Object Scan

ใช้ camera_scan เพื่ออ่าน:

- object id
- สี
- workspace_xy
- per_arm_robot_plane
- reachable_by
- work zone หรือ source context

Object scan ตอบคำถามว่า “มีวัตถุอะไรอยู่ที่ไหน และแขนใดเอื้อมถึง”

### Slot Scan

ใช้ capacity scan เพื่ออ่าน:

- Slot ใดมี marker ดำ
- Slot ใดถูกวัตถุแทนที่
- สีของวัตถุใน Slot
- occupancy ของ Registry ทั้ง 16 ช่อง

Slot scan ตอบคำถามว่า “ช่องเก็บของใดว่างหรือมีวัตถุสีอะไร”

ห้ามใช้ object scan แทน Slot scan และห้ามใช้ Slot registry เก่าแทนผล capacity scan ล่าสุด

หมายเหตุจาก Hardware test ล่าสุด: object verifier อาจไม่พบวัตถุหลังวางเมื่อวัตถุทับ black marker แต่ capacity scan สามารถยืนยัน Slot occupied และสีได้ ดังนั้น verifier ต้องเลือก detector ที่เหมาะกับคำถามที่กำลังตรวจ

## 7. Motion และ Z Surface

Pick/place ใช้พิกัด XY จาก profile ของแขน และใช้ Z surface แยกตามแขน:

- Arm1: Z=-150.0
- Arm2: Z=-152.0
- Hover profile ปัจจุบันประมาณ Z=83.41

Runtime ต้องอ่านค่า Z จาก per-arm pick profile ไม่ใช้ SURFACE_Z global เป็นค่าจริงของทุกแขน

ทุก motion ต้องผ่าน:

- active/online check
- geometry/transform readiness
- reachability
- dynamic Z reach
- shared-zone/collision guardrail
- current pose availability

## 8. ขั้นตอน Pick/Place ที่ถูกต้อง

ลำดับปกติ:

~~~text
1. Scan object
2. เลือก object ตาม color/source และ reachable_by
3. เลือก arm ที่ active/online และมี transform
4. Resolve Storage group/slot
5. ตรวจ slot empty และ arm reachability
6. Reserve slot
7. Pick: hover -> descend -> suction on -> lift
8. Place: hover -> descend -> suction off -> lift
9. Park/Home
10. Capacity scan หลังวาง
11. Verify ตำแหน่งและสี
12. Commit occupied หรือ unknown
13. รายงานผลตาม verification ไม่ใช่ตาม motion response อย่างเดียว
~~~

ถ้า source และ destination ใช้แขนเดียวกัน ให้ direct plan

ถ้าต้องใช้คนละแขน ให้ใช้ handoff plan เฉพาะเมื่อ:

- source arm pick ได้
- receiver arm destination reach ได้
- source arm release และ retreat แล้ว
- shared station ปลอดภัยก่อน receiver เข้า

ยังไม่ควรทำ dual-arm motion พร้อมกันโดยไม่มี arbitration/scheduling ที่ชัดเจน

## 9. Guardrail และ Error Semantics

ARC ต้องแยกสาเหตุของ failure ให้ชัด:

- arm_inactive
- arm_offline
- transform_not_ready
- slot_unreachable
- slot_unavailable
- capacity_full
- capacity_unknown
- needs_storage_policy
- guardrail/shared-zone collision
- current pose unavailable
- post-place verification failed

เมื่อ recovery หรือ reconnect ต้อง invalidate pose cache และอ่าน pose จริงใหม่

ถ้า pose ปัจจุบันอ่านไม่ได้ ห้ามเดาว่าแขนอยู่ Home หรือปลอดภัย

## 10. พฤติกรรม ARC ที่มีอยู่แล้ว

ARC runtime มี tool สำหรับ:

- camera_scan
- list_storage_groups
- list_storage_slots
- resolve_storage_slot
- place_to_storage_slot
- planning แบบ calibrated workspace/Storage
- arm status และ motion guardrail

อย่างไรก็ตาม ณ วันที่เอกสารนี้:

- list_storage_slots อ่าน Registry ล่าสุดได้
- resolve_storage_slot หา Slot ว่างที่แขนเอื้อมถึงได้
- ยังไม่มี tool ระดับ ARC ที่ชื่อชัดเจนสำหรับ fresh scan_storage_slots
- camera_scan เป็น object scan ไม่ใช่ Slot scan
- service endpoint POST /storage/scan และ runtime storage_service.scan() ทำ fresh Slot scan ได้

ดังนั้น ARC context ต้องแยกคำว่า “scan objects” กับ “scan storage slots” อย่างชัดเจน

## 11. หลักคิดสำหรับ Tool Context ในอนาคต

Tool context ไม่ควรยัดข้อมูล raw ทั้งหมดให้ AI แล้วให้ AI คำนวณเอง ควรให้ context เป็น snapshot ที่มี contract ชัดเจน:

### A. Capability snapshot

- active/online ของแต่ละแขน
- geometry_ready
- transform_ready
- fit error
- current fault/pose availability
- surface Z profile

### B. Perception snapshot

- object list จาก scan ล่าสุด
- slot list จาก capacity scan ล่าสุด
- timestamp และ source image
- stale status

### C. Planning contract

- source reference
- object filter
- selected arm หรือ arm constraint
- destination group/slot
- direct vs handoff
- required verification

### D. Execution contract

- tool จะขยับหรือไม่
- ต้อง reserve หรือไม่
- expected lifecycle transition
- success criteria
- failure code
- recovery/rollback behavior

### E. Reporting contract

ผลลัพธ์ควรตอบแยกกัน:

- motion result
- tool/suction result
- park result
- scan result
- verification result
- final Slot state

## 12. กฎที่ ARC ต้องยึด

1. อย่าเลือกแขนจากชื่อ storage_zone/drop_zone แบบ legacy
2. อย่าใช้ robot_plane เก่าแทน per_arm_robot_plane[arm_id]
3. อย่าคิดว่า Slot ว่างเพียงเพราะ place_at() ตอบสำเร็จ
4. อย่าเริ่ม pick/place หาก arm หรือ transform ยังไม่พร้อม
5. อย่าจอง Slot จากข้อมูล snapshot ที่ stale
6. อย่ารายงาน occupied หากยังไม่มี post-place verification
7. อย่าเรียก object scan แล้วอ้างว่าเป็น Slot scan
8. อย่าคำนวณ reach จาก current pose แทน robot base/profile
9. อย่าให้การ calibrate หรือ reset Arm หนึ่งเปลี่ยนข้อมูลอีก Arm
10. เมื่อไม่แน่ใจให้ fail closed พร้อม error code ที่แยกสาเหตุได้

## 13. สถานะ Hardware ล่าสุดที่ใช้เป็น baseline

การทดสอบล่าสุด:

- Restart Calibration Web สำเร็จ
- Arm2 online และกลับ Home ได้
- Arm2 ใช้ Z=-152.0
- หยิบวัตถุสีน้ำเงินได้
- วางที่ RT1 ได้
- Capacity scan ยืนยัน RT1=occupied, สี blue
- Registry สุดท้าย: occupied 6/16, empty 10/16, ไม่มี unknown/reservation ค้าง

Baseline นี้ใช้ตรวจความสอดคล้องของ ARC Tools ในรอบถัดไปได้ แต่ไม่ควร hardcode เป็นข้อมูลถาวร เพราะ occupancy เปลี่ยนตามการใช้งานจริง

## 14. Files ที่เป็นหลักฐานของ behavior

- arc_runtime/workspace_calibration.py
- arc_runtime/object_calibration.py
- arc_runtime/capacity_slots.py
- arc_runtime/storage_service.py
- arc_runtime/camera_controller.py
- arc_runtime/fleet_controller.py
- arc_runtime/surface_model.py
- arc_runtime/ai_fleet.py
- calibration_web.py
- runtime_state/workspace_calibration.json
- runtime_state/capacity_slots.json

เอกสารนี้สรุป behavior ล่าสุดเท่านั้น การวางแผนปรับ Tools/Tool Context ต้องตรวจ implementation และ contract ของ ARC ต่อจากเอกสารนี้อีกครั้ง
## 15. Implementation Update - 2026-08-09

ส่วนนี้เป็นสถานะล่าสุดและแทนข้อความในหัวข้อก่อนหน้าที่ระบุว่า ARC ยังไม่มี fresh Slot scan tool

สิ่งที่เปิดใช้งานแล้ว:

- get_runtime_context: อ่าน active/online, geometry/transform readiness, Z profile, Workspace readiness, Slot summary และอายุของ Registry โดยไม่สแกนหรือขยับแขน
- scan_storage_slots: สแกน capacity ของ Storage ใหม่และอัปเดต Registry กลาง
- list_storage_slots: อ่าน Registry ล่าสุด พร้อม updated_at, source_scan, summary และ Slot ที่กรองแล้ว
- resolve_storage_slot: resolve กลุ่มหรือ Slot โดยตรวจ active/online/transform/reachability และคืน error code แบบแยกสาเหตุ
- place_to_storage_slot: resolve -> reserve -> place -> park -> capacity scan -> verify -> occupied/unknown
- generic drop ของ ARC ปฏิเสธ LT/LB/RT/RB, Slot เช่น LB3, และ legacy aliases เพื่อบังคับใช้ verified placement path
- camera_scan ยังคงเป็น object scan เท่านั้น และไม่ถูกใช้แทน Slot scan
- workspace_xy ใช้สำหรับ shared geometry; motion XY ต้องมาจาก per_arm_robot_plane[robot_id]
- Arm2 ที่มี applied transform แล้วจะไม่ถูกตีความว่า geometry ไม่พร้อมเพียงเพราะ draft configured flag จาก UI รุ่นเก่าค้างเป็น false

Runtime interfaces ที่เพิ่ม:

    arc.py workspace --json context
    arc.py storage --json scan --expected-count 16
    arc.py storage --json list [--group LT|LB|RT|RB] [--occupancy ...]
    arc.py storage --json resolve [--reference ...] [--color ...] [--robot-id ...]
    arc.py storage --json place --robot-id Arm1|Arm2 [--reference ...] [--color ...]

HTTP interfaces:

    GET  /runtime/context
    POST /storage/scan
    GET  /storage/slots
    POST /storage/resolve
    POST /storage/place

ARC TypeScript default surface และ simple read-only surface ได้รับเครื่องมือ Slot ใหม่แล้ว ฝั่ง default surface มี verified placement ส่วน simple surface ไม่มี motion placement tool

Verification ล่าสุด:

- Python regression: 98 tests ผ่าน, skipped 1
- ARC Storage tool tests: 4 tests ผ่าน
- ARC production build สำเร็จ (dist/cli.mjs, dist/sdk.mjs)
- global arc package อัปเดตจาก local build แล้ว
- ARC_PYTHON_BIN ระดับ user ถูก pin ไปยัง .venv\Scripts\python.exe ของ workspace นี้
- TypeScript repository-wide typecheck ยังมี baseline errors จำนวนมากในโมดูลที่ไม่เกี่ยวกับ RobotControl; focused Storage tests และ production build ผ่าน

Baseline Registry ขณะอัปเดตเอกสาร:

    slot_count=16
    empty=10
    occupied=6
    unknown=0
    reserved=0
    verification_pending=0