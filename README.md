# ARC Robot Repo

This is the current IF-EDUBOT ARC repository snapshot. It combines three
shared-runtime systems: ARC core (S5), Vision Coordination (S2), and Robot
Tutor (S3). VoiceInput (S1) and Chatbot/RAG (S4) are separate repositories that
can connect through the bridge described in [docs/IF_EDUBOT_REPOSITORY.md](docs/IF_EDUBOT_REPOSITORY.md).

Read these before using the source:

- [Repository scope and source map](docs/IF_EDUBOT_REPOSITORY.md)
- [System boundaries](docs/SYSTEM_BOUNDARIES.md)
- [Safety and limits](docs/SAFETY_AND_LIMITS.md)
- [Setup and testing](docs/SETUP_AND_TESTING.md)

Primary runtime entrypoints remain at the repository root:

- `arc.py`
- `arc_service.py`

## Python Environment

Use one uv-managed venv at the repository root. Python 3.12 is pinned in
`.python-version` because the existing Windows venv was created with Python
3.12 and the camera/OpenCV stack is more predictable there than on newer
interpreter releases.

```bash
uv sync --locked
source .venv/bin/activate
```

On Windows PowerShell, activate with:

```powershell
.\.venv\Scripts\Activate.ps1
```

Then run ARC through the venv:

```bash
ARC_SIM_MODE=1 python arc.py camera --json scan
python -m unittest test_workspace_calibration.py test_object_calibration.py test_robot_guardrails.py test_calibration_web.py
```

Direct Python dependencies are declared in `pyproject.toml`: `numpy` and
`opencv-python-headless` for calibration, object detection, camera tools, and
the calibration/object tests; `fastapi`, `uvicorn[standard]`, and `pydantic`
for `arc_service.py`; and `requests` for the AI fleet manager. The active tests
use `unittest`, so no separate pytest dependency is required. The Hikvision
`MvImport` SDK used by live camera paths is not a PyPI package and must remain
installed/provided separately on robot hosts.

## Documentation

Project documentation is organized under [`docs/`](docs/README.md):

- [`docs/current/`](docs/current/) - current implementation and status docs
- [`docs/hardware/`](docs/hardware/) - hardware, calibration, and workspace references
- [`docs/plans/`](docs/plans/) - plans and session notes
- [`docs/operations/`](docs/operations/) - tooling and operational flow docs
- [`docs/archive/`](docs/archive/) - older concept/spec/history docs

Important compatibility locator:

- [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) -> [`docs/current/IMPLEMENTATION_STATUS.md`](docs/current/IMPLEMENTATION_STATUS.md)
