export const ROBOT_ARM_TOOL_NAME = 'RobotArm'

export const DESCRIPTION = `Legacy fallback for basic MG400 robot arm actions through the existing ARC Python runtime.

Prefer the dedicated RobotControl tools when available:
- preflight_pose / preflight_task for safety checks
- camera_scan and pick/place tools for camera-aware grasp workflows
- move_to_pose, move_joint_to_pose, move_to_named_target, run_path, and run_task for explicit robot control

Supported actions:
- list
- status
- state
- pose
- home
- move
- square
- stop
- recover
- recover_home

Contract rules:
- Use this legacy tool only for simple list/status/state/pose/home/move/square/stop/recover actions.
- The "home" action is motion-only. It moves the arm to its configured shared home destination and does not pick, place, or toggle suction.
- If the user asks to pick an object and then place it at home, do not use the plain "home" action. Use a grasp workflow tool instead.

Safety rules:
- Read state or pose before motion when the situation is unclear.
- Prefer preflight_pose or preflight_task before new explicit poses or tasks.
- If a result contains guardrail_blocked=true or fault_type=guardrail_blocked, treat it as a planned safety stop and do not retry the same command.
- Treat stop, recover, and recover_home as operator-level actions.
- Runtime faults must be reported, not silently retried.
- If a motion command returns runtime_fault, saw_running_motion=true, or recommended_action=await_operator_decision, treat it as a safety incident.
- For a safety incident, do not autonomously recover, home, retry, or continue. Immediately tell the user the robot faulted during motion and wait for explicit operator instruction.`
