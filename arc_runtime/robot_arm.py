import socket
import time
import threading
import struct
import re


def _format_number(value):
    numeric = float(value)
    if numeric.is_integer():
        return str(int(numeric))
    return str(numeric)

# ==========================================
# 🤖 ROBOT CONTROL CLASS (DOBOT MG400)
# ==========================================
class MG400Robot:
    ROBOT_MODE_NAMES = {
        1: "init",
        2: "brake_open",
        3: "power_off",
        4: "disabled",
        5: "enabled",
        6: "backdrive",
        7: "running",
        8: "recording",
        9: "error",
        10: "pause",
        11: "jog",
    }

    def __init__(self, robot_id, ip, port):
        self.robot_id = robot_id
        self.ip = ip
        self.dashboard_port = port
        self.move_port = 30003
        self.feed_port = 30004
        self.dashboard_sock = None
        self.move_sock = None
        self.is_connected = False
        self.lock = threading.Lock()
        self.HOME_POS = "250,0,50,0"
        self.motion_timeout_s = 30.0
        self.motion_poll_interval_s = 0.1
        self.motion_start_distance_threshold_mm = 1.0
        self.motion_settle_polls = 3
        self.runtime_fault_distance_threshold_mm = 5.0
        self.motion_target_tolerance_mm = 1.0

    def connect(self):
        try:
            self.disconnect()
            self.dashboard_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.dashboard_sock.settimeout(5)
            self.dashboard_sock.connect((self.ip, self.dashboard_port))

            self.move_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.move_sock.settimeout(5)
            self.move_sock.connect((self.ip, self.move_port))
            self.is_connected = True
            
            print(f"[INFO] {self.robot_id} connecting to {self.ip}...")
              
            self.send_dashboard("ClearError()", wait_idle=False)
            time.sleep(0.1)
            self.send_dashboard("EnableRobot()", wait_idle=False)
            time.sleep(0.5)
            
            self.send_dashboard("User(0)", wait_idle=False)
            self.send_dashboard("Tool(0)", wait_idle=False)
            self.send_dashboard("SpeedL(50)", wait_idle=False)
            self.send_dashboard("AccL(50)", wait_idle=False)
            
            print(f"[OK] {self.robot_id} connected and enabled.")
            return True
        except Exception as e:
            print(f"[ERROR] {self.robot_id} connection error: {e}")
            self.is_connected = False
            return False

    def disconnect(self):
        if self.dashboard_sock:
            try:
                self.dashboard_sock.close()
            except:
                pass
        if self.move_sock:
            try:
                self.move_sock.close()
            except:
                pass
        self.dashboard_sock = None
        self.move_sock = None
        self.is_connected = False

    def get_status(self):
        return {
            "robot_id": self.robot_id,
            "ip": self.ip,
            "port": self.dashboard_port,
            "move_port": self.move_port,
            "feed_port": self.feed_port,
            "connected": self.is_connected,
        }

    def get_feedback_snapshot(self):
        packet_size = 1440
        magic_value = 0x123456789ABCDEF

        feed_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        feed_sock.settimeout(3)
        try:
            feed_sock.connect((self.ip, self.feed_port))
            packets = []
            for _ in range(2):
                data = b""
                while len(data) < packet_size:
                    chunk = feed_sock.recv(packet_size - len(data))
                    if not chunk:
                        break
                    data += chunk
                if len(data) == packet_size:
                    packets.append(data)

            if not packets:
                return {"ok": False, "error": "Incomplete feedback packet: got 0 bytes"}

            data = packets[-1]

            test_value = struct.unpack_from("<q", data, 48)[0]
            if test_value != magic_value:
                return {"ok": False, "error": f"Unexpected feedback magic: {hex(test_value)}"}

            robot_mode = struct.unpack_from("<q", data, 24)[0]
            joint_values = struct.unpack_from("<6d", data, 432)
            pose_values = struct.unpack_from("<6d", data, 624)

            brake_status = struct.unpack_from("<b", data, 1025)[0]
            enable_status = struct.unpack_from("<b", data, 1026)[0]
            running_status = struct.unpack_from("<b", data, 1028)[0]
            error_status = struct.unpack_from("<b", data, 1029)[0]

            return {
                "ok": True,
                "robot_mode": robot_mode,
                "robot_mode_name": self.ROBOT_MODE_NAMES.get(robot_mode, "unknown"),
                "enable_status": bool(enable_status),
                "running_status": bool(running_status),
                "error_status": bool(error_status),
                "brake_status": bool(brake_status),
                "joints": {
                    "j1": joint_values[0],
                    "j2": joint_values[1],
                    "j3": joint_values[2],
                    "j4": joint_values[3],
                    "j5": joint_values[4],
                    "j6": joint_values[5],
                },
                "pose": {
                    "x": pose_values[0],
                    "y": pose_values[1],
                    "z": pose_values[2],
                    "r": pose_values[3],
                    "rx": pose_values[4],
                    "ry": pose_values[5],
                },
            }
        except Exception as e:
            return {"ok": False, "error": f"Feedback read failed: {e}"}
        finally:
            try:
                feed_sock.close()
            except:
                pass

    def _pose_distance_mm(self, start_pose, current_pose):
        if not start_pose or not current_pose:
            return None
        dx = float(current_pose["x"]) - float(start_pose["x"])
        dy = float(current_pose["y"]) - float(start_pose["y"])
        dz = float(current_pose["z"]) - float(start_pose["z"])
        return (dx * dx + dy * dy + dz * dz) ** 0.5

    def _extract_absolute_target_pose(self, cmd):
        match = re.match(r"^(MovL|MovJ)\(([-0-9.,]+)\)$", cmd.strip())
        if not match:
            return None

        values = [part.strip() for part in match.group(2).split(",")]
        if len(values) != 4:
            return None

        return {
            "x": float(values[0]),
            "y": float(values[1]),
            "z": float(values[2]),
            "r": float(values[3]),
        }

    def _classify_motion_fault(self, saw_running_motion, max_deviation_mm):
        if saw_running_motion or max_deviation_mm >= self.runtime_fault_distance_threshold_mm:
            return {
                "fault_type": "runtime_fault",
                "recommended_action": "await_operator_decision",
            }
        return {
            "fault_type": "immediate_fault",
            "recommended_action": "auto_recover_allowed",
        }

    def _wait_for_motion_completion_inside_lock(self, cmd, command_response, start_feedback):
        started_at = time.time()
        start_pose = start_feedback.get("pose") if start_feedback.get("ok") else None
        absolute_target_pose = self._extract_absolute_target_pose(cmd)
        saw_running_motion = False
        max_deviation_mm = 0.0
        stable_idle_polls = 0
        last_feedback = start_feedback
        feedback_failures = []

        while time.time() - started_at <= self.motion_timeout_s:
            feedback = self.get_feedback_snapshot()
            if not feedback.get("ok"):
                feedback_failures.append(feedback["error"])
                time.sleep(self.motion_poll_interval_s)
                continue

            last_feedback = feedback
            deviation_mm = self._pose_distance_mm(start_pose, feedback["pose"])
            if deviation_mm is not None:
                max_deviation_mm = max(max_deviation_mm, deviation_mm)

            if feedback["running_status"] or max_deviation_mm >= self.motion_start_distance_threshold_mm:
                saw_running_motion = True

            if feedback["error_status"] or feedback["robot_mode_name"] == "error":
                fault = self._classify_motion_fault(saw_running_motion, max_deviation_mm)
                return {
                    "ok": False,
                    "status": "fault",
                    "command": cmd,
                    "command_response": command_response,
                    "start_state": start_feedback,
                    "final_state": feedback,
                    "saw_running_motion": saw_running_motion,
                    "max_deviation_mm": max_deviation_mm,
                    **fault,
                }

            if saw_running_motion:
                if not feedback["running_status"] and feedback["robot_mode_name"] == "enabled":
                    stable_idle_polls += 1
                    if stable_idle_polls >= self.motion_settle_polls:
                        return {
                            "ok": True,
                            "status": "completed",
                            "command": cmd,
                            "command_response": command_response,
                            "start_state": start_feedback,
                            "final_state": feedback,
                            "saw_running_motion": saw_running_motion,
                            "max_deviation_mm": max_deviation_mm,
                        }
                else:
                    stable_idle_polls = 0
            elif absolute_target_pose is not None:
                target_distance_mm = self._pose_distance_mm(absolute_target_pose, feedback["pose"])
                if target_distance_mm is not None and target_distance_mm <= self.motion_target_tolerance_mm:
                    stable_idle_polls += 1
                    if stable_idle_polls >= self.motion_settle_polls:
                        return {
                            "ok": True,
                            "status": "completed_noop",
                            "command": cmd,
                            "command_response": command_response,
                            "start_state": start_feedback,
                            "final_state": feedback,
                            "saw_running_motion": saw_running_motion,
                            "max_deviation_mm": max_deviation_mm,
                        }
                else:
                    stable_idle_polls = 0

            time.sleep(self.motion_poll_interval_s)

        return {
            "ok": False,
            "status": "timeout",
            "command": cmd,
            "command_response": command_response,
            "start_state": start_feedback,
            "final_state": last_feedback,
            "saw_running_motion": saw_running_motion,
            "max_deviation_mm": max_deviation_mm,
            "feedback_failures": feedback_failures,
            "error": "Timed out waiting for motion completion",
        }

    def _send_ascii_command(self, sock, cmd):
        full_cmd = f"{cmd}\r\n"
        sock.sendall(full_cmd.encode())
        return sock.recv(1024).decode().strip()

    def send_dashboard(self, cmd, wait_idle=True):
        if not self.is_connected or not self.dashboard_sock:
            if not self.connect():
                return f"Error: {self.robot_id} Disconnected"

        with self.lock:
            try:
                return self._send_ascii_command(self.dashboard_sock, cmd)
            except Exception as e:
                print(f"[WARN] {self.robot_id} send error: {e}")
                self.is_connected = False
                return f"Error: {e}"

    def send_motion(self, cmd, wait_idle=True):
        if not self.is_connected or not self.move_sock:
            if not self.connect():
                return f"Error: {self.robot_id} Disconnected"

        with self.lock:
            try:
                start_feedback = (
                    self.get_feedback_snapshot()
                    if wait_idle
                    else None
                )
                res = self._send_ascii_command(self.move_sock, cmd)

                if wait_idle:
                    return self._wait_for_motion_completion_inside_lock(
                        cmd,
                        res,
                        start_feedback or {"ok": False, "error": "No start feedback"},
                    )

                return res
            except Exception as e:
                print(f"[WARN] {self.robot_id} motion send error: {e}")
                self.is_connected = False
                return f"Error: {e}"

    def _wait_for_idle_inside_lock(self):
        try:
            self._send_ascii_command(self.dashboard_sock, "Sync()")
            time.sleep(0.05)
        except:
            pass

    def check_and_fix_status(self):
        self.send_dashboard("ClearError()", wait_idle=False)
        self.send_dashboard("EnableRobot()", wait_idle=False)

    def home(self):
        return self.send_motion(f"MovL({self.HOME_POS})")

    def move_relative(self, dx: float, dy: float, dz: float, dr: float = 0.0):
        return self.send_motion(f"RelMovL({dx},{dy},{dz},{dr})")

    def move_to_pose(self, x: float, y: float, z: float, r: float = 0.0):
        return self.send_motion(f"MovL({x},{y},{z},{r})")

    def move_joint_to_pose(self, x: float, y: float, z: float, r: float = 0.0):
        return self.send_motion(f"MovJ({x},{y},{z},{r})")

    def move_relative_joint(self, dx: float, dy: float, dz: float, dr: float = 0.0):
        return self.send_motion(f"RelMovJ({dx},{dy},{dz},{dr})")

    def arc_to_points(self, through_pose, target_pose):
        return self.send_motion(
            "Arc("
            f"{through_pose['x']},{through_pose['y']},{through_pose['z']},{through_pose.get('r', 0.0)},"
            f"{target_pose['x']},{target_pose['y']},{target_pose['z']},{target_pose.get('r', 0.0)}"
            ")"
        )

    def circle_to_points(self, through_pose, target_pose, count: int = 1):
        return self.send_motion(
            "Circle3("
            "{"
            f"{_format_number(through_pose['x'])},{_format_number(through_pose['y'])},{_format_number(through_pose['z'])},{_format_number(through_pose.get('r', 0.0))}"
            "},"
            "{"
            f"{_format_number(target_pose['x'])},{_format_number(target_pose['y'])},{_format_number(target_pose['z'])},{_format_number(target_pose.get('r', 0.0))}"
            "},"
            f"{int(count)}"
            ")"
        )

    def set_speed_j(self, value: float):
        return self.send_dashboard(f"SpeedJ({_format_number(value)})", wait_idle=False)

    def set_acc_j(self, value: float):
        return self.send_dashboard(f"AccJ({_format_number(value)})", wait_idle=False)

    def set_speed_l(self, value: float):
        return self.send_dashboard(f"SpeedL({_format_number(value)})", wait_idle=False)

    def set_acc_l(self, value: float):
        return self.send_dashboard(f"AccL({_format_number(value)})", wait_idle=False)

    def continue_queue(self):
        return self.send_dashboard("Continue()", wait_idle=False)

    def send_native_motion_command(self, command: str):
        return self.send_motion(command)

    def stop(self):
        response = self.send_dashboard("Stop()", wait_idle=False)
        if not str(response).startswith("0,{},"):
            fallback = self.send_dashboard("ResetRobot()", wait_idle=False)
            return f"Stop response: {response}; ResetRobot fallback: {fallback}"
        return response

    def recover(self):
        clear_res = self.send_dashboard("ClearError()", wait_idle=False)
        enable_res = self.send_dashboard("EnableRobot()", wait_idle=False)
        return {
            "clear_error": clear_res,
            "enable_robot": enable_res,
        }

    def set_tool_output(self, index: int, enable: bool):
        state = 1 if enable else 0
        return self.send_dashboard(f"DO({int(index)},{state})", wait_idle=False)

    def move_direction(self, action: str, step: float = 25.0):
        if action == "HOME":
            return self.send_motion(f"MovL({self.HOME_POS})")
        if action == "UP":
            return self.send_motion(f"RelMovL(0,0,{step},0)")
        if action == "DOWN":
            return self.send_motion(f"RelMovL(0,0,{-step},0)")
        if action == "LEFT":
            return self.send_motion(f"RelMovL(0,{step},0,0)")
        if action == "RIGHT":
            return self.send_motion(f"RelMovL(0,{-step},0,0)")
        if action == "FORWARD":
            return self.send_motion(f"RelMovL({step},0,0,0)")
        if action == "BACK":
            return self.send_motion(f"RelMovL({-step},0,0,0)")
        return f"Error: Invalid action '{action}'"
