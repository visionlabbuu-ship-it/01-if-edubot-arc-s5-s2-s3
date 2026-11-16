# ARC repo restructure report - 2026-06-29

## Safety baseline

- Project root: `/mnt/c/Users/kuk_g/Downloads/2 robot`
- Git was not usable: `.git/` exists but `git status` reports that this is not a repository.
- Baseline smoke commands passed before restructure:
  - `ARC_SIM_MODE=1 python3 arc.py camera --json scan`
  - `ARC_SIM_MODE=1 python3 arc.py arm --json list`
  - `ARC_SIM_MODE=1 python3 arc.py workspace --json state`

Backup attempts:

- Directory copy attempts were started under `/mnt/c/Users/kuk_g/Downloads/2_robot_backups/`, but the Windows filesystem copy did not complete `runtime_state/` before hanging.
- A source checksum manifest was created at `/mnt/c/Users/kuk_g/Downloads/2_robot_backups/2_robot_20260629_141139_source_manifest.sha256`.
- Critical runtime/calibration state was copied to `/mnt/c/Users/kuk_g/Downloads/2_robot_backups/critical_state_20260629_141120`.

Protected state checksums after restructure:

```text
c8a2f83fb2daa45498863ba35270b77c2cba82f43bf23ae8d70c607ce3a5d582  runtime_state/workspace_calibration.json
b4cbca242087141ee51bd06077351d7e0a2b256e633782c8e66e15a9fa6fee68  runtime_state/manual_calibration_geometry.json
5d6080b1eee3c2f1e919837e08d36e954722d6d2afadbb71c89039058dc09214  runtime_state/world_state.json
d8fcc32d844ffb9e22c4c8530f52a5b5048e950e0375a8c4fcd64402b4d08ac6  runtime_state/sim_mode.json
fe745ffef6edc6ac09921ffecb608db99a496e6568ccec5f59831b905219caa3  new_calibration.json
fd0704c8d188009e844a9a861952179cbe952bfdfb42b93ada5cd53fec11d621  calibration_pairs.json
```

## Runtime package

Python runtime modules now live under `arc_runtime/`.

Root compatibility entrypoints remain:

- `arc.py` imports and runs `arc_runtime.cli.main`.
- `arc_service.py` imports `arc_runtime.service.app` and `arc_runtime.service.main`.

Root compatibility module wrappers remain for existing imports, including:

- `config.py`
- `camera_controller.py`
- `fleet_controller.py`
- `workspace_calibration.py`
- `workspace_model.py`
- `object_calibration.py`
- simulator, guardrail, task, plan, and support modules

Centralized runtime paths:

- `arc_runtime/state_paths.py`
- `ARC_RUNTIME_STATE_DIR` can override the runtime-state directory.
- `ARC_PY_PATH` can override the root CLI path used by background workers.
- Default sim scan image now resolves to `artifacts/captures/scan_capture.png`.

## Documentation

Root documentation was organized into:

- `docs/current/`
- `docs/archive/`
- `docs/hardware/`
- `docs/plans/`
- `docs/operations/`

Root docs entrypoints:

- `README.md`
- `IMPLEMENTATION_STATUS.md` compatibility stub pointing to `docs/current/IMPLEMENTATION_STATUS.md`
- `docs/README.md`

## Artifacts and runtime state

Generated root images/reports moved to:

- `artifacts/captures/`
- `artifacts/diagnostics/`

`runtime_state/` remains in place. Critical state directories were not moved:

- `runtime_state/object_calibration/`
- `runtime_state/local_object_calibration/`
- `runtime_state/incidents/`

Policy files added:

- `runtime_state/README.md`
- `artifacts/README.md`
- `.gitignore`

## Tools

Root calibration and diagnostic scripts remain for compatibility.

New wrapper entrypoints were added under:

- `tools/calibration/`
- `tools/diagnostics/`

`open_calibration.cmd` now launches the calibration web tool through `python -m tools.calibration.web`.

## Bridge and external launcher

`bridge/arc-bridge.mjs` already resolved the repo root and honored overrides.

Updated:

- `external/Agent-Robotic-Cluster/bin/arc`

The launcher now defaults `ARC_ROBOT_WORKSPACE` from the launcher location instead of a hard-coded Windows path. Existing overrides still win:

- `ARC_ROBOT_WORKSPACE`
- `ARC_BRIDGE_BIN`
- `ARC_RUNTIME_MODE`

## Verification

Passed:

- `python3 -m py_compile arc.py arc_service.py *.py arc_runtime/*.py tools/*.py tools/calibration/*.py tools/diagnostics/*.py`
- `ARC_SIM_MODE=1 python3 arc.py camera --json scan`
- `ARC_SIM_MODE=1 python3 arc.py arm --json list`
- `ARC_SIM_MODE=1 python3 arc.py workspace --json state`
- `ARC_SIM_MODE=1 python3 -m arc_runtime.cli arm --json list`
- `python3 -m unittest test_robot_guardrails.py`
- `cd bridge && npm test`

Not fully runnable in this environment:

- Full calibration/object unit suite needs `numpy` and `cv2`; `/usr/bin/python3` here has neither.
- `arc_service.py` import needs `fastapi`, which is not installed here.
- `bun run typecheck` for `external/Agent-Robotic-Cluster` was skipped because `bun` is not installed.

## Restore notes

For protected runtime state, copy files back from:

`/mnt/c/Users/kuk_g/Downloads/2_robot_backups/critical_state_20260629_141120`

The critical backup contains root calibration JSON files and preserved runtime-state directories. The source checksum manifest can be used to audit source-file drift, but the broad directory/archive backup attempts were not completed successfully on this filesystem.
