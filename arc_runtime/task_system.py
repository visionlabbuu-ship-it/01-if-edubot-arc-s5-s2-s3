import copy
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from .config import ROBOT_FLEET_CONFIG
from .fleet_controller import FleetController
from .state_paths import arc_py_path, project_path, runtime_state_path


TASK_STATE_DIR = os.fspath(runtime_state_path("tasks"))
ACTION_STEP_TYPES = {
    "motion_profile",
    "run_path",
    "move_relative",
    "move_to_pose",
    "move_joint_pose",
    "recover_robot",
    "recover_home_robot",
    "stop_robot",
    "delay",
}
CONTROL_STEP_TYPES = {"sequence", "parallel"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_utc_timestamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def compute_duration_ms(started_at, completed_at):
    start_dt = parse_utc_timestamp(started_at)
    end_dt = parse_utc_timestamp(completed_at)
    if not start_dt or not end_dt:
        return None
    return max(0, int((end_dt - start_dt).total_seconds() * 1000))


def ensure_task_state_dir():
    os.makedirs(TASK_STATE_DIR, exist_ok=True)


def task_state_path(task_id: str):
    ensure_task_state_dir()
    return os.path.join(TASK_STATE_DIR, f"{task_id}.json")


def count_task_steps(steps):
    total = 0
    for step in steps or []:
        step_type = step.get("type")
        if step_type in CONTROL_STEP_TYPES:
            total += count_task_steps(step.get("steps", []))
        elif step_type in ACTION_STEP_TYPES:
            total += 1
    return total


def validate_task_document(task_doc):
    if not isinstance(task_doc, dict):
        raise ValueError("Task document must be a JSON object")

    steps = task_doc.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("Task document must contain a non-empty top-level steps array")

    def validate_node(node, path):
        if not isinstance(node, dict):
            raise ValueError(f"{path} must be an object")
        step_type = node.get("type")
        if not isinstance(step_type, str) or not step_type:
            raise ValueError(f"{path}.type is required")

        if step_type in CONTROL_STEP_TYPES:
            child_steps = node.get("steps")
            if not isinstance(child_steps, list) or not child_steps:
                raise ValueError(f"{path}.steps must be a non-empty array")
            for index, child in enumerate(child_steps):
                validate_node(child, f"{path}.steps[{index}]")
            return

        if step_type not in ACTION_STEP_TYPES:
            raise ValueError(f"{path}.type '{step_type}' is not supported")

        if step_type == "delay":
            delay_ms = node.get("delay_ms")
            if not isinstance(delay_ms, int) or delay_ms < 0:
                raise ValueError(f"{path}.delay_ms must be a non-negative integer")
            return

        arm_id = node.get("arm_id")
        if not isinstance(arm_id, str) or not arm_id:
            raise ValueError(f"{path}.arm_id is required")

        params = node.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"{path}.params must be an object")

        if step_type == "motion_profile":
            if not any(key in params for key in ("speed_l", "acc_l", "speed_j", "acc_j")):
                raise ValueError(f"{path}.params must include at least one motion profile field")
        elif step_type == "run_path":
            if not isinstance(params.get("path_type"), str) or not params.get("path_type"):
                raise ValueError(f"{path}.params.path_type is required")
        elif step_type == "move_relative":
            for key in ("dx", "dy", "dz"):
                value = params.get(key)
                if not isinstance(value, (int, float)):
                    raise ValueError(f"{path}.params.{key} must be a number")
        elif step_type in {"move_to_pose", "move_joint_pose"}:
            for key in ("x", "y", "z"):
                value = params.get(key)
                if not isinstance(value, (int, float)):
                    raise ValueError(f"{path}.params.{key} must be a number")

    for index, step in enumerate(steps):
        validate_node(step, f"steps[{index}]")


def enrich_task_state(state):
    enriched = copy.deepcopy(state)
    enriched["step_count"] = count_task_steps(enriched.get("steps", []))
    enriched["event_count"] = len(enriched.get("events", []))
    enriched["duration_ms"] = compute_duration_ms(
        enriched.get("started_at"),
        enriched.get("completed_at"),
    )
    return enriched


def collect_motion_targets(node: dict, path: str):
    node_type = node.get("type")
    targets = []

    if node_type in CONTROL_STEP_TYPES:
        for index, child in enumerate(node.get("steps", [])):
            targets.extend(
                collect_motion_targets(
                    child,
                    f"{path}.steps[{index}]",
                )
            )
        return targets

    arm_id = node.get("arm_id")
    params = node.get("params", {})
    if not isinstance(arm_id, str) or not isinstance(params, dict):
        return targets

    if node_type in {"move_to_pose", "move_joint_pose"}:
        targets.append(
            {
                "path": path,
                "step_type": node_type,
                "arm_id": arm_id,
                "pose": {
                    "x": float(params["x"]),
                    "y": float(params["y"]),
                    "z": float(params["z"]),
                    "r": float(params.get("r", 0.0)),
                },
            }
        )
        return targets

    if node_type == "run_path":
        path_type = params.get("path_type")
        if path_type == "waypoints":
            for waypoint_index, waypoint in enumerate(params.get("waypoints") or []):
                targets.append(
                    {
                        "path": f"{path}.params.waypoints[{waypoint_index}]",
                        "step_type": node_type,
                        "arm_id": arm_id,
                        "pose": {
                            "x": float(waypoint["x"]),
                            "y": float(waypoint["y"]),
                            "z": float(waypoint["z"]),
                            "r": float(waypoint.get("r", 0.0)),
                        },
                    }
                )
        elif path_type == "arc":
            for pose_name in ("through_pose", "target_pose"):
                pose = params.get(pose_name)
                if isinstance(pose, dict):
                    targets.append(
                        {
                            "path": f"{path}.params.{pose_name}",
                            "step_type": node_type,
                            "arm_id": arm_id,
                            "pose": {
                                "x": float(pose["x"]),
                                "y": float(pose["y"]),
                                "z": float(pose["z"]),
                                "r": float(pose.get("r", 0.0)),
                            },
                        }
                    )

    return targets


class TaskStore:
    def __init__(self):
        ensure_task_state_dir()
        self.lock = threading.Lock()

    def create(self, task_doc, task_id=None, mode="inline"):
        task_id = task_id or f"task-{uuid.uuid4().hex[:12]}"
        state = {
            "ok": True,
            "task_id": task_id,
            "name": task_doc.get("name", task_id),
            "status": "pending",
            "created_at": utc_now(),
            "started_at": None,
            "completed_at": None,
            "mode": mode,
            "cancel_requested": False,
            "incident": None,
            "current_step_path": None,
            "steps": copy.deepcopy(task_doc.get("steps", [])),
            "events": [
                {
                    "ts": utc_now(),
                    "type": "task_created",
                    "message": "Task created",
                }
            ],
            "result": None,
        }
        self.save(state)
        return enrich_task_state(state)

    def load(self, task_id: str):
        path = task_state_path(task_id)
        if not os.path.exists(path):
            return None
        with self.lock:
            last_error = None
            for _ in range(3):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return enrich_task_state(json.load(f))
                except json.JSONDecodeError as exc:
                    last_error = exc
                    time.sleep(0.05)
            if last_error:
                raise last_error
            return None

    def save(self, state):
        path = task_state_path(state["task_id"])
        temp_path = f"{path}.{threading.get_ident()}.tmp"
        with self.lock:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, path)

    def list(self):
        ensure_task_state_dir()
        results = []
        for name in os.listdir(TASK_STATE_DIR):
            if not name.endswith(".json"):
                continue
            path = os.path.join(TASK_STATE_DIR, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    results.append(enrich_task_state(json.load(f)))
            except Exception:
                continue
        results.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return results

    def request_cancel(self, task_id: str):
        state = self.load(task_id)
        if not state:
            return {
                "ok": False,
                "task_id": task_id,
                "error": "Task not found",
            }
        state["cancel_requested"] = True
        state["events"].append(
            {
                "ts": utc_now(),
                "type": "cancel_requested",
                "message": "Cancel requested",
            }
        )
        self.save(state)
        return {
            "ok": True,
            "task_id": task_id,
            "status": state["status"],
            "cancel_requested": True,
        }


class TaskRunner:
    def __init__(self, task_state, store: TaskStore):
        self.state = task_state
        self.store = store
        self.controller = FleetController(ROBOT_FLEET_CONFIG)
        self.state_lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.failure_lock = threading.Lock()
        self.first_failure = None

    def _save(self):
        self.store.save(self.state)

    def _append_event(self, event_type: str, message: str, **extra):
        event = {"ts": utc_now(), "type": event_type, "message": message}
        event.update(extra)
        with self.state_lock:
            self.state["events"].append(event)
            self._save()

    def _set_status(self, status: str):
        with self.state_lock:
            self.state["status"] = status
            if status == "running" and not self.state.get("started_at"):
                self.state["started_at"] = utc_now()
            if status in {"completed", "failed", "blocked", "cancelled", "aborted"}:
                self.state["completed_at"] = utc_now()
            self._save()

    def _mark_failure(self, result):
        with self.failure_lock:
            if self.first_failure is None:
                self.first_failure = result
                self.cancel_event.set()

    def _check_cancel_requested(self):
        refreshed = self.store.load(self.state["task_id"])
        if refreshed and refreshed.get("cancel_requested"):
            self.cancel_event.set()
            return True
        return False

    def _build_step_result(self, ok: bool, step_type: str, path: str, **extra):
        result = {
            "ok": ok,
            "type": step_type,
            "path": path,
        }
        result.update(extra)
        return result

    def _collect_parallel_motion_targets(self, node: dict, path: str):
        return collect_motion_targets(node, path)

    def _execute_action_step(self, step: dict, path: str):
        step_type = step.get("type")
        arm_id = step.get("arm_id")
        params = step.get("params", {})

        self._append_event("step_started", f"{step_type} started", path=path, arm_id=arm_id)

        if step_type == "delay":
            delay_ms = int(step.get("delay_ms", 0))
            waited_ms = 0
            chunk_ms = 100
            while waited_ms < delay_ms:
                if self.cancel_event.is_set() or self._check_cancel_requested():
                    return self._build_step_result(False, step_type, path, error="Delay cancelled")
                sleep_ms = min(chunk_ms, delay_ms - waited_ms)
                time.sleep(sleep_ms / 1000.0)
                waited_ms += sleep_ms
            result = self._build_step_result(True, step_type, path, delay_ms=delay_ms)
            self._append_event("step_completed", f"{step_type} completed", path=path, result=result)
            return result

        if self.cancel_event.is_set() or self._check_cancel_requested():
            return self._build_step_result(False, step_type, path, error="Task cancelled before step execution")

        if step_type == "run_path":
            run_path_params = dict(params)
            path_type = run_path_params.pop("path_type")
            controller_result = self.controller.run_path(arm_id, path_type, **run_path_params)
        elif step_type == "move_relative":
            controller_result = self.controller.move_relative_arm(
                arm_id, params.get("dx", 0.0), params.get("dy", 0.0), params.get("dz", 0.0), params.get("dr", 0.0)
            )
        elif step_type == "move_to_pose":
            controller_result = self.controller.move_to_pose_arm(
                arm_id, params["x"], params["y"], params["z"], params.get("r", 0.0)
            )
        elif step_type == "move_joint_pose":
            controller_result = self.controller.move_joint_to_pose_arm(
                arm_id, params["x"], params["y"], params["z"], params.get("r", 0.0)
            )
        elif step_type == "recover_robot":
            controller_result = self.controller.recover_arm(arm_id)
        elif step_type == "recover_home_robot":
            controller_result = self.controller.recover_and_home_arm(arm_id)
        elif step_type == "stop_robot":
            controller_result = self.controller.stop_arm(arm_id)
        elif step_type == "motion_profile":
            controller_result = self.controller.set_motion_profile(
                arm_id,
                speed_l=params.get("speed_l"),
                acc_l=params.get("acc_l"),
                speed_j=params.get("speed_j"),
                acc_j=params.get("acc_j"),
            )
        else:
            controller_result = {"ok": False, "error": f"Unsupported step type '{step_type}'"}

        ok = bool(controller_result.get("ok"))
        result = self._build_step_result(ok, step_type, path, arm_id=arm_id, result=controller_result)
        if ok:
            self._append_event("step_completed", f"{step_type} completed", path=path, arm_id=arm_id, result=result)
        else:
            incident = controller_result.get("result") if isinstance(controller_result.get("result"), dict) else None
            safety_incident = None
            if (
                controller_result.get("guardrail_blocked") is True
                or controller_result.get("fault_type") == "guardrail_blocked"
            ):
                safety_incident = controller_result
            elif isinstance(incident, dict) and (
                incident.get("fault_type") == "runtime_fault"
                or incident.get("fault_type") == "guardrail_blocked"
                or incident.get("recommended_action") == "await_operator_decision"
                or incident.get("saw_running_motion") is True
            ):
                safety_incident = incident

            if safety_incident:
                with self.state_lock:
                    self.state["incident"] = {
                        "arm_id": arm_id,
                        "path": path,
                        "step_type": step_type,
                        "details": safety_incident,
                    }
                    self._save()
            self._append_event("step_failed", f"{step_type} failed", path=path, arm_id=arm_id, result=result)
        return result

    def _execute_node(self, node: dict, path: str):
        if self.cancel_event.is_set() or self._check_cancel_requested():
            return self._build_step_result(False, node.get("type", "unknown"), path, error="Task cancelled")

        node_type = node.get("type", "sequence")

        with self.state_lock:
            self.state["current_step_path"] = path
            self._save()

        if node_type == "sequence":
            results = []
            for index, child in enumerate(node.get("steps", [])):
                child_path = f"{path}.steps[{index}]"
                child_result = self._execute_node(child, child_path)
                results.append(child_result)
                if not child_result.get("ok"):
                    return self._build_step_result(False, node_type, path, results=results, error="Sequence failed")
            return self._build_step_result(True, node_type, path, results=results)

        if node_type == "parallel":
            children = node.get("steps", [])
            results = [None] * len(children)
            threads = []
            parallel_targets = []
            for index, child in enumerate(children):
                parallel_targets.extend(
                    self._collect_parallel_motion_targets(
                        child,
                        f"{path}.steps[{index}]",
                    )
                )
            guardrail_block = self.controller.preflight_parallel_targets(
                parallel_targets,
                path=path,
            )
            if guardrail_block:
                result = self._build_step_result(
                    False,
                    node_type,
                    path,
                    result=guardrail_block,
                    error=guardrail_block.get("error"),
                )
                with self.state_lock:
                    self.state["incident"] = {
                        "path": path,
                        "step_type": node_type,
                        "details": guardrail_block,
                    }
                    self._save()
                self._append_event("step_failed", "Parallel group blocked by guardrail", path=path, result=result)
                return result

            def run_child(index, child):
                try:
                    child_path = f"{path}.steps[{index}]"
                    child_result = self._execute_node(child, child_path)
                except Exception as exc:
                    child_result = self._build_step_result(
                        False,
                        child.get("type", "unknown"),
                        f"{path}.steps[{index}]",
                        error=f"Unhandled worker exception: {exc}",
                    )
                    self._append_event(
                        "step_failed",
                        "Worker thread raised an exception",
                        path=f"{path}.steps[{index}]",
                        result=child_result,
                    )
                results[index] = child_result
                if not child_result.get("ok"):
                    self._mark_failure(child_result)

            for index, child in enumerate(children):
                thread = threading.Thread(target=run_child, args=(index, child), daemon=True)
                threads.append(thread)
                thread.start()

            for thread in threads:
                thread.join()

            normalized_results = [item for item in results if item is not None]
            ok = len(normalized_results) == len(children) and all(
                item.get("ok") for item in normalized_results
            )
            return self._build_step_result(ok, node_type, path, results=normalized_results, error=None if ok else "Parallel group failed")

        return self._execute_action_step(node, path)

    def run(self):
        try:
            self._set_status("running")
            task_doc = {"type": "sequence", "steps": self.state.get("steps", [])}
            result = self._execute_node(task_doc, "task")

            if self._check_cancel_requested():
                with self.state_lock:
                    self.state["result"] = result
                self._set_status("cancelled")
                self._append_event("task_cancelled", "Task cancelled")
                return enrich_task_state(self.state)

            with self.state_lock:
                self.state["result"] = result
                self._save()

            if result.get("ok"):
                self._set_status("completed")
                self._append_event("task_completed", "Task completed", result=result)
            else:
                if self.state.get("incident"):
                    self._set_status("blocked")
                    self._append_event("task_blocked", "Task blocked by incident", result=result)
                else:
                    self._set_status("failed")
                    self._append_event("task_failed", "Task failed", result=result)
            return enrich_task_state(self.state)
        finally:
            self.controller.disconnect_all()


def _static_collect_motion_targets(node: dict, path: str):
    return collect_motion_targets(node, path)


def _preflight_task_node(controller: FleetController, node: dict, path: str):
    node_type = node.get("type", "sequence")

    if node_type in CONTROL_STEP_TYPES:
        if node_type == "parallel":
            parallel_targets = []
            for index, child in enumerate(node.get("steps", [])):
                parallel_targets.extend(
                    _static_collect_motion_targets(
                        child,
                        f"{path}.steps[{index}]",
                    )
                )
            guardrail_block = controller.preflight_parallel_targets(
                parallel_targets,
                path=path,
            )
            if guardrail_block:
                return guardrail_block

        for index, child in enumerate(node.get("steps", [])):
            blocked = _preflight_task_node(
                controller,
                child,
                f"{path}.steps[{index}]",
            )
            if blocked:
                return blocked
        return None

    if node_type in {"move_to_pose", "move_joint_pose"}:
        params = node.get("params", {})
        result = controller.preflight_pose(
            node.get("arm_id"),
            {
                "x": params["x"],
                "y": params["y"],
                "z": params["z"],
                "r": params.get("r", 0.0),
            },
            operation=f"task_preflight:{path}:{node_type}",
            check_collision=False,
        )
        return None if result.get("ok") else result

    if node_type == "run_path":
        for target in _static_collect_motion_targets(node, path):
            result = controller.preflight_pose(
                target["arm_id"],
                target["pose"],
                operation=f"task_preflight:{target['path']}:{target['step_type']}",
                check_collision=False,
            )
            if not result.get("ok"):
                return result

    return None


def preflight_task(task_doc):
    validate_task_document(task_doc)
    controller = FleetController(ROBOT_FLEET_CONFIG)
    try:
        task_node = {"type": "sequence", "steps": copy.deepcopy(task_doc.get("steps", []))}
        blocked = _preflight_task_node(controller, task_node, "task")
        if blocked:
            return {
                "ok": False,
                "guardrail_blocked": True,
                "fault_type": "guardrail_blocked",
                "recommended_action": blocked.get("recommended_action", "wait_or_replan"),
                "error": blocked.get("error", "Task blocked by ARC safety guardrail."),
                "details": blocked,
            }
        return {
            "ok": True,
            "guardrail_passed": True,
            "step_count": count_task_steps(task_doc.get("steps", [])),
        }
    finally:
        controller.disconnect_all()


def load_task_document(file_path=None, json_text=None):
    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            task_doc = json.load(f)
            validate_task_document(task_doc)
            return task_doc
    if json_text:
        task_doc = json.loads(json_text)
        validate_task_document(task_doc)
        return task_doc
    raise ValueError("Expected either file_path or json_text")


def run_task_inline(task_doc, task_id=None):
    store = TaskStore()
    state = store.create(task_doc, task_id=task_id, mode="inline")
    runner = TaskRunner(state, store)
    return runner.run()


def start_task_background(task_doc, source_file=None):
    store = TaskStore()
    state = store.create(task_doc, mode="background")
    task_id = state["task_id"]
    command = [sys.executable, os.fspath(arc_py_path()), "task", "worker", "--task-id", task_id]
    if source_file:
        command.extend(["--file", os.path.abspath(source_file)])
    else:
        inline_path = os.path.join(TASK_STATE_DIR, f"{task_id}.task.json")
        with open(inline_path, "w", encoding="utf-8") as f:
            json.dump(task_doc, f, ensure_ascii=False, indent=2)
        command.extend(["--file", inline_path])

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(
        command,
        cwd=os.fspath(project_path()),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )
    return {
        "ok": True,
        "task_id": task_id,
        "status": "pending",
        "mode": "background",
        "step_count": count_task_steps(task_doc.get("steps", [])),
    }


def run_background_worker(task_id: str, file_path: str):
    store = TaskStore()
    state = store.load(task_id)
    if not state:
        task_doc = load_task_document(file_path=file_path)
        state = store.create(task_doc, task_id=task_id, mode="background")
    else:
        task_doc = load_task_document(file_path=file_path)
        state["steps"] = copy.deepcopy(task_doc.get("steps", []))
        store.save(state)
    runner = TaskRunner(state, store)
    return runner.run()
