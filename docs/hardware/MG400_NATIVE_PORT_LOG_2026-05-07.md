# MG400 Native Port Log

Date: 2026-05-07

## Goal

Port the MG400 control runtime closer to Dobot's native controller model so motion behaves more like the real robot instead of treating all commands as generic dashboard traffic.

## Changes made

- Split low-level sockets in [robot_arm.py](PATH_TO_IF_EDUBOT_ARC\robot_arm.py:1):
  - dashboard socket on `29999`
  - motion socket on `30003`
  - feedback socket on `30004`
- Kept monitoring logic for completion/fault tracking after motion submission.
- Switched these motions to send on the native motion socket:
  - `MovL`
  - `RelMovL`
  - `MovJ`
  - `RelMovJ`
- Added new low-level methods:
  - `move_joint_to_pose`
  - `move_relative_joint`
  - `send_native_motion_command`
- Added new fleet-level wrappers in [fleet_controller.py](PATH_TO_IF_EDUBOT_ARC\fleet_controller.py:1):
  - `move_joint_to_pose_arm`
  - `move_relative_joint_arm`
  - `send_native_motion_arm`
- Added new CLI commands in [arc.py](PATH_TO_IF_EDUBOT_ARC\arc.py:1):
  - `move-joint-pose`
  - `move-relative-joint`
  - `native-motion`

## Real hardware proof

Executed:

```powershell
python arc.py arm move-joint-pose Arm1 --x 250 --y 0 --z 50 --r 0
```

Observed:

- controller returned success for native motion command on the motion path
- command string accepted:

```text
MovJ(250.0,0.0,50.0,0.0)
```

- controller response:

```text
0,{},MovJ(250.0,0.0,50.0,0.0);
```

- runtime tracked the motion from start to completion using feedback
- final pose reached approximately:
  - `x = 249.993`
  - `y = 0.002`
  - `z = 49.996`

Also executed successfully:

```powershell
python arc.py arm --json arc Arm1 --through-x 230 --through-y 20 --through-z 50 --through-r 0 --target-x 210 --target-y 0 --target-z 50 --target-r 0
```

Observed:

- native `Arc(...)` was accepted on the real controller
- command response:

```text
0,{},Arc(230.0,20.0,50.0,0.0,210.0,0.0,50.0,0.0);
```

- runtime tracked the move to completion

Also executed successfully:

```powershell
python arc.py arm --json circle Arm1 --radius 20 --count 1
```

Observed:

- full circle now runs as `2` native `Arc(...)` segments
- first arc moves from the current start point to the far side of the circle
- second arc returns to the start point
- this completed successfully on real hardware

## Important compatibility note

- `Circle3(...)` was tested experimentally and did not execute successfully on this controller/firmware.
- It returned a non-success style response and no real motion completion.
- Because of that, the runtime now uses `Arc + Arc` as the working native full-circle implementation for this MG400 setup.

## What this proves

- Native motion submission on port `30003` works on the real MG400 setup in this workspace.
- `MovJ` is now available as a first-class monitored primitive in our runtime.
- `Arc` is available as a first-class monitored primitive in our runtime.
- Native full-circle motion is now implemented with `2` hardware arcs instead of waypoint stepping.
- We no longer have to route all motion through the dashboard socket model.

## What is still missing

- direct controller-native `Circle3` support on this firmware, if it exists in a usable form
- deeper motion parameter surface for continuity options beyond the current speed/acc wrappers
- ARC tool-layer exposure for the new native commands beyond the existing `run_path(circle)` path
- broader controller-specific validation for other curved-path syntaxes and options

## Recommended next step

- let ARC test against the new runtime
- observe whether `run_path(circle)` now feels smooth enough in operator testing
- if needed, add radius/plane/direction/task abstractions on top of the working `arc-pair` implementation
