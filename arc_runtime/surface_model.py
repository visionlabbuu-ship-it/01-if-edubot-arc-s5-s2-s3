from .config import ARM_PICK_CONFIG

SURFACE_Z = -150.0


def surface_z_for_pose(arm_id, x, y):
    """Return the calibrated pick/place surface height for one arm.

    The old implementation returned one global value for every arm. That
    bypassed the per-arm pick profile and caused Arm2 to descend to a height
    different from the value shown in calibration/configuration. Keep the
    legacy constant as a defensive fallback for incomplete profiles.
    """
    cfg = ARM_PICK_CONFIG.get(arm_id) or ARM_PICK_CONFIG.get("Arm1", {})
    try:
        configured_z = float(cfg.get("pick_z", SURFACE_Z))
    except (TypeError, ValueError):
        configured_z = SURFACE_Z
    return configured_z


def surface_z_for_named_target(target):
    if not isinstance(target, dict):
        return SURFACE_Z
    try:
        configured_z = float(target.get("place_z", SURFACE_Z))
    except (TypeError, ValueError):
        configured_z = SURFACE_Z
    return max(configured_z, SURFACE_Z)


def phase_z_from_pick_config(arm_id, phase, x=None, y=None):
    phase_key = str(phase or "").strip().lower()
    cfg = ARM_PICK_CONFIG.get(arm_id, ARM_PICK_CONFIG.get("Arm1", {}))
    if phase_key == "hover":
        return float(cfg["hover_z"])
    if phase_key == "surface":
        return surface_z_for_pose(arm_id, x, y)
    return None
