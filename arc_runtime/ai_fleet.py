import time
import threading
import ast
import json
import requests
from .camera_controller import CameraController
from .config import ARM_REACH_ZONES, CAMERA_CONFIG, active_config, active_robot_ids
from .fleet_controller import FleetController
from .storage_service import compact_storage_view, load as load_storage, list_slots as list_storage_slots, resolve as resolve_storage, policy as storage_policy, scan as scan_storage, set_policy as set_storage_policy
from .robot_arm import MG400Robot
from .tool_context import build_tool_context
from .workspace_model import list_workspace_zones as runtime_workspace_zones, list_storage_groups as runtime_storage_groups, plan_workspace_intent

# ==========================================
# ๐ API CONFIGURATION (shared with Sim web chat)
# Override: ARC_SIM_LLM_BASE_URL / ARC_SIM_LLM_API_KEY / ARC_SIM_LLM_MODEL
# ==========================================
from .config import SIM_LLM_API_KEY, SIM_LLM_BASE_URL, SIM_LLM_MODEL
from .jetson_sidecar import CollectionRun, current_run

BASE_URL = SIM_LLM_BASE_URL
API_KEY = SIM_LLM_API_KEY
MODEL = SIM_LLM_MODEL

# ==========================================
# ๐ง  AI CONTROLLER (CUSTOM API FLEET MANAGER)
# ==========================================
class OllamaFleetManager:
    def __init__(self, model_name, fleet_config): # เธฃเธฑเธ model_name เธกเธฒเนเธ•เนเน€เธฃเธฒเธเธฐเนเธเนเธเธฒเธเธ•เธฑเธงเนเธเธฃ MODEL เธเนเธฒเธเธเธเนเธ—เธเนเธ”เน
        self.active_arm_ids = active_robot_ids(fleet_config)
        active_fleet_config = active_config(fleet_config)
        self.robots = {}
        for rid, cfg in active_fleet_config.items():
            self.robots[rid] = MG400Robot(rid, cfg["ip"], cfg["port"])
        self.camera = CameraController(CAMERA_CONFIG, ARM_REACH_ZONES, active_arm_ids=self.active_arm_ids)
        self.fleet = FleetController(active_fleet_config)
        
        self._connect_all_robots()
        
        self.instruction = """You control calibrated Dobot MG400 arms through ARC tools.

Runtime contract:
- Call get_runtime_context when readiness, calibration, or cached capacity may affect a decision.
- camera_scan detects workspace objects. scan_storage_slots is the only fresh Storage occupancy scan.
- workspace_xy is shared geometry only. Motion XY must come from per_arm_robot_plane[robot_id].
- Use only an arm that is active, online, transform-ready, and listed in reachable_by.
- Storage is four calibrated groups: LT, LB, RT, RB, each with named Slots such as LT1.
- Before a Storage task, refresh Slots when the cached scan may be stale, then resolve_storage_slot.
- place_to_storage_slot owns resolve, atomic reservation, placement, park, capacity verification, and final occupancy.
- A place succeeds only when its response confirms verified occupied state. unknown is a failed task.
- storage_zone and drop_zone are compatibility aliases, not new planning destinations.
- Keep arm motion sequential by default. Do not run two arms concurrently without an explicit collision-safe plan.
- Stop after any failed, faulted, offline, guardrail-blocked, or verification-failed step.
"""
        if len(self.active_arm_ids) == 1:
            self.instruction += (
                f"\nOnly {self.active_arm_ids[0]} is active. Never plan another arm, handoff, "
                "or parallel dual-arm motion."
            )

        self.messages = [{'role': 'system', 'content': self.instruction}]

    def _connect_all_robots(self):
        threads = []
        for bot in self.robots.values():
            t = threading.Thread(target=bot.connect)
            threads.append(t)
            t.start()
        for t in threads: t.join()

    def _get_robot(self, robot_id: str):
        rid_m = str(robot_id).strip()
        if rid_m in ["Arm1", "1", "arm1"]: return self.robots.get("Arm1")
        if rid_m in ["Arm2", "2", "arm2"]: return self.robots.get("Arm2")
        return None

    def _active_arm_error(self, robot_id: str):
        rid_m = str(robot_id).strip()
        if rid_m in ["Arm1", "1", "arm1"]:
            normalized = "Arm1"
        elif rid_m in ["Arm2", "2", "arm2"]:
            normalized = "Arm2"
        else:
            normalized = rid_m
        if normalized not in self.active_arm_ids:
            return f"Error: {normalized} is not active. Active arms: {', '.join(self.active_arm_ids)}"
        return None

    def _active_arm_json_error(self, robot_id: str):
        error = self._active_arm_error(robot_id)
        if not error:
            return None
        return json.dumps(
            {
                "ok": False,
                "robot_id": robot_id,
                "error": error,
                "active_arm_ids": self.active_arm_ids,
            },
            ensure_ascii=False,
        )

    # --- ๐ ๏ธ Tools for Robot ---
    def control_robot(self, robot_id: str, action: str, step: float = 50.0) -> str:
        inactive = self._active_arm_error(robot_id)
        if inactive: return inactive
        bot = self._get_robot(robot_id)
        if not bot: return f"Error: Robot '{robot_id}' not found"
        
        print(f"๐ค– [{bot.robot_id}] Action: {action} (Distance: {step} mm)")
        bot.check_and_fix_status()
        
        if action == "HOME": res = bot.send_dashboard(f"MovL({bot.HOME_POS})")
        elif action == "UP": res = bot.send_dashboard(f"RelMovL(0,0,{step},0)")
        elif action == "DOWN": res = bot.send_dashboard(f"RelMovL(0,0,{-step},0)")
        elif action == "LEFT": res = bot.send_dashboard(f"RelMovL(0,{step},0,0)")
        elif action == "RIGHT": res = bot.send_dashboard(f"RelMovL(0,{-step},0,0)")
        elif action == "FORWARD": res = bot.send_dashboard(f"RelMovL({step},0,0,0)")
        elif action == "BACK": res = bot.send_dashboard(f"RelMovL({-step},0,0,0)")
        else: return f"Error: Invalid action '{action}'"
        
        return f"Success: {bot.robot_id} did {action} by {step} mm ({res})"

    def move_to_coords(self, robot_id: str, x: float, y: float, z: float, r: float = 0.0) -> str:
        inactive = self._active_arm_error(robot_id)
        if inactive: return inactive
        bot = self._get_robot(robot_id)
        if not bot: return f"Error: Robot '{robot_id}' not found"
        
        print(f"๐“ [{bot.robot_id}] Moving to: X={x:.1f}, Y={y:.1f}, Z={z:.1f}")
        bot.check_and_fix_status()
        res = bot.send_dashboard(f"MovL({x},{y},{z},{r})")
        return f"Success: {bot.robot_id} moved to X={x},Y={y},Z={z},R={r} ({res})"

    def tool_control(self, robot_id: str, enable: bool) -> str:
        inactive = self._active_arm_error(robot_id)
        if inactive: return inactive
        bot = self._get_robot(robot_id)
        if not bot: return f"Error: Robot '{robot_id}' not found"
        
        print(f"๐งฒ [{bot.robot_id}] Tool Enabled: {enable}")
        bot.check_and_fix_status()
        state = 1 if enable else 0
        res = bot.send_dashboard(f"DO(1,{state})", wait_idle=False)
        time.sleep(0.5)
        return f"Success: {bot.robot_id} tool set to {enable} ({res})"

    def get_robot_state(self, robot_id: str) -> str:
        inactive = self._active_arm_error(robot_id)
        if inactive: return inactive
        bot = self._get_robot(robot_id)
        if not bot:
            return f"Error: Robot '{robot_id}' not found"

        feedback = bot.get_feedback_snapshot()
        if not feedback.get("ok"):
            return f"Error: {feedback['error']}"
        return json.dumps({
            "robot_id": bot.robot_id,
            "robot_mode": feedback["robot_mode"],
            "robot_mode_name": feedback["robot_mode_name"],
            "enable_status": feedback["enable_status"],
            "running_status": feedback["running_status"],
            "error_status": feedback["error_status"],
            "brake_status": feedback["brake_status"],
            "pose": feedback["pose"],
            "joints": feedback["joints"],
        }, ensure_ascii=False)

    def get_robot_pose(self, robot_id: str) -> str:
        inactive = self._active_arm_error(robot_id)
        if inactive: return inactive
        bot = self._get_robot(robot_id)
        if not bot:
            return f"Error: Robot '{robot_id}' not found"

        feedback = bot.get_feedback_snapshot()
        if not feedback.get("ok"):
            return f"Error: {feedback['error']}"
        return json.dumps({
            "robot_id": bot.robot_id,
            "robot_mode": feedback["robot_mode"],
            "robot_mode_name": feedback["robot_mode_name"],
            "pose": feedback["pose"],
            "joints": feedback["joints"],
        }, ensure_ascii=False)

    def recover_robot(self, robot_id: str) -> str:
        inactive = self._active_arm_error(robot_id)
        if inactive: return inactive
        bot = self._get_robot(robot_id)
        if not bot:
            return f"Error: Robot '{robot_id}' not found"

        result = bot.recover()
        return json.dumps({
            "robot_id": bot.robot_id,
            "clear_error": result["clear_error"],
            "enable_robot": result["enable_robot"],
        }, ensure_ascii=False)

    def recover_and_home_robot(self, robot_id: str) -> str:
        state_raw = self.get_robot_state(robot_id)
        if state_raw.startswith("Error:"):
            return state_raw

        state = json.loads(state_raw)
        response = {
            "robot_id": robot_id,
            "initial_state": state,
        }

        if state["error_status"] or state["robot_mode_name"] != "enabled":
            recover_raw = self.recover_robot(robot_id)
            if recover_raw.startswith("Error:"):
                return recover_raw
            response["recover"] = json.loads(recover_raw)

            state_raw = self.get_robot_state(robot_id)
            if state_raw.startswith("Error:"):
                return state_raw
            state = json.loads(state_raw)
            response["post_recover_state"] = state

        if state["error_status"] or state["robot_mode_name"] != "enabled":
            return json.dumps({
                "robot_id": robot_id,
                "ok": False,
                "error": f"Robot not ready for home: mode={state['robot_mode_name']}",
                "details": response,
            }, ensure_ascii=False)

        home_result = self.control_robot(robot_id, "HOME")
        response["home"] = home_result
        response["ok"] = not home_result.startswith("Error:")
        return json.dumps(response, ensure_ascii=False)

    def camera_status(self, camera_id: str = "Cam1") -> str:
        return json.dumps(self.camera.get_status(camera_id), ensure_ascii=False)

    def camera_scan(self, camera_id: str = "Cam1", color: str = "any", selection: str = "first") -> str:
        return json.dumps(
            self.camera.scan_workspace(camera_id=camera_id, color=color, selection=selection),
            ensure_ascii=False,
        )

    def camera_assign_targets(self, assignments, camera_id: str = "Cam1") -> str:
        if isinstance(assignments, str):
            assignments = ast.literal_eval(assignments)
        return json.dumps(
            self.camera.assign_targets(assignments, camera_id=camera_id),
            ensure_ascii=False,
        )

    def list_storage_slots(self, group=None, occupancy=None) -> str:
        registry = load_storage()
        slots = list_storage_slots(registry=registry, group=group, occupancy=occupancy)
        return json.dumps({
            "ok": True,
            "fresh_scan": False,
            "updated_at": registry.get("updated_at"),
            "source_scan": registry.get("source_scan"),
            "policy": registry.get("slot_policy") or storage_policy(),
            **compact_storage_view(slots),
        }, ensure_ascii=False)
    def get_runtime_context(self) -> str:
        online = [
            arm_id for arm_id, robot in getattr(self, "robots", {}).items()
            if getattr(robot, "is_connected", False)
        ]
        return json.dumps(build_tool_context(online_arms=online), ensure_ascii=False)

    def scan_storage_slots(self) -> str:
        """Run one fresh capacity scan and return a compact Slot/color view."""
        result = scan_storage()
        if not result.get("ok"):
            return json.dumps(result, ensure_ascii=False)
        registry = result.get("registry") or {}
        compact = result.get("compact") or compact_storage_view(result.get("slots") or [])
        return json.dumps({
            "ok": True,
            "fresh_scan": True,
            "updated_at": registry.get("updated_at"),
            "source_scan": registry.get("source_scan"),
            "scan": result.get("scan"),
            "slots_path": result.get("slots_path"),
            **compact,
        }, ensure_ascii=False)
    def list_workspace_zones(self) -> str:
        return json.dumps(runtime_workspace_zones(), ensure_ascii=False)

    def list_storage_groups(self) -> str:
        return json.dumps(runtime_storage_groups(), ensure_ascii=False)

    def plan_intent(self, destination_reference: str, source_zone=None, color="any", robot_id=None) -> str:
        scan = self.camera.scan_workspace(camera_id="Cam1", color="any", selection="first", require_saved_workspace=True)
        if not scan.get("ok"):
            return json.dumps(scan, ensure_ascii=False)
        return json.dumps(plan_workspace_intent(scan.get("objects", []), destination_reference, source_zone=source_zone, color=color, robot_id=robot_id, active_arms=active_robot_ids()), ensure_ascii=False)

    def resolve_storage_slot(self, reference=None, color=None, robot_id=None) -> str:
        online = [
            arm_id for arm_id, robot in getattr(self, "robots", {}).items()
            if getattr(robot, "is_connected", False)
        ]
        return json.dumps(resolve_storage(
            reference=reference,
            color=color,
            arm_id=robot_id,
            active_arms=self.active_arm_ids,
            online_arms=online if robot_id else None,
        ), ensure_ascii=False)

    def set_storage_policy(self, color_groups=None, default_group=None, selection_order="index_ascending") -> str:
        return json.dumps(set_storage_policy({"color_groups": color_groups or {}, "default_group": default_group, "selection_order": selection_order}).get("slot_policy"), ensure_ascii=False)
    def pick_detected_place(self, robot_id: str, color: str, destination: str, camera_id: str = "Cam1", block: str = None) -> str:
        inactive = self._active_arm_json_error(robot_id)
        if inactive: return inactive
        return json.dumps(
            self.fleet.pick_detected_and_place(
                robot_id=robot_id,
                color=color,
                destination=destination,
                camera_id=camera_id,
                block=block,
            ),
            ensure_ascii=False,
        )

    def pick_all_detected_place(self, robot_id: str, destination: str, color: str = "any", camera_id: str = "Cam1", max_picks: int = 10) -> str:
        inactive = self._active_arm_json_error(robot_id)
        if inactive: return inactive
        return json.dumps(
            self.fleet.pick_all_detected_and_place(
                robot_id=robot_id,
                destination=destination,
                color=color,
                camera_id=camera_id,
                max_picks=max_picks,
            ),
            ensure_ascii=False,
        )

    def pick_at_pose(self, robot_id: str, x: float, y: float, r: float = 0.0) -> str:
        inactive = self._active_arm_json_error(robot_id)
        if inactive: return inactive
        return json.dumps(
            self.fleet.pick_at(robot_id=robot_id, x=float(x), y=float(y), r=float(r)),
            ensure_ascii=False,
        )

    def place_to_storage_slot(self, robot_id: str, reference=None, color=None) -> str:
        inactive = self._active_arm_json_error(robot_id)
        if inactive: return inactive
        return json.dumps(self.fleet.place_to_storage_slot(robot_id=robot_id, reference=reference, color=color), ensure_ascii=False)
    def place_at_named(self, robot_id: str, destination: str) -> str:
        inactive = self._active_arm_json_error(robot_id)
        if inactive: return inactive
        return json.dumps(
            self.fleet.place_to_named_target(robot_id=robot_id, destination=destination),
            ensure_ascii=False,
        )

    def place_at_pose(self, robot_id: str, x: float, y: float, r: float = 0.0) -> str:
        inactive = self._active_arm_json_error(robot_id)
        if inactive: return inactive
        return json.dumps(
            self.fleet.place_at(robot_id=robot_id, x=float(x), y=float(y), r=float(r)),
            ensure_ascii=False,
        )

    def move_to_named_target(self, robot_id: str, destination: str) -> str:
        inactive = self._active_arm_json_error(robot_id)
        if inactive: return inactive
        return json.dumps(
            self.fleet.move_to_named_target(robot_id=robot_id, destination=destination),
            ensure_ascii=False,
        )

    def set_tool_state(self, robot_id: str, enable: bool) -> str:
        inactive = self._active_arm_json_error(robot_id)
        if inactive: return inactive
        return json.dumps(
            self.fleet.set_tool_state(robot_id=robot_id, enable=enable),
            ensure_ascii=False,
        )

    def execute_parallel(self, commands) -> str:
        if len(self.active_arm_ids) < 2:
            return "Error: execute_parallel is disabled in single-arm mode."
        print("๐” Executing Parallel Commands...")
        
        if isinstance(commands, str):
            try:
                commands = ast.literal_eval(commands)
            except Exception as e:
                print(f"โ Error parsing parallel commands: {e}")
                return f"Error: Cannot parse commands. Got: {commands}"
                
        if not isinstance(commands, list):
            return f"Error: Commands must be a list. Got {type(commands)}"

        threads = []
        results = []
        
        def run_command(cmd_info):
            if isinstance(cmd_info, str):
                try: cmd_info = ast.literal_eval(cmd_info)
                except: return results.append(f"Error format: {cmd_info}")
                    
            if not isinstance(cmd_info, dict):
                return results.append(f"Error format: Expected dict")

            func_name = cmd_info.get("function")
            kwargs = cmd_info.get("kwargs", {})
            
            if func_name == "move_to_coords": results.append(self.move_to_coords(**kwargs))
            elif func_name == "tool_control": results.append(self.tool_control(**kwargs))
            elif func_name == "control_robot": results.append(self.control_robot(**kwargs))
            elif func_name == "get_robot_state": results.append(self.get_robot_state(**kwargs))
            elif func_name == "get_robot_pose": results.append(self.get_robot_pose(**kwargs))
            elif func_name == "recover_robot": results.append(self.recover_robot(**kwargs))
            elif func_name == "recover_and_home_robot": results.append(self.recover_and_home_robot(**kwargs))
            elif func_name == "camera_status": results.append(self.camera_status(**kwargs))
            elif func_name == "camera_scan": results.append(self.camera_scan(**kwargs))
            elif func_name == "camera_assign_targets": results.append(self.camera_assign_targets(**kwargs))
            elif func_name == "move_to_named_target": results.append(self.move_to_named_target(**kwargs))
            elif func_name == "set_tool_state": results.append(self.set_tool_state(**kwargs))
            elif func_name == "pick_detected_place": results.append(self.pick_detected_place(**kwargs))
            elif func_name == "pick_all_detected_place": results.append(self.pick_all_detected_place(**kwargs))
            elif func_name == "pick_at_pose": results.append(self.pick_at_pose(**kwargs))
            elif func_name == "place_at_named": results.append(self.place_at_named(**kwargs))
            elif func_name == "place_at_pose": results.append(self.place_at_pose(**kwargs))
            else: results.append(f"Error: Unknown function '{func_name}'")
                
        for cmd in commands:
            t = threading.Thread(target=run_command, args=(cmd,))
            threads.append(t)
            t.start()
            
        for t in threads: t.join()
        return f"Parallel Execution Completed: {results}"

    # ==========================================
    # โ๏ธ API Tools Schema (เนเธเธฅเธเนเธซเน AI เน€เธเนเธฒเนเธ)
    # ==========================================
    def get_api_tools(self):
        return [{"type": "function", "function": {"name": "get_runtime_context", "description": "Read compact calibration, arm readiness, scan contract, and cached Slot summary without motion.", "parameters": {"type": "object", "properties": {}, "required": []}}}, {"type": "function", "function": {"name": "list_workspace_zones", "description": "List calibrated Outer Box and work zones such as A1-A8.", "parameters": {"type": "object", "properties": {}}}}, {"type": "function", "function": {"name": "list_storage_groups", "description": "List calibrated Storage groups LT/LB/RT/RB.", "parameters": {"type": "object", "properties": {}}}}, {"type": "function", "function": {"name": "plan_intent", "description": "Dry-run a calibrated source-zone to destination plan.", "parameters": {"type": "object", "properties": {"destination_reference": {"type": "string"}, "source_zone": {"type": "string"}, "color": {"type": "string"}, "robot_id": {"type": "string"}}, "required": ["destination_reference"]}}}, {"type": "function", "function": {"name": "place_to_storage_slot", "description": "Place the held object into a named Storage slot or group (LT/LB/RT/RB). Legacy storage_zone/drop_zone are compatibility aliases only.", "parameters": {"type": "object", "properties": {"robot_id": {"type": "string"}, "reference": {"type": ["string", "null"]}, "color": {"type": ["string", "null"]}}, "required": ["robot_id"]}}},            {"type": "function", "function": {"name": "list_storage_slots", "description": "List all Storage groups and slot occupancy.", "parameters": {"type": "object", "properties": {"group": {"type": "string"}, "occupancy": {"type": "string"}}}}},
            {"type": "function", "function": {"name": "scan_storage_slots", "description": "Run a fresh camera capacity scan and refresh the calibrated Storage Slot Registry.", "parameters": {"type": "object", "properties": {}, "required": []}}},
            {"type": "function", "function": {"name": "resolve_storage_slot", "description": "Resolve LT3 or a Storage group to an empty reachable slot.", "parameters": {"type": "object", "properties": {"reference": {"type": "string"}, "color": {"type": "string"}, "robot_id": {"type": "string"}}}}},
            {"type": "function", "function": {"name": "set_storage_policy", "description": "Set color to Storage group policy.", "parameters": {"type": "object", "properties": {"color_groups": {"type": "object"}, "default_group": {"type": "string"}}}}},
            {
                "type": "function",
                "function": {
                    "name": "camera_status",
                    "description": "เธ•เธฃเธงเธเธชเธ–เธฒเธเธฐเธเธฅเนเธญเธเนเธฅเธฐ calibration readiness",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "camera_id": {"type": "string"}
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "camera_scan",
                    "description": "เธชเนเธเธเธ เธฒเธเธเธฒเธเธเธฅเนเธญเธ เธซเธฒ object เนเธ workspace เธเธฃเนเธญเธกเธเธดเธเธฑเธ” robot_plane เนเธฅเธฐ reachable_by",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "camera_id": {"type": "string"},
                            "color": {"type": "string", "enum": ["any", "red", "green", "blue", "unknown"]},
                            "selection": {"type": "string", "enum": ["first", "largest", "leftmost", "rightmost", "topmost", "bottommost"]}
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "camera_assign_targets",
                    "description": "เธเธฑเธเธเธนเนเธงเธฑเธ•เธ–เธธเธเธฒเธเธเธฅเนเธญเธเนเธซเนเนเธ•เนเธฅเธฐเนเธเธเธ•เธฒเธกเธชเธต เนเธ”เธขเธ•เธฃเธงเธ reachable_by เนเธซเนเนเธฅเนเธง",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "camera_id": {"type": "string"},
                            "assignments": {
                                "type": "string",
                                "description": "Python/JSON list เน€เธเนเธ [{'arm_id':'Arm1','color':'green'}]"
                            }
                        },
                        "required": ["assignments"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "move_to_named_target",
                    "description": "เน€เธเธฅเธทเนเธญเธเนเธเธเนเธเธขเธฑเธ named destination เธญเธขเนเธฒเธเน€เธ”เธตเธขเธง เน€เธเนเธ home, storage_zone เธซเธฃเธทเธญ drop_zone เนเธ”เธขเนเธกเนเธซเธขเธดเธ เนเธกเนเธงเธฒเธ เนเธฅเธฐเนเธกเนเน€เธเธดเธ”เธซเธฑเธงเธ”เธนเธ”",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"},
                            "destination": {"type": "string"}
                        },
                        "required": ["robot_id", "destination"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "set_tool_state",
                    "description": "เธเธงเธเธเธธเธกเธซเธฑเธงเธ”เธนเธ”เธซเธฃเธทเธญ tool output เนเธ”เธขเธ•เธฃเธ: True = เน€เธเธดเธ”, False = เธเธดเธ”",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"},
                            "enable": {"type": "boolean"}
                        },
                        "required": ["robot_id", "enable"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "pick_detected_place",
                    "description": "เนเธเนเน€เธเธเธฒเธฐเน€เธกเธทเนเธญเธเธนเนเนเธเนเธชเธฑเนเธเนเธซเนเธซเธขเธดเธเธซเธฃเธทเธญเธเธฑเธ object เธ—เธตเนเธ•เธฃเธงเธเธเธฑเธเนเธ”เนเนเธเนเธเนเธญเธเน€เธ”เธตเธขเธง เนเธฅเนเธงเธงเธฒเธเธ—เธตเน named destination เธเธญเธเนเธเธเธ—เธตเนเนเธเนเธเธฒเธ เน€เธเนเธ storage_zone เธชเธณเธซเธฃเธฑเธ Arm1 เธเธฃเนเธญเธก post-pick verification เธฃเธฑเธ block (เน€เธเนเธ 'Block 1,1') เธ–เนเธฒเธเธนเนเนเธเนเธฃเธฐเธเธธเน€เธเธฒเธฐเธเธ เธซเนเธฒเธกเนเธเนเธ–เนเธฒเธเธนเนเนเธเนเนเธเนเธชเธฑเนเธเนเธซเนเน€เธเธฅเธทเนเธญเธเธ—เธตเนเธเธฅเธฑเธ home เธซเธฃเธทเธญเธ–เนเธฒเธเธนเนเนเธเนเธชเธฑเนเธเนเธซเนเธซเธขเธดเธเธซเธฅเธฒเธขเธเนเธญเธ (เนเธเน pick_all_detected_place เนเธ—เธ)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"},
                            "color": {"type": "string", "enum": ["red", "green", "blue", "unknown"]},
                            "destination": {"type": "string", "description": "named destination เน€เธเนเธ drop_zone เธซเธฃเธทเธญ storage_zone"},
                            "camera_id": {"type": "string"},
                            "block": {"type": "string", "description": "เธฃเธฐเธเธธ block เน€เธเธฒเธฐเธเธ เน€เธเนเธ 'Block 1,1' (optional)"}
                        },
                        "required": ["robot_id", "color", "destination"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "pick_all_detected_place",
                    "description": "เธซเธขเธดเธเธ—เธธเธเธเนเธญเธเธ—เธตเนเธซเธขเธดเธเนเธ”เนเธเธญเธเนเธเธเธเธฑเนเธ (เธชเธฒเธกเธฒเธฃเธ– filter เธชเธตเธ”เนเธงเธข color='red'|'green'|'blue' เธซเธฃเธทเธญ 'any' เธ—เธฑเนเธเธซเธกเธ”) เนเธฅเนเธงเน€เธญเธฒเนเธเธงเธฒเธเธ—เธตเน destination เน€เธ”เธตเธขเธงเธเธฑเธ เธงเธเธเนเธณ scanโ’pickโ’place เธเธเธเธงเนเธฒเนเธกเนเธกเธต object เธ—เธตเนเน€เธซเธฅเธทเธญ เนเธเนเน€เธกเธทเนเธญเธเธนเนเนเธเนเธชเธฑเนเธเนเธซเนเธซเธขเธดเธ 'เธ—เธฑเนเธเธซเธกเธ”' เธซเธฃเธทเธญ 'เธ—เธธเธเธเนเธญเธ'",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"},
                            "destination": {"type": "string", "description": "named destination เน€เธเนเธ drop_zone เธซเธฃเธทเธญ storage_zone"},
                            "color": {"type": "string", "enum": ["any", "red", "green", "blue", "unknown"]},
                            "camera_id": {"type": "string"},
                            "max_picks": {"type": "number", "description": "เธเธณเธเธฑเธ”เธเธณเธเธงเธเธฃเธญเธเธชเธนเธเธชเธธเธ” (default 10)"}
                        },
                        "required": ["robot_id", "destination"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "control_robot",
                    "description": "เธเธงเธเธเธธเธกเธซเธธเนเธเธขเธเธ•เนเธเธทเนเธเธเธฒเธ: HOME, UP, DOWN, LEFT, RIGHT, FORWARD, BACK",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"},
                            "action": {"type": "string", "enum": ["HOME", "UP", "DOWN", "LEFT", "RIGHT", "FORWARD", "BACK"]},
                            "step": {"type": "number", "description": "เธฃเธฐเธขเธฐเธเธฒเธฃเธเธขเธฑเธ (เธกเธดเธฅเธฅเธดเน€เธกเธ•เธฃ)"}
                        },
                        "required": ["robot_id", "action"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "move_to_coords",
                    "description": "เธชเธฑเนเธเธซเธธเนเธเธขเธเธ•เนเน€เธเธฅเธทเนเธญเธเธ—เธตเนเนเธเธ—เธตเนเธเธดเธเธฑเธ” X, Y, Z, R",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"},
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "z": {"type": "number"},
                            "r": {"type": "number"}
                        },
                        "required": ["robot_id", "x", "y", "z"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "tool_control",
                    "description": "เธเธงเธเธเธธเธกเธซเธฑเธงเธเธฑเธ: True = เธซเธเธตเธ/เธ”เธนเธ”, False = เธเธฅเนเธญเธข",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"},
                            "enable": {"type": "boolean"}
                        },
                        "required": ["robot_id", "enable"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_robot_state",
                    "description": "เธญเนเธฒเธเธชเธ–เธฒเธเธฐเธซเธธเนเธเธขเธเธ•เนเธเธฑเธเธเธธเธเธฑเธ เธฃเธงเธกเธ–เธถเธ mode, enable/running/error เนเธฅเธฐ pose/joints เธฅเนเธฒเธชเธธเธ”",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"}
                        },
                        "required": ["robot_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_robot_pose",
                    "description": "เธญเนเธฒเธเธ•เธณเนเธซเธเนเธเธเธฑเธเธเธธเธเธฑเธเธเธญเธเธซเธธเนเธเธขเธเธ•เน เธฃเธงเธกเธ–เธถเธ pose เนเธฅเธฐ joints เธฅเนเธฒเธชเธธเธ”",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"}
                        },
                        "required": ["robot_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "recover_robot",
                    "description": "เน€เธเธฅเธตเธขเธฃเน error เนเธฅเธฐเธชเธฑเนเธ EnableRobot เนเธซเธกเน เนเธเนเน€เธกเธทเนเธญเธซเธธเนเธเธ•เธดเธ” fault เธ—เธตเน recover เนเธ”เน",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"}
                        },
                        "required": ["robot_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "recover_and_home_robot",
                    "description": "เธ•เธฃเธงเธเธชเธ–เธฒเธเธฐเธซเธธเนเธ เธ–เนเธฒเธกเธต error เนเธซเนเน€เธเธฅเธตเธขเธฃเนเนเธฅเธฐ enable เนเธซเธกเน เธเธฒเธเธเธฑเนเธเธเธฅเธฑเธ Home เน€เธกเธทเนเธญเธชเธ–เธฒเธเธฐเธเธฃเนเธญเธก",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"}
                        },
                        "required": ["robot_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "pick_at_pose",
                    "description": "Primitive grasp: approachโ’descendโ’suction-onโ’lift เธ—เธตเนเธเธดเธเธฑเธ” (x,y) เธเธญเธ arm-local frame เนเธ”เธขเธ•เธฃเธ เนเธกเนเธกเธต reach filter เนเธกเนเธกเธต color filter AI เน€เธฅเธทเธญเธเนเธเธเนเธฅเธฐเธเธดเธเธฑเธ”เน€เธญเธ เนเธเนเน€เธกเธทเนเธญเธ•เนเธญเธเธเธฒเธฃ cross-arm (เธชเธฑเนเธเนเธเธเธซเธเธถเนเธเธซเธขเธดเธเธเธญเธเนเธเธเธฑเนเธเธญเธตเธเนเธเธ) เธซเธฃเธทเธญ pick_detected_place เธเธเธดเน€เธชเธเธงเนเธฒ unreachable เนเธ•เนเธขเธฑเธเธญเธขเธฒเธเธฅเธญเธ เธญเนเธฒเธเธเธดเธเธฑเธ”เธเธฒเธ camera_scan -> per_arm_robot_plane[arm].x/.y เธเนเธญเธ",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"},
                            "x": {"type": "number", "description": "x เนเธ arm-local frame เธเธญเธเนเธเธเธ—เธตเนเธเธฐเนเธเนเธซเธขเธดเธ (mm)"},
                            "y": {"type": "number", "description": "y เนเธ arm-local frame เธเธญเธเนเธเธเธ—เธตเนเธเธฐเนเธเนเธซเธขเธดเธ (mm)"},
                            "r": {"type": "number"}
                        },
                        "required": ["robot_id", "x", "y"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "place_at_named",
                    "description": "Primitive place: approachโ’descendโ’suction-offโ’depart เธ—เธตเน named destination (เน€เธเนเธ storage_zone, drop_zone, home) เนเธเนเธเธนเนเธเธฑเธ pick_at_pose เน€เธเธทเนเธญเธซเธขเนเธญเธเธเธญเธเธฅเธเธเธฅเธฒเธขเธ—เธฒเธ",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"},
                            "destination": {"type": "string"}
                        },
                        "required": ["robot_id", "destination"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "place_at_pose",
                    "description": "Primitive place เธ—เธตเนเธเธดเธเธฑเธ” (x,y) เธญเธดเธชเธฃเธฐ เธเธงเธฒเธกเธชเธนเธเธ•เธญเธเธงเธฒเธ = pick_z (เธเธงเธฒเธกเธชเธนเธเน€เธ”เธตเธขเธงเธเธฑเธเธ•เธญเธเธซเธขเธดเธ โ€” เน€เธเธฃเธฒเธฐเธงเธฑเธ•เธ–เธธเธเธฑเนเธเธญเธขเธนเนเธเธ workspace surface) เนเธเนเน€เธกเธทเนเธญเธ•เนเธญเธเธเธฒเธฃเธงเธฒเธ เธ“ เธ•เธณเนเธซเธเนเธเน€เธเธเธฒเธฐเธ—เธตเนเนเธกเนเนเธเน named destination",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "robot_id": {"type": "string"},
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "r": {"type": "number"}
                        },
                        "required": ["robot_id", "x", "y"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_parallel",
                    "description": "เธ—เธณเธเธฒเธเธเธฃเนเธญเธกเธเธฑเธเธ—เธฑเนเธเธชเธญเธเธ•เธฑเธง (Parallel)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "commands": {
                                "type": "string",
                                "description": "JSON string of list. Example: '[{\"function\": \"control_robot\", \"kwargs\": {\"robot_id\": \"Arm1\", \"action\": \"HOME\"}}]'"
                            }
                        },
                        "required": ["commands"]
                    }
                }
            }
        ]

    def get_available_functions(self):
        return {
            'get_runtime_context': self.get_runtime_context,
            'camera_status': self.camera_status,
            'camera_scan': self.camera_scan,
            'list_workspace_zones': self.list_workspace_zones,
            'list_storage_groups': self.list_storage_groups,
            'plan_intent': self.plan_intent,
            'list_storage_slots': self.list_storage_slots,
            'scan_storage_slots': self.scan_storage_slots,
            'resolve_storage_slot': self.resolve_storage_slot,
            'set_storage_policy': self.set_storage_policy,
            'camera_assign_targets': self.camera_assign_targets,
            'move_to_named_target': self.move_to_named_target,
            'set_tool_state': self.set_tool_state,
            'pick_detected_place': self.pick_detected_place,
            'pick_all_detected_place': self.pick_all_detected_place,
            'pick_at_pose': self.pick_at_pose,
            'place_at_named': self.place_at_named,
            'place_to_storage_slot': self.place_to_storage_slot,
            'place_at_pose': self.place_at_pose,
            'control_robot': self.control_robot,
            'move_to_coords': self.move_to_coords,
            'tool_control': self.tool_control,
            'get_robot_state': self.get_robot_state,
            'get_robot_pose': self.get_robot_pose,
            'recover_robot': self.recover_robot,
            'recover_and_home_robot': self.recover_and_home_robot,
            'execute_parallel': self.execute_parallel
        }

    # ==========================================
    # ๐“ก Stream Handler Logic
    # ==========================================
    def _stream_and_get_response(self):
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": MODEL,
            "messages": self.messages,
            "tools": self.get_api_tools(),
            "max_tokens": 1500,
            "temperature": 0.2,
            "stream": True,
        }

        run = current_run()
        request_cm = run.request() if run is not None else None
        try:
            if request_cm is not None:
                request_cm.__enter__()
            with requests.post(f"{BASE_URL}/v1/chat/completions", headers=headers, json=payload, stream=True, timeout=600) as resp:
                resp.raise_for_status()

                final_text_parts = []
                tool_calls_accumulator = {}
                
                reasoning_started = False
                answer_started = False

                for raw_line in resp.iter_lines(decode_unicode=True):
                    if not raw_line or not raw_line.startswith("data: "):
                        continue

                    data_str = raw_line[len("data: "):].strip()
                    if data_str == "[DONE]": break

                    chunk = json.loads(data_str)
                    choices = chunk.get("choices", [])
                    if not choices: continue

                    delta = choices[0].get("delta", {})

                    # --- 1. เนเธชเธ”เธ Reasoning (เธเธฒเธฃเธเธดเธ”) ---
                    reasoning_piece = delta.get("reasoning_content")
                    if reasoning_piece:
                        if not reasoning_started:
                            reasoning_started = True
                            print("\n๐ง  [เธเธณเธฅเธฑเธเธงเธดเน€เธเธฃเธฒเธฐเธซเน...]\n", end="", flush=True)
                        print(reasoning_piece, end="", flush=True)

                    # --- 2. เนเธชเธ”เธ เธเธณเธ•เธญเธ (Content) ---
                    content_piece = delta.get("content")
                    if content_piece:
                        if not answer_started:
                            answer_started = True
                            if reasoning_started:
                                print("\n\n๐’ฌ [เธเธณเธ•เธญเธ]\n", end="", flush=True)
                        print(content_piece, end="", flush=True)
                        final_text_parts.append(content_piece)

                    # --- 3. เธชเธฐเธชเธก Tool Calls เธ—เธตเนเธ–เธนเธเธซเธฑเนเธเธ•เธญเธ Stream ---
                    if "tool_calls" in delta:
                        for tc in delta["tool_calls"]:
                            idx = tc["index"]
                            if idx not in tool_calls_accumulator:
                                tool_calls_accumulator[idx] = {
                                    "id": tc.get("id", ""),
                                    "type": "function",
                                    "function": {"name": tc.get("function", {}).get("name", ""), "arguments": ""}
                                }
                            if "function" in tc and "arguments" in tc["function"]:
                                tool_calls_accumulator[idx]["function"]["arguments"] += tc["function"]["arguments"]

                print("\n", flush=True)
                return "".join(final_text_parts).strip(), list(tool_calls_accumulator.values())

        except Exception as e:
            print(f"\nโ API Error: {e}\n", flush=True)
            return None, None

        finally:
            if request_cm is not None:
                request_cm.__exit__(None, None, None)


    # ==========================================
    # ๐€ Main Logic Call
    # ==========================================
    def send_prompt(self, user_input: str):
        with CollectionRun(task_id="ai-fleet"):
            return self._send_prompt_body(user_input)

    def _send_prompt_body(self, user_input: str):
        print(f"\n๐‘ค User: {user_input}")
        self.messages.append({'role': 'user', 'content': user_input})
        
        # 1. เธชเนเธเธเธณเธชเธฑเนเธเธฃเธญเธเนเธฃเธเนเธซเน AI (เน€เธเธทเนเธญเธเธดเธ”เนเธฅเธฐเน€เธฃเธตเธขเธ Tools)
        final_text, tool_calls = self._stream_and_get_response()
        
        if final_text is None and tool_calls is None:
            return # เธญเธญเธเธ–เนเธฒ API Error

        # 2. เธเธฑเธเธ—เธถเธเธเธณเธ•เธญเธเธเธฅเธฑเธเธฅเธ Chat History
        ai_message = {'role': 'assistant'}
        if final_text: ai_message['content'] = final_text
        if tool_calls: ai_message['tool_calls'] = tool_calls
        self.messages.append(ai_message)

        # 3. เธ–เนเธฒ AI เธกเธตเธเธฒเธฃเธชเธฑเนเธเธเธฒเธเธซเธธเนเธเธขเธเธ•เน (Tool Calls) เนเธซเนเธ—เธณเธเธฒเธเธ—เธตเนเธเธตเน
        if tool_calls:
            available_functions = self.get_available_functions()
            
            for tc in tool_calls:
                func_name = tc['function']['name']
                args_str = tc['function']['arguments']
                
                print(f"โ๏ธ AI เธชเธฑเนเธเนเธเนเธเธฑเธเธเนเธเธฑเธ: {func_name}")
                
                # เธเธขเธฒเธขเธฒเธกเนเธเธฅเธ String เน€เธเนเธ Dictionary
                try: args = json.loads(args_str)
                except: args = args_str
                
                func_to_call = available_functions.get(func_name)
                if func_to_call:
                    try:
                        # เธชเธฑเนเธเธซเธธเนเธเธขเธเธ•เนเธเธขเธฑเธ!
                        if isinstance(args, dict): function_response = func_to_call(**args)
                        else: function_response = "Error: Invalid Arguments format"
                            
                        print(f"โ… เธเธฅเธฅเธฑเธเธเน: {function_response}")
                        
                        # เธเธฑเธเธ—เธถเธเธชเธ–เธฒเธเธฐเธงเนเธฒเธซเธธเนเธเธขเธเธ•เนเธ—เธณเน€เธชเธฃเนเธเนเธฅเนเธง
                        self.messages.append({
                            'role': 'tool',
                            'content': str(function_response),
                            'tool_call_id': tc['id'],
                            'name': func_name
                        })
                    except Exception as e:
                        print(f"โ Execution Error: {e}")
                        self.messages.append({'role': 'tool', 'content': f"Error: {str(e)}", 'tool_call_id': tc['id'], 'name': func_name})
            
            # 4. เน€เธกเธทเนเธญเธซเธธเนเธเธขเธเธ•เนเธ—เธณเน€เธชเธฃเนเธเธซเธกเธ” เธชเนเธเธเธฅเนเธซเน AI เธชเธฃเธธเธเธญเธตเธเธเธฃเธฑเนเธ
            print("๐” เนเธเนเธเธชเธ–เธฒเธเธฐเธเธฅเธฑเธเนเธซเน AI เธ—เธฃเธฒเธ...")
            summary_text, _ = self._stream_and_get_response()
            if summary_text:
                self.messages.append({'role': 'assistant', 'content': summary_text})






