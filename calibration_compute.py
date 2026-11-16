import json
from pathlib import Path

import cv2
import numpy as np


SESSION_PATH = Path("runtime_state/calibration_session_20260526_141910.json")
REPORT_PATH = Path("runtime_state/calibration_session_20260526_141910_report.json")

OLD_CAMERA_POINTS = np.float32(
    [
        [1356.0, 481.0],
        [2215.0, 561.0],
        [1944.0, 922.0],
        [1300.0, 912.0],
    ]
)
OLD_ARM1_POINTS = np.float32(
    [
        [244.508, -92.628],
        [267.690, 170.562],
        [377.581, 88.214],
        [375.471, -110.825],
    ]
)


def _points_for(session, arm_id):
    points = [point for point in session["points"] if point["arm_id"] == arm_id]
    points.sort(key=lambda point: point["point_id"])
    camera_points = np.float32(
        [[point["camera_px"]["x"], point["camera_px"]["y"]] for point in points]
    )
    robot_points = np.float32(
        [[point["robot_pose"]["x"], point["robot_pose"]["y"]] for point in points]
    )
    return points, camera_points, robot_points


def _apply_perspective(matrix, camera_points):
    return cv2.perspectiveTransform(camera_points.reshape((-1, 1, 2)), matrix).reshape(
        (-1, 2)
    )


def _perspective_report(camera_points, robot_points):
    matrix = cv2.getPerspectiveTransform(camera_points, robot_points)
    predicted = _apply_perspective(matrix, camera_points)
    errors = np.linalg.norm(predicted - robot_points, axis=1)
    return matrix, predicted, errors


def _serial_matrix(matrix):
    return [[float(value) for value in row] for row in matrix.tolist()]


def _serial_points(points):
    return [[float(value) for value in row] for row in points.tolist()]


def main():
    session = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    old_matrix = cv2.getPerspectiveTransform(OLD_CAMERA_POINTS, OLD_ARM1_POINTS)
    report = {
        "session_path": str(SESSION_PATH),
        "arms": {},
    }

    matrices = {}
    for arm_id in ("Arm1", "Arm2"):
        points, camera_points, robot_points = _points_for(session, arm_id)
        matrix, predicted, errors = _perspective_report(camera_points, robot_points)
        matrices[arm_id] = matrix
        arm_report = {
            "point_ids": [point["point_id"] for point in points],
            "camera_points": _serial_points(camera_points),
            "robot_points": _serial_points(robot_points),
            "matrix": _serial_matrix(matrix),
            "fit_predicted_robot_points": _serial_points(predicted),
            "fit_errors_mm": [float(value) for value in errors.tolist()],
            "fit_max_error_mm": float(errors.max()),
            "fit_mean_error_mm": float(errors.mean()),
        }
        if arm_id == "Arm1":
            old_predicted = _apply_perspective(old_matrix, camera_points)
            old_errors = np.linalg.norm(old_predicted - robot_points, axis=1)
            arm_report["old_config_predicted_robot_points"] = _serial_points(
                old_predicted
            )
            arm_report["old_config_errors_mm"] = [
                float(value) for value in old_errors.tolist()
            ]
            arm_report["old_config_max_error_mm"] = float(old_errors.max())
            arm_report["old_config_mean_error_mm"] = float(old_errors.mean())
        else:
            old_arm1_predicted = _apply_perspective(old_matrix, camera_points)
            old_arm2_predicted = np.column_stack(
                [790.0 - old_arm1_predicted[:, 0], -old_arm1_predicted[:, 1]]
            )
            old_errors = np.linalg.norm(old_arm2_predicted - robot_points, axis=1)
            arm_report["old_config_predicted_robot_points"] = _serial_points(
                old_arm2_predicted
            )
            arm_report["old_config_errors_mm"] = [
                float(value) for value in old_errors.tolist()
            ]
            arm_report["old_config_max_error_mm"] = float(old_errors.max())
            arm_report["old_config_mean_error_mm"] = float(old_errors.mean())
        report["arms"][arm_id] = arm_report

    all_camera_points = np.float32(
        [[point["camera_px"]["x"], point["camera_px"]["y"]] for point in session["points"]]
    )
    arm1_plane = _apply_perspective(matrices["Arm1"], all_camera_points)
    arm2_plane = _apply_perspective(matrices["Arm2"], all_camera_points)
    affine_basis = np.column_stack(
        [arm1_plane[:, 0], arm1_plane[:, 1], np.ones(len(arm1_plane))]
    )
    coef_x, *_ = np.linalg.lstsq(affine_basis, arm2_plane[:, 0], rcond=None)
    coef_y, *_ = np.linalg.lstsq(affine_basis, arm2_plane[:, 1], rcond=None)
    affine_predicted = np.column_stack([affine_basis @ coef_x, affine_basis @ coef_y])
    affine_errors = np.linalg.norm(affine_predicted - arm2_plane, axis=1)
    report["arm1_to_arm2_affine_diagnostic"] = {
        "transform": {
            "a": float(coef_x[0]),
            "b": float(coef_x[1]),
            "tx": float(coef_x[2]),
            "c": float(coef_y[0]),
            "d": float(coef_y[1]),
            "ty": float(coef_y[2]),
        },
        "errors_mm": [float(value) for value in affine_errors.tolist()],
        "max_error_mm": float(affine_errors.max()),
        "mean_error_mm": float(affine_errors.mean()),
    }

    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
