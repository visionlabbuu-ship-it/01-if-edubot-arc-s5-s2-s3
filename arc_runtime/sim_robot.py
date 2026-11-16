"""Simulation backend for MG400 robot arms + world model.

SimRobot สืบทอดจาก MG400Robot แล้ว override "เฉพาะชั้นที่คุยกับฮาร์ดแวร์"
ส่วน logic ระดับสูง — move_to_pose, home, pick, การรอ motion, การจับ fault —
ยังเป็นของคลาสแม่ ทำให้ guardrails / planning / contract วิ่งเหมือนจริงทุกบรรทัด

Motion animation profile (config / env):
  ARC_SIM_MOTION_SPEED_MM_S  ความเร็ว mm/s (default 100)
  ARC_SIM_MOTION_FPS         อัปเดต pose ≥25 (default 50)
  ARC_SIM_MOTION_MIN_S       เวลา move ขั้นต่ำ (default 0.8)
  ARC_SIM_MOTION_MAX_S       เวลา move สูงสุด (default 6.0)
  ARC_SIM_MOTION_EASING      linear | smoothstep

Server เดิน trajectory ตาม wall-clock + เผยแพร่ motion segment
ให้ UI เล่นอนิเมชันเองที่ 60fps (ไม่พึ่ง poll อย่างเดียว)
"""

from __future__ import annotations

import math
import re
import threading
import time

from . import config
from .robot_arm import MG400Robot
from .sim_world import WorldModel


def _now_ms() -> float:
    return time.time() * 1000.0


class SimRobot(MG400Robot):
    def __init__(self, robot_id, ip, port, start_pose=None, world: WorldModel = None):
        super().__init__(robot_id, ip, port)
        self.lock = threading.RLock()
        self.world = world or WorldModel()

        sx, sy, sz, sr = self._parse_pose_string(self.HOME_POS)
        if start_pose:
            sx = float(start_pose.get("x", sx))
            sy = float(start_pose.get("y", sy))
            sz = float(start_pose.get("z", sz))
            sr = float(start_pose.get("r", sr))
        self._pose = {"x": sx, "y": sy, "z": sz, "r": sr}
        self._tool_enabled = False
        self._robot_mode = 4
        self._error = False
        self._running = False

        # Active motion segment (for client-side 60fps playback)
        self._motion = None  # type: dict | None
        self._apply_motion_profile_from_config()

    def _apply_motion_profile_from_config(self):
        self._motion_speed_mm_s = float(getattr(config, "SIM_MOTION_SPEED_MM_S", 100.0))
        self._motion_fps = float(getattr(config, "SIM_MOTION_FPS", 50.0))
        self._motion_dt_s = float(getattr(config, "SIM_MOTION_DT_S", 1.0 / 50.0))
        self._motion_min_s = float(getattr(config, "SIM_MOTION_MIN_S", 0.8))
        self._motion_max_s = float(getattr(config, "SIM_MOTION_MAX_S", 6.0))
        self._motion_easing = str(getattr(config, "SIM_MOTION_EASING", "linear")).lower()

    def motion_profile(self) -> dict:
        return {
            "speed_mm_s": self._motion_speed_mm_s,
            "fps": self._motion_fps,
            "dt_s": self._motion_dt_s,
            "min_s": self._motion_min_s,
            "max_s": self._motion_max_s,
            "easing": self._motion_easing,
        }

    @staticmethod
    def _parse_pose_string(text):
        parts = [p.strip() for p in str(text).split(",")]
        vals = [float(p) for p in parts[:4]] + [0.0] * (4 - len(parts))
        return vals[0], vals[1], vals[2], vals[3]

    @staticmethod
    def _parse_args(cmd):
        match = re.search(r"\(([^)]*)\)", cmd)
        if not match:
            return []
        raw = match.group(1).replace("{", "").replace("}", "")
        out = []
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                out.append(float(token))
            except ValueError:
                pass
        return out

    def _dashboard_ok(self, cmd):
        return f"0,{{}},{cmd};"

    def _copy_pose(self, pose=None):
        p = pose or self._pose
        return {
            "x": float(p["x"]),
            "y": float(p["y"]),
            "z": float(p["z"]),
            "r": float(p["r"]),
        }

    # ---- override: connection ----
    def connect(self):
        self.is_connected = True
        self._robot_mode = 5
        self._error = False
        self._running = False
        self._motion = None
        return True

    def disconnect(self):
        self.is_connected = False
        self.dashboard_sock = None
        self.move_sock = None
        self._running = False
        self._motion = None

    # ---- override: I/O layer ----
    def send_dashboard(self, cmd, wait_idle=True):
        if not self.is_connected:
            self.connect()
        c = str(cmd).strip()
        if c.startswith("ClearError"):
            self._error = False
        elif c.startswith("EnableRobot"):
            self._robot_mode = 5
        elif c.startswith(("Stop", "ResetRobot")):
            self._robot_mode = 5
            self._running = False
            self._motion = None
        elif c.startswith("SpeedL"):
            # SpeedL(1..100) → scale sim linear speed relative to profile default
            args = self._parse_args(c)
            if args:
                pct = max(1.0, min(100.0, float(args[0])))
                base = float(getattr(config, "SIM_MOTION_SPEED_MM_S", 100.0))
                self._motion_speed_mm_s = base * (pct / 50.0)
        return self._dashboard_ok(c)

    def send_motion(self, cmd, wait_idle=True):
        if not self.is_connected:
            self.connect()
        with self.lock:
            # ensure pose is current before starting a new segment
            self._sync_pose_from_motion_unlocked()
            start_state = self._feedback_dict_unlocked()
            if wait_idle:
                self._apply_motion_animated(cmd)
            else:
                self._apply_motion_instant(cmd)
            final_state = self._feedback_dict_unlocked()
            if not wait_idle:
                return self._dashboard_ok(str(cmd).strip())
            return {
                "ok": True,
                "status": "completed",
                "command": cmd,
                "command_response": self._dashboard_ok(str(cmd).strip()),
                "start_state": start_state,
                "final_state": final_state,
                "saw_running_motion": True,
                "max_deviation_mm": self._pose_distance_mm(
                    start_state.get("pose"), final_state.get("pose")
                )
                or 0.0,
            }

    def _target_from_cmd(self, cmd):
        c = str(cmd).strip()
        args = self._parse_args(c)
        if c.startswith("RelMovL") or c.startswith("RelMovJ"):
            if len(args) < 3:
                return None
            return {
                "x": self._pose["x"] + args[0],
                "y": self._pose["y"] + args[1],
                "z": self._pose["z"] + args[2],
                "r": self._pose["r"] + (args[3] if len(args) >= 4 else 0.0),
            }
        if c.startswith("MovL") or c.startswith("MovJ"):
            if len(args) < 3:
                return None
            return {
                "x": args[0],
                "y": args[1],
                "z": args[2],
                "r": args[3] if len(args) >= 4 else self._pose["r"],
            }
        if c.startswith("Arc") or c.startswith("Circle"):
            if len(args) < 8:
                return None
            return {
                "x": args[4],
                "y": args[5],
                "z": args[6],
                "r": args[7],
            }
        return None

    def _set_pose_unlocked(self, x, y, z, r):
        self._pose["x"] = float(x)
        self._pose["y"] = float(y)
        self._pose["z"] = float(z)
        self._pose["r"] = float(r)
        self._update_world_pose(self._pose["x"], self._pose["y"], self._pose["z"])

    def _apply_motion_instant(self, cmd):
        target = self._target_from_cmd(cmd)
        if target is None:
            return
        self._motion = None
        self._running = False
        self._set_pose_unlocked(target["x"], target["y"], target["z"], target["r"])

    @staticmethod
    def _ease(t: float, mode: str) -> float:
        t = max(0.0, min(1.0, t))
        if mode in ("smoothstep", "ease", "ease_in_out"):
            return t * t * (3.0 - 2.0 * t)
        # linear — ความเร็วคงที่ เหมาะกับหุ่นยนต์
        return t

    def _path_length_mm(self, start: dict, target: dict) -> float:
        dx = target["x"] - start["x"]
        dy = target["y"] - start["y"]
        dz = target["z"] - start["z"]
        # r องศา → mm-equivalent คร่าวๆ
        dr = (target["r"] - start["r"]) * 2.0
        return math.sqrt(dx * dx + dy * dy + dz * dz + dr * dr)

    def _motion_duration_s(self, start: dict, target: dict) -> float:
        dist = self._path_length_mm(start, target)
        speed = max(20.0, float(self._motion_speed_mm_s))
        duration = dist / speed
        return max(self._motion_min_s, min(self._motion_max_s, duration))

    def _pose_at_progress(self, start: dict, target: dict, t: float) -> dict:
        u = self._ease(t, self._motion_easing)
        return {
            "x": start["x"] + (target["x"] - start["x"]) * u,
            "y": start["y"] + (target["y"] - start["y"]) * u,
            "z": start["z"] + (target["z"] - start["z"]) * u,
            "r": start["r"] + (target["r"] - start["r"]) * u,
        }

    def _sync_pose_from_motion_unlocked(self):
        """อัปเดต pose จาก trajectory ตามเวลาจริง (ถ้ากำลังเคลื่อนที่)"""
        m = self._motion
        if not m:
            return
        now = _now_ms()
        t0 = float(m["t0_ms"])
        duration_ms = max(1.0, float(m["duration_ms"]))
        t = (now - t0) / duration_ms
        if t >= 1.0:
            end = m["to"]
            self._set_pose_unlocked(end["x"], end["y"], end["z"], end["r"])
            self._motion = None
            self._running = False
            self._robot_mode = 5
            return
        if t < 0.0:
            t = 0.0
        p = self._pose_at_progress(m["from"], m["to"], t)
        self._set_pose_unlocked(p["x"], p["y"], p["z"], p["r"])
        self._running = True
        self._robot_mode = 7

    def _apply_motion_animated(self, cmd):
        """เดิน trajectory ตาม wall-clock — ปล่อย lock ระหว่างรอ เพื่อให้ poll อ่าน pose ได้

        เผยแพร่ motion segment (from/to/t0/duration) ให้ UI interpolate เอง ≥25–60 FPS
        """
        target = self._target_from_cmd(cmd)
        if target is None:
            return

        start = self._copy_pose()
        dist = self._path_length_mm(start, target)
        if dist < 0.5 and abs(target["r"] - start["r"]) < 0.5:
            self._motion = None
            self._set_pose_unlocked(target["x"], target["y"], target["z"], target["r"])
            return

        duration_s = self._motion_duration_s(start, target)
        duration_ms = duration_s * 1000.0
        t0_ms = _now_ms()
        self._motion = {
            "from": start,
            "to": self._copy_pose(target),
            "t0_ms": t0_ms,
            "duration_ms": duration_ms,
            "easing": self._motion_easing,
            "speed_mm_s": self._motion_speed_mm_s,
        }
        self._running = True
        self._robot_mode = 7

        dt = max(0.01, float(self._motion_dt_s))  # ≥25 FPS → dt ≤ 0.04; default 50 FPS
        try:
            while True:
                self._sync_pose_from_motion_unlocked()
                if self._motion is None:
                    break
                # ปล่อย lock ให้ HTTP อ่าน scene/arms ระหว่าง animate
                self.lock.release()
                try:
                    time.sleep(dt)
                finally:
                    self.lock.acquire()
        finally:
            # snap to end
            self._set_pose_unlocked(target["x"], target["y"], target["z"], target["r"])
            self._motion = None
            self._running = False
            self._robot_mode = 5

    def _apply_motion(self, cmd):
        """legacy name — instant apply"""
        self._apply_motion_instant(cmd)

    def _feedback_dict_unlocked(self):
        self._sync_pose_from_motion_unlocked()
        mode_name = self.ROBOT_MODE_NAMES.get(self._robot_mode, "unknown")
        motion = None
        if self._motion:
            m = self._motion
            motion = {
                "from": dict(m["from"]),
                "to": dict(m["to"]),
                "t0_ms": float(m["t0_ms"]),
                "duration_ms": float(m["duration_ms"]),
                "easing": m.get("easing", "linear"),
                "speed_mm_s": float(m.get("speed_mm_s", self._motion_speed_mm_s)),
                "server_now_ms": _now_ms(),
            }
        return {
            "ok": True,
            "robot_mode": self._robot_mode,
            "robot_mode_name": mode_name,
            "enable_status": self._robot_mode in (5, 7),
            "running_status": bool(self._running or self._motion),
            "error_status": self._error,
            "brake_status": False,
            "joints": {
                "j1": 0.0,
                "j2": 0.0,
                "j3": 0.0,
                "j4": self._pose["r"],
                "j5": 0.0,
                "j6": 0.0,
            },
            "pose": {
                "x": self._pose["x"],
                "y": self._pose["y"],
                "z": self._pose["z"],
                "r": self._pose["r"],
                "rx": 0.0,
                "ry": 0.0,
            },
            "motion": motion,
            "motion_profile": self.motion_profile(),
            "server_now_ms": _now_ms(),
        }

    def _feedback_dict(self):
        with self.lock:
            return self._feedback_dict_unlocked()

    def get_feedback_snapshot(self):
        if not self.is_connected:
            self.connect()
        return self._feedback_dict()

    def get_motion_public(self):
        """lightweight pose + trajectory สำหรับ UI poll เร็ว"""
        with self.lock:
            fb = self._feedback_dict_unlocked()
            return {
                "arm_id": self.robot_id,
                "pose": {
                    "x": fb["pose"]["x"],
                    "y": fb["pose"]["y"],
                    "z": fb["pose"]["z"],
                    "r": fb["pose"]["r"],
                },
                "running_status": fb["running_status"],
                "robot_mode_name": fb["robot_mode_name"],
                "enable_status": fb["enable_status"],
                "error_status": fb["error_status"],
                "motion": fb.get("motion"),
                "server_now_ms": fb.get("server_now_ms"),
            }

    # ---- override: tool / stop / recover ----
    def set_tool_output(self, index, enable):
        self._tool_enabled = bool(enable)
        return self._dashboard_ok(f"DO({int(index)},{1 if enable else 0})")

    def stop(self):
        with self.lock:
            self._sync_pose_from_motion_unlocked()
            self._motion = None
            self._robot_mode = 5
            self._running = False
        return self._dashboard_ok("Stop()")

    def recover(self):
        with self.lock:
            self._error = False
            self._robot_mode = 5
            self._running = False
            self._motion = None
        return {
            "clear_error": self._dashboard_ok("ClearError()"),
            "enable_robot": self._dashboard_ok("EnableRobot()"),
        }

    # ---- world-aware motion overrides ----
    def move_to_pose(self, x, y, z, r=0.0):
        result = self.send_motion(f"MovL({x},{y},{z},{r})")
        if result.get("ok"):
            self._update_world_pose(x, y, z)
        return result

    def move_relative(self, dx, dy, dz, dr=0.0):
        result = self.send_motion(f"RelMovL({dx},{dy},{dz},{dr})")
        if result.get("ok"):
            with self.lock:
                self._sync_pose_from_motion_unlocked()
                self._update_world_pose(self._pose["x"], self._pose["y"], self._pose["z"])
        return result

    def _update_world_pose(self, x, y, z):
        """อัปเดตตำแหน่งวัตถุที่ถือ — x,y จาก motion เป็น arm-local → เก็บเป็น workspace"""
        if not self.held_object:
            return
        try:
            from .sim_world import arm_local_to_workspace
            ws = arm_local_to_workspace(self.robot_id, float(x), float(y))
            if hasattr(self.world, "update_held_workspace"):
                self.world.update_held_workspace(float(ws["x"]), float(ws["y"]))
            else:
                from .sim_world import _normalize_object
                self.held_object["robot_plane"] = {"x": float(ws["x"]), "y": float(ws["y"])}
                updated = _normalize_object(dict(self.held_object))
                self.held_object.clear()
                self.held_object.update(updated)
        except Exception:
            # อย่าทับด้วย local ดิบถ้าแปลงไม่ได้
            pass

    @property
    def held_object(self):
        return self.world._held

    def pick_at(self, x, y, z, tolerance_mm=35.0):
        if not self.is_connected:
            self.connect()
        pose = {"x": x, "y": y, "z": z}
        result = self.world.pick_at(pose, tolerance_mm, arm_id=self.robot_id)
        if result.get("ok"):
            self._update_world_pose(x, y, z)
        return result

    def place_at(self, x, y, z):
        if not self.is_connected:
            self.connect()
        # x,y จาก motion = arm-local → world แปลงเป็น workspace เอง
        pose = {"x": x, "y": y, "z": z}
        return self.world.place_at(pose, arm_id=self.robot_id)

    def release_grip(self):
        if not self.is_connected:
            self.connect()
        return self.world.release()
