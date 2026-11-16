import threading
import time

from config import ROBOT_FLEET_CONFIG
from fleet_controller import FleetController


STEP_MM = 25.0
ARM1_STOP_AFTER_SEC = 10.0
ARM2_STOP_DELAY_SEC = 3.0


def loop_square(controller, robot_id, stop_event, results):
    while not stop_event.is_set():
        result = controller.trace_square(robot_id, STEP_MM)
        results[robot_id].append(result)
        if not result.get("ok"):
            stop_event.set()
            break


def main():
    controller = FleetController(ROBOT_FLEET_CONFIG)
    arm1_stop = threading.Event()
    arm2_stop = threading.Event()
    results = {"Arm1": [], "Arm2": []}

    arm1_thread = threading.Thread(
        target=loop_square, args=(controller, "Arm1", arm1_stop, results), daemon=True
    )
    arm2_thread = threading.Thread(
        target=loop_square, args=(controller, "Arm2", arm2_stop, results), daemon=True
    )

    try:
        arm1_thread.start()
        arm2_thread.start()

        time.sleep(ARM1_STOP_AFTER_SEC)
        arm1_stop.set()
        arm1_stop_result = controller.stop_arm("Arm1")
        print(f"[TEST] Arm1 stop result: {arm1_stop_result}")

        time.sleep(ARM2_STOP_DELAY_SEC)
        arm2_stop.set()
        arm2_stop_result = controller.stop_arm("Arm2")
        print(f"[TEST] Arm2 stop result: {arm2_stop_result}")

        arm1_thread.join(timeout=10)
        arm2_thread.join(timeout=10)

        print(f"[TEST] Arm1 loop count: {len(results['Arm1'])}")
        print(f"[TEST] Arm2 loop count: {len(results['Arm2'])}")
    finally:
        controller.disconnect_all()


if __name__ == "__main__":
    main()
