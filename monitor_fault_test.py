import threading
import time

from config import ROBOT_FLEET_CONFIG
from fleet_controller import FleetController


ROBOT_ID = "Arm1"
STEP_MM = 25.0
POLL_INTERVAL_SEC = 0.2
STEP_SETTLE_SEC = 0.1
SEQUENCE = ["FORWARD", "LEFT", "BACK", "RIGHT"]


def motion_loop(controller, robot_id, stop_event, result_state):
    cycle = 0
    step_index = 0
    while not stop_event.is_set():
        action = SEQUENCE[step_index]
        result_state["last_command"] = f"{action} {STEP_MM}"
        result = controller.run_motion_step(robot_id, action, STEP_MM)
        result_state["last_motion_result"] = result
        result_state["cycle"] = cycle + 1
        result_state["step_index"] = step_index

        if not result.get("ok"):
            result_state["motion_error"] = result
            stop_event.set()
            break

        step_index = (step_index + 1) % len(SEQUENCE)
        if step_index == 0:
            cycle += 1

        time.sleep(STEP_SETTLE_SEC)


def monitor_loop(controller, robot_id, stop_event, result_state):
    start_pose = None
    max_deviation = 0.0
    saw_running_motion = False

    while not stop_event.is_set():
        state = controller.get_arm_state(robot_id)
        result_state["last_state"] = state

        if not state.get("ok"):
            result_state["monitor_error"] = state
            print(f"[MONITOR] state read failed: {state}")
            stop_event.set()
            break

        arm_state = state["state"]
        pose = arm_state["pose"]
        if start_pose is None:
            start_pose = dict(pose)
            result_state["start_pose"] = start_pose

        dx = float(pose["x"]) - float(start_pose["x"])
        dy = float(pose["y"]) - float(start_pose["y"])
        dz = float(pose["z"]) - float(start_pose["z"])
        deviation = (dx * dx + dy * dy + dz * dz) ** 0.5
        max_deviation = max(max_deviation, deviation)
        result_state["max_deviation_mm"] = max_deviation

        if arm_state["running_status"] and deviation >= controller.IMMEDIATE_FAULT_DISTANCE_THRESHOLD_MM:
            saw_running_motion = True
            result_state["saw_running_motion"] = True

        print(
            f"[MONITOR] mode={arm_state['robot_mode_name']} "
            f"running={arm_state['running_status']} "
            f"error={arm_state['error_status']} "
            f"pose=({pose['x']:.2f}, {pose['y']:.2f}, {pose['z']:.2f}, {pose['r']:.2f})"
        )

        if arm_state["error_status"] or arm_state["robot_mode_name"] == "error":
            fault_info = controller.classify_fault(start_pose, pose)
            if saw_running_motion or max_deviation >= controller.IMMEDIATE_FAULT_DISTANCE_THRESHOLD_MM:
                fault_info = {
                    "fault_type": "runtime_fault",
                    "travel_distance_mm": max_deviation,
                    "recommended_action": "await_operator_decision",
                }
            incident = {
                "robot_id": robot_id,
                "cycle": result_state.get("cycle", 0),
                "last_command": result_state.get("last_command"),
                "start_pose": start_pose,
                "fault_pose": dict(pose),
                "max_deviation_mm": max_deviation,
                "robot_mode_name": arm_state["robot_mode_name"],
                "running_status": arm_state["running_status"],
                "error_status": arm_state["error_status"],
                "fault_type": fault_info["fault_type"],
                "travel_distance_mm": fault_info["travel_distance_mm"],
                "recommended_action": fault_info["recommended_action"],
            }
            print(f"[ALERT] fault detected: {incident}")
            result_state["fault_state"] = state
            result_state["incident"] = incident
            stop_event.set()
            break

        time.sleep(POLL_INTERVAL_SEC)


def main():
    controller = FleetController(ROBOT_FLEET_CONFIG)
    stop_event = threading.Event()
    result_state = {
        "cycle": 0,
        "last_command": None,
    }

    motion_thread = threading.Thread(
        target=motion_loop, args=(controller, ROBOT_ID, stop_event, result_state), daemon=True
    )
    monitor_thread = threading.Thread(
        target=monitor_loop, args=(controller, ROBOT_ID, stop_event, result_state), daemon=True
    )

    try:
        print(f"[TEST] starting monitored loop on {ROBOT_ID} with {STEP_MM} mm square pattern")
        motion_thread.start()
        monitor_thread.start()

        while not stop_event.is_set():
            time.sleep(0.1)

        motion_thread.join(timeout=5)
        monitor_thread.join(timeout=5)

        print(f"[TEST] completed cycles: {result_state.get('cycle', 0)}")
        if "incident" in result_state:
            print(f"[TEST] incident report: {result_state['incident']}")
        elif "fault_state" in result_state:
            print(f"[TEST] detected fault state: {result_state['fault_state']}")
        elif "motion_error" in result_state:
            print(f"[TEST] motion error: {result_state['motion_error']}")
        elif "monitor_error" in result_state:
            print(f"[TEST] monitor error: {result_state['monitor_error']}")
        else:
            print(f"[TEST] stopped without fault: {result_state.get('last_state')}")
    finally:
        controller.disconnect_all()


if __name__ == "__main__":
    main()
