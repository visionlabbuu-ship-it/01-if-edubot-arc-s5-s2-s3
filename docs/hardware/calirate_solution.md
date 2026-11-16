# Calibration And Workspace Geometry Notes

## Current Goal

ระบบนี้ใช้แปลงภาพจากกล้องให้เป็นพิกัดพื้นที่ทำงานจริงของหุ่น และต่อยอดเป็นพิกัด `Arm1` สำหรับการหยิบวัตถุ

ตอนนี้ระบบมี 2 ชั้นหลัก:

- Workspace calibration: หา `outer_box` และ zone ด้านใน แล้วแปลง `camera pixel -> workspace_mm`
- Object calibration: ใช้วัตถุทรงกลม 9 จุด เพื่อเรียนรู้ `workspace_mm -> Arm1 robot_pose.xy`

`outer_box` คือพื้นที่ทำงานจริงบนโลกจริง ส่วนกรอบย่อยด้านในคือ zone ภายในพื้นที่นั้น

## Detection Model

`calibrate_grid_viewer.py` ใช้ `boxes` mode เป็นหลัก และ detect กรอบจาก contour ของเส้นดำ โดยส่งออกเป็น quadrilateral ไม่ใช่ bbox ตรงเสมอ:

- `corners_px`: มุม polygon สำหรับวาด overlay ตามภาพจริง
- `transform_corners_px`: มุมสำหรับ perspective transform
- `source = contour_quad`: กรอบย่อยที่ได้จาก contour quad
- `outer_box.source = outer_quad_from_children`: outer box สร้างจากมุมของกรอบย่อยที่ผ่าน filter แล้ว

เหตุผลที่ใช้ quadrilateral คือมุมกล้องทำให้กรอบในภาพเอียงหรือเป็น trapezoid ได้ แต่การคำนวณจริงต้องแปลงกลับเป็นสี่เหลี่ยมตรงในหน่วย mm ด้วย perspective transform

## Workspace Calibration Web

หน้าเว็บอยู่ที่:

```text
http://127.0.0.1:8787/
```

ข้อมูล detection default อ่านจาก:

```text
runtime_state/calibration_grid_outer_test/live_report.json
```

runtime calibration บันทึกที่:

```text
runtime_state/workspace_calibration.json
```

หน้าเว็บมีแท็บหลัก:

- `Workspace`: ปรับขนาดจริงของ outer box, ตำแหน่งหุ่น, reach และ zone
- `Object Points`: เก็บ 9 จุดจากวัตถุทรงกลมเพื่อ calibrate พิกัดหุ่นจริง

หน้า Workspace จะเทียบ `source_report.timestamp` ใน `runtime_state/workspace_calibration.json`
กับ `runtime_state/calibration_grid_outer_test/live_report.json` เสมอ ถ้าไฟล์ saved
มาจาก detection เก่า หน้าเว็บจะใช้ geometry/corners จาก live report ล่าสุดเพื่อให้ overlay
ตรงกับภาพ review แต่ยังคงค่า mm, robot และ zone metadata ที่ผู้ใช้ save ไว้ จากนั้นผู้ใช้ต้องกด
`Save` หลัง review เพื่ออัปเดตไฟล์ runtime calibration ให้ตรงกับ detection ล่าสุด

ใน overlay ของแท็บ Workspace:

- เส้น outer คือพื้นที่ทำงานทั้งหมด
- กรอบ zone คือช่องด้านใน
- เส้น `WIDTH` คือแนวกว้างจริงของ outer box
- เส้น `HEIGHT` คือแนวยาว/สูงจริงของ outer box
- overlay ใช้ `ResizeObserver` เพื่อให้ตรงกับภาพเมื่อขยาย/ย่อหน้าเว็บหรือแท็บ

ผู้ใช้ต้องกรอก:

- `Outer width mm`: ความกว้างจริงของ outer box
- `Outer height mm`: ความสูงจริงของ outer box
- `Robot edge`: หุ่นอยู่ด้านไหนของ outer box เช่น `top`, `right`, `bottom`, `left`
- `Robot center to outer center mm`: ระยะจากจุดกึ่งกลางหุ่นถึงจุดกึ่งกลาง outer box
- `Min reach mm`: รัศมีใกล้สุดที่หุ่นเอื้อมได้ วัดจากจุดกึ่งกลางหุ่น
- `Max reach mm`: รัศมีไกลสุดที่หุ่นเอื้อมได้ วัดจากจุดกึ่งกลางหุ่น

## Robot Reference Point

ข้อตกลงล่าสุดคือทุกค่า reach และตำแหน่งหุ่นอิงจากจุดกึ่งกลางของหุ่น ไม่ใช่ขอบฐาน

ชื่อ canonical ใน calibration JSON คือ:

```json
"robot_center_to_outer_center_mm": 350.0
```

ความหมาย:

```text
ระยะจากจุดกึ่งกลางหุ่น -> จุดกึ่งกลาง outer box
```

ระบบยังรับ field เก่า `base_to_outer_center_mm` เพื่อ compatibility กับไฟล์เก่า แต่เมื่อ enrich/save calibration ใหม่ ระบบจะ normalize เป็น `robot_center_to_outer_center_mm` และไม่ emit ชื่อเก่า

ตัวอย่างถ้า outer box ขนาด `600 x 400 mm`, center คือ `(300, 200)` และเลือก `robot.edge = bottom`, `robot_center_to_outer_center_mm = 350`:

```text
robot center = (300, 200 + 350) = (300, 550)
```

## Reach Calculation

ขั้นตอนคำนวณ reach แบบ geometry เดิม:

1. pixel point จากกล้องถูกแปลงเป็น workspace mm ด้วย perspective transform ของ outer box
2. workspace mm ถูกแปลงเป็น robot-local xy จาก `robot_center_workspace_xy_mm`
3. คำนวณ radius:

```text
r_mm = sqrt(local_x^2 + local_y^2)
```

4. เทียบกับช่วง reach:

```text
min_reach_mm <= r_mm <= max_reach_mm
```

`reach_preview` ในหน้าเว็บใช้ center point ของแต่ละ zone เป็น preview เท่านั้น ไม่ได้แปลว่าทั้ง zone เอื้อมถึงหรือเอื้อมไม่ถึงทั้งหมด เพราะในหนึ่ง zone อาจมีทั้งส่วนที่อยู่ใน reach และอยู่นอก reach

## Object-Based Calibration

ฟีเจอร์ใหม่ใช้วัตถุทรงกลมเพื่อ calibrate พิกัดหุ่นจริงของ `Arm1`

Flow:

1. วางวัตถุทรงกลมใน workspace
2. เปิดแท็บ `Object Points`
3. กด `Scan` หนึ่งครั้งเพื่อถ่ายภาพ object snapshot แล้ว freeze ภาพ/รายการ object ไว้
4. ระบบ detect object ด้วย camera scan เดิม และกรอง candidate ด้วย `circularity`, area และ diameter
5. ระบบใส่ `slot` ให้ object ตามลำดับ marker บนภาพ snapshot เช่น marker 7 คือ `Arm1-P7`
6. คลิก marker ของวัตถุที่ต้องการใน preview
7. ผู้ใช้ลาก `Arm1` ไปชี้/แตะตำแหน่งวัตถุนั้นเอง
8. กด `Save Pose` เพื่ออ่าน current pose ของ `Arm1` และบันทึกลง slot ของ marker ที่เลือก
9. ทำครบ 9 จุด
10. กด `Compute & Apply`

ระบบนี้อ่าน pose เท่านั้น ไม่สั่ง motion หุ่น

ข้อมูล session ปัจจุบันเก็บที่:

```text
runtime_state/object_calibration/current_session.json
```

scan ล่าสุดเก็บที่:

```text
runtime_state/object_calibration/latest_scan.json
runtime_state/object_calibration/latest_scan.jpg
```

เมื่อ apply สำเร็จ จะ archive session เป็น:

```text
runtime_state/object_calibration/object_calibration_<timestamp>.json
```

แต่ละ point เก็บข้อมูลสำคัญ:

- `point_id`
- `point_index`: slot ของ marker จากภาพ snapshot
- `arm_id = Arm1`
- object metadata: `id`, `slot`, `center_px`, `bbox`, `area`, `circularity`, `color`
- `workspace_xy`: จุดวัตถุในหน่วย mm หลัง perspective correction
- `robot_pose`: pose จริงของ Arm1 ตอนผู้ใช้กด save
- `robot_mode`, `robot_mode_name`, timestamp

## Object Calibration Math

Workspace calibration เดิมยังเป็น source หลักสำหรับ:

```text
camera pixel -> workspace_mm
```

Object calibration ใหม่เรียนรู้เฉพาะ:

```text
workspace_mm -> Arm1 robot_pose.xy
```

ใช้ least-squares affine transform จาก 9 จุด:

```text
robot_x = a*x_ws + b*y_ws + tx
robot_y = c*x_ws + d*y_ws + ty
```

หลัง compute ระบบรายงาน:

- predicted robot xy ของแต่ละจุด
- error ต่อจุดในหน่วย mm
- mean error
- max error
- RMS error

เมื่อกด `Compute & Apply` transform จะถูกบันทึกใน:

```json
"robot": {
  "workspace_to_robot_transform": {
    "enabled": true,
    "source": "object_calibration",
    "arm_id": "Arm1",
    "type": "affine",
    "a": 1.0,
    "b": 0.0,
    "tx": 0.0,
    "c": 0.0,
    "d": 1.0,
    "ty": 0.0
  }
}
```

หลังจากนั้น `camera_controller.scan_workspace()` จะใช้ transform นี้ก่อน geometry เดิมเมื่อคำนวณ `robot_plane` สำหรับ object ที่ detect ได้

ถ้ายังไม่มี `workspace_to_robot_transform` หรือ `enabled` ไม่เป็น true ระบบจะ fallback ไปใช้ geometry จาก `robot_center_to_outer_center_mm` ตามเดิม

## Runtime Object Scan Contract

object ที่ `camera_controller.py` ส่งออกมี field เพิ่ม:

- `perimeter`
- `circularity`
- `detection_source` เมื่อ object มาจาก Hough circle fallback
- `slot` ใน `round_objects` สำหรับ mapping marker -> calibration point

แท็บ Object Points ใช้ candidate ที่:

- อยู่ใน workspace
- มี `workspace_xy`
- `circularity >= 0.55`
- `area >= 2500 px`
- diameter จาก bbox อย่างน้อย `28 px`

ค่า `circularity` คำนวณจาก:

```text
circularity = 4*pi*area/perimeter^2
```

วงกลมที่ดีจะใกล้ 1.0 ส่วนวัตถุยาว/เหลี่ยม/เงารบกวนมักต่ำกว่า

แท็บ Object Points ไม่ live scan ระหว่าง calibration แล้ว ต้องกด `Scan` เองหนึ่งครั้งเพื่อสร้าง snapshot จากนั้น preview, marker, object list และ slot จะค้างอยู่จนกว่าจะกด `Scan` ใหม่หรือ `Reset` ดังนั้นถ้าคลิก marker 7 แล้วกด `Save Pose` ระบบจะบันทึกเป็น `Arm1-P7` ไม่ใช่ append เป็นจุดที่ 1

## Output Contract

ใน `boxes` mode report มีส่วนสำคัญ:

- `boxes`: กรอบย่อยที่ผ่าน size consensus แล้ว
- `cells`: alias ของ `boxes`
- `outer_box`: กรอบใหญ่ที่ครอบทุก box
- `detector.source = contour_quad`
- `detector.size_consensus`: รายละเอียดการคัดกรอบตามขนาด
- `detector.outer_box`: รายละเอียดการสร้าง outer box

ใน runtime workspace calibration มีส่วนสำคัญ:

- `outer_box.width_mm`
- `outer_box.height_mm`
- `outer_box.corners_px`
- `outer_box.corners_mm`
- `zones[].polygon_mm`
- `zones[].center_mm`
- `zones[].robot_local_center_mm`
- `zones[].reach_preview`
- `robot.robot_center_to_outer_center_mm`
- `robot.robot_center_workspace_xy_mm`
- `robot.outer_center_workspace_xy_mm`
- `robot.workspace_to_robot_transform` ถ้า apply object calibration แล้ว

## Useful Commands

รัน detector จากกล้องหนึ่ง frame:

```powershell
python calibrate_grid_viewer.py --once --no-window --mode boxes --output-dir runtime_state\calibration_grid_outer_test
```

รัน detector จากภาพ saved frame แล้วเขียนทับ live report:

```powershell
python calibrate_grid_viewer.py --image runtime_state\calibration_grid_outer_test\live_source_image.png --no-window --mode boxes --output-dir runtime_state\calibration_grid_outer_test --output-prefix live
```

เปิด calibration web:

```powershell
python calibration_web.py --host 127.0.0.1 --port 8787
```

ตรวจ syntax และ tests:

```powershell
python -m py_compile calibrate_grid_viewer.py workspace_calibration.py object_calibration.py calibration_web.py camera_controller.py workspace_model.py test_workspace_calibration.py test_object_calibration.py
python -m unittest test_workspace_calibration.py test_object_calibration.py test_robot_guardrails.py
```

## Implementation Notes

ไฟล์หลัก:

- `calibrate_grid_viewer.py`: detect กรอบย่อย/outer box และเขียน report
- `workspace_calibration.py`: schema, perspective transform, robot-center geometry, reach preview
- `object_calibration.py`: session, round-object filtering, affine fit, apply report
- `calibration_web.py`: UI สำหรับ Workspace และ Object Points
- `camera_controller.py`: ใช้ runtime calibration แปลง object pixel เป็น workspace/robot plane
- `workspace_model.py`: expose calibrated workspace layout ให้ระบบอื่นอ่าน

หลักสำคัญที่ต้องรักษา:

- Overlay วาดตามภาพจริงเป็น quadrilateral
- การคำนวณภาพใช้ perspective transform กลับเป็น mm
- Robot reference point คือจุดกึ่งกลางหุ่น
- `min_reach_mm` และ `max_reach_mm` เป็น radius จากจุดกึ่งกลางหุ่น
- Object calibration ไม่แทนที่ outer-box perspective correction แต่ต่อยอดหลังได้ `workspace_xy`
- ชื่อเก่า `base_to_outer_center_mm` มีไว้รับไฟล์เก่าเท่านั้น ไม่ใช่ความหมายปัจจุบัน

## Robot Plane Correction Layer

หลังจาก object calibration 9 จุด ระบบยังอาจมี error ที่เป็น pattern ตามตำแหน่งใน workspace ได้ เช่น ฝั่งบนของภาพเพี้ยนแกน `x` มากกว่าฝั่งล่าง หรือฝั่งล่างเพี้ยนแกน `y` มากกว่า offset เดียวทั้งกระดาน

ตอนนี้ runtime เพิ่ม correction layer หลัง affine เดิม:

```text
camera pixel -> workspace_xy
workspace_xy -> robot_plane ด้วย object calibration affine
robot_plane -> corrected robot_plane ด้วย robot_plane_correction
```

ค่า correction ปัจจุบันบันทึกอยู่ใน:

```text
runtime_state/workspace_calibration.json
robot.workspace_to_robot_transform.robot_plane_correction
```

ชนิด correction ที่ใช้ตอนนี้คือ affine จากจุดเทียบจริง:

```text
corrected_x = a*x + b*y + tx
corrected_y = c*x + d*y + ty
```

โดย `x,y` คือค่า `robot_plane` ก่อน correction และ output คือพิกัด `Arm1` ที่ควรใช้สั่งหยิบจริง

ข้อมูล calibration ชุดแรก fit จาก 4 comparison points ที่ผู้ใช้ขยับแขนจริงไปแตะ object:

```text
blue_top
blue_middle
red_upper_left
red_lower_right
```

ผล fit ของ correction ชุดนี้:

```text
before correction: mean error ~= 5.65 mm, max ~= 8.57 mm
after correction:  mean error ~= 0.71 mm, max ~= 1.03 mm
```

เมื่อ correction เปิดอยู่ `camera_controller.scan_workspace()` จะส่งออก:

- `calibration = object_workspace_affine_corrected`
- `uncorrected_robot_plane`: พิกัดก่อน correction สำหรับ debug
- `robot_plane_correction`: summary ของ correction ที่ใช้

ข้อควรระวัง: correction นี้ fit จาก 4 จุดเทียบจริง จึงควรทดสอบเพิ่มอีกหลายตำแหน่ง โดยเฉพาะขวาบนและซ้ายล่าง ถ้า error นอกจุด fit ยังเป็น pattern ซับซ้อน อาจต้องเพิ่ม point แล้ว refit หรือเปลี่ยนเป็น polynomial/thin-plate correction ภายหลัง
