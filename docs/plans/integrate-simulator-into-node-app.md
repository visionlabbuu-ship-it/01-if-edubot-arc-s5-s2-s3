# Integrate Simulator Backend into Node.js App

## ปัญหา
ผมสร้าง `browser_ui/` (plain HTML/CSS/JS + Python server `sim_server.py`) แยกออกมา แต่ `npm run dev` เปิด Node.js app ที่ `external/Agent-Robotic-Cluster/web/` ซึ่งมี 3D scene อยู่แล้ว

ตอนนี้มี 2 UI ที่ทำงานซ้ำกัน — ต้องรวมให้เป็นอันเดียว

## เป้าหมาย
ทำให้ Node.js app (React + Three.js) เรียก Python simulator backend ได้ผ่าน API แล้วลบ `browser_ui/` ออก

## สถาปัตยกรรม
```
┌──────────────────────┐     HTTP/JSON     ┌──────────────────────┐
│  npm run dev         │ ──────────────────▶ │  python sim_server.py
│  (localhost:3000)    │                     │  (localhost:8770)    │
│                      │                     │                      │
│  App.tsx             │                     │  FleetController     │
│  ├─ AI Chat          │                     │  WorldModel          │
│  └─ WorkspacePanel   │                     │  sim_world.py        │
│     ├─ Scene (3D)    │◄── scene_state()    │  /api/sim/scene      │
│     ├─ Spawn/Remove  │── POST spawn/remove │  /api/sim/spawn      │
│     └─ Sim Chat      │── POST chat         │  /api/sim/chat       │
│                      │                     │  /api/sim/toggle     │
│                      │                     │  /api/sim/reset      │
└──────────────────────┘                     └──────────────────────┘
```

## สิ่งที่ต้องทำ

### STEP 1: แก้ `sim_server.py` — เพิ่ม CORS
**ไฟล์:** `C:\Users\kuk_g\Downloads\2 robot\sim_server.py`

**ปัญหา:** Server bind 127.0.0.1 ไม่มี CORS header
**แก้:**
- เพิ่ม CORS header ทุก response: `Access-Control-Allow-Origin: *`
- เพิ่ม handler สำหรับ `OPTIONS` preflight
- เปลี่ยน default host จาก `127.0.0.1` → `0.0.0.0`
- เพิ่ม arg `--bind` สำหรับกำหนด host

---

### STEP 2: สร้าง `web/src/lib/sim_api.ts` — API Client
**ไฟล์ใหม่:** `C:\Users\kuk_g\Downloads\2 robot\external\Agent-Robotic-Cluster\web\src\lib\sim_api.ts`

สร้าง utility functions สำหรับเรียก Python server:

```
API_BASE = 'http://localhost:8770'

async function api(path, method, body?)
async function refreshScene()              → GET /api/sim/scene
async function spawnObject(color)          → POST /api/sim/spawn
async function removeObject(id)            → POST /api/sim/remove
async function moveObject(id, x, y)        → POST /api/sim/move
async function resetScene()                → POST /api/sim/reset
async function toggleSim(on?)              → POST /api/sim/toggle
async function sendChat(message)           → POST /api/sim/chat
```

---

### STEP 3: Transform data — Python → WorkspaceSnapshot
**ไฟล์ใหม่:** `C:\Users\kuk_g\Downloads\2 robot\external\Agent-Robotic-Cluster\web\src\lib\sim_transform.ts`

สร้างฟังก์ชันแปลง `scene_state()` จาก Python ให้เป็น `WorkspaceSnapshot` ที่ Scene Component ใช้งาน:

```typescript
// scene_state() → WorkspaceSnapshot
transformToSnapshot(data: SceneState): WorkspaceSnapshot
```

Mapping:
- `data.objects[]` → `snapshot.scan.objects[]`
- `data.held` → `snapshot.state.arms[].holding`
- `data.zones` → `snapshot.scan.workspace.zones`
- `data.sim_mode` → badge สี (green = SIM, red = REAL)

---

### STEP 4: แก้ `WorkspacePanel.tsx` — เพิ่ม Simulator Controls
**ไฟล์:** `C:\Users\kuk_g\Downloads\2 robot\external\Agent-Robotic-Cluster\web\src\components\robot\WorkspacePanel.tsx`

แทนที่ mock scenario selector ด้วย:
- **Sim mode badge** (SQUARE badge: สีเขียว=SIM, สีแดง=REAL)
- **Toggle Sim button** (เรียก `toggleSim()`)
- **Reset Scene button** (เรียก `resetScene()`)
- **Spawn dropdown** (เลือกสี: green/red/blue) + **Add button** (เรียก `spawnObject()`)
- **Sim Chat panel** (input + send button → เรียก `sendChat()`)
- แสดง object count, held object

Fetch scene state ทุก 2 วินาที (polling) หรือ refresh หลัง interaction

---

### STEP 5: แก้ `SceneObject.tsx` — เพิ่ม Drag Support
**ไฟล์:** `C:\Users\kuk_g\Downloads\2 robot\external\Agent-Robotic-Cluster\web\src\components\robot\scene\SceneObject.tsx`

เพิ่ม:
- Prop `draggable?: boolean`
- Prop `onDragStart?(object: DetectedObject)`
- Prop `onDragEnd?(object: DetectedObject, newWorldPos: {x,y})`
- `onPointerDown` → เริ่ม drag, disable OrbitControls
- `onPointerMove` → highlight object (เพิ่ม glow/bloom)
- `onPointerUp` → คำนวณตำแหน่งใหม่ → เรียก `onDragEnd()`

ใช้ `THREE.Raycaster` คำนวณ 2D mouse → 3D ground plane

---

### STEP 6: แก้ `Scene.tsx` — รับ drag events
**ไฟล์:** `C:\Users\kuk_g\Downloads\2 robot\external\Agent-Robotic-Cluster\web\src\components\robot\scene\Scene.tsx`

เพิ่ม:
- `onObjectDragStart` → ส่ง object ID ไป WorkspacePanel
- `onObjectDragEnd` → เรียก `moveObject()` API

---

### STEP 7: เพิ่ม Trash Zone ใน Scene
**ไฟล์:** แก้ `WorkspaceFloor.tsx` หรือ `Scene.tsx`

วาดไอคอนถังขยะมุมขวาล่างของ scene (mapped coordinate)
- ถ้า object ถูก drag ไปเหนือ trash zone → highlight trash
- ถ้าปล่อย → เรียก `removeObject()`

---

### STEP 8: ลบ `browser_ui/`
ลบ directory `C:\Users\kuk_g\Downloads\2 robot\browser_ui/`

---

### STEP 9: Test
1. `python sim_server.py --port 8770`
2. `npm run dev`
3. Verify: scene โหลด 3 objects เดิม
4. Test: spawn object ใหม่ → ปรากฏใน scene
5. Test: drag object → ย้ายถูกตำแหน่ง
6. Test: drag ไป trash → ลบออก
7. Test: toggle sim → badge เปลี่ยน
8. Test: reset → กลับ default
9. Test: chat "add blue" → spawn object
10. Test: chat "scan" → แสดง objects

---

## Dependencies
- Python server ต้องมี CORS header (STEP 1)
- Node.js app เรียกผ่าน `http://localhost:8770`
- ไม่มี WebSocket — ใช้ polling + request/response ธรรมดา
- Error handling ถ้า Python server offline
