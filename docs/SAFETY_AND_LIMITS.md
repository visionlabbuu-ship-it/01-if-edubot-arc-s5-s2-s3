# Safety and Limits

The runtime safety boundary is implemented in the Python layer. Relevant files
include `robot_guardrails.py`, `fleet_controller.py`, `semantic_plan.py`, and
`task_system.py`.

The documented checks include intent/plan validation, minimum and dynamic reach,
workspace bounds, shared-zone conflict checks, conservative handling of missing
robot pose, cancellation, stop/recovery, and rejection of unparseable native
motion commands.

Development defaults should use `ARC_SIM_MODE=1`. Hardware mode requires an
explicit operator decision and a verified robot workspace. Do not place robot
credentials, provider keys, calibration captures, or live logs in Git.

Passing a unit or simulator test demonstrates software behavior only. It does
not establish collision-free hardware execution, accuracy, latency, autonomy,
or learning outcomes.
