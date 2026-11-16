# Plan: Python Service + Contract + Bridge Integration

> แผนทำ Python runtime เป็น service ค้าง + นิยาม contract ชัด + ต่อ visualization
> เขียน: 2026-06-26
> Phase 1-2 ทำก่อน (foundation), Phase 3 ตามมา (visualization)

---

## เป้าหมาย

1. **Contract กลาง** — มี schema ชัดเจนว่า scan/get_state คืนอะไร ไม่ต้องเดาจาก stdout
2. **Python เป็น service** — เปิดค้างแทน spawn ใหม่ทุกครั้ง → latency ดีขึ้น, state ต่อเนื่อง
3. **TS เรียก service** — เปลี่ยน `spawn` เป็น `fetch` ที่จุดเดียว (shared.ts)
4. **Visualization ใช้ได้** — browser ได้ structured data ไปวาด

---

## Phase 1 — Contract กลาง (foundation)

จุดประสงค์: ทำให้ output ของ Python มีโครงชัดเจน browser วาดได้ทันที

### 1.1 เลือก tool ที่ viz ต้องใช้ (3 ตัว)

- `scan` — คืนวัตถุที่เจอ (ตำแหน่ง, สี, reachable)
- `get_state` — คืนตำแหน่งแขนทั้งหมด
- `plan-pick-place` / `plan-handoff` — คืน step plan (optional ทีหลัง)

### 1.2 อ่าน output จริงของ Python

เปิด terminal รัน:
```bash
cd "C:\Users\kuk_g\Downloads\2 robot"
python arc.py camera --json scan Cam1
python arc.py workspace --json state
```

บันทึกโครง JSON ที่คืนมาจริง (field อะไรบ้าง, nested ยังไง) จะได้รู้ว่าต้องนิยาม schema แบบไหน

### 1.3 นิยาม contract schema ไฟล์เดียว

สร้าง `robot_contract.md` ที่ root เป็นเอกสาร contract กลาง เขียนโครง TypeScript + Python ของแต่ละคำสั่ง

ตัวอย่าง:
```typescript
// ScanResult
{
  camera_id: string
  objects: Array<{
    id: string
    color: "red" | "green" | "blue" | "unknown"
    x_mm: number
    y_mm: number
    block: string  // เช่น "A1", "B2"
    reachable_by: string[]  // ["Arm1", "Arm2"]
    per_arm_robot_plane?: {  // พิกัดในระบบแขน
      [arm_id: string]: { x: number, y: number }
    }
  }>
  workspace: {
    width_mm: number
    height_mm: number
  }
}

// RobotState
{
  arms: Array<{
    id: string
    pose: { x: number, y: number, z: number, r: number }
    holding: boolean
    zone: string  // "own", "shared", "other"
    fault?: string
  }>
}
```

### 1.4 แก้ shared.ts ให้แตก field

แก้ `src/tools/RobotControl/shared.ts`:
- เพิ่ม field ใน `robotCommandOutputSchema`:
  ```typescript
  scan_result: z.object({
    camera_id: z.string(),
    objects: z.array(...),
    workspace: z.object(...)
  }).optional(),
  robot_state: z.object({
    arms: z.array(...)
  }).optional()
  ```
- แก้ `executeRobotCommand` ให้ detect tool name แล้วแตก field จาก `parsed`:
  ```typescript
  if (toolName === 'scan' && isRecord(parsed)) {
    return { ...baseResult, scan_result: parsed }
  }
  if (toolName === 'get_state' && isRecord(parsed)) {
    return { ...baseResult, robot_state: parsed }
  }
  ```

### 1.5 ทดสอบ

รัน ARC ผ่าน CLI ปกติ เช็คว่า tool result มี field `scan_result` / `robot_state` ครบ

---

## Phase 2 — Python Service (core)

จุดประสงค์: เปิด Python เป็น HTTP server ค้างแทน spawn ใหม่ทุกครั้ง

### 2.1 สร้าง service wrapper

สร้าง `arc_service.py` ที่ root:

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uvicorn

from camera_controller import CameraController
from config import CAMERA_CONFIG, ARM_REACH_ZONES, ROBOT_FLEET_CONFIG
from fleet_controller import FleetController
from workspace_model import build_workspace_state, build_pick_place_plan, build_handoff_plan

# ต้อง import เพิ่มถ้าใช้ task/plan
# from task_system import TaskStore, ...
# from semantic_plan import SemanticPlanStore, ...

app = FastAPI(title="ARC Robot Service")

# Persistent controller (เปิดค้าง ไม่ใช่ new ทุกครั้ง)
camera_controller = CameraController(CAMERA_CONFIG)
fleet_controller = FleetController(ROBOT_FLEET_CONFIG, ARM_REACH_ZONES)

# เชื่อม controller ตอน startup
@app.on_event("startup")
def startup():
    try:
        camera_controller.connect()
        fleet_controller.connect_all()
        print("ARC service connected to hardware")
    except Exception as e:
        print(f"Warning: hardware connection failed: {e}")
        # ไม่ throw — ให้ service ตอบ error ต่อ request แทน

@app.on_event("shutdown")
def shutdown():
    camera_controller.disconnect()
    fleet_controller.disconnect_all()
    print("ARC service disconnected")

# Pydantic models (ตาม contract)
class ScanRequest(BaseModel):
    camera_id: str = "Cam1"
    color: str = "any"
    selection: str = "first"

class MoveRequest(BaseModel):
    arm_id: str
    action: Optional[str] = None  # "HOME", "UP", ...
    step: Optional[float] = None
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    r: Optional[float] = None

# Endpoints
@app.post("/scan")
def scan(req: ScanRequest):
    try:
        result = camera_controller.scan_workspace(
            camera_id=req.camera_id,
            color_filter=req.color,
            selection=req.selection
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/state")
def get_state():
    try:
        result = build_workspace_state(
            lambda arm_id: fleet_controller.get_arm_pose(arm_id)
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/move")
def move(req: MoveRequest):
    try:
        if req.action:
            result = fleet_controller.move_arm(req.arm_id, req.action, req.step or 25.0)
        elif req.x is not None and req.y is not None and req.z is not None:
            result = fleet_controller.move_to_pose_arm(req.arm_id, req.x, req.y, req.z, req.r or 0.0)
        else:
            raise ValueError("Must provide either action or x/y/z")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stop")
def stop(arm_id: str):
    try:
        result = fleet_controller.stop_arm(arm_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    return {"ok": True, "service": "arc-robot"}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
```

### 2.2 ระวัง concurrency

**สำคัญมาก** service ค้างแปลว่ารับหลาย request พร้อมกัน ต้องกัน race condition:

- ใส่ `asyncio.Lock` ต่อแขน ไม่ให้ move สองคำสั่งชนกัน
- endpoint ที่ไม่ต้อง move (scan, get_state) ไม่ต้อง lock
- ถ้า guardrails ฝั่ง Python ดูแล concurrency อยู่แล้ว ไม่ต้องเพิ่ม (ต้องเช็ค)

เพิ่มใน service:
```python
import asyncio

arm_locks: Dict[str, asyncio.Lock] = {}

def get_arm_lock(arm_id: str) -> asyncio.Lock:
    if arm_id not in arm_locks:
        arm_locks[arm_id] = asyncio.Lock()
    return arm_locks[arm_id]

@app.post("/move")
async def move(req: MoveRequest):
    lock = get_arm_lock(req.arm_id)
    async with lock:
        # ... ทำงานต่อ
```

### 2.3 รัน service

```bash
cd "C:\Users\kuk_g\Downloads\2 robot"
pip install fastapi uvicorn  # ถ้ายังไม่มี
python arc_service.py
```

ทดสอบด้วย curl:
```bash
curl http://127.0.0.1:8765/health
curl -X POST http://127.0.0.1:8765/scan -H "Content-Type: application/json" -d '{"camera_id":"Cam1"}'
```

### 2.4 เปลี่ยน shared.ts ให้เรียก service

แก้ `src/tools/RobotControl/shared.ts` ให้ switch ระหว่าง spawn / service:

```typescript
const ARC_RUNTIME_MODE = process.env.ARC_RUNTIME_MODE || 'subprocess'  // 'subprocess' | 'service'
const ARC_SERVICE_URL = process.env.ARC_SERVICE_URL || 'http://127.0.0.1:8765'

async function callRobotService(
  endpoint: string,
  body: Record<string, unknown>,
  signal: AbortSignal
): Promise<{ code: number; stdout: string; stderr: string; command: string }> {
  const url = `${ARC_SERVICE_URL}${endpoint}`
  const command = `POST ${url}`
  
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal
    })
    
    if (!response.ok) {
      const errorText = await response.text()
      return {
        code: response.status >= 500 ? 1 : 2,
        stdout: '',
        stderr: errorText,
        command
      }
    }
    
    const result = await response.json()
    return {
      code: 0,
      stdout: JSON.stringify(result),
      stderr: '',
      command
    }
  } catch (error) {
    return {
      code: 1,
      stdout: '',
      stderr: error instanceof Error ? error.message : String(error),
      command
    }
  }
}

async function runPythonCommand(
  args: string[],
  cwd: string,
  signal: AbortSignal,
): Promise<{ code: number; stdout: string; stderr: string; command: string }> {
  if (ARC_RUNTIME_MODE === 'service') {
    // แปลง args เป็น service call
    // args = ['arc.py', 'camera', '--json', 'scan', 'Cam1', '--color', 'any']
    const subcommand = args[1]  // 'camera'
    const action = args[args.indexOf('--json') >= 0 ? 4 : 3]  // 'scan'
    
    if (subcommand === 'camera' && action === 'scan') {
      const cameraId = args[args.indexOf('scan') + 1] || 'Cam1'
      const colorIdx = args.indexOf('--color')
      const color = colorIdx >= 0 ? args[colorIdx + 1] : 'any'
      return callRobotService('/scan', { camera_id: cameraId, color }, signal)
    }
    
    if (subcommand === 'workspace' && action === 'state') {
      return callRobotService('/state', {}, signal)
    }
    
    if (subcommand === 'arm' && action === 'move') {
      const armId = args[args.indexOf('move') + 1]
      const moveAction = args[args.indexOf('move') + 2]
      return callRobotService('/move', { arm_id: armId, action: moveAction }, signal)
    }
    
    // fallback ไปยัง spawn ถ้าไม่รู้จักคำสั่ง
    console.warn(`Service mode: unknown command, falling back to subprocess: ${args.join(' ')}`)
  }
  
  // subprocess mode (เดิม)
  const command = ['python', ...args].join(' ')
  return await new Promise((resolvePromise, rejectPromise) => {
    const child = spawn('python', args, { cwd, signal, windowsHide: true })
    // ... ต่อเหมือนเดิม
  })
}
```

### 2.5 ทดสอบ

```bash
# เปิด service ก่อน
python arc_service.py

# terminal ใหม่
cd external/Agent-Robotic-Cluster
set ARC_RUNTIME_MODE=service
node dist/cli.mjs -p "list robots but don't move"
```

เช็คว่า latency เร็วขึ้นจริงไหม (ไม่ต้องรอ import numpy ทุกครั้ง)

---

## Phase 3 — Visualization Panel

จุดประสงค์: browser แสดง workspace + แขนกลจาก structured data

### 3.1 แก้ arc-bridge.mjs ให้ส่ง field พิเศษ

`bridge.tool_result` ที่บรรทัด 970 ส่ง `content` ซึ่งตอนนี้เป็น JSON ดิบ

แก้ให้แตก field `scan_result` / `robot_state` ออกมา:
```javascript
for (const toolResult of nextToolResults) {
  const resultData = typeof toolResult.content === 'string' 
    ? tryParseJson(toolResult.content) 
    : toolResult.content
  
  sendSseEvent(response, 'bridge.tool_result', {
    ...toolResult,
    scan_result: resultData?.scan_result,
    robot_state: resultData?.robot_state
  })
}
```

### 3.2 เพิ่ม visualization component

ใน `web/src/components/robot/` สร้าง:
- `WorkspaceView.tsx` — canvas 2D วาด workspace + วัตถุ + แขน
- `RobotStatePanel.tsx` — แสดง pose ของแขนแต่ละตัว

### 3.3 กิน SSE event มาวาด

แก้ `App.tsx` ให้ฟัง `bridge.tool_result`:
```typescript
if (event.event === 'bridge.tool_result') {
  const data = JSON.parse(event.data)
  if (data.scan_result) {
    setScanData(data.scan_result)
  }
  if (data.robot_state) {
    setRobotState(data.robot_state)
  }
}
```

### 3.4 วาด 2D ก่อน (ง่าย เห็นผลเร็ว)

ใช้ `<canvas>` หรือ SVG วาด top-view:
- พื้นหลัง = workspace (ขนาดจาก contract)
- วงกลมสี = วัตถุที่ scan เจอ
- จุด + เส้น = ตำแหน่งแขน

### 3.5 อัปเป็น 3D (ถ้าต้องการ)

เพิ่ม `react-three-fiber`:
```bash
cd web
npm install three @react-three/fiber @react-three/drei
```

ใช้ 3D model ของแขนจริง (หาจาก URDF / STL) หรือวาดง่าย ๆ เป็น box + cylinder

---

## ความปลอดภัย (สำคัญ)

1. **Service ต้องฟัง localhost เท่านั้น** (127.0.0.1) ห้ามเปิด 0.0.0.0
2. **Concurrency lock** — ห้ามแขนเดียวรับ move สองคำสั่งพร้อมกัน
3. **Guardrails ต้องยังทำงาน** — service ไม่ได้ bypass safety check
4. **Health check + auto-restart** — ถ้า service crash ต้อง restart ได้ หรือ fallback ไป subprocess
5. **ทดสอบบน hardware จริง** — ก่อน deploy ต้องรันบนแขนจริงเช็คว่า safety ไม่พัง

---

## Rollback Plan

ถ้า Phase 2 พัง:
- ตั้ง `ARC_RUNTIME_MODE=subprocess` กลับไป spawn เหมือนเดิม
- Service ไม่ลบ subprocess path ทิ้ง เก็บไว้เป็น fallback

ถ้า Phase 1 พัง:
- Field ที่เพิ่มเป็น optional ทั้งหมด ไม่กระทบของเดิม
- Browser ที่ยังไม่อ่าน field ใหม่ยังใช้ `stdout` ได้ตามปกติ

---

## Checklist

Phase 1:
- [ ] รัน `python arc.py camera --json scan` บันทึก output จริง
- [ ] รัน `python arc.py workspace --json state` บันทึก output จริง
- [ ] เขียน `robot_contract.md`
- [ ] แก้ `shared.ts` เพิ่ม field `scan_result`, `robot_state`
- [ ] ทดสอบ CLI ปกติเช็ค field ครบ

Phase 2:
- [ ] เขียน `arc_service.py` (FastAPI)
- [ ] เพิ่ม concurrency lock
- [ ] ทดสอบ service ด้วย curl
- [ ] แก้ `shared.ts` เพิ่ม service mode
- [ ] ตั้ง `ARC_RUNTIME_MODE=service` ทดสอบผ่าน ARC CLI
- [ ] วัด latency เปรียบเทียบ subprocess vs service
- [ ] ทดสอบบน hardware จริง เช็ค safety

Phase 3:
- [ ] แก้ `arc-bridge.mjs` ส่ง field พิเศษ
- [ ] สร้าง `WorkspaceView.tsx`
- [ ] แก้ `App.tsx` กิน SSE
- [ ] วาด 2D workspace
- [ ] (optional) วาด 3D แขนกล

---

## ลำดับจริง

1. Phase 1.2 — อ่าน output จริง (รันคำสั่ง Python)
2. Phase 1.3 — เขียน contract
3. Phase 1.4-1.5 — แก้ shared.ts + ทดสอบ
4. Phase 2.1-2.3 — เขียน service + รัน + ทดสอบ curl
5. Phase 2.4-2.5 — แก้ shared.ts service mode + ทดสอบ CLI
6. Phase 3 — visualization

ไม่ต้องรีบ ทีละ phase ให้นิ่งก่อนไปต่อ ไม่งั้นจะ debug ไม่รู้เรื่อง
