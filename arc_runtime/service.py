"""
ARC Robot Service โ€” Long-lived HTTP server wrapper for robot control

This service keeps camera and arm controllers open, avoiding the overhead
of spawning Python + importing numpy/cv2 + reconnecting hardware on every command.

Safety: Preserves all guardrails. Adds concurrency locks per arm to prevent
simultaneous move commands on the same hardware.

Usage:
    python arc_service.py

Then point ARC CLI to it with: set ARC_RUNTIME_MODE=service
"""

import asyncio
import sys
import traceback
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# Import actual robot controllers
from .camera_controller import CameraController
from .config import CAMERA_CONFIG, ARM_REACH_ZONES, ROBOT_FLEET_CONFIG, active_robot_ids
from .fleet_controller import FleetController
from .storage_service import load as load_storage, list_slots as list_storage_slots, policy as storage_policy, resolve as resolve_storage, set_policy as set_storage_policy, scan as scan_storage, update_occupancy, set_verification_pending
from .tool_context import build_tool_context
from .workspace_model import (
    build_workspace_state,
    build_workspace_layout,
    build_pick_place_plan,
    build_handoff_plan,
)

app = FastAPI(
    title="ARC Robot Service",
    description="Long-lived HTTP wrapper for robot control",
    version="1.0.0",
)

# Persistent controllers (opened once on startup)
camera_controller: Optional[CameraController] = None
fleet_controller: Optional[FleetController] = None

# Concurrency locks โ€” one per arm to prevent simultaneous moves
arm_locks: Dict[str, asyncio.Lock] = {}


def get_arm_lock(arm_id: str) -> asyncio.Lock:
    """Get or create lock for an arm to serialize motion commands."""
    if arm_id not in arm_locks:
        arm_locks[arm_id] = asyncio.Lock()
    return arm_locks[arm_id]


# === Pydantic request models ===


class ScanRequest(BaseModel):
    camera_id: str = "Cam1"
    color: str = "any"
    selection: str = "first"


class MoveRequest(BaseModel):
    arm_id: str
    action: Optional[str] = None  # "HOME", "UP", "DOWN", etc.
    step: Optional[float] = None
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    r: Optional[float] = None


class StopRequest(BaseModel):
    arm_id: str


class RecoverRequest(BaseModel):
    arm_id: str


class StorageResolveRequest(BaseModel):
    reference: Optional[str] = None
    color: Optional[str] = None
    robot_id: Optional[str] = None
    arm_id: Optional[str] = None  # compatibility
    require_empty: bool = True


class StoragePlaceRequest(BaseModel):
    robot_id: str
    reference: Optional[str] = None
    color: Optional[str] = None


class StoragePolicyRequest(BaseModel):
    color_groups: Dict[str, Any] = {}
    default_group: Optional[str] = None
    selection_order: str = "index_ascending"


class StorageOccupancyRequest(BaseModel):
    occupancy: str
    held_object: Optional[Dict[str, Any]] = None
    source: str = "runtime"
    verification: Optional[Dict[str, Any]] = None

class GripRequest(BaseModel):
    arm_id: str
    enable: bool  # True = on, False = off


# === Lifecycle ===


@app.on_event("startup")
async def startup():
    """Connect to hardware on startup. Service stays connected."""
    global camera_controller, fleet_controller

    print("ARC Service starting...")

    try:
        camera_controller = CameraController(CAMERA_CONFIG)
        camera_controller.connect()
        print("โ“ Camera controller connected")
    except Exception as e:
        print(f"โ  Camera connection failed: {e}")
        print("  (Service will return errors on camera requests)")
        camera_controller = CameraController(CAMERA_CONFIG)  # partial init

    try:
        fleet_controller = FleetController(ROBOT_FLEET_CONFIG)
        fleet_controller.connect_all()
        print("โ“ Fleet controller connected")
    except Exception as e:
        print(f"โ  Fleet connection failed: {e}")
        print("  (Service will return errors on arm requests)")
        fleet_controller = FleetController(ROBOT_FLEET_CONFIG)

    print("ARC Service ready on http://127.0.0.1:8765")


@app.on_event("shutdown")
async def shutdown():
    """Disconnect on shutdown."""
    if camera_controller:
        try:
            camera_controller.disconnect()
            print("โ“ Camera disconnected")
        except Exception as e:
            print(f"โ  Camera disconnect error: {e}")

    if fleet_controller:
        try:
            fleet_controller.disconnect_all()
            print("โ“ Fleet disconnected")
        except Exception as e:
            print(f"โ  Fleet disconnect error: {e}")

    print("ARC Service shut down")


# === Endpoints ===


@app.get("/health")
def health():
    """Health check โ€” always returns 200 if service is running."""
    return {
        "ok": True,
        "service": "arc-robot",
        "camera_connected": camera_controller is not None,
        "fleet_connected": fleet_controller is not None,
    }


@app.post("/scan")
async def scan(req: ScanRequest):
    """
    Scan workspace for objects.

    Returns ScanResult (see robot_contract.md)
    """
    if not camera_controller:
        raise HTTPException(status_code=503, detail="Camera controller not initialized")

    try:
        result = camera_controller.scan_workspace(
            camera_id=req.camera_id,
            color=req.color,
            selection=req.selection,
        )
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Scan failed: {str(e)}")


@app.get("/state")
async def get_state():
    """
    Get current robot state (all arms).

    Returns RobotState (see robot_contract.md)
    """
    if not fleet_controller:
        raise HTTPException(status_code=503, detail="Fleet controller not initialized")

    try:
        result = build_workspace_state(
            lambda arm_id: fleet_controller.get_arm_pose(arm_id)
        )
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"State failed: {str(e)}")


@app.post("/move")
async def move(req: MoveRequest):
    """
    Move arm.

    Two modes:
    1. Named action: { arm_id, action, step }
    2. Explicit pose: { arm_id, x, y, z, r }

    Uses per-arm lock to prevent concurrent moves on same arm.
    """
    if not fleet_controller:
        raise HTTPException(status_code=503, detail="Fleet controller not initialized")

    # Acquire arm lock to serialize motion commands
    lock = get_arm_lock(req.arm_id)
    async with lock:
        try:
            if req.action:
                # Named action mode
                result = fleet_controller.move_arm(
                    req.arm_id, req.action, req.step or 25.0
                )
            elif req.x is not None and req.y is not None and req.z is not None:
                # Explicit pose mode
                result = fleet_controller.move_to_pose_arm(
                    req.arm_id, req.x, req.y, req.z, req.r or 0.0
                )
            else:
                raise ValueError("Must provide either action or x/y/z")

            return result
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Move failed: {str(e)}")


@app.post("/stop")
async def stop(req: StopRequest):
    """Stop arm motion immediately."""
    if not fleet_controller:
        raise HTTPException(status_code=503, detail="Fleet controller not initialized")

    try:
        result = fleet_controller.stop_arm(req.arm_id)
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Stop failed: {str(e)}")


@app.post("/recover")
async def recover(req: RecoverRequest):
    """Clear error and re-enable arm."""
    if not fleet_controller:
        raise HTTPException(status_code=503, detail="Fleet controller not initialized")

    try:
        result = fleet_controller.recover_arm(req.arm_id)
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Recover failed: {str(e)}")


@app.post("/grip")
async def grip(req: GripRequest):
    """Set tool state (suction on/off)."""
    if not fleet_controller:
        raise HTTPException(status_code=503, detail="Fleet controller not initialized")

    try:
        result = fleet_controller.set_tool_state(req.arm_id, req.enable)
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Grip failed: {str(e)}")


@app.get("/storage/slots")
def storage_slots(group: Optional[str] = None, occupancy: Optional[str] = None):
    return {"ok": True, "registry": load_storage(), "slots": list_storage_slots(group=group, occupancy=occupancy)}


@app.post("/storage/resolve")
def storage_resolve(req: StorageResolveRequest):
    robot_id = req.robot_id or req.arm_id
    active = fleet_controller.active_arm_ids if fleet_controller else active_robot_ids(ROBOT_FLEET_CONFIG)
    online = [arm_id for arm_id, robot in (fleet_controller.robots.items() if fleet_controller else []) if robot.is_connected]
    return resolve_storage(
        reference=req.reference,
        color=req.color,
        arm_id=robot_id,
        require_empty=req.require_empty,
        active_arms=active,
        online_arms=online if robot_id else None,
    )


@app.post("/storage/place")
async def storage_place(req: StoragePlaceRequest):
    if not fleet_controller:
        raise HTTPException(status_code=503, detail="Fleet controller not initialized")
    async with get_arm_lock(req.robot_id):
        return fleet_controller.place_to_storage_slot(
            robot_id=req.robot_id,
            reference=req.reference,
            color=req.color,
        )


@app.get("/storage/policy")
def get_storage_policy():
    return {"ok": True, "policy": storage_policy()}


@app.put("/storage/policy")
def put_storage_policy(req: StoragePolicyRequest):
    payload = set_storage_policy(req.model_dump())
    return {"ok": True, "policy": payload.get("slot_policy")}


@app.post("/storage/scan")
def storage_scan(image_path: Optional[str] = None, expected_count: int = 16):
    return scan_storage(image_path=image_path, expected_count=expected_count)


@app.get("/storage/slots/{slot_name}")
def storage_slot(slot_name: str):
    from .storage_service import find_slot
    slot = find_slot(load_storage(), slot_name)
    if not slot:
        raise HTTPException(status_code=404, detail={"error_code": "slot_not_found", "slot_name": slot_name})
    return {"ok": True, "slot": slot}


@app.patch("/storage/slots/{slot_name}")
def patch_storage_slot(slot_name: str, req: StorageOccupancyRequest):
    try:
        payload = update_occupancy(slot_name, req.occupancy, req.held_object, req.source, req.verification)
        slot = next(slot for slot in payload.get("slots", []) if slot.get("name") == slot_name)
        return {"ok": True, "slot": slot}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "slot_not_found", "error": str(exc)})

@app.get("/list_robots")
async def list_robots():
    """List all configured robots."""
    if not fleet_controller:
        raise HTTPException(status_code=503, detail="Fleet controller not initialized")

    try:
        result = fleet_controller.list_arms()
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"List failed: {str(e)}")


# === Main ===

def main():
    print("=" * 60)
    print("ARC Robot Service")
    print("=" * 60)
    print()
    print("Starting server on http://127.0.0.1:8765")
    print()
    print("Safety:")
    print("  - Preserves all guardrails from Python runtime")
    print("  - Per-arm locks prevent concurrent motion commands")
    print("  - Listens ONLY on localhost (127.0.0.1)")
    print()
    print("To use from ARC CLI:")
    print("  set ARC_RUNTIME_MODE=service")
    print()
    print("=" * 60)
    print()

    uvicorn.run(
        app,
        host="127.0.0.1",  # localhost only โ€” safety
        port=8765,
        log_level="info",
    )


if __name__ == "__main__":
    main()



@app.post("/storage/slots/{slot_name}/verify")
def verify_storage_slot(slot_name: str, verified: bool, color: Optional[str] = None, verification: Optional[Dict[str, Any]] = None):
    """Commit occupied only after an explicit post-place scan verification."""
    from .storage_service import find_slot
    slot = find_slot(load_storage(), slot_name)
    if not slot:
        raise HTTPException(status_code=404, detail={"error_code": "slot_not_found", "slot_name": slot_name})
    payload = update_occupancy(slot_name, "occupied" if verified else "unknown", held_object={"color": color} if verified and color else None, source="post_place_scan", verification=verification or {"verified": bool(verified)})
    updated = next(item for item in payload.get("slots", []) if item.get("name") == slot.get("name"))
    return {"ok": bool(verified), "slot": updated, "error_code": None if verified else "capacity_unknown"}
from .workspace_model import list_workspace_zones, list_storage_groups, plan_workspace_intent
class IntentPlanRequest(BaseModel):
    objects: list[Dict[str, Any]] = []
    destination_reference: str
    source_zone: Optional[str] = None
    color: str = "any"
    robot_id: Optional[str] = None
    active_arms: Optional[list[str]] = None
    online_arms: Optional[list[str]] = None


@app.get("/workspace/layout")
def workspace_layout():
    return build_workspace_layout()


@app.get("/runtime/context")
def runtime_context():
    online = [arm_id for arm_id, robot in (fleet_controller.robots.items() if fleet_controller else []) if robot.is_connected]
    return build_tool_context(online_arms=online)

@app.get("/workspace/zones")
def workspace_zones():
    return list_workspace_zones()


@app.get("/storage/groups")
def storage_groups():
    return list_storage_groups()


@app.post("/planning/intent")
def planning_intent(req: IntentPlanRequest):
    objects = req.objects
    if not objects:
        if not camera_controller:
            return {"ok": False, "error_code": "camera_offline", "error": "Camera controller not initialized"}
        scan = camera_controller.scan_workspace(camera_id="Cam1", color="any", selection="first", require_saved_workspace=True)
        if not scan.get("ok"):
            return scan
        objects = scan.get("objects", [])
    return plan_workspace_intent(
        objects,
        req.destination_reference,
        source_zone=req.source_zone,
        color=req.color,
        robot_id=req.robot_id,
        active_arms=req.active_arms,
        online_arms=req.online_arms,
    )



