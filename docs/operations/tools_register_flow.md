# Tools Register Flow

Date: 2026-05-14

เอกสารนี้สรุป flow การพัฒนาและ register tools ในระบบ ARC เพื่อให้ใช้เป็นแนวทางในงานรอบต่อไป

## 1. เป้าหมายของเอกสาร

เวลาจะเพิ่ม tool ใหม่ในระบบนี้ ต้องแยกให้ชัดก่อนว่า tool จะไปอยู่ที่ runtime ไหน

ตอนนี้มีอย่างน้อย 2 ชั้น:

1. Python ARC runtime ใน `PATH_TO_IF_EDUBOT_ARC`
2. Terminal `arc` runtime ใน `external\Agent-Robotic-Cluster`

บทเรียนสำคัญจากรอบนี้คือ:

- แค่เพิ่ม logic ใน Python ยังไม่พอ
- ถ้า terminal `arc` ใช้ runtime อีกตัวหนึ่ง ต้อง register tools เข้า runtime นั้นด้วย

## 2. แยก runtime ให้ถูกก่อน

ก่อนเริ่มเพิ่ม tools ให้เช็กก่อนว่าผู้ใช้เรียกจากอะไร

### 2.1 Python runtime

ตัวอย่าง:

```bash
python main.py
python arc.py ...
```

### 2.2 Global terminal arc

ตัวอย่าง:

```bash
arc
arc -p "..."
```

ในระบบนี้เคยตรวจพบว่า `arc` ชี้ไปที่:

- `C:\Users\informatics\AppData\Roaming\npm\arc`
- และ package จริงอยู่ที่
- `C:\Users\informatics\AppData\Roaming\npm\node_modules\arc-openclaude`

ดังนั้นการแก้เฉพาะ `PATH_TO_IF_EDUBOT_ARC\ai_fleet.py` ไม่ทำให้ terminal `arc` เห็น tools ใหม่

## 3. Flow มาตรฐานในการเพิ่ม tool ใหม่

### Step 1: ทำ capability จริงใน Python ก่อน

เริ่มจาก logic จริงก่อน เช่น:

- controller
- config
- CLI command
- orchestration

ตัวอย่างจากรอบนี้:

- [camera_controller.py](PATH_TO_IF_EDUBOT_ARC\camera_controller.py:1)
- [fleet_controller.py](PATH_TO_IF_EDUBOT_ARC\fleet_controller.py:1)
- [arc.py](PATH_TO_IF_EDUBOT_ARC\arc.py:1)

หลักคือ:

- ให้ capability รันได้จาก CLI ก่อน
- ทดสอบให้ command ทำงานได้จริง

ตัวอย่าง:

```bash
python arc.py camera --json status
python arc.py camera --json scan
python arc.py arm pick-detected-place Arm1 --color blue --destination home
```

### Step 2: ถ้าจะใช้ใน Python interactive runtime ให้ register ใน ai_fleet.py

ถ้า user ใช้:

```bash
python main.py
```

ให้เพิ่ม 3 จุดใน [ai_fleet.py](PATH_TO_IF_EDUBOT_ARC\ai_fleet.py:1):

1. method implementation
2. schema ใน `get_api_tools()`
3. function mapping ใน `get_available_functions()`

ถ้ามี `execute_parallel` ก็ต้องเพิ่ม dispatch ตรงนั้นด้วย

ตัวอย่าง tools ที่เพิ่มรอบนี้:

- `camera_status`
- `camera_scan`
- `camera_assign_targets`
- `pick_detected_place`

### Step 3: ถ้าจะใช้ใน terminal arc ต้อง register ใน Agent-Robotic-Cluster

ถ้า user ใช้:

```bash
arc
arc -p "..."
```

ต้องเพิ่ม built-in tool ใน:

- `external\Agent-Robotic-Cluster\src\tools\RobotControl`

และเพิ่ม import/register ใน:

- `external\Agent-Robotic-Cluster\src\tools.ts`

รอบนี้เพิ่ม:

- `CameraStatusTool.ts`
- `CameraScanTool.ts`
- `CameraAssignTargetsTool.ts`
- `PickDetectedPlaceTool.ts`

## 4. โครงสร้าง built-in tool ฝั่ง terminal arc

pattern ที่ควรใช้:

1. สร้างไฟล์ tool ใน `src/tools/RobotControl`
2. ใช้ `buildTool(...)`
3. กำหนด
   - `name`
   - `inputSchema`
   - `outputSchema`
   - `isReadOnly`
   - `checkPermissions` ถ้าเป็น mutating tool
4. ใน `call(...)` ให้เรียก `executeRobotCommand(...)`
5. ตัว `executeRobotCommand` จะไปยิง `python arc.py ...`

สรุปคือ terminal arc ไม่ควร duplicate robot logic  
ควรให้ TypeScript tool เป็น wrapper ไปเรียก Python ARC command

## 5. Flow ของ command wrapper

แนวทางที่ใช้ในรอบนี้:

```text
arc terminal tool
    ->
RobotControl/*.ts
    ->
executeRobotCommand(...)
    ->
python arc.py ...
    ->
FleetController / CameraController / Robot driver
```

ข้อดี:

- logic จริงอยู่ฝั่ง Python ที่คุม hardware
- terminal arc แค่ expose tool ให้ model ใช้
- ลดการซ้ำซ้อนของ business logic

## 6. วิธีเช็กว่า tool เข้า runtime หรือยัง

### Python runtime

เช็กว่า:

- `ai_fleet.py` compile ผ่าน
- เปิด `python main.py` ใหม่
- ลองให้ model เรียก tool ตรงๆ

### Terminal arc

เช็กว่า:

1. source มี tool file แล้ว
2. `src/tools.ts` import และ register แล้ว
3. build ผ่าน:

```bash
bun run build
```

4. install/relink ตัว global package
5. เปิด session `arc` ใหม่
6. ทดสอบด้วย prompt สั้นๆ เช่น:

```bash
arc -p "Call the camera_status tool for Cam1 and return a short answer."
```

## 7. Build และ install flow สำหรับ terminal arc

หลังเพิ่ม tool ใน `external\Agent-Robotic-Cluster`

### Build

```bash
bun run build
```

### Install ให้ global arc ใช้ของใหม่

```bash
npm install -g .
```

ต้องรันจาก:

- `PATH_TO_IF_EDUBOT_ARC\external\Agent-Robotic-Cluster`

เหตุผล:

- global `arc` ในเครื่องนี้ใช้ package ที่ install ไว้ใน npm global
- ไม่ได้อ่านจาก source checkout ตรงๆ

## 8. Checklist มาตรฐานเวลาเพิ่ม tool ใหม่

ใช้ checklist นี้ทุกครั้ง

### Layer A: capability

- มี logic จริงใน Python แล้วหรือยัง
- มี config ครบหรือยัง
- มี command ใน `arc.py` แล้วหรือยัง
- รัน command ตรงได้จริงหรือยัง

### Layer B: Python interactive runtime

- เพิ่ม method ใน `ai_fleet.py`
- เพิ่ม schema ใน `get_api_tools()`
- เพิ่มใน `get_available_functions()`
- เพิ่ม dispatch ใน `execute_parallel` ถ้าจำเป็น

### Layer C: terminal arc runtime

- สร้างไฟล์ tool ใน `src/tools/RobotControl`
- ใช้ `executeRobotCommand(...)` เป็น wrapper
- import เข้า `src/tools.ts`
- register เข้า tool list
- build ใหม่
- install/relink ใหม่

### Layer D: verification

- runtime เห็น tool แล้วจริงหรือยัง
- tool call แล้ว command ถูกยิงจริงหรือยัง
- parsed output กลับมาได้หรือยัง
- permission prompt ถูกต้องหรือยัง
- ถ้าเป็น mutating tool มี safety check หรือยัง

## 9. Pattern การออกแบบ tool ที่ควรใช้

### 9.1 เริ่มจาก high-level tool

อย่า expose tool ย่อยมากเกินไปให้ AI งง

ดี:

- `camera_status`
- `camera_scan`
- `camera_assign_targets`
- `move_to_named_target`
- `set_tool_state`
- `pick_detected_place`

ไม่ควรรีบเปิดเยอะเกินไป:

- pixel conversion tool ย่อย
- raw calibration tool
- low-level camera parameter tool

### 9.2 ให้ terminal tool เป็น wrapper

อย่าเขียน hardware logic ซ้ำใน TypeScript  
ให้ TS tool เรียก `python arc.py ...` เสมอเมื่อเป็นงาน hardware

### 9.3 แยก read-only กับ mutating ให้ชัด

ตัวอย่าง:

- read-only:
  - `camera_status`
  - `camera_scan`
  - `camera_assign_targets`

- mutating:
  - `pick_detected_place`

mutating tool ควรมี `checkPermissions(...)`

## 10. ปัญหาที่เจอในรอบนี้

### ปัญหา 1

เพิ่ม tool ใน `ai_fleet.py` แล้ว แต่ terminal `arc` ยังบอกว่าไม่มี tools

สาเหตุ:

- terminal `arc` ใช้ runtime คนละตัว

บทเรียน:

- ต้องเช็ก runtime path ก่อนเสมอ

### ปัญหา 2

แก้ source ใน checkout แล้ว แต่ global `arc` ยังไม่เห็นผล

สาเหตุ:

- global `arc` ใช้ package install ที่มี `dist` ของตัวเอง

บทเรียน:

- ต้อง build และ install/relink ใหม่

### ปัญหา 3

Typecheck ทั้ง repo มี error เดิมจำนวนมาก

บทเรียน:

- อย่าใช้ repo-wide type errors เป็นตัวตัดสินว่าของใหม่ผิดเสมอ
- ให้แยกดูว่า build ผ่านไหม
- ให้ทดสอบ tool call จริงประกอบ

### ปัญหา 4

คำว่า `home` มีความหมายซ้อนกัน

อาการ:

- AI ใช้ `pick_detected_place(... destination=home)` ทั้งที่ user แค่สั่งให้กลับ home

บทเรียน:

- motion-only intent ต้องมี tool แยก
- ห้าม overload คำว่า `home` ระหว่าง motion กับ grasp workflow

สิ่งที่แก้แล้ว:

- เพิ่ม `move_to_named_target`
- ทำให้ `home` ชั้น ARC เป็น named destination ที่ชัดเจน
- ปรับ prompt/tool description ให้ห้ามใช้ pick tool ถ้าไม่มี intent เรื่องหยิบ
- ทำให้ canonical `home` ตอนนี้ตรงค่าหน้างานจริงคือ `x=350, y=0, z=0` สำหรับทั้ง `Arm1` และ `Arm2`

## 10.1 บทเรียนเรื่อง system data

ถ้ามีการยืนยันค่าหน้างานใหม่ เช่น `home`, workspace, calibration:

- แก้ `config.py` ก่อน
- เช็กว่า orchestration ใช้ค่านั้นจริง ไม่ใช่มี local fallback คนละค่า
- อัปเดตเอกสารทันที
- ถ้าค่า vision defaults มาจากโปรเจกต์ต้นทาง ให้บันทึก source path ไว้ชัดเจน

ตัวอย่างรอบนี้:

- `home` ที่ถูกต้องคือ `350,0,0` ทั้งสองแขน
- calibration/workspace defaults มาจาก `D:\cmr\MVS_Camera_Thaan (1)\MVS_Camera_Thaan\main.py`

## 11. แนวทางสำหรับ tools รอบถัดไป

ถ้าจะเพิ่ม tool ใหม่ในระบบนี้ แนะนำ flow นี้:

1. ทำ Python command ให้เสร็จก่อน
2. ทดสอบ hardware flow จาก `python arc.py ...`
3. เพิ่มเข้า `ai_fleet.py` ถ้าต้องใช้ใน Python interactive runtime
4. เพิ่มเข้า `external\Agent-Robotic-Cluster` ถ้าต้องใช้ใน terminal `arc`
5. build
6. install/relink
7. test ผ่าน `arc -p "..."`

## 12. ข้อสรุป

การเพิ่ม tools ในระบบ ARC นี้มี 2 งานเสมอ:

1. ทำ capability จริง
2. register capability ให้ runtime ที่ user ใช้อยู่จริง

ถ้าขาดข้อใดข้อหนึ่ง จะเกิดอาการ:

- command มี แต่ AI มองไม่เห็น
- หรือ AI เห็นชื่อ tool แต่เรียกแล้วไม่ทำงาน

ดังนั้นทุกครั้งที่พัฒนา tool ใหม่ ให้คิดเป็น chain นี้:

```text
Python capability
-> CLI command
-> Python runtime registration
-> terminal arc registration
-> build
-> install/relink
-> runtime verification
```
