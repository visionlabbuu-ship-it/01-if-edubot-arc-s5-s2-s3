"""World model — โลกจำลองที่วัตถุมีตำแหน่งและตอบสนองการหยิบ/วาง

โลกนี้ persist ลงดิสก์ (runtime_state/world_state.json) ดังนั้นแม้ arc.py จะสร้าง
FleetController ใหม่ทุกคำสั่ง โลกก็จำวัตถุที่หยิบ/วางไว้ได้

Flow:
  1. โหลดโลกจากไฟล์ ถ้าไม่มี → สร้างจาก DEFAULT_OBJECTS
  2. pick → world ลบวัตถุ, บันทึกที่ถือ
  3. place → world วางวัตถุที่ถือลงตำแหน่งใหม่
  4. scan → world คืน object list ปัจจุบัน (สะท้อนการหยิบ/วาง)
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from .state_paths import runtime_state_path

WORLD_STATE_FILE = runtime_state_path("world_state.json")


# ---------------------------------------------------------------------------
# Default world — สร้างจาก scan_capture.png โดยประมาณ (ใช้ค่าจริงจาก CV)
# ---------------------------------------------------------------------------

# robot_plane = shared workspace mm (ตรงกับ zone bounds)
# per_arm ถูกเติมตอน normalize — ห้ามใส่ค่า workspace ซ้ำทั้งสองแขน
# ตำแหน่ง default ต้องอยู่ในระยะเอื้อม (~planar ≤ 400mm) ของ preferred arm
# block 2,2 กลาง (300,100) ไกลเกินทั้งสองแขน → วางฝั่งขวาของ 2,2 ให้ Arm2 หยิบได้
_DEFAULT_OBJECTS_RAW = [
    {
        "id": "obj-1",
        "color": "green",
        "robot_plane": {"x": 158.4, "y": -19.8},
        "reachable_by": ["Arm1"],
        "preferred_arm": "Arm1",
        "block": "2,1",
        "inside_workspace": True,
        "area": 1200.0,
        "detection_source": "color_contour",
    },
    {
        "id": "obj-2",
        "color": "green",
        # บล็อก 2,2 ฝั่งขวา — Arm2 planar ≈ 344mm (อยู่ใน max_safe_r)
        "robot_plane": {"x": 360.0, "y": 120.0},
        "reachable_by": ["Arm1", "Arm2"],
        "preferred_arm": "Arm2",
        "block": "2,2",
        "inside_workspace": True,
        "area": 1350.0,
        "detection_source": "color_contour",
    },
    {
        "id": "obj-3",
        "color": "blue",
        "robot_plane": {"x": 500.0, "y": -50.0},
        "reachable_by": ["Arm1", "Arm2"],
        "preferred_arm": "Arm2",
        "block": "2,3",
        "inside_workspace": True,
        "area": 1100.0,
        "detection_source": "color_contour",
    },
]

# ---------------------------------------------------------------------------
# Helpers + frame transforms
# ---------------------------------------------------------------------------

def _point_distance_mm(a: dict, b: dict) -> float:
    dx = float(a["x"]) - float(b["x"])
    dy = float(a["y"]) - float(b["y"])
    return (dx * dx + dy * dy) ** 0.5


# Canonical workspace zones — ใช้ทั้ง scan payload และ scene editor
WORKSPACE_ZONES = [
    {"name": "shared", "role": "shared", "bounds_mm": {"x_min": 200, "x_max": 400, "y_min": 0, "y_max": 400}},
    {"name": "storage", "role": "storage", "bounds_mm": {"x_min": 0, "x_max": 200, "y_min": -200, "y_max": 0}},
    {"name": "drop", "role": "drop", "bounds_mm": {"x_min": 400, "x_max": 600, "y_min": -200, "y_max": 0}},
]

# จานสี default สำหรับ spawn
_SPAWN_COLORS = {"green", "red", "blue"}

# ฐานแขนใน shared workspace frame (mm) — ตรงกับ web geometry / layout reference
# robot_plane ของวัตถุ = workspace; คำสั่ง MG400 = arm-local
ARM_BASES = {
    "Arm1": {"x": -95.0, "y": 200.0, "facing": 1.0},
    "Arm2": {"x": 695.0, "y": 200.0, "facing": -1.0},
}


def workspace_to_arm_local(arm_id: str, wx: float, wy: float) -> dict:
    """shared workspace (mm) → arm-local ที่ใช้ MovL/MovJ"""
    base = ARM_BASES.get(arm_id) or ARM_BASES["Arm1"]
    f = float(base["facing"])
    return {
        "x": f * (float(wx) - float(base["x"])),
        "y": f * (float(wy) - float(base["y"])),
        "calibration": "workspace_to_arm_local",
    }


def arm_local_to_workspace(arm_id: str, lx: float, ly: float) -> dict:
    """arm-local → shared workspace (mm)"""
    base = ARM_BASES.get(arm_id) or ARM_BASES["Arm1"]
    f = float(base["facing"])
    return {
        "x": float(base["x"]) + f * float(lx),
        "y": float(base["y"]) + f * float(ly),
    }


def _zone_for_point(x: float, y: float) -> Optional[dict]:
    """หาว่าตำแหน่ง (x, y) ตกอยู่ในโซนไหน — คืน None ถ้าไม่อยู่ในโซนใด"""
    for zone in WORKSPACE_ZONES:
        b = zone["bounds_mm"]
        if b["x_min"] <= x <= b["x_max"] and b["y_min"] <= y <= b["y_max"]:
            return {"name": zone["name"], "role": zone["role"], "bounds_mm": dict(b)}
    return None


def _sync_per_arm_planes(obj: dict) -> dict:
    """robot_plane = workspace → เติม per_arm_robot_plane เป็น local ต่อแขน"""
    rp = obj.get("robot_plane") or {}
    try:
        wx = float(rp.get("x", 0.0))
        wy = float(rp.get("y", 0.0))
    except (TypeError, ValueError):
        wx, wy = 0.0, 0.0
    obj["robot_plane"] = {"x": wx, "y": wy}
    obj["per_arm_robot_plane"] = {
        arm_id: workspace_to_arm_local(arm_id, wx, wy) for arm_id in ARM_BASES
    }
    return obj


def _normalize_object(obj: dict) -> dict:
    o = dict(obj)
    o = _sync_per_arm_planes(o)
    rp = o["robot_plane"]
    o["zone"] = _zone_for_point(rp["x"], rp["y"]) or o.get("zone")
    return o


def _default_objects() -> list:
    return [_normalize_object(dict(o)) for o in _DEFAULT_OBJECTS_RAW]


DEFAULT_OBJECTS = _default_objects()


def _make_scan_payload(objects: list, camera_id: str = "Cam1") -> dict:
    """สร้าง scan payload ที่โครงสร้างตรงกับ scan_workspace() จริง"""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ok": True,
        "camera_id": camera_id,
        "timestamp": now,
        "frame": {"width": 640, "height": 480, "mode": "bgr"},
        "calibration_ready": True,
        "calibration_arm": "Arm1",
        "workspace": {
            "detected": True,
            "source": "saved",
            "width_mm": 600,
            "height_mm": 400,
            "saved": True,
            "detection_ready": True,
            "zones": [dict(z, bounds_mm=dict(z["bounds_mm"])) for z in WORKSPACE_ZONES],
        },
        "object_count": len(objects),
        "objects": objects,
        "selected_object": objects[0] if objects else None,
    }


# ---------------------------------------------------------------------------
# WorldModel
# ---------------------------------------------------------------------------

class WorldModel:
    """โลกจำลอง — persist ลงดิสก์, object มีตำแหน่งจริง"""
    _instance = None
    _state_file = None

    def __new__(cls, state_file=None):
        target = state_file or WORLD_STATE_FILE
        if cls._instance is None or cls._state_file != target:
            cls._instance = super().__new__(cls)
            cls._state_file = target
        return cls._instance

    def __init__(self, state_file: Optional[Path] = None):
        if getattr(self, "_initialized", False):
            return
        self.state_file = state_file or WORLD_STATE_FILE
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._objects: list[dict] = []
        self._held: Optional[dict] = None  # วัตถุที่กำลังถือ
        self._held_by: Optional[str] = None  # arm_id ที่ถือ (Arm1/Arm2)
        self._file_mtime: float = 0.0
        self._initialized = True
        self._load(force=True)

    def _apply_state_data(self, data: dict) -> None:
        self._objects = [_normalize_object(dict(o)) for o in data.get("objects", [])]
        held = data.get("held")
        self._held = _normalize_object(dict(held)) if held else None
        self._held_by = data.get("held_by") if self._held else None
        if self._held is None:
            self._held_by = None

    def _load(self, force: bool = False) -> None:
        """โหลดจากไฟล์

        - เขียน atomic แล้วเท่านั้นถึงจะ parse ได้
        - ไฟล์พัง/ว่างครึ่งทาง: **ห้าม** รีเซ็ตเป็น DEFAULT ถ้า memory มีของอยู่แล้ว
          (นี่คือสาเหตุเดิมที่ลากเรียงแล้ว pick แล้วกระดานกลับจุดเริ่ม)
        """
        with self._lock:
            path = self.state_file
            if path.exists():
                try:
                    mtime = path.stat().st_mtime
                    if not force and mtime <= self._file_mtime and (self._objects or self._held):
                        return
                    raw = path.read_text(encoding="utf-8")
                    if not raw.strip():
                        # ไฟล์ว่างระหว่างเขียน — คง memory
                        if self._objects or self._held is not None:
                            return
                        raise json.JSONDecodeError("empty", raw, 0)
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise json.JSONDecodeError("not object", raw, 0)
                    self._apply_state_data(data)
                    self._file_mtime = mtime
                    return
                except (json.JSONDecodeError, OSError, TypeError, ValueError):
                    # อย่าทับ memory ที่ยังมี layout/held ของผู้ใช้
                    if self._objects or self._held is not None:
                        return
            # ครั้งแรก / ไม่มีไฟล์ / memory ว่างจริง
            self._objects = _default_objects()
            self._held = None
            self._held_by = None
            self._save_unlocked()

    def reload_if_changed(self) -> bool:
        """โหลดใหม่เฉพาะเมื่อไฟล์บนดิสก์ใหม่กว่า memory (subprocess/CLI)"""
        with self._lock:
            path = self.state_file
            if not path.exists():
                return False
            try:
                mtime = path.stat().st_mtime
            except OSError:
                return False
            if mtime <= self._file_mtime:
                return False
            before_ids = [o.get("id") for o in self._objects]
            before_held = self._held_by
            self._load(force=True)
            after_ids = [o.get("id") for o in self._objects]
            return before_ids != after_ids or before_held != self._held_by

    def _save_unlocked(self) -> None:
        """atomic write: tmp → fsync → replace — กัน partial JSON ตอน poll/pick พร้อมกัน"""
        data = {
            "objects": self._objects,
            "held": self._held,
            "held_by": self._held_by,
        }
        path = self.state_file
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".world_state.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
            try:
                self._file_mtime = path.stat().st_mtime
            except OSError:
                self._file_mtime = 0.0
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _save(self) -> None:
        with self._lock:
            self._save_unlocked()

    def reset(self) -> None:
        """ล้างโลกกลับสู่ default"""
        with self._lock:
            self._objects = _default_objects()
            self._held = None
            self._held_by = None
            self._save_unlocked()

    # ---- object query ----
    @property
    def objects(self) -> list[dict]:
        with self._lock:
            return list(self._objects)

    def get_object_by_id(self, obj_id: str) -> Optional[dict]:
        with self._lock:
            for o in self._objects:
                if o.get("id") == obj_id:
                    return o
            return None

    def find_nearby(self, pose: dict, tolerance_mm: float = 35.0, arm_id: Optional[str] = None) -> Optional[dict]:
        """หาวัตถุใกล้ pose

        pose อาจเป็น:
          - workspace (robot_plane) — ระยะตรงกับ object.robot_plane
          - arm-local — ถ้าส่ง arm_id มา จะเทียบกับ per_arm_robot_plane[arm]
        """
        with self._lock:
            best = None
            best_d = float("inf")
            px, py = float(pose.get("x", 0.0)), float(pose.get("y", 0.0))
            for obj in self._objects:
                candidates = [obj.get("robot_plane") or {}]
                if arm_id:
                    per = (obj.get("per_arm_robot_plane") or {}).get(arm_id)
                    if isinstance(per, dict):
                        candidates.append(per)
                for rp in candidates:
                    try:
                        d = _point_distance_mm(rp, {"x": px, "y": py})
                    except (TypeError, KeyError, ValueError):
                        continue
                    if d <= tolerance_mm and d < best_d:
                        best = obj
                        best_d = d
            return best

    # ---- pick ----
    def pick_at(self, pose: dict, tolerance_mm: float = 35.0, arm_id: Optional[str] = None) -> dict:
        """หยิบวัตถุใกล้ pose (workspace หรือ arm-local ถ้ามี arm_id)

        เก็บ held.robot_plane เป็น workspace เสมอ — อย่าเขียนทับด้วย local
        """
        with self._lock:
            obj = None
            # inline find to stay under same lock
            px, py = float(pose.get("x", 0.0)), float(pose.get("y", 0.0))
            best_d = float("inf")
            for candidate in self._objects:
                planes = [candidate.get("robot_plane") or {}]
                if arm_id:
                    per = (candidate.get("per_arm_robot_plane") or {}).get(arm_id)
                    if isinstance(per, dict):
                        planes.append(per)
                for rp in planes:
                    try:
                        d = _point_distance_mm(rp, {"x": px, "y": py})
                    except (TypeError, KeyError, ValueError):
                        continue
                    if d <= tolerance_mm and d < best_d:
                        obj = candidate
                        best_d = d
            if obj is None and arm_id:
                ws = arm_local_to_workspace(arm_id, float(pose["x"]), float(pose["y"]))
                wx, wy = float(ws["x"]), float(ws["y"])
                best_d = float("inf")
                for candidate in self._objects:
                    rp = candidate.get("robot_plane") or {}
                    try:
                        d = _point_distance_mm(rp, {"x": wx, "y": wy})
                    except (TypeError, KeyError, ValueError):
                        continue
                    if d <= tolerance_mm and d < best_d:
                        obj = candidate
                        best_d = d
            if obj is None:
                return {"ok": False, "error": "No object found at this position"}

            self._held = self._objects.pop(self._objects.index(obj))
            self._held_by = arm_id or self._held.get("preferred_arm") or "Arm1"
            self._held = _normalize_object(self._held)
            self._save_unlocked()
            return {"ok": True, "object": dict(self._held), "held_by": self._held_by}

    # ---- place ----
    def place_at(self, pose: dict, arm_id: Optional[str] = None) -> dict:
        """วางวัตถุที่ถือ

        ถ้ามี arm_id: pose.x/y = arm-local (จาก motion ของแขน) → แปลงเป็น workspace
        ถ้าไม่มี arm_id: pose = workspace โดยตรง
        """
        with self._lock:
            if self._held is None:
                return {"ok": False, "error": "Nothing held to place"}

            px, py = float(pose["x"]), float(pose["y"])
            if arm_id:
                ws = arm_local_to_workspace(arm_id, px, py)
                px, py = float(ws["x"]), float(ws["y"])

            obj = self._held
            obj["robot_plane"] = {"x": px, "y": py}
            obj = _normalize_object(obj)
            self._objects.append(obj)
            self._held = None
            self._held_by = None
            self._save_unlocked()
            return {"ok": True, "object": obj}

    def release(self) -> dict:
        """ปล่อยวัตถุ (ไม่ลงที่ใด — ถือไว้แต่ไม่ถือ)"""
        with self._lock:
            if self._held is None:
                return {"ok": False, "error": "Nothing released"}
            self._held = None
            self._held_by = None
            self._save_unlocked()
            return {"ok": True}

    # ---- scene editing (spawn / remove / move) ----
    def _next_object_id_unlocked(self) -> str:
        """สร้าง ID ใหม่ = obj-{max+1} โดยดูจาก ID ที่มีอยู่ (รวมที่ถืออยู่)"""
        max_n = 0
        pool = list(self._objects)
        if self._held is not None:
            pool.append(self._held)
        for o in pool:
            oid = str(o.get("id", ""))
            if oid.startswith("obj-"):
                try:
                    max_n = max(max_n, int(oid.split("-", 1)[1]))
                except (ValueError, IndexError):
                    continue
        return f"obj-{max_n + 1}"

    def spawn_object(self, color: str = "green", x: float = 250.0, y: float = -50.0,
                     obj_id: Optional[str] = None) -> dict:
        """สร้างวัตถุใหม่ลงโลก — auto-generate ID, default อยู่กลาง workspace"""
        color = str(color or "green").strip().lower()
        if color not in _SPAWN_COLORS:
            return {"ok": False, "error": f"Unsupported color '{color}'. Use one of {sorted(_SPAWN_COLORS)}"}

        with self._lock:
            new_id = obj_id or self._next_object_id_unlocked()
            for o in self._objects:
                if o.get("id") == new_id:
                    return {"ok": False, "error": f"Object id '{new_id}' already exists"}
            if self._held is not None and self._held.get("id") == new_id:
                return {"ok": False, "error": f"Object id '{new_id}' already exists"}

            x = float(x)
            y = float(y)
            obj = _normalize_object({
                "id": new_id,
                "color": color,
                "robot_plane": {"x": x, "y": y},
                "reachable_by": ["Arm1", "Arm2"],
                "preferred_arm": "Arm1",
                "block": None,
                "inside_workspace": True,
                "area": 1200.0,
                "detection_source": "scene_editor",
            })
            self._objects.append(obj)
            self._save_unlocked()
            return {"ok": True, "object": dict(obj)}

    def remove_object(self, obj_id: str) -> dict:
        """ลบวัตถุออกจากโลกตาม ID (รวมถ้ากำลังถืออยู่)"""
        with self._lock:
            obj = None
            for o in self._objects:
                if o.get("id") == obj_id:
                    obj = o
                    break
            if obj is not None:
                self._objects.pop(self._objects.index(obj))
                self._save_unlocked()
                return {"ok": True, "object": obj}
            if self._held is not None and self._held.get("id") == obj_id:
                removed = self._held
                self._held = None
                self._held_by = None
                self._save_unlocked()
                return {"ok": True, "object": removed}
            return {"ok": False, "error": f"Object '{obj_id}' not found"}

    def move_object(self, obj_id: str, x: float, y: float) -> dict:
        """ย้ายวัตถุ — x,y = shared workspace mm (จาก drag บน canvas)"""
        with self._lock:
            obj = None
            for o in self._objects:
                if o.get("id") == obj_id:
                    obj = o
                    break
            if obj is None:
                return {"ok": False, "error": f"Object '{obj_id}' not found"}
            idx = self._objects.index(obj)
            obj["robot_plane"] = {"x": float(x), "y": float(y)}
            self._objects[idx] = _normalize_object(obj)
            self._save_unlocked()
            return {"ok": True, "object": dict(self._objects[idx])}

    # ---- scan ----
    def simulate_scan(self, camera_id: str = "Cam1") -> dict:
        """scan จากโลกปัจจุบัน (ไม่มีการถืออยู่)"""
        with self._lock:
            visible = [dict(o) for o in self._objects]
        return _make_scan_payload(visible, camera_id)

    # ---- scene rendering ----
    def scene_state(self) -> dict:
        """สถานะฉากสำหรับ UI — objects, held, held_by, zones"""
        with self._lock:
            return {
                "ok": True,
                "objects": [dict(o) for o in self._objects],
                "held": dict(self._held) if self._held else None,
                "held_by": self._held_by if self._held else None,
                "zones": [dict(z, bounds_mm=dict(z["bounds_mm"])) for z in WORKSPACE_ZONES],
            }

    # ---- state ----
    def state_summary(self) -> dict:
        with self._lock:
            return {
                "object_count": len(self._objects),
                "held_color": self._held["color"] if self._held else None,
                "held_id": self._held["id"] if self._held else None,
                "held_by": self._held_by if self._held else None,
                "objects": [
                    {"id": o["id"], "color": o["color"], "robot_plane": o["robot_plane"]}
                    for o in self._objects
                ],
            }

    def held_snapshot(self) -> tuple:
        """(held_dict|None, held_by|None) แบบ thread-safe สำหรับ arms poll"""
        with self._lock:
            held = dict(self._held) if self._held else None
            held_by = self._held_by if held else None
            return held, held_by

    def update_held_workspace(self, wx: float, wy: float) -> None:
        """อัปเดตตำแหน่ง held บน workspace ขณะแขนขยับ (thread-safe)"""
        with self._lock:
            if self._held is None:
                return
            self._held["robot_plane"] = {"x": float(wx), "y": float(wy)}
            self._held = _normalize_object(self._held)
            # ไม่ save ทุกเฟรม motion — ลด I/O; pick/place/move ยัง save
