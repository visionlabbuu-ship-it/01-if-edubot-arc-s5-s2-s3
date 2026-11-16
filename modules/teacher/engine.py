"""UC4 Teacher engine — state machine + demo runner (no LLM).

SIM lock: see SIM_LOCK.md and capabilities.json (version sim-1.0).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from .adaptive import next_level, should_auto_demo
from .grader import grade_challenge

_TEACHER_DIR = Path(__file__).resolve().parent
_CATALOG_PATH = _TEACHER_DIR / "catalog.json"
_CAPABILITIES_PATH = _TEACHER_DIR / "capabilities.json"

# process-wide engines keyed by student_id
_ENGINES: dict[str, "TeacherEngine"] = {}
_ENGINES_LOCK = threading.Lock()


def load_catalog(path: Optional[Path] = None) -> dict:
    p = path or _CATALOG_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_capabilities(path: Optional[Path] = None) -> dict:
    p = path or _CAPABILITIES_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def get_engine(controller, student_id: str = "default") -> "TeacherEngine":
    with _ENGINES_LOCK:
        eng = _ENGINES.get(student_id)
        if eng is None or eng.controller is not controller:
            eng = TeacherEngine(controller, student_id=student_id)
            _ENGINES[student_id] = eng
        return eng


class TeacherEngine:
    """Deterministic robot-teacher session on top of FleetController (sim author)."""

    def __init__(
        self,
        controller,
        student_id: str = "default",
        catalog: Optional[dict] = None,
        capabilities: Optional[dict] = None,
    ):
        self.controller = controller
        self.student_id = student_id
        self.catalog = catalog or load_catalog()
        self.capabilities = capabilities or load_capabilities()
        self._lock = threading.RLock()
        self.reset_session()

    def lock_info(self) -> dict:
        """ขอบเขต sim + author (skeleton → generate text → teach)."""
        from .skeletons import author_meta, list_capabilities, list_skeletons

        caps = self.capabilities or {}
        cat = self.catalog or {}
        meta = author_meta(caps)
        sim_mode = None
        try:
            from arc_runtime import config as arc_config

            sim_mode = bool(getattr(arc_config, "SIM_MODE", None))
        except Exception:
            sim_mode = None
        session_n = len(self.session_challenges)
        return {
            "ok": True,
            "module": "UC4_robot_teacher",
            "version": caps.get("version") or cat.get("version") or "sim-1.1-author",
            "runtime": cat.get("runtime") or caps.get("runtime") or "sim",
            "llm": False,
            "author": meta.get("author") or {},
            "catalog_schema": cat.get("catalog_schema"),
            "challenge_count": len(cat.get("challenges") or []),
            "session_challenge_count": session_n,
            "skeleton_count": meta.get("skeleton_count"),
            "allowed_demo_ops": list(caps.get("allowed_demo_ops") or []),
            "capabilities": list_capabilities(caps),
            "skeletons": list_skeletons(caps=caps),
            "out_of_scope": list(caps.get("out_of_scope_sim_v1") or []),
            "sim_mode": sim_mode,
            "lock_doc": "modules/teacher/SIM_LOCK.md",
            "message_th": (
                "sim tutor: คุยหาหัวข้อ → generate ชุด MCQ → เรียน + demo บน simulator "
                "(demo ล็อกจาก skeleton; ข้อความ seed/LLM)"
            ),
            "chat": True,
            "endpoints": [
                "GET /api/teacher/lock",
                "POST /api/teacher/chat",
                "POST /api/teacher/generate",
                "POST /api/teacher/start",
                "POST /api/teacher/act",
            ],
        }

    # ---- catalog helpers ----
    def _all_challenges(self) -> list[dict]:
        """Built-in catalog + session-generated challenges."""
        items = list(self.catalog.get("challenges") or [])
        # session overrides same id
        by_id = {c["id"]: c for c in items if c.get("id")}
        for c in self.session_challenges:
            if c.get("id"):
                by_id[c["id"]] = c
        # preserve catalog order then append pure session ids
        ordered = []
        seen = set()
        for c in items:
            cid = c.get("id")
            if cid in by_id and cid not in seen:
                ordered.append(by_id[cid])
                seen.add(cid)
        for c in self.session_challenges:
            cid = c.get("id")
            if cid and cid not in seen:
                ordered.append(c)
                seen.add(cid)
        return ordered

    def list_challenges(self, level: Optional[int] = None) -> list[dict]:
        items = self._all_challenges()
        if level is not None:
            items = [c for c in items if int(c.get("level", 1)) == int(level)]
        return [
            {
                "id": c["id"],
                "level": c.get("level"),
                "mode": c.get("mode") or "robot",
                "title_th": c.get("title_th"),
                "intro": c.get("intro"),
                "question_th": c.get("question_th"),
                "source": (c.get("meta") or {}).get("source"),
                "capability_id": (c.get("meta") or {}).get("capability_id"),
                "skeleton_id": (c.get("meta") or {}).get("skeleton_id"),
            }
            for c in items
        ]

    def _challenge_by_id(self, challenge_id: str) -> Optional[dict]:
        for c in self._all_challenges():
            if c.get("id") == challenge_id:
                return c
        return None

    def _pick_challenge(self, challenge_id: Optional[str] = None) -> Optional[dict]:
        if challenge_id:
            return self._challenge_by_id(challenge_id)
        # prefer session set if loaded
        pool_src = self.session_challenges or list(self.catalog.get("challenges") or [])
        level = self.level
        pool = [c for c in pool_src if int(c.get("level", 1)) == level]
        if not pool:
            pool = list(pool_src)
        done = set(self.passed_ids)
        for c in pool:
            if c["id"] not in done:
                return c
        return pool[0] if pool else None

    # ---- session ----
    def reset_session(self) -> dict:
        with self._lock:
            self.state = "idle"
            self.level = 1
            self.challenge: Optional[dict] = None
            self.consecutive_pass = 0
            self.consecutive_fail = 0
            self.passed_ids: list[str] = []
            self.waypoint_log: list[dict] = []
            self.procedure_log: list[str] = []
            self.selected_choice: Optional[str] = None
            self.last_explanation: Optional[str] = None
            self.answered: bool = False
            self.last_grade: Optional[dict] = None
            self.session_challenges: list[dict] = []
            self.session_meta: dict = {}
            self.chat_history: list[dict] = []
            self.message = "พร้อมเริ่มบทเรียน — คุยหาหัวข้อหรือเลือกโจทย์จากคลัง"
            return self.status()

    def chat(
        self,
        message: str,
        *,
        use_llm: Optional[bool] = None,
        count: Optional[int] = None,
    ) -> dict:
        """Tutor chat: คุย → map capability → generate ชุด MCQ บน sim."""
        from .tutor import handle_tutor_turn

        with self._lock:
            # ensure attrs for older sessions
            if not hasattr(self, "chat_history") or self.chat_history is None:
                self.chat_history = []
            if not hasattr(self, "session_meta") or self.session_meta is None:
                self.session_meta = {}
            if not hasattr(self, "session_challenges") or self.session_challenges is None:
                self.session_challenges = []
            return handle_tutor_turn(
                self,
                message,
                use_llm=use_llm,
                count=count,
                auto_generate=True,
            )

    def load_challenge_set(
        self,
        challenges: list[dict],
        *,
        meta: Optional[dict] = None,
        start_first: bool = False,
    ) -> dict:
        """Load validated generated challenges into this student session."""
        from .author import validate_challenge

        with self._lock:
            accepted = []
            rejected = []
            for ch in challenges or []:
                gate = validate_challenge(ch, self.capabilities)
                if gate["ok"]:
                    accepted.append(ch)
                else:
                    rejected.append({"id": ch.get("id"), "errors": gate["errors"]})
            self.session_challenges = accepted
            self.session_meta = dict(meta or {})
            self.session_meta["loaded"] = len(accepted)
            self.session_meta["rejected"] = len(rejected)
            self.passed_ids = []
            self.challenge = None
            self.state = "idle"
            self.message = (
                f"โหลดชุดโจทย์ {len(accepted)} ข้อ"
                + (f" (ปฏิเสธ {len(rejected)})" if rejected else "")
                + " — กด start"
            )
            out = {
                "ok": bool(accepted),
                "action": "load_set",
                "loaded": len(accepted),
                "rejected": rejected,
                "challenge_ids": [c["id"] for c in accepted],
                "status": self.status(),
            }
            if start_first and accepted:
                started = self.start(accepted[0]["id"])
                out["start"] = started
            return out

    def generate_set(
        self,
        *,
        capability_id: Optional[str] = None,
        topic: Optional[str] = None,
        skeleton_ids: Optional[list] = None,
        count: Optional[int] = None,
        use_llm: Optional[bool] = None,
        start_first: bool = True,
    ) -> dict:
        """Generate MCQ set from skeletons and load into session."""
        from .generate import generate_set as gen_set

        result = gen_set(
            capability_id=capability_id,
            topic=topic,
            skeleton_ids=skeleton_ids,
            count=count,
            use_llm=use_llm,
            caps=self.capabilities,
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "action": "generate_set",
                "error": result.get("error") or "generate failed",
                "result": result,
                "status": self.status(),
            }
        loaded = self.load_challenge_set(
            result.get("challenges") or [],
            meta={
                "capability_id": result.get("capability_id"),
                "topic": topic,
                "teacher_llm": result.get("teacher_llm"),
                "generate_results": result.get("results"),
            },
            start_first=start_first,
        )
        return {
            "ok": loaded.get("ok"),
            "action": "generate_set",
            "capability_id": result.get("capability_id"),
            "topic": topic,
            "generated": result.get("generated"),
            "results": result.get("results"),
            "teacher_llm": result.get("teacher_llm"),
            "message_th": result.get("message_th"),
            "load": loaded,
            "status": self.status(),
        }

    def status(self) -> dict:
        with self._lock:
            ch = self.challenge
            mode = (ch.get("mode") if ch else None) or "mcq"
            return {
                "ok": True,
                "module": "UC4_robot_teacher",
                "version": (self.capabilities or {}).get("version")
                or (self.catalog or {}).get("version")
                or "sim-1.1-author",
                "runtime": (self.catalog or {}).get("runtime") or "sim",
                "llm": False,
                "student_id": self.student_id,
                "state": self.state,
                "level": self.level,
                "challenge_id": ch.get("id") if ch else None,
                "mode": mode,
                "title_th": ch.get("title_th") if ch else None,
                "intro": ch.get("intro") if ch else None,
                "question_th": ch.get("question_th") if ch else None,
                "message": self.message,
                "selected_choice": self.selected_choice,
                "answered": self.answered,
                "explanation_th": self.last_explanation,
                "consecutive_pass": self.consecutive_pass,
                "consecutive_fail": self.consecutive_fail,
                "passed_ids": list(self.passed_ids),
                "waypoints_logged": len(self.waypoint_log),
                "procedure_log": list(self.procedure_log),
                "procedure_expected_count": len(ch.get("correct_order") or []) if ch else 0,
                "last_grade": self.last_grade,
                "auto_demo_suggested": should_auto_demo(consecutive_fail=self.consecutive_fail),
                "session_challenge_count": len(getattr(self, "session_challenges", None) or []),
                "session_challenge_ids": [
                    c.get("id") for c in (getattr(self, "session_challenges", None) or [])
                ],
                "session_meta": dict(getattr(self, "session_meta", None) or {}),
                "chat_turns": len(getattr(self, "chat_history", None) or []),
            }

    def start(self, challenge_id: Optional[str] = None) -> dict:
        with self._lock:
            ch = self._pick_challenge(challenge_id)
            if not ch:
                return {"ok": False, "error": "No challenges in catalog"}
            self.challenge = ch
            self.state = "intro"
            self.waypoint_log = []
            self.procedure_log = []
            self.selected_choice = None
            self.last_explanation = None
            self.answered = False
            self.last_grade = None
            setup_result = self._run_setup(ch.get("setup") or {})
            self.message = ch.get("question_th") or ch.get("intro") or ch.get("title_th") or ch["id"]
            self.state = "challenge"
            return {
                "ok": True,
                "action": "start",
                "challenge": {
                    "id": ch["id"],
                    "level": ch.get("level"),
                    "mode": ch.get("mode") or "mcq",
                    "title_th": ch.get("title_th"),
                    "intro": ch.get("intro"),
                    "question_th": ch.get("question_th"),
                    "choices": ch.get("choices"),
                    "waypoints": ch.get("waypoints"),
                    "steps_bank": ch.get("steps_bank"),
                    "correct_count": len(ch.get("correct_order") or []),
                },
                "setup": setup_result,
                "status": self.status(),
            }

    def _park_other_arms(self, active_arm: str) -> list:
        """จอดแขนอื่นนอก shared zone — กัน guardrail บล็อก home/demo ของแขนที่เรียน"""
        steps = []
        ctrl = self.controller
        others = {
            "Arm1": ("storage_zone", "hover"),
            "Arm2": ("drop_zone", "hover"),
        }
        for arm_id, (dest, phase) in others.items():
            if arm_id == active_arm:
                continue
            if not ctrl.get_robot(arm_id):
                continue
            if hasattr(ctrl, "move_to_named_pose"):
                r = ctrl.move_to_named_pose(arm_id, dest, phase)
            else:
                r = {"ok": False, "error": "no move_to_named_pose"}
            steps.append({"op": "park_other", "arm": arm_id, "destination": dest, "result": r})
        return steps

    def _run_setup(self, setup: dict) -> dict:
        steps = []
        ctrl = self.controller
        if setup.get("reset_scene") and hasattr(ctrl, "reset_scene"):
            steps.append({"op": "reset_scene", "result": ctrl.reset_scene()})
        # เคลียร์ shared zone ก่อน home / pre_steps (Arm2 มักเริ่มที่ home ชน Arm1)
        active = setup.get("home_arm") or setup.get("active_arm") or (self.challenge or {}).get("arm") or "Arm1"
        if setup.get("park_others", True):
            steps.extend(self._park_other_arms(active))
        if setup.get("home_arm") and hasattr(ctrl, "home_arm"):
            arm = setup["home_arm"]
            steps.append({"op": "home", "arm": arm, "result": ctrl.home_arm(arm)})
        for pre in setup.get("pre_steps") or []:
            steps.append({"op": pre.get("op"), "result": self._exec_step(pre, record_waypoint=False)})
        if setup.get("clear_waypoints"):
            self.waypoint_log = []
            steps.append({"op": "clear_waypoints", "result": {"ok": True}})
        return {"ok": True, "steps": steps}

    # ---- demo ----
    def demo(self) -> dict:
        with self._lock:
            if not self.challenge:
                return {"ok": False, "error": "No active challenge — call start first"}
            self.state = "demo"
            result = self._run_demo_steps()
            self.state = "challenge"
            if not result.get("ok"):
                self.message = f"Demo ล้มเหลว: {result.get('error')}"
                return {"ok": False, "error": self.message, "steps": result.get("steps"), "status": self.status()}
            mode = self.challenge.get("mode") or "mcq"
            if mode == "mcq":
                self.message = "จบตัวอย่างบนหุ่นแล้ว — กลับไปเลือก Choice ที่ถูก"
            else:
                self.message = "จบตัวอย่างแล้ว — ลองทำเอง แล้วกด check"
            return {"ok": True, "action": "demo", "steps": result.get("steps"), "status": self.status()}

    def _allowed_demo_ops(self) -> set[str]:
        ops = (self.capabilities or {}).get("allowed_demo_ops")
        if ops:
            return {str(o) for o in ops}
        return {"reset_scene", "home", "move_named", "pick_object", "place_named", "move_pose"}

    def _exec_step(self, step: dict, *, record_waypoint: bool = False) -> dict:
        ctrl = self.controller
        op = step.get("op")
        arm = step.get("arm") or "Arm1"

        # sim-1.0: ปฏิเสธ op นอก allow-list (กัน catalog/อนาคต LLM ใส่ op แปลก)
        if op not in self._allowed_demo_ops():
            return {
                "ok": False,
                "error": f"op '{op}' ไม่อยู่ใน sim-1.0 allow-list",
                "allowed_demo_ops": sorted(self._allowed_demo_ops()),
            }

        if op == "reset_scene":
            return ctrl.reset_scene() if hasattr(ctrl, "reset_scene") else {"ok": False, "error": "no reset_scene"}

        if op == "home":
            return ctrl.home_arm(arm)

        if op == "move_named":
            dest = step.get("destination") or "home"
            phase = step.get("phase") or "hover"
            allowed_dest = set(
                (self.capabilities or {}).get("allowed_named_destinations")
                or ["home", "storage_zone", "drop_zone"]
            )
            if dest not in allowed_dest:
                return {
                    "ok": False,
                    "error": f"destination '{dest}' ไม่อยู่ใน sim-1.0 named targets",
                    "allowed": sorted(allowed_dest),
                }
            if hasattr(ctrl, "move_to_named_pose"):
                return ctrl.move_to_named_pose(arm, dest, phase)
            return ctrl.move_to_named_target(arm, dest)

        if op == "pick_object":
            oid = step.get("object_id")
            scene = ctrl.scene_state()
            obj = None
            for o in scene.get("objects") or []:
                if o.get("id") == oid:
                    obj = o
                    break
            if not obj:
                return {"ok": False, "error": f"object {oid} not in scene"}
            rp = obj.get("robot_plane") or {}
            return ctrl.pick_at(arm, float(rp["x"]), float(rp["y"]))

        if op == "place_named":
            dest = step.get("destination") or "storage_zone"
            allowed_dest = set(
                (self.capabilities or {}).get("allowed_named_destinations")
                or ["home", "storage_zone", "drop_zone"]
            )
            if dest not in allowed_dest:
                return {
                    "ok": False,
                    "error": f"destination '{dest}' ไม่อยู่ใน sim-1.0 named targets",
                    "allowed": sorted(allowed_dest),
                }
            return ctrl.place_to_named_target(arm, dest)

        if op == "move_pose":
            x, y = float(step["x"]), float(step["y"])
            z = float(step.get("z", 50.0))
            r = float(step.get("r", 0.0))
            robot = ctrl.get_robot(arm)
            if not robot:
                return {"ok": False, "error": f"Robot {arm} not found"}
            if not robot.is_connected:
                robot.connect()
            if hasattr(robot, "move_joint_to_pose"):
                result = robot.move_joint_to_pose(x, y, z, r)
            elif hasattr(robot, "move_to_pose"):
                result = robot.move_to_pose(x, y, z, r)
            else:
                return {"ok": False, "error": "No move_to_pose on robot"}
            ok = result.get("ok", False) if isinstance(result, dict) else not str(result).startswith("Error:")
            if ok and record_waypoint:
                self.record_waypoint(arm, x, y, z)
            return result if isinstance(result, dict) else {"ok": ok, "result": result}

        return {"ok": False, "error": f"Unknown demo op '{op}'"}

    def record_waypoint(self, arm: str, x: float, y: float, z: float = 0.0) -> dict:
        """Students / demo can log waypoints for L3_square grading."""
        with self._lock:
            entry = {"arm": arm, "x": float(x), "y": float(y), "z": float(z)}
            self.waypoint_log.append(entry)
            return {"ok": True, "count": len(self.waypoint_log), "entry": entry}

    # ---- grade / adaptive ----
    def check(self) -> dict:
        with self._lock:
            if not self.challenge:
                return {"ok": False, "error": "No active challenge — call start first"}
            mode = self.challenge.get("mode") or "mcq"
            # MCQ: ตรวจผ่านการกด choice แล้ว — check แค่รายงานสถานะล่าสุด
            if mode == "mcq":
                if self.last_grade and self.last_grade.get("passed"):
                    return {
                        "ok": True,
                        "action": "check",
                        "grade": self.last_grade,
                        "explanation_th": self.last_explanation,
                        "status": self.status(),
                    }
                if not self.selected_choice and not self.procedure_log:
                    return {
                        "ok": False,
                        "error": "ยังไม่ได้เลือกคำตอบ — กด Choice A/B/C/D ก่อน",
                        "status": self.status(),
                    }
                # ถ้ามี selected ล่าสุดที่ยังไม่ผ่าน — บอกให้เลือกใหม่
                return {
                    "ok": True,
                    "action": "check",
                    "grade": self.last_grade
                    or {
                        "ok": True,
                        "passed": False,
                        "hint": "ยังไม่ถูก — เลือก Choice อื่น",
                        "challenge_id": self.challenge["id"],
                    },
                    "explanation_th": self.last_explanation,
                    "status": self.status(),
                }

            self.state = "grade"
            grade = grade_challenge(
                self.controller,
                self.challenge,
                waypoint_log=self.waypoint_log,
                procedure_log=self.procedure_log,
            )
            self.last_grade = grade
            passed = bool(grade.get("passed"))
            if passed:
                self.consecutive_pass += 1
                self.consecutive_fail = 0
                cid = self.challenge["id"]
                if cid not in self.passed_ids:
                    self.passed_ids.append(cid)
                self.message = f"ผ่านโจทย์ {cid}!"
            else:
                self.consecutive_fail += 1
                self.consecutive_pass = 0
                self.message = grade.get("hint") or "ยังไม่ผ่าน"
            self.level = next_level(
                self.level,
                passed=passed,
                consecutive_pass=self.consecutive_pass,
                consecutive_fail=self.consecutive_fail,
            )
            self.state = "challenge"
            return {
                "ok": True,
                "action": "check",
                "grade": grade,
                "auto_demo_suggested": should_auto_demo(consecutive_fail=self.consecutive_fail),
                "status": self.status(),
            }

    def next_challenge(self) -> dict:
        """Advance to next unfinished challenge at current level (or any)."""
        with self._lock:
            done = set(self.passed_ids)
            # prefer same level unfinished
            for c in self.catalog.get("challenges") or []:
                if int(c.get("level", 1)) == self.level and c["id"] not in done:
                    return self.start(c["id"])
            for c in self.catalog.get("challenges") or []:
                if c["id"] not in done:
                    return self.start(c["id"])
            # all done — restart first at level
            return self.start()

    def _run_demo_steps(self) -> dict:
        """รัน demo_steps ของโจทย์ปัจจุบัน (ใช้ตอนตอบถูก / ปุ่มดูตัวอย่าง)."""
        if not self.challenge:
            return {"ok": False, "error": "No challenge"}
        results = []
        active = self.challenge.get("arm") or "Arm1"
        for park_step in self._park_other_arms(active):
            results.append({"step": {"op": "park_other"}, "result": park_step.get("result")})
        for step in self.challenge.get("demo_steps") or []:
            r = self._exec_step(step, record_waypoint=True)
            results.append({"step": step, "result": r})
            if isinstance(r, dict) and r.get("ok") is False:
                return {"ok": False, "error": r.get("error"), "steps": results}
        return {"ok": True, "steps": results}

    def student_actions(self) -> dict:
        """รายการตัวเลือก — mcq = A/B/C/D, procedure = เรียงขั้น, robot = ปุ่มหุ่น."""
        with self._lock:
            ch = self.challenge
            if not ch:
                return {
                    "ok": True,
                    "challenge_id": None,
                    "mode": None,
                    "actions": [],
                    "choices": [],
                    "procedure_log": [],
                    "hint": "กด «เริ่มโจทย์» ก่อน แล้วจะมี Choice ให้เลือก",
                }
            cid = ch["id"]
            mode = ch.get("mode") or "mcq"

            # ---- MCQ: ตัวเลือก A/B/C/D ----
            if mode == "mcq":
                choices = list(ch.get("choices") or [])
                return {
                    "ok": True,
                    "challenge_id": cid,
                    "mode": "mcq",
                    "title_th": ch.get("title_th"),
                    "question_th": ch.get("question_th") or ch.get("intro"),
                    "actions": [
                        {"id": c["id"], "label": c["label"], "order": i + 1}
                        for i, c in enumerate(choices)
                    ],
                    "choices": choices,
                    "selected_choice": self.selected_choice,
                    "answered": self.answered,
                    "explanation_th": self.last_explanation,
                    "correct_choice": ch.get("correct_choice") if self.answered else None,
                    "procedure_log": list(self.procedure_log),
                    "hint": (
                        "เลือกข้อ A/B/C/D ที่ถูก — ตอบถูกจะมีคำอธิบาย และหุ่นจะทำงานให้ดู"
                        if not self.answered
                        else (self.last_explanation or "ตอบแล้ว")
                    ),
                }

            if mode == "procedure":
                bank = list(ch.get("steps_bank") or [])
                need = len(ch.get("correct_order") or [])
                return {
                    "ok": True,
                    "challenge_id": cid,
                    "mode": "procedure",
                    "title_th": ch.get("title_th"),
                    "question_th": ch.get("question_th") or ch.get("intro"),
                    "actions": [
                        {"id": s["id"], "label": s["label"], "order": i + 1}
                        for i, s in enumerate(bank)
                    ],
                    "procedure_log": list(self.procedure_log),
                    "expected_count": need,
                    "hint": (
                        f"กดเลือกขั้นตอนตามลำดับที่ถูก ({len(self.procedure_log)}/{need}) "
                        "แล้วกด «ตรวจคำตอบ»"
                    ),
                }
            # robot practice
            actions = list(ch.get("robot_actions") or _ACTIONS_FOR_CHALLENGE.get(cid) or [])
            return {
                "ok": True,
                "challenge_id": cid,
                "mode": "robot",
                "title_th": ch.get("title_th"),
                "actions": actions,
                "procedure_log": [],
                "hint": "กดปุ่มด้านล่างเพื่อสั่งหุ่น แล้วกด «ตรวจคำตอบ»",
            }

    def student_act(self, action_id: str) -> dict:
        """mcq: เลือกคำตอบ + อธิบาย + โชว์หุ่น / procedure / robot."""
        with self._lock:
            if not self.challenge:
                return {"ok": False, "error": "ยังไม่เริ่มโจทย์ — กดเริ่มโจทย์ก่อน"}
            action_id = str(action_id or "").strip()
            mode = self.challenge.get("mode") or "mcq"

            # ---- MCQ: กด choice แล้วตรวจทันที ----
            if mode == "mcq":
                return self._answer_mcq(action_id)

            # ---- procedure: เรียงลำดับขั้นตอน ----
            if mode == "procedure":
                if action_id in ("undo", "clear", "reset_steps"):
                    if action_id == "undo" and self.procedure_log:
                        removed = self.procedure_log.pop()
                        self.message = f"ถอดขั้นล่าสุด: {removed}"
                    else:
                        self.procedure_log = []
                        self.message = "ล้างลำดับขั้นตอนแล้ว"
                    return {
                        "ok": True,
                        "action": "student_act",
                        "action_id": action_id,
                        "mode": "procedure",
                        "procedure_log": list(self.procedure_log),
                        "status": self.status(),
                    }

                bank_ids = {s["id"] for s in (self.challenge.get("steps_bank") or [])}
                if action_id not in bank_ids:
                    return {"ok": False, "error": f"ขั้น '{action_id}' ไม่อยู่ในตัวเลือก"}
                if action_id in self.procedure_log:
                    return {
                        "ok": False,
                        "error": "เลือกขั้นนี้ไปแล้ว — ถ้าผิดลำดับ กด «ถอนขั้น» หรือ «ล้างลำดับ»",
                        "procedure_log": list(self.procedure_log),
                        "status": self.status(),
                    }
                need = len(self.challenge.get("correct_order") or [])
                if need and len(self.procedure_log) >= need:
                    return {
                        "ok": False,
                        "error": f"ครบ {need} ขั้นแล้ว — กดตรวจคำตอบ หรือล้างลำดับแล้วเลือกใหม่",
                        "procedure_log": list(self.procedure_log),
                        "status": self.status(),
                    }
                self.procedure_log.append(action_id)
                labels = {s["id"]: s["label"] for s in (self.challenge.get("steps_bank") or [])}
                self.message = (
                    f"เลือกขั้น {len(self.procedure_log)}/{need or '?'}: "
                    f"{labels.get(action_id, action_id)}"
                )
                return {
                    "ok": True,
                    "action": "student_act",
                    "action_id": action_id,
                    "mode": "procedure",
                    "procedure_log": list(self.procedure_log),
                    "status": self.status(),
                }

            # ---- robot practice ----
            allowed = {
                a["id"]
                for a in (
                    self.challenge.get("robot_actions")
                    or _ACTIONS_FOR_CHALLENGE.get(self.challenge["id"])
                    or []
                )
            }
            if action_id not in allowed and not action_id.startswith("square_"):
                return {"ok": False, "error": f"คำสั่ง '{action_id}' ใช้กับโจทย์นี้ไม่ได้"}

            active = self.challenge.get("arm") or "Arm1"
            self._park_other_arms(active)

            result: dict
            if action_id == "home":
                result = self._exec_step({"op": "home", "arm": active})
            elif action_id == "park_storage":
                result = self._exec_step(
                    {"op": "move_named", "arm": active, "destination": "storage_zone", "phase": "hover"}
                )
            elif action_id == "pick_obj1":
                result = self._exec_step({"op": "pick_object", "arm": active, "object_id": "obj-1"})
            elif action_id == "place_storage":
                result = self._exec_step(
                    {"op": "place_named", "arm": active, "destination": "storage_zone"}
                )
            elif action_id.startswith("square_"):
                try:
                    idx = int(action_id.split("_", 1)[1])
                except (IndexError, ValueError):
                    return {"ok": False, "error": "รูปแบบ square_N ไม่ถูก"}
                wps = self.challenge.get("waypoints") or []
                if idx < 0 or idx >= len(wps):
                    return {"ok": False, "error": f"ไม่มีมุมที่ {idx + 1}"}
                wp = wps[idx]
                result = self._exec_step(
                    {
                        "op": "move_pose",
                        "arm": active,
                        "x": wp["x"],
                        "y": wp["y"],
                        "z": wp.get("z", 50.0),
                        "r": wp.get("r", 0.0),
                    },
                    record_waypoint=True,
                )
            else:
                return {"ok": False, "error": f"Unknown action {action_id}"}

            ok = bool(result.get("ok")) if isinstance(result, dict) else False
            self.message = (
                f"ทำคำสั่ง «{action_id}» {'สำเร็จ' if ok else 'ไม่สำเร็จ'}"
                + (f" — {result.get('error')}" if not ok and isinstance(result, dict) else "")
            )
            return {
                "ok": ok,
                "action": "student_act",
                "action_id": action_id,
                "mode": "robot",
                "result": result,
                "status": self.status(),
                "error": None if ok else (result.get("error") if isinstance(result, dict) else "failed"),
            }

    def _answer_mcq(self, choice_id: str) -> dict:
        """เลือกข้อ A/B/C/D — ถูก: อธิบาย + รัน demo ให้ดู / ผิด: บอกเหตุผล."""
        ch = self.challenge
        assert ch is not None
        choice_id = str(choice_id or "").strip().upper()
        choices = list(ch.get("choices") or [])
        valid = {str(c.get("id", "")).upper() for c in choices}
        if choice_id not in valid:
            return {"ok": False, "error": f"ไม่มีตัวเลือก «{choice_id}» — เลือก A/B/C/D"}

        if self.answered and self.selected_choice == ch.get("correct_choice"):
            return {
                "ok": True,
                "action": "student_act",
                "mode": "mcq",
                "action_id": choice_id,
                "already_passed": True,
                "passed": True,
                "explanation_th": self.last_explanation,
                "status": self.status(),
            }

        correct = str(ch.get("correct_choice") or "").strip().upper()
        self.selected_choice = choice_id
        self.procedure_log = [choice_id]  # grader อ่านจาก procedure_log
        self.answered = True
        passed = choice_id == correct

        demo_result = None
        if passed:
            expl = ch.get("explanation_th") or "ถูกต้อง!"
            self.last_explanation = expl
            self.message = f"✅ ถูก ({choice_id}) — {expl}"
            # เกรด + adaptive
            grade = grade_challenge(
                self.controller,
                ch,
                waypoint_log=self.waypoint_log,
                procedure_log=self.procedure_log,
            )
            grade["explanation_th"] = expl
            grade["selected_choice"] = choice_id
            grade["correct_choice"] = correct
            self.last_grade = grade
            self.consecutive_pass += 1
            self.consecutive_fail = 0
            cid = ch["id"]
            if cid not in self.passed_ids:
                self.passed_ids.append(cid)
            self.level = next_level(
                self.level,
                passed=True,
                consecutive_pass=self.consecutive_pass,
                consecutive_fail=self.consecutive_fail,
            )
            # โชว์หุ่นทำงาน
            if ch.get("demo_on_correct", True) and ch.get("demo_steps"):
                self.state = "demo"
                demo_result = self._run_demo_steps()
                self.state = "challenge"
                if demo_result.get("ok"):
                    self.message = f"✅ ถูก ({choice_id}) — {expl} · หุ่นโชว์ให้ดูแล้ว"
                else:
                    self.message = (
                        f"✅ ถูก ({choice_id}) — {expl} · "
                        f"(โชว์หุ่นไม่สำเร็จ: {demo_result.get('error')})"
                    )
            return {
                "ok": True,
                "action": "student_act",
                "mode": "mcq",
                "action_id": choice_id,
                "passed": True,
                "explanation_th": expl,
                "correct_choice": correct,
                "selected_choice": choice_id,
                "demo": demo_result,
                "grade": grade,
                "status": self.status(),
            }

        # ผิด
        wrong_hints = ch.get("wrong_hints") or {}
        hint = wrong_hints.get(choice_id) or wrong_hints.get(choice_id.lower())
        if not hint:
            hint = ch.get("hint_fail") or "ยังไม่ถูก — ลองข้ออื่น"
        # อย่าเฉลยข้อถูกจนกว่าจะตอบถูก (ให้ลองใหม่)
        self.last_explanation = f"❌ ข้อ {choice_id} ยังไม่ถูก — {hint}"
        self.message = self.last_explanation
        grade = grade_challenge(
            self.controller,
            ch,
            waypoint_log=self.waypoint_log,
            procedure_log=self.procedure_log,
        )
        grade["explanation_th"] = self.last_explanation
        grade["selected_choice"] = choice_id
        grade["hint"] = hint
        # อนุญาตเลือกใหม่
        self.answered = False
        self.procedure_log = []
        self.selected_choice = choice_id  # เก็บข้อล่าสุดที่ลอง
        self.last_grade = grade
        self.consecutive_fail += 1
        self.consecutive_pass = 0
        self.level = next_level(
            self.level,
            passed=False,
            consecutive_pass=self.consecutive_pass,
            consecutive_fail=self.consecutive_fail,
        )
        return {
            "ok": True,
            "action": "student_act",
            "mode": "mcq",
            "action_id": choice_id,
            "passed": False,
            "explanation_th": self.last_explanation,
            "selected_choice": choice_id,
            "hint": hint,
            "grade": grade,
            "status": self.status(),
        }


# fallback ปุ่ม robot ถ้า catalog ไม่ระบุ robot_actions
_ACTIONS_FOR_CHALLENGE: dict[str, list[dict]] = {
    "R2_pick_practice": [
        {"id": "pick_obj1", "label": "หยิบ obj-1 ด้วย Arm1", "order": 1},
    ],
    "R2_pick_place_practice": [
        {"id": "pick_obj1", "label": "ขั้น 1: หยิบ obj-1", "order": 1},
        {"id": "place_storage", "label": "ขั้น 2: วางที่ storage_zone", "order": 2},
    ],
    "L1_home": [{"id": "home", "label": "พา Arm1 กลับ home", "order": 1}],
    "L1_park": [{"id": "park_storage", "label": "พา Arm1 ไป storage_zone", "order": 1}],
    "L2_pick": [{"id": "pick_obj1", "label": "หยิบ obj-1 ด้วย Arm1", "order": 1}],
    "L2_pick_place": [
        {"id": "pick_obj1", "label": "ขั้น 1: หยิบ obj-1", "order": 1},
        {"id": "place_storage", "label": "ขั้น 2: วางที่ storage_zone", "order": 2},
    ],
    "L3_square": [
        {"id": "square_0", "label": "ไปมุม 1", "order": 1},
        {"id": "square_1", "label": "ไปมุม 2", "order": 2},
        {"id": "square_2", "label": "ไปมุม 3", "order": 3},
        {"id": "square_3", "label": "ไปมุม 4", "order": 4},
    ],
}
