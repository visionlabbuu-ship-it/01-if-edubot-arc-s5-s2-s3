# UC4 Robot Teacher (sim-1.1-author)

สไลด์ Use Case 4 ออกจาก ARC core เป็นแพ็กเกจ `modules/teacher`  
เกรด + demo ยัง **deterministic** — LLM (ถ้าเปิด) เติมเฉพาะข้อความโจทย์

**Runtime:** simulator · ดู [`SIM_LOCK.md`](./SIM_LOCK.md) + [`capabilities.json`](./capabilities.json)

## โครงสร้าง

```
modules/teacher/
  SIM_LOCK.md        # ขอบเขต sim
  capabilities.json  # capability + demo_skeleton + question_skeletons
  catalog.json       # โจทย์ MCQ คงที่ (fallback)
  skeletons.py       # อ่าน registry / map topic
  author.py          # assemble + validate (demo ล็อกจาก skeleton)
  generate.py        # LLM เติมข้อความ หรือ seed offline
  adaptive.py / grader.py / engine.py / api.py
```

## โฟลว์เต็มบน sim (คุย → ออกโจทย์ → เรียน)

```text
UI /teacher แท็บ «คุย»
    → POST /api/teacher/chat {message}
    → tutor map topic → capability
    → generate seed/LLM text + demo ล็อก skeleton
    → load session + start โจทย์แรก
    → UI สลับแท็บ «เรียน» → เลือก A–D → demo หุ่น
```

```bash
# คุยแล้วออกโจทย์ (offline seed)
curl -s -X POST http://127.0.0.1:8770/api/teacher/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"อยากเรียนลำดับหยิบของ","use_llm":false}'

# generate ตรงๆ
curl -s -X POST http://127.0.0.1:8770/api/teacher/generate \
  -H 'Content-Type: application/json' \
  -d '{"capability_id":"pick_pipeline","use_llm":false,"count":2}'
```

UI: `http://localhost:3000/teacher`

| GET | ความหมาย |
|-----|----------|
| `/api/teacher/capabilities` | capability + demo skeleton |
| `/api/teacher/skeletons` | รายการ question skeleton |
| `/api/teacher/lock` | ขอบเขต + counts |

| POST | Body |
|------|------|
| `/api/teacher/chat` | `message`, `use_llm?`, `count?` |
| `/api/teacher/generate` | `capability_id` \| `topic`, `use_llm?`, `count?` |
| `/api/teacher/load_set` | `challenges: [...]` |
| `/api/teacher/validate` | challenge หรือ MCQ text |

## โหมดโจทย์ (sim-1.0)

| mode | สถานะ lock | ความหมาย |
|------|------------|----------|
| `mcq` | **ล็อกแล้ว** | โจทย์ + Choice A–D → อธิบาย + หุ่น demo |
| `procedure` | โค้ดมี ยังไม่นับ lock | เรียงลำดับขั้นตอน |
| `robot` | โค้ดมี ยังไม่นับ lock | ฝึกสั่งหุ่นบน sim |

### โฟลว์ MCQ

1. `POST /api/teacher/start` `{challenge_id}`
2. `GET /api/teacher/actions` → ได้ `choices` A–D + คำถาม
3. `POST /api/teacher/act` `{action_id:"B"}`  
   - ผิด → `passed:false` + hint ทำไมผิด (เลือกใหม่ได้)  
   - ถูก → `passed:true` + `explanation_th` + รัน `demo_steps` ให้หุ่นทำงาน

## API

| Method | Path | Body |
|--------|------|------|
| GET | `/api/teacher/lock` | — ขอบเขต sim-1.0 |
| GET | `/api/teacher/capabilities` | — capability ที่สอนได้ |
| GET | `/api/teacher/catalog` | — |
| GET | `/api/teacher/status` | — |
| GET | `/api/teacher/actions` | — (รายการตัวเลือก) |
| POST | `/api/teacher/start` | `{"challenge_id"?}` |
| POST | `/api/teacher/demo` | `{}` |
| POST | `/api/teacher/check` | `{}` |
| POST | `/api/teacher/next` | `{}` |
| POST | `/api/teacher/reset` | `{}` |
| POST | `/api/teacher/act` | `{"action_id"}` — เลือก Choice / ขั้น / สั่งหุ่น |
| POST | `/api/teacher/waypoint` | `{"arm","x","y","z?"}` |

## เดโมเร็ว (sim)

```bash
export ARC_SIM_MODE=1
# arc.py serve --port 8770

curl -s http://127.0.0.1:8770/api/teacher/lock

curl -s -X POST http://127.0.0.1:8770/api/teacher/start \
  -H 'Content-Type: application/json' \
  -d '{"challenge_id":"Q1_why_scan"}'

curl -s -X POST http://127.0.0.1:8770/api/teacher/act \
  -H 'Content-Type: application/json' -d '{"action_id":"B"}'
```

UI: `http://localhost:3000/teacher`

## กับ UC อื่น / ขั้นถัดไป

- **UC4 sim-1.0** = โมดูลนี้ (บทเรียน deterministic บน sim)
- **ยังไม่ทำ:** chat เลือก topic → LLM สร้างโจทย์ → Teach Gate
- **UC5** = ARC ทั้งก้อน (agent / factory)
- **hw-1.0** = ปลดล็อก hardware ทีหลัง แยกจาก sim lock

รายละเอียดขอบเขตและ acceptance: [`SIM_LOCK.md`](./SIM_LOCK.md)
