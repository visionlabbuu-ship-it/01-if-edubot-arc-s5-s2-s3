# UC4 Robot Teacher — SIM Lock v1.0

**สถานะ:** ล็อกสำหรับ simulator เท่านั้น  
**เวอร์ชัน:** `sim-1.0`  
**วันที่ล็อก:** 2026-07-17  
**LLM ในรอบเรียน:** ปิด (`llm: false`)

เอกสารนี้คือขอบเขตที่ทีมยอมรับว่า “ทำได้บน sim แล้ว” ก่อนจะต่อ chat-topic / LLM author / hardware

---

## 1. เป้าหมายรอบนี้

| ทำ | ไม่ทำ (ยัง) |
|----|-------------|
| โจทย์ MCQ จาก `catalog.json` คงที่ | คุย topic แล้วให้ LLM สร้างโจทย์สด |
| เลือก A–D → hint / อธิบาย | เกรดด้วย LLM |
| ตอบถูก → รัน `demo_steps` บน **sim** | สั่งแขนจริง (MG400) จาก teacher |
| motion ผ่าน `FleetController` + guardrail เดิม | freeform move / op นอก allow-list |
| ปรับระดับ L1–L3 แบบ rule (`adaptive.py`) | dual-arm สอนพร้อมกันเป็น demo หลัก |

---

## 2. Runtime ที่ล็อก

```text
ARC_SIM_MODE=1
serve:  arc.py serve --host 0.0.0.0 --port 8770
web:    /teacher  (Vite :3000 → proxy /api → 8770)
module: modules/teacher
```

- ถ้าไม่ใช่ sim → teacher ยังรัน API ได้ แต่ **demo อาจ fail / ไม่ใช่เป้าหมายที่ lock**
- ไฟล์บอกขอบเขต: `capabilities.json` + ฟิลด์ใน `catalog.json`

---

## 3. Capability ที่ “สอนได้” บน sim-1.0

ดูรายละเอียดใน `capabilities.json`

| id | เรื่อง | โจทย์ | demo skeleton |
|----|--------|-------|----------------|
| `pick_pipeline` | ทำไม/ลำดับ scan→hover→descend→suction→lift | Q1_*, Q2_hover, Q2_suction, Q3_* | `reset_scene` → `pick_object` |
| `pick_then_place` | หยิบแล้ววาง storage | Q2_pick_place | `reset` → `pick` → `place_named(storage_zone)` |

นอกตาราง = **out of scope** สำหรับ sim-1.0 (รวม LLM generate, hardware, dual-arm demo)

---

## 4. Demo ops ที่อนุญาต (allow-list)

Engine รับเฉพาะ:

- `reset_scene`
- `home`
- `move_named` — destination ∈ {`home`, `storage_zone`, `drop_zone`}
- `pick_object` — default `obj-1`, arm `Arm1` (แนะนำ)
- `place_named` — แนะนำ `storage_zone`
- `move_pose` — ใช้ sparingly (robot practice / อนาคต) ยังไม่ใช่โจทย์หลัก MCQ

ก่อนรัน demo ทุกครั้ง: **park แขนอื่น** นอก shared zone (`_park_other_arms`)  
Motion จริงยังผ่าน `RobotSafetyGuardrail` ของ ARC

---

## 5. โจทย์ที่ล็อกใน catalog (6 ข้อ)

| id | level | capability |
|----|-------|------------|
| Q1_pick_pipeline | 1 | pick_pipeline |
| Q1_why_scan | 1 | pick_pipeline |
| Q2_hover_first | 2 | pick_pipeline |
| Q2_suction_when | 2 | pick_pipeline |
| Q2_pick_place | 2 | pick_then_place |
| Q3_wrong_order | 3 | pick_pipeline |

โหมดหลัก: **`mcq` only** สำหรับ lock นี้  
`procedure` / `robot` อาจมีโค้ดรองรับ แต่ **ยังไม่นับว่า lock ผ่านบน sim**

---

## 6. โฟลว์ที่รับประกัน

```text
GET  /api/teacher/catalog     → รายการโจทย์ + lock meta
GET  /api/teacher/lock        → ขอบเขต sim-1.0 + capabilities
POST /api/teacher/start       → setup scene (reset + park)
GET  /api/teacher/actions     → choices A–D
POST /api/teacher/act         → เลือกข้อ; ถูก=อธิบาย+demo; ผิด=hint ลองใหม่
POST /api/teacher/demo        → รัน demo_steps โดยไม่ต้องตอบ
POST /api/teacher/check       → รายงานสถานะ MCQ
POST /api/teacher/next|reset
```

ไม่มี `/api/teacher/chat` หรือ `/generate` ใน sim-1.0

---

## 7. เกณฑ์ “ผ่าน lock” (acceptance)

1. `ARC_SIM_MODE=1` แล้ว `GET /api/teacher/lock` ขึ้น `runtime: sim`, `version: sim-1.0`
2. เริ่มแต่ละ challenge ในตารางข้อ 5 ได้
3. เลือกข้อผิด → ได้ hint, ยังไม่ demo (หรือ demo เฉพาะปุ่มดูตัวอย่าง)
4. เลือกข้อถูก → `passed: true` + explanation + **demo บน sim สำเร็จ** (`demo.ok`)
5. `Q2_pick_place` demo ครบ pick + place ที่ storage
6. ไม่มี path ที่ teacher เรียก LLM

---

## 8. Author path (sim-1.1-author) — ทำแล้วบางส่วน

| ชิ้น | สถานะ |
|------|--------|
| Capability + `demo_skeleton` + `question_skeletons` | ✅ `capabilities.json` |
| Assemble + validate (demo ล็อกจาก skeleton) | ✅ `author.py` |
| Generate offline seed / LLM text-only | ✅ `generate.py` + `POST /api/teacher/generate` |
| Topic keyword → capability | ✅ `match_capability_from_topic` |
| Session load generated set | ✅ `engine.load_challenge_set` |
| Chat UI คุย topic ยาว | ยังไม่ทำ |
| Dry-run demo บน sim ก่อน publish ทุกข้อ | ยังไม่ทำ (validate schema แล้ว; demo ตอนตอบถูก) |
| Hardware unlock (`hw-1.0`) | ยังไม่ทำ |

```bash
# offline generate
curl -s -X POST http://127.0.0.1:8770/api/teacher/generate \
  -H 'Content-Type: application/json' \
  -d '{"topic":"อยากเรียนลำดับหยิบ","use_llm":false,"count":2}'
```

---

## 9. วิธีรัน smoke เร็ว

```bash
export ARC_SIM_MODE=1
# server ที่ :8770

curl -s http://127.0.0.1:8770/api/teacher/lock | jq .

curl -s -X POST http://127.0.0.1:8770/api/teacher/start \
  -H 'Content-Type: application/json' \
  -d '{"challenge_id":"Q1_why_scan"}'

# ผิด
curl -s -X POST http://127.0.0.1:8770/api/teacher/act \
  -H 'Content-Type: application/json' -d '{"action_id":"A"}'

# ถูก + demo
curl -s -X POST http://127.0.0.1:8770/api/teacher/act \
  -H 'Content-Type: application/json' -d '{"action_id":"B"}'
```

UI: `http://localhost:3000/teacher` (hard refresh ถ้า cache เก่า)
