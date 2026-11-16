"""Local S2 standalone web application.

Run with ``python -m s2_vision_coordination.web`` and open the printed URL.
The browser owns plan editing; ARC owns live re-scan, safety checks, and robot
execution through the reusable FleetController.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .standalone import S2StandaloneController


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
_controller: S2StandaloneController | None = None


def controller() -> S2StandaloneController:
    global _controller
    if _controller is None:
        _controller = S2StandaloneController()
    return _controller


app = FastAPI(
    title="S2 Vision Planner",
    description="Standalone visual planning surface backed by ARC safety and execution",
    version="0.1.0",
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        current = controller()
        return {
            "ok": True,
            "service": "s2-vision-planner",
            "scope": "S2",
            "simulation_mode": current.simulation_mode,
            "active_arm_ids": list(current.active_arm_ids),
            "execution_backend": "ARC FleetController",
            "hardware_execution_enabled": current.simulation_mode
            or os.getenv("S2_ALLOW_HARDWARE_EXECUTE", "").strip().lower()
            in {"1", "true", "yes", "on"},
        }
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error_code": "startup_failed", "error": str(exc)},
        )


@app.get("/api/options")
def options(arm_id: str | None = None) -> dict[str, Any]:
    return controller().options(arm_id=arm_id)


@app.post("/api/scan")
def scan(request: dict[str, Any] | None = None) -> dict[str, Any]:
    request = request or {}
    result = controller().scan(
        camera_id=str(request.get("camera_id") or "Cam1"),
        mode=str(request.get("mode") or "snapshot"),
    )
    result["image_url"] = "/api/snapshot-image"
    return result


@app.get("/api/scan/latest")
def latest_scan() -> dict[str, Any]:
    current = controller()
    if current.last_scan is None:
        return {"ok": False, "error_code": "scan_required", "error": "No scan yet"}
    return {"ok": True, "scan": current.last_scan, "image_url": "/api/snapshot-image"}


@app.post("/api/plan")
def create_plan(request: dict[str, Any]) -> dict[str, Any]:
    return controller().create_plan(request.get("steps") or [])


@app.post("/api/plan/confirm")
def confirm_plan() -> dict[str, Any]:
    return controller().confirm_plan()


@app.get("/api/plan")
def current_plan() -> dict[str, Any]:
    current = controller().last_plan
    return {"ok": current is not None, "plan": current}


@app.post("/api/execute")
def execute_plan() -> dict[str, Any]:
    return controller().execute_plan()


@app.post("/api/plan/reset")
def reset_plan() -> dict[str, Any]:
    return controller().reset_plan()


@app.get("/api/state")
def state() -> dict[str, Any]:
    current = controller()
    fleet = current.fleet
    try:
        if current.simulation_mode and hasattr(fleet, "scene_state"):
            return fleet.scene_state()
        if hasattr(fleet, "arms_motion_state"):
            return fleet.arms_motion_state()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "arms": []}


@app.post("/api/sim/reset")
def reset_simulation() -> dict[str, Any]:
    current = controller()
    if not current.simulation_mode or not hasattr(current.fleet, "reset_scene"):
        return {"ok": False, "error_code": "simulation_only", "error": "Scene reset is available only in simulation"}
    current.reset_plan()
    return current.fleet.reset_scene()


@app.get("/api/snapshot-image")
def snapshot_image() -> FileResponse:
    current = controller()
    candidates: list[Path] = []
    # The displayed image must be the same frame ARC used to calculate
    # center_px. Simulation has no camera output, so it intentionally uses
    # the fixed simulator image as its first source.
    if current.simulation_mode:
        try:
            from arc_runtime.config import SIM_SCAN_IMAGE

            candidates.append(Path(SIM_SCAN_IMAGE))
        except Exception:
            pass
    else:
        outputs = (current.last_scan or {}).get("outputs") or {}
        for key in ("source_image", "image"):
            value = outputs.get(key)
            if value:
                candidates.append(Path(value))
        candidates.append(Path("runtime_state/s2_web/latest_scan.jpg"))

    candidates.extend(
        [
            Path("runtime_state/object_calibration/latest_scan_objects.jpg"),
            Path("artifacts/captures/scan_capture.png"),
        ]
    )
    for path in candidates:
        if path.exists() and path.is_file():
            media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            return FileResponse(path, media_type=media_type, filename=path.name)
    raise FileNotFoundError("No scan image is available")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="s2-assets")


def main() -> None:
    host = os.getenv("S2_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("S2_WEB_PORT", "8788"))
    print(f"S2 Vision Planner: http://{host}:{port}")
    print("Execution backend: ARC FleetController")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

