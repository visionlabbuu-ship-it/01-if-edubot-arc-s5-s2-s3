# Setup and Testing

## Python runtime

The repository pins Python 3.12 and declares dependencies in `pyproject.toml`.
Use a local environment outside the committed tree:

```powershell
uv sync --locked
.\.venv\Scripts\Activate.ps1
```

For simulator checks:

```powershell
$env:ARC_SIM_MODE = "1"
python arc.py camera --json scan
```

Run focused Python checks with:

```powershell
python -m unittest test_workspace_calibration.py test_object_calibration.py test_robot_guardrails.py test_calibration_web.py
```

The S2 module also has `test_s2_vision_coordination.py`. Robot Tutor tests are
under `modules/teacher/tests/`.

## TypeScript agent and bridge

The TypeScript project under `external/Agent-Robotic-Cluster/` and the bridge
under `bridge/` need their own package-manager setup. Do not commit
`node_modules/`; use the lockfiles to recreate dependencies.

## Hardware prerequisites

Live camera paths may require the vendor Hikvision `MvImport` SDK, robot network
access, and verified calibration. These are deployment prerequisites, not bundled
GitHub assets.
