---
name: robot-calibration-correction
description: Use when working in the PATH_TO_IF_EDUBOT_ARC ARC project on camera-to-workspace calibration, 9-point object calibration, workspace-to-Arm1 robot-plane mapping, residual robot-plane correction, or diagnosing pick accuracy patterns from predicted-vs-actual robot poses.
---

# Robot Calibration Correction

Use this skill for the `PATH_TO_IF_EDUBOT_ARC` ARC calibration workflow that maps camera detections to accurate `Arm1` pick coordinates.

## Current Contract

The runtime coordinate pipeline is:

```text
camera pixel
-> workspace_xy mm using outer_box perspective transform
-> robot_plane using 9-point object calibration affine
-> corrected robot_plane using manual point-comparison correction
```

The active calibration lives in:

```text
runtime_state/workspace_calibration.json
robot.workspace_to_robot_transform
```

When correction is active, `camera_controller.scan_workspace()` should output:

```text
calibration = object_workspace_affine_corrected
uncorrected_robot_plane = value before correction
robot_plane = corrected value used for pick/move
```

## Workflow

1. Keep `outer_box` calibration as the source for `pixel -> workspace_xy`.
2. Use object calibration with 9 round-object points to fit:

```text
robot_x = a*x_ws + b*y_ws + tx
robot_y = c*x_ws + d*y_ws + ty
```

3. Test several real objects by scanning, reading the predicted `robot_plane`, manually moving `Arm1` to the physical object center, and reading actual pose.
4. If errors form a pattern by workspace position, fit a second correction from predicted robot plane to actual robot pose:

```text
corrected_x = a*x + b*y + tx
corrected_y = c*x + d*y + ty
```

5. Store that correction under `robot.workspace_to_robot_transform.robot_plane_correction`.
6. Re-scan and verify the target reports `object_workspace_affine_corrected`.

## Important Local Files

- `object_calibration.py`: fit/apply 9-point affine and robot-plane correction helpers.
- `camera_controller.py`: applies correction during object scan.
- `runtime_state/workspace_calibration.json`: active calibration and correction coefficients.
- `calirate_solution.md`: human-readable project notes.
- `test_object_calibration.py`: regression tests for fit/apply behavior.

## Detailed Reference

Read [references/calculation-method.md](references/calculation-method.md) when changing formulas, re-fitting correction points, or explaining why the current result became accurate.
