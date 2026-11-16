# ARC Robot Calibration Calculation Method

This reference documents the calibration method currently used in `PATH_TO_IF_EDUBOT_ARC`.

## 1. Workspace Perspective Calibration

The workspace detector finds the outer frame and stores a quadrilateral in:

```text
runtime_state/workspace_calibration.json
outer_box.corners_px
outer_box.width_mm
outer_box.height_mm
```

The perspective transform maps camera pixels into real workspace millimeters:

```text
pixel(x, y) -> workspace_xy(x_ws, y_ws)
```

This step corrects camera perspective and frame skew. Do not replace it with robot calibration; robot calibration starts after `workspace_xy` exists.

## 2. 9-Point Object Calibration

The user places round objects in the workspace, scans once, selects each object, manually moves `Arm1` to the physical object center, and saves the current pose.

Each calibration point stores:

```text
workspace_xy = object center in workspace mm
robot_pose.x/y = actual Arm1 pose at that physical center
```

The 9 points fit a least-squares affine transform:

```text
robot_x = a*x_ws + b*y_ws + tx
robot_y = c*x_ws + d*y_ws + ty
```

The active coefficients are stored in:

```text
robot.workspace_to_robot_transform
```

Current active 9-point fit quality:

```text
fit_mean_error_mm = 0.554
fit_max_error_mm = 1.179
fit_rms_error_mm = 0.630
```

This means the 9 saved calibration points are internally consistent. It does not guarantee all runtime object detections are perfect, because detector center bias, camera distortion, and hand-touched reference differences can still create runtime error.

## 3. Runtime Accuracy Test

For each test object:

1. Run scan, e.g.

```powershell
python arc.py camera --json scan Cam1 --color blue --selection first
```

2. Record predicted `robot_plane.x/y`.
3. Move `Arm1` manually to the real object center.
4. Read actual pose:

```powershell
python arc.py arm --json pose Arm1
```

5. Compute:

```text
dx = actual_x - predicted_x
dy = actual_y - predicted_y
error = sqrt(dx^2 + dy^2)
```

The measured points showed a position-dependent pattern, not a single global offset:

```text
blue_top:        dx -6.33, dy +3.87, error ~= 7.42 mm
blue_middle:     dx -1.01, dy +1.95, error ~= 2.19 mm
red_upper_left:  dx -8.57, dy -0.05, error ~= 8.57 mm
red_lower_right: dx -0.32, dy +4.39, error ~= 4.41 mm
```

Because the direction and magnitude change by location, do not solve this with a constant offset.

## 4. Robot-Plane Correction

The correction layer maps the first-stage prediction to the actual robot pose:

```text
predicted robot_plane -> corrected robot_plane
```

Fit form:

```text
corrected_x = a*x + b*y + tx
corrected_y = c*x + d*y + ty
```

Where `x,y` are the uncorrected robot-plane values from the 9-point affine.

Current active correction:

```text
a  = 0.9915865176806269
b  = 0.026214184655271033
tx = -1.2080882601703067
c  = -0.032543559437403315
d  = 1.0018286030112618
ty = 11.601000755963792
```

Current correction fit quality from 4 comparison points:

```text
before correction: mean ~= 5.65 mm, max ~= 8.57 mm, rms ~= 6.18 mm
after correction:  mean ~= 0.71 mm, max ~= 1.03 mm, rms ~= 0.75 mm
```

The correction is stored under:

```text
robot.workspace_to_robot_transform.robot_plane_correction
```

## 5. Runtime Behavior

`camera_controller._compute_workspace_robot_plane()` does this:

```text
uncorrected_robot_xy = apply_affine_transform(workspace_to_robot_transform, workspace_xy)
robot_xy = apply_robot_plane_correction(robot_plane_correction, uncorrected_robot_xy)
reach = reach_for_local_xy(robot_xy, robot)
```

The scan output includes:

```text
robot_plane.x/y = corrected target used for pick
uncorrected_robot_plane.x/y = first-stage value for debugging
robot_plane_correction = summary of applied correction
```

## 6. Validation Commands

Run syntax and unit tests after changes:

```powershell
python -m py_compile object_calibration.py calibration_web.py camera_controller.py test_object_calibration.py test_workspace_calibration.py test_robot_guardrails.py
python -m unittest test_object_calibration.py test_workspace_calibration.py test_robot_guardrails.py
```

Run a real scan and confirm `object_workspace_affine_corrected`:

```powershell
python arc.py camera --json scan Cam1 --color blue --selection first
```

## 7. When To Refit

Refit or extend correction when:

- New comparison points show errors above target tolerance.
- The camera moves.
- The outer frame moves.
- Object size/color/detector behavior changes.
- Error is good near the 4 comparison points but bad in untested corners.

Recommended next comparison points are `right-top` and `left-bottom`, because the first correction was fit from top/middle/upper-left/lower-right only.

If the error remains smoothly position-dependent, affine correction is acceptable. If it becomes local/nonlinear, move to polynomial or thin-plate correction using more comparison points.
