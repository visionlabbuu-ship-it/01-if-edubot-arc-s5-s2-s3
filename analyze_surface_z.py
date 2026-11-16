import json
import math
from pathlib import Path

import numpy as np


SESSION_PATH = Path("runtime_state/calibration_session_20260526_141910.json")


def fit_plane(points):
    basis = np.array(
        [[p["robot_pose"]["x"], p["robot_pose"]["y"], 1.0] for p in points],
        dtype=float,
    )
    z = np.array([p["robot_pose"]["z"] for p in points], dtype=float)
    coef, *_ = np.linalg.lstsq(basis, z, rcond=None)
    pred = basis @ coef
    residual = pred - z
    return coef, pred, residual, z


def main():
    session = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    for arm_id in ("Arm1", "Arm2"):
        points = [p for p in session["points"] if p["arm_id"] == arm_id]
        coef, pred, residual, z_values = fit_plane(points)
        print(arm_id)
        print(f"  z = {coef[0]:.8f} * x + {coef[1]:.8f} * y + {coef[2]:.8f}")
        print(f"  z_span_mm = {z_values.max() - z_values.min():.3f}")
        print(f"  z_min_mm = {z_values.min():.3f}")
        print(f"  z_max_mm = {z_values.max():.3f}")
        for point, plane_z, err in zip(points, pred, residual):
            pose = point["robot_pose"]
            radius = math.hypot(pose["x"], pose["y"])
            print(
                "  "
                f"{point['point_id']} "
                f"x={pose['x']:.3f} y={pose['y']:.3f} r={radius:.3f} "
                f"taught_z={pose['z']:.3f} plane_z={plane_z:.3f} residual={err:.3f}"
            )
        print()


if __name__ == "__main__":
    main()
