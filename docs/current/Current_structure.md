# ARC — Current Structure Map

> เอกสารนี้คือแผนที่ของระบบจริง ณ ตอนนี้ ใช้หาว่า "อะไรอยู่ไหน" เวลาจะแก้ จะได้ไม่หลง
> อิงจากการอ่านโค้ดจริง ไม่ใช่การเดา ปรับปรุงล่าสุด: 2026-06-26

---

## ภาพรวม 1 หน้า

ระบบมี 4 ชั้น เรียงจากหน้าบ้านลงไปหาฮาร์ดแวร์ แต่ละชั้นรันแยกโปรเซสและคุยกันด้วย "spawn + อ่าน stdout"

```
┌─ [1] Browser UI            React + Vite + Tailwind
│       external/Agent-Robotic-Cluster/web/
│         │  fetch POST /v1/chat/completions  +  อ่าน SSE events
│         ▼
├─ [2] arc-bridge.mjs        OpenAI-compatible HTTP shim (ไฟล์เดียว ~30KB)
│       bridge/arc-bridge.mjs   ← server ที่ฟังพอร์ต 9797
│         │  spawn arc --print --output-format stream-json
│         ▼
├─ [3] ARC CLI               agent loop + robot tools (Claude Code fork)
│       external/Agent-Robotic-Cluster/
│         │  spawn('python', ['arc.py', ...])   ← coupling point เดียว
│         ▼
└─ [4] Python runtime        guardrails / camera / planning / MG400
        *.py ที่ root ของโปรเจกต์
```

หลักการอ่าน: **ของที่คุณเขียนเองอยู่ "นอก" ARC CLI ทั้งหมด** (Python ที่ root + bridge ที่ root + web)
ส่วน ARC CLI (`external/...`) คือ Claude Code ที่ยืมมา มีของใช้จริงปนส่วนเกินเยอะ

---

## [1] Browser UI — `external/Agent-Robotic-Cluster/web/`

สถานะ: ใช้จริง / ทำได้แค่ chat ยังไม่มี visualization

| ไฟล์ | หน้าที่ |
| --- | --- |
| `web/src/App.tsx` | ตัวหลัก เชื่อม bridge ผ่าน `fetch`. `bridgeUrl` = `http://127.0.0.1:9797` (App.tsx:124) |
| `web/src/components/ai/` | conversation, message, tool, confirmation, choice-question — UI ของ chat |
| `web/src/components/ui/` | shadcn components (button, dialog, ฯลฯ) |
| `web/package.json` | React 19 + Vite + Tailwind + shadcn. มี `react-three-fiber` ยัง — ต้องเพิ่มถ้าจะทำ 3D |

จุดเชื่อม bridge ใน `App.tsx`:
- `GET /health` (line 423) — เช็คว่า bridge มีชีวิต
- `POST /v1/chat/completions` (line 732) — ส่งข้อความ
- `POST /v1/bridge/approvals/{id}` (line 667) — ตอบ approval ของ tool

---

## [2] arc-bridge.mjs — `bridge/arc-bridge.mjs`

สถานะ: ใช้จริง / เป็นหัวใจที่ทำให้ browser เป็นไปได้ / ของที่คุณเขียนเอง

ไฟล์เดียวจบ ~1100 บรรทัด เปิด HTTP server แปลง ARC stream-json เป็น OpenAI SSE

| endpoint | บรรทัด | หน้าที่ |
| --- | --- | --- |
| `GET /health` | 729 | health + config ปัจจุบัน |
| `GET /v1/models` | 745 | list model (compat) |
| `POST /v1/chat/completions` | 829 | รับ chat, spawn ARC, สตรีมกลับ |
| `POST /v1/bridge/approvals/{id}` | 760 | ตอบ allow/deny ของ tool |

SSE events ที่ bridge ยิงออก (สำคัญต่อ visualization):
- `bridge.session` (line 901) — session id
- `bridge.tool` (line 952) — **tool call + input** (เช่น scan Cam1)
- `bridge.approval` (line 956) — ขอ approval
- `bridge.tool_result` (line 970) — **ผลลัพธ์ tool** ← ข้อมูลสำหรับวาดอยู่ตรงนี้
- `bridge.message` / data chunk — ข้อความ assistant

> สำคัญ: เส้นทางข้อมูลสำหรับ visualization **มีครบแล้ว** ตัว `bridge.tool_result`
> ส่ง content กลับ browser ได้ ติดแค่ content ยังเป็น JSON ดิบ (string) ที่ต้อง parse
> ไม่ใช่ field ที่วาดได้ทันที → งานที่เหลือคือทำให้ result มี structured field

ตั้งค่าได้ผ่าน env: `ARC_BRIDGE_PORT`, `ARC_BRIDGE_BIN`, `ARC_BRIDGE_TOOLS`, `ARC_BRIDGE_WORKSPACE` ฯลฯ

---

## [3] ARC CLI — `external/Agent-Robotic-Cluster/`

สถานะ: ใช้จริงบางส่วน / ส่วนเกินเยอะ (Claude Code fork)

### 3a. ของใช้จริง (core)

| path | หน้าที่ |
| --- | --- |
| `src/tools/RobotControl/` | robot tools 46 ตัว — **เป็นแค่ตัวแปลงคำสั่ง ไม่มี logic** |
| `src/tools/RobotControl/shared.ts` | **coupling point เดียว** — `executeRobotCommand()` = `spawn('python', ['arc.py'...])`. Node-only |
| `src/tools/RobotControl/prompt.ts` | prompt สั้นของแต่ละ tool + `ROBOT_CONTROL_SHARED_GUIDANCE` |
| `src/tools.ts` | ตัดสินว่า tool ไหน enabled. `getRobotBaseTools()` = lean set 17 ตัว |
| `src/constants/prompts.ts` | system prompt + identity ของ ARC. `getUsingYourToolsSection()` สลับ robot/coding |
| `src/main.tsx` | entry CLI, parse flags |
| agent loop + API client + permission | (กระจายใน src/) — เหตุผลเดียวที่ยืม Claude Code มา |

robot tool ที่เป็น default lean 15-17 ตัว (ใน `getRobotBaseTools` ของ `tools.ts`):
`list_robots, scan, get_state, move, grip, pick, drop, plan_task, validate_plan, execute_plan, get_plan_status, stop, cancel_plan, recover, ack_incident` (+ AskUserQuestion, TaskStop)

### 3b. ส่วนเกิน (ติดมากับ Claude Code ไม่ได้ใช้ในงานหุ่นยนต์)

| path | สถานะ | หมายเหตุ |
| --- | --- | --- |
| `src/bridge/` | **ส่วนเกินก้อนใหญ่** | remote-polling daemon คุยกับ infra Anthropic + worktree. **web UI ไม่ได้ใช้** (ใช้ `bridge/arc-bridge.mjs` ที่ root แทน) |
| Bash/Read/Edit/Write/Glob/Grep/LSP tools | ปิดด้วย flag แล้วบางส่วน | ยังถูก bundle อยู่ — Leaner_plan.md กำลังไล่ปิด |
| `vscode-extension/` | ส่วนเกิน | VS Code extension เดิม |
| coding slash commands, MCP, cron, workflow | ส่วนเกินเกือบหมด | ยังอยู่ในต้นไม้ |
| robot developer tools (~30 ตัว: move_to_pose, run_task, pick_at_pose ฯลฯ) | gate ไว้แล้ว | ใช้เฉพาะ `ARC_ROBOT_DEVELOPER_TOOLS` ไม่ใช่ default |
| `dist/`, `bin/`, `scripts/`, `docs/`, `python/` (provider) | ปนกัน | build output + provider profiles |

### 3c. เอกสารแผน

| ไฟล์ | หน้าที่ |
| --- | --- |
| `Leaner_plan.md` | แผนลด context / ทำ robot-only 8 ข้อ |
| `IMPLEMENTATION_STATUS.md` | สถานะ implement |

---

## [4] Python runtime — `*.py` ที่ root โปรเจกต์

สถานะ: ใช้จริง 100% / ทรัพย์สินจริงทั้งหมดของคุณ / classical CV ไม่มี ML

| ไฟล์ | หน้าที่ |
| --- | --- |
| `arc.py` | **entry point** ที่ TS spawn เข้ามา รับ subcommand (camera/arm/workspace/plan) คืน JSON |
| `fleet_controller.py` | คุมหลายแขน |
| `robot_guardrails.py` | guardrails ความปลอดภัย (+ `test_robot_guardrails.py`) |
| `semantic_plan.py` | plan_task / validate / execute |
| `camera_controller.py` | กล้อง Hikvision (MvImport) + classical CV |
| `workspace_model.py`, `workspace_calibration.py` | model พิกัด workspace |
| `object_calibration.py`, `surface_model.py` | calibration วัตถุ / surface Z |
| `task_system.py`, `incident_store.py`, `config.py` | task state, incident, config |
| `robot_arm.py` | legacy low-level arm |
| `calibration_web.py`, `calibrate_grid_viewer.py` | เครื่องมือ calibration (เว็บแยก) |

dependency: `numpy`, `cv2` (OpenCV), `MvImport` (Hikvision SDK). ดู `python/requirements.txt` (ฝั่ง provider) แยกจาก root

---

## สรุปสั้น: ทำอะไรได้ / ยังไม่ได้

ทำได้แล้ว:
- สั่งหุ่นด้วยภาษาคนผ่าน CLI (ได้เหรียญทอง)
- รันผ่าน browser แบบ chat (พิมพ์คุย เห็น tool ทำงาน เห็น approval)
- guardrails + vision + planning ครบ
- เส้นทางข้อมูล tool result ถึง browser แล้ว (SSE)

ยังทำไม่ได้:
- **เห็นภาพ** — ยังไม่มี visualization แขนกล/workspace/camera (เป้าหมายถัดไป)
- รัน browser ล้วนไม่มี backend (ทำไม่ได้และไม่ควร — Python ต้องอยู่ใกล้หุ่น)
- lean native จริง (ยังแบก Claude Code เต็มก้อน)

coupling ที่ต้องแก้ถ้าจะแยก robot layer ออกจาก shell มีแค่ 2 จุด:
1. `spawn('python')` ใน `src/tools/RobotControl/shared.ts` (Node-only)
2. output schema คืน `stdout: string` ดิบ ไม่มี structured field สำหรับวาด
