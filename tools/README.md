# Tools

This directory groups calibration and diagnostics entry points without breaking
existing root-level imports and scripts.

Calibration tools:

- `python -m tools.calibration.grid_viewer` -> `calibrate_grid_viewer.py`
- `python -m tools.calibration.web` -> `calibration_web.py`
- `python -m tools.calibration.tuning` -> `calibration_tuning.py`
- `python -m tools.calibration.compute` -> `calibration_compute.py`

Diagnostics tools:

- `python -m tools.diagnostics.workspace_frame` -> `analyze_workspace_frame.py`
- `python -m tools.diagnostics.surface_z` -> `analyze_surface_z.py`
- `python -m tools.diagnostics.camera_block` -> `camera_block_diagnostic.py`
- `python -m tools.diagnostics.capture_workspace_frame` -> `capture_workspace_frame.py`
- `python -m tools.diagnostics.dual_arm_stop_test` -> `dual_arm_stop_test.py`
- `python -m tools.diagnostics.monitor_fault_test` -> `monitor_fault_test.py`

Compatibility notes:

- Root-level Python files remain the source of truth for imports and tests.
- Tool modules here are thin wrappers that execute the corresponding root script.
- `open_calibration.cmd` can launch the calibration web UI through the organized
  module entry point.
