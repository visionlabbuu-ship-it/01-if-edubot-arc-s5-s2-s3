# ARC environment setup report - 2026-06-29

## Python

The repo now has one uv-managed Python environment:

- `.python-version`: `3.12`
- `pyproject.toml`: root dependency metadata
- `uv.lock`: locked dependency graph
- `.venv/`: Linux/WSL virtualenv created by `uv sync --python 3.12`

The previous `.venv/` was a Windows-style venv with `Scripts/` and no
`.venv/bin/python`, so it could not be used from WSL. `uv sync --python 3.12`
recreated it as a WSL/Linux venv.

Direct dependencies:

- `numpy`
- `opencv-python-headless`
- `fastapi`
- `uvicorn[standard]`
- `pydantic`
- `requests`

Verification:

```text
uv sync --locked
.venv/bin/python -m unittest test_workspace_calibration.py test_object_calibration.py test_robot_guardrails.py test_calibration_web.py
74 tests passed, 1 skipped
```

Also passed:

```text
ARC_SIM_MODE=1 .venv/bin/python arc.py camera --json scan
ARC_SIM_MODE=1 .venv/bin/python arc.py arm --json list
ARC_SIM_MODE=1 .venv/bin/python arc.py workspace --json state
.venv/bin/python -m py_compile arc.py arc_service.py *.py arc_runtime/*.py tools/*.py tools/calibration/*.py tools/diagnostics/*.py
```

`arc_service.py` now imports successfully inside the venv.

## Bun

Bun is installed user-locally:

```text
~/.bun/bin/bun
~/.local/bin/bun -> ~/.bun/bin/bun
bun 1.3.14
```

The official installer could not run directly because the WSL environment lacks
the `unzip` system package and `sudo apt-get` could not authenticate in this
non-interactive shell. Bun was installed by downloading the official Linux zip
and extracting it with Python's standard `zipfile` module.

## External typecheck

`bun run typecheck` now starts correctly, but it fails on existing TypeScript
errors inside `external/Agent-Robotic-Cluster`.

Representative failures:

- missing internal modules such as `./services/compact/snipProjection.js`
- missing exports from `types/message.js` and `types/tools.js`
- missing package/type declarations such as `@anthropic-ai/claude-agent-sdk`
- many existing strict type errors in tests and UI command files

This means the remaining failure is no longer an environment setup blocker.
The external TypeScript tree itself needs a separate fix/pass if typecheck must
be green.
