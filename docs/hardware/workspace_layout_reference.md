# Workspace Layout Reference

Date: 2026-05-14

เอกสารนี้เป็น source of truth สำหรับ geometry หน้างานของระบบหุ่นยนต์และกล้อง  
ให้ยึดเอกสารนี้เป็นหลักเมื่อคุยเรื่อง:

- workspace
- block layout
- storage / drop zones
- ตำแหน่งฐานหุ่น
- ตำแหน่ง home
- shared world frame
- local frame ของหุ่น
- การแปลงพิกัดระหว่างกล้อง, workspace, และหุ่น

## 1. Canonical Layout

ให้ยึดภาพนี้เป็น layout หลัก

```text
                         Workspace = 600 x 400 mm
                         3 columns x 2 rows
                         each block = 200 x 200 mm

                 x=0               x=200             x=400             x=600
                  |                  |                 |                 |
                  +------------------+-----------------+-----------------+
                  |                  |                 |                 |
                  |    Block 1,1     |    Block 1,2    |    Block 1,3    |
                  |                  |                 |                 |
        B1 -----> +------------------+-------(C)-------+-----------------+ <----- B2
                  |                  |                 |                 |
                  |    Block 2,1     |    Block 2,2    |    Block 2,3    |
                  |                  |                 |                 |
                  +------------------+-----------------+-----------------+
                  |                  |                 |                 |
                  |   Storage Zone   |                 |    Drop Zone    |
                  |                  |                 |                 |
                  +------------------+                 +-----------------+a                                                                                         
```

ความหมาย:

- กรอบ `600 x 400 mm` คือ workspace หลัก
- workspace หลักแบ่งเป็น `3 คอลัมน์ x 2 แถว`
- แต่ละ block มีขนาด `200 x 200 mm`
- `B1` คือฐานของ `Arm1`
- `B2` คือฐานของ `Arm2`
- `(C)` คือจุดกึ่งกลางของ workspace
- `B1`, `(C)`, `B2` ต้องอยู่ในเส้นแนวนอนเดียวกัน
- `Arm1` อยู่ทางซ้ายและหันเข้าหา workspace
- `Arm2` อยู่ทางขวาและหันเข้าหา workspace
- `Storage Zone` อยู่นอก workspace ใต้ `Block 2,1`
- `Drop Zone` อยู่นอก workspace ใต้ `Block 2,3`

## 2. Shared Workspace Frame

ใช้ workspace เป็น shared frame กลางของระบบ

นิยาม:

- ซ้ายสุดของ workspace = `x = 0`
- ขวาสุดของ workspace = `x = 600`
- ขอบบนของ workspace = `y = 400`
- ขอบล่างของ workspace = `y = 0`

ดังนั้น:

- workspace center `(C)` = `(300, 200)` mm

หมายเหตุ:

- ภาพ ASCII ใช้แถวบนก่อนเพื่อให้อ่านง่าย
- แต่พิกัดจริงของระบบในเอกสารนี้ใช้ `y = 0` ที่ขอบล่าง และ `y = 400` ที่ขอบบน

## 3. Real-World Distances

ข้อมูลหน้างานที่ยืนยันแล้ว:

- workspace หลัก = `600 x 400 mm`
- ระยะจากฐานหุ่นถึงขอบ workspace ฝั่งที่หันเข้าไป = `95 mm`
- ระยะจากฐานหุ่นถึง `home` ตามแนวที่แขนยื่น = `350 mm`

ดังนั้น:

- ระยะฐานถึงฐาน = `95 + 600 + 95 = 790 mm`

ตำแหน่งฐานใน shared workspace frame:

- `B1 = (-95, 200)` mm
- `B2 = (695, 200)` mm

## 4. Home Positions

เพราะ:

- `base -> workspace edge = 95 mm`
- `base -> home = 350 mm`

จึงได้ว่า:

- `home` อยู่ข้างใน workspace ลึกจากขอบเข้าไป `350 - 95 = 255 mm`

พิกัด `home` ใน shared workspace frame:

- `Arm1 home = (255, 200)` mm
- `Arm2 home = (345, 200)` mm

เชิงความสัมพันธ์:

- `Arm1 home` อยู่ห่างจากขอบซ้ายเข้าไป `255 mm`
- `Arm2 home` อยู่ห่างจากขอบขวาเข้าไป `255 mm`
- ทั้งสอง `home` อยู่บนเส้นกึ่งกลาง `y = 200 mm`

## 5. Robot Local Frames

นอกจาก shared workspace frame แล้ว ให้ใช้ local frame ของหุ่นแต่ละตัวด้วย

ข้อมูลที่ยืนยันแล้ว:

- `Arm1 home` ใน local frame ของ `Arm1` = `(350, 0, 0)`
- `Arm2 home` ใน local frame ของ `Arm2` = `(350, 0, 0)`

ความหมาย:

- สำหรับหุ่นแต่ละตัว `home` คือจุดที่อยู่หน้า base ของตัวเอง `350 mm`

ดังนั้น:

- shared frame ใช้กับ vision / blocks / zones
- local robot frame ใช้กับ motion command ของหุ่น

## 6. Shared-To-Local Interpretation

ความเข้าใจเชิงทิศทาง:

- `Arm1` หันเข้าหา workspace จากซ้ายไปขวา
- `Arm2` หันเข้าหา workspace จากขวาไปซ้าย

จึงตีความแกนเดินหน้าของหุ่นได้แบบนี้:

- `Arm1 local +x` = `workspace +x`
- `Arm2 local +x` = `workspace -x`

อย่างน้อยในแกนตามแนวความกว้างของ workspace:

- `Arm1 base -> Arm1 home` = `(-95,200) -> (255,200)`
- `Arm2 base -> Arm2 home` = `(695,200) -> (345,200)`

กฎแปลงแบบใช้งานเฉพาะแกน `x`:

- `Arm1`: `workspace_x = -95 + local_x`
- `Arm2`: `workspace_x = 695 - local_x`

แกน `y` local ของหุ่นยังควรยืนยันเพิ่มตอนผูก motion model เต็ม

## 7. Workspace Blocks

workspace หลักมี `6 blocks`

- 3 คอลัมน์
- 2 แถว
- แต่ละ block = `200 x 200 mm`

การตั้งชื่อ block ตามภาพ:

- แถวบน: `Block 1,1`, `Block 1,2`, `Block 1,3`
- แถวล่าง: `Block 2,1`, `Block 2,2`, `Block 2,3`

พิกัดแต่ละ block ใน shared workspace frame:

- `Block 1,1` = `x: 0..200`, `y: 200..400`
- `Block 1,2` = `x: 200..400`, `y: 200..400`
- `Block 1,3` = `x: 400..600`, `y: 200..400`
- `Block 2,1` = `x: 0..200`, `y: 0..200`
- `Block 2,2` = `x: 200..400`, `y: 0..200`
- `Block 2,3` = `x: 400..600`, `y: 0..200`

center ของแต่ละ block:

- `Block 1,1 center = (100, 300)` mm
- `Block 1,2 center = (300, 300)` mm
- `Block 1,3 center = (500, 300)` mm
- `Block 2,1 center = (100, 100)` mm
- `Block 2,2 center = (300, 100)` mm
- `Block 2,3 center = (500, 100)` mm

## 8. External Zones

external zones ไม่ใช่ส่วนของ workspace หลัก `600 x 400 mm`

### Storage Zone

อยู่ใต้ `Block 2,1`

ถ้าใช้ขนาดเท่ากับ 1 block:

- `Storage Zone = x: 0..200, y: -200..0`
- `Storage Zone center = (100, -100)` mm

### Drop Zone

อยู่ใต้ `Block 2,3`

ถ้าใช้ขนาดเท่ากับ 1 block:

- `Drop Zone = x: 400..600, y: -200..0`
- `Drop Zone center = (500, -100)` mm

## 9. Consistency Notes

ข้อสรุปที่สำคัญ:

- workspace `60 x 40 cm` หมายถึงเฉพาะ block `1,1` ถึง `2,3`
- `Storage Zone` และ `Drop Zone` อยู่นอก workspace หลัก
- `B1`, `(C)`, `B2` อยู่บนเส้นแนวนอนเดียวกัน
- `Arm1` และ `Arm2` มี `home = (350,0,0)` ใน local frame ของตัวเอง
- เมื่อแปลงมา shared frame จะได้:
  - `Arm1 home = (255,200)` mm
  - `Arm2 home = (345,200)` mm

## 10. Usage

ให้ใช้อ้างอิงเอกสารนี้เมื่อทำงานเรื่อง:

- calibration
- camera-to-workspace mapping
- reachability
- target assignment
- home / storage / drop semantics
- prompt / tool contract เรื่อง geometry

