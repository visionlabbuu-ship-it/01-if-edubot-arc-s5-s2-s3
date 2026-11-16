# Robot Contract — Data Schemas

> Contract กลางระหว่าง Python runtime ↔ TypeScript tools ↔ Browser UI
> อ้างอิงจากโครงสร้างจริงใน `camera_controller.py`, `workspace_model.py`
> เขียน: 2026-06-26

---

## ScanResult — camera scan Cam1

คืนจาก `camera_controller.scan_workspace()` และ tool `scan`

### TypeScript

```typescript
interface ScanResult {
  ok: boolean
  camera_id: string
  timestamp: string  // ISO 8601 UTC
  frame: {
    width: number
    height: number
    mode: string  // "bgr" | "rgb"
  }
  calibration_ready: boolean
  calibration_arm: string  // "Arm1" | "Arm2"
  workspace: {
    detected: boolean
    source: string  // "saved" | "detected" | ...
    calibration_path?: string
    saved: boolean
    stale_saved: boolean
    detection_ready: boolean
    width_mm: number
    height_mm: number
    zones: Array<{
      name: string
      role: string
      bounds_mm: {
        x_min: number
        x_max: number
        y_min: number
        y_max: number
      }
    }>
    robot?: any
  }
  object_count: number
  objects: Array<{
    id: string
    color: "red" | "green" | "blue" | "unknown"
    contour_area: number
    pixel_centroid: { x: number, y: number }
    mm_centroid: { x: number, y: number }
    robot_plane: { x: number, y: number }  // พิกัดในระบบหลัก
    per_arm_robot_plane?: {  // พิกัดแต่ละแขน
      [arm_id: string]: { x: number, y: number }
    }
    block?: string  // "A1", "B2" ฯลฯ
    reachable_by: string[]  // ["Arm1"] | ["Arm2"] | ["Arm1", "Arm2"]
    preferred_arm?: string
    circle?: {
      center: { x: number, y: number }
      radius: number
    }
  }>
  selected_object?: {  // เมื่อใช้ selection
    id: string
    color: string
    // ... (เหมือน objects[i])
  }
  outputs?: {
    image_path?: string
    overlay_path?: string
  }
}
```

### Python (TypedDict style)

```python
from typing import TypedDict, List, Optional

class PixelPoint(TypedDict):
    x: float
    y: float

class WorkspaceZone(TypedDict):
    name: str
    role: str
    bounds_mm: dict  # x_min, x_max, y_min, y_max

class DetectedObject(TypedDict):
    id: str
    color: str  # "red" | "green" | "blue" | "unknown"
    contour_area: float
    pixel_centroid: PixelPoint
    mm_centroid: PixelPoint
    robot_plane: PixelPoint
    per_arm_robot_plane: Optional[dict]  # arm_id -> {x, y}
    block: Optional[str]
    reachable_by: List[str]
    preferred_arm: Optional[str]
    circle: Optional[dict]

class ScanResult(TypedDict):
    ok: bool
    camera_id: str
    timestamp: str
    frame: dict
    calibration_ready: bool
    calibration_arm: str
    workspace: dict
    object_count: int
    objects: List[DetectedObject]
    selected_object: Optional[DetectedObject]
    outputs: Optional[dict]
```

---

## RobotState — workspace state

คืนจาก `workspace_model.build_workspace_state()` และ tool `get_state`

### TypeScript

```typescript
interface RobotState {
  ok: boolean
  workspace_state: boolean
  safety_geometry: {
    // ... (จาก guardrails.safety_geometry())
  }
  arms: Array<{
    arm_id: string
    pose: {
      x: number
      y: number
      z: number
      r: number
    }
    joint_angles?: number[]
    zone: string  // "own" | "shared" | "other" | "unknown"
    zone_name?: string
    occupies_shared: boolean
    is_active: boolean
    is_at_standard_park: boolean
    holding: string  // "unknown" (ตอนนี้ไม่ infer จาก pose)
    holding_contract: string
  }>
  shared_zone_occupancy: {
    occupied_by: string[]  // arm_id[]
    is_clear: boolean
    requires_retreat_before_other_enters: boolean
  }
  zones: {
    [zone_name: string]: {
      // layout info
      bounds_mm?: {
        x_min: number
        x_max: number
        y_min: number
        y_max: number
      }
      role?: string
      arm_id?: string
      // occupancy
      occupancy: {
        occupied_by_arms: string[]
        is_clear_of_arms: boolean
      }
    }
  }
  standard_parking: {
    clear_workspace: {
      [arm_id: string]: boolean
    }
    home_is_clear_workspace: boolean
  }
}
```

### Python (TypedDict style)

```python
class ArmPose(TypedDict):
    x: float
    y: float
    z: float
    r: float

class ArmState(TypedDict):
    arm_id: str
    pose: ArmPose
    joint_angles: Optional[List[float]]
    zone: str
    zone_name: Optional[str]
    occupies_shared: bool
    is_active: bool
    is_at_standard_park: bool
    holding: str
    holding_contract: str

class RobotState(TypedDict):
    ok: bool
    workspace_state: bool
    safety_geometry: dict
    arms: List[ArmState]
    shared_zone_occupancy: dict
    zones: dict
    standard_parking: dict
```

---

## Usage — Browser visualization

เมื่อ browser กิน SSE event `bridge.tool_result`:

```typescript
// ฟัง SSE
eventSource.addEventListener('bridge.tool_result', (event) => {
  const data = JSON.parse(event.data)
  
  if (data.scan_result) {
    const scan: ScanResult = data.scan_result
    // วาด workspace + objects
    drawWorkspace(scan.workspace.width_mm, scan.workspace.height_mm)
    scan.objects.forEach(obj => {
      drawObject(obj.robot_plane.x, obj.robot_plane.y, obj.color)
    })
  }
  
  if (data.robot_state) {
    const state: RobotState = data.robot_state
    // วาด arm poses
    state.arms.forEach(arm => {
      drawArm(arm.arm_id, arm.pose.x, arm.pose.y, arm.zone)
    })
  }
})
```

---

## Error cases

เมื่อ `ok: false`:

```typescript
{
  ok: false,
  camera_id?: string,
  error: string,
  // อาจมี workspace metadata บางส่วน
}
```

Guardrail blocked:

```typescript
{
  ok: false,
  guardrail_blocked: true,
  fault_type: string,
  recommended_action: string,
  robot_id?: string,
  operation?: string,
  guardrail?: {
    // รายละเอียด guardrail ที่บล็อก
  }
}
```

---

## Notes

- พิกัด `robot_plane` คือพิกัดในระบบที่แขนใช้งาน (mm)
- `per_arm_robot_plane` คือพิกัดเฉพาะแต่ละแขน (calibrated สำหรับแขนนั้น)
- `reachable_by` บอกว่าแขนไหนเอื้อมถึงได้ (ผ่าน reach zone check)
- `zone` ของแขน: "own" = อยู่ในโซนตัวเอง, "shared" = อยู่ในโซนร่วม, "other" = ไปข้ามโซนอื่น
- `holding: "unknown"` เพราะ ARC ไม่ infer จาก pose ต้องใช้ tool-state + verification
