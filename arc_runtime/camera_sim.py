"""Camera simulator — scan จาก world model แทนอ่านจากกล้องจริง

เมื่อ ARC_SIM_MODE=1, camera_controller จะใช้ simulator นี้แทน scan_workspace()
payload ที่ได้จะมีโครงสร้างเดียวกับ scan_workspace() จริง — world model รักษา
โครงสร้าง object/scan payload เดิมไว้ให้ camera ไม่ต้องแก้ logic ใดๆ
"""

from typing import Optional

from .sim_world import WorldModel


class CameraSimulator:
    """กล้องจำลอง — scan จาก world model แทนอ่านจากกล้องจริง"""

    def __init__(self, world: WorldModel):
        self.world = world

    def scan(self, camera_id: str = "Cam1", color: str = "any", selection: str = "first") -> dict:
        """scan workspace จากโลกปัจจุบัน"""
        scan = self.world.simulate_scan(camera_id)
        if color != "any":
            scan["objects"] = [o for o in scan["objects"] if o.get("color") == color]
            scan["object_count"] = len(scan["objects"])
            if scan["objects"]:
                scan["selected_object"] = scan["objects"][0] if selection == "first" else scan["objects"][-1]
            else:
                scan["selected_object"] = None
        return scan

    def assign_targets(self, assignments: list, camera_id: str = "Cam1") -> dict:
        """assign_targets — ละเอียดกว่า scan ธรรมดา (ใช้โดย pick_detected_and_place)"""
        scan = self.scan(camera_id=camera_id, color="any", selection="first")
        remaining = list(scan["objects"])
        targets = []
        unresolved = []

        for req in assignments:
            arm_id = req.get("arm_id", "Arm1")
            color = req.get("color", "any")
            block = req.get("block")
            target_object_id = str(req.get("object_id") or "").strip()

            assigned = None
            for obj in remaining:
                if target_object_id and str(obj.get("id") or "") != target_object_id:
                    continue
                if color != "any" and obj.get("color") != color:
                    continue
                if block and obj.get("block") != block:
                    continue
                if arm_id not in obj.get("reachable_by", []):
                    continue
                assigned = obj
                remaining.remove(obj)
                break

            if assigned:
                # per_arm = arm-local สำหรับ motion; calibrated = workspace
                arm_plane = (assigned.get("per_arm_robot_plane") or {}).get(arm_id)
                if not isinstance(arm_plane, dict):
                    from .sim_world import workspace_to_arm_local
                    ws = assigned.get("robot_plane") or {}
                    arm_plane = workspace_to_arm_local(
                        arm_id, float(ws.get("x", 0.0)), float(ws.get("y", 0.0))
                    )
                targets.append({
                    "arm_id": arm_id,
                    "color": color,
                    "block": block,
                    "object_id": assigned["id"],
                    "robot_plane": {"x": float(arm_plane["x"]), "y": float(arm_plane["y"])},
                    "calibrated_robot_plane": assigned.get("robot_plane"),
                    "reachable_by": assigned.get("reachable_by", []),
                    "preferred_arm": assigned.get("preferred_arm"),
                })
            else:
                unresolved.append({
                    "arm_id": arm_id,
                    "color": color,
                    "block": block,
                    "object_id": target_object_id or None,
                    "reason": "No reachable matching object found",
                })

        return {
            "ok": len(unresolved) == 0,
            "camera_id": camera_id,
            "timestamp": scan["timestamp"],
            "object_count": len(targets),
            "workspace": scan["workspace"],
            "targets": targets,
            "unresolved": unresolved,
            "remaining_objects": remaining,
        }
