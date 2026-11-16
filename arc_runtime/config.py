import os

MVS_PYTHON_ROOT = os.getenv(
    "ARC_MVS_PYTHON_ROOT",
    r"C:\Program Files (x86)\MVS\Development\Samples\Python",
)
MVS_RUNTIME_DIR = os.getenv(
    "ARC_MVS_RUNTIME_DIR",
    r"C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64",
)

# ==========================================
# โ๏ธ CONFIGURATION
# ==========================================
MODEL_NAME = 'llama3.1' # เนเธเธฐเธเธณ llama3.1 เธซเธฃเธทเธญ qwen2.5 (เธ•เนเธญเธเนเธซเธฅเธ”เธฅเธเน€เธเธฃเธทเนเธญเธเธเนเธฒเธ ollama เธเนเธญเธ)

ROBOT_FLEET_CONFIG = {
    "Arm1": {
        "ip": os.getenv("ARC_ARM1_IP", ""),
        "port": 29999,
        "color": "GREEN",
        "facing": "toward_arm2",
        "shared_home_pose": {"x": 250.0, "y": 0.0, "z": 50.0, "r": 0.0},
    },
    "Arm2": {
        "ip": os.getenv("ARC_ARM2_IP", ""),
        "port": 29999,
        "color": "BLUE",
        "facing": "toward_arm1",
        "shared_home_pose": {"x": 250.0, "y": 0.0, "z": 50.0, "r": 0.0},
    },
}


def _parse_active_robot_ids():
    raw = os.getenv("ARC_ACTIVE_ROBOT_IDS")
    if raw is None:
        # dual-arm sim/UI เน€เธเนเธเธเนเธฒเน€เธฃเธดเนเธกเธ•เนเธ โ€” เธเธดเธ” Arm2 เธ”เนเธงเธข ARC_ACTIVE_ROBOT_IDS=Arm1
        return []
    value = raw.strip()
    if value.lower() in {"", "all", "*"}:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


# [] = เธ—เธธเธเนเธเธเนเธ ROBOT_FLEET_CONFIG (Arm1+Arm2)
# เธเธณเธเธฑเธ”เธ”เนเธงเธข ARC_ACTIVE_ROBOT_IDS=Arm1 เธซเธฃเธทเธญ Arm1,Arm2
ACTIVE_ROBOT_IDS = _parse_active_robot_ids()


# Simulation mode โ€” เธฃเธฑเธเนเธ”เนเนเธ”เธขเนเธกเนเธ•เนเธญเธเน€เธเธทเนเธญเธกเธ•เนเธญเนเธเธ MG400 เธเธฃเธดเธ
# เน€เธเธดเธ”เธ”เนเธงเธข ARC_SIM_MODE=1 เนเธเน SimRobot เนเธ—เธ MG400Robot เนเธฅเธฐเนเธซเนเธเธฅเนเธญเธเธญเนเธฒเธเธ เธฒเธเธเธฒเธเนเธเธฅเน
#
# เธฅเธณเธ”เธฑเธเธเธงเธฒเธกเธชเธณเธเธฑเธ (priority):
#   1. ARC_SIM_MODE env var (เธ–เนเธฒเธ•เธฑเนเธเนเธงเนเธเธฑเธ”เน€เธเธ โ’ เธเธเธฐเธ—เธธเธเธญเธขเนเธฒเธ)
#   2. runtime_state/sim_mode.json (toggle เธเนเธฒเธ UI/API โ€” persist เธเนเธฒเธก subprocess)
#   3. default = True  (lab/sim-first โ€” เธขเธฑเธเนเธกเนเธเธฑเธเธเธฑเธเน€เธเธทเนเธญเธกเธซเธธเนเธเธเธฃเธดเธ)
#      เธเธดเธ”เธ”เนเธงเธข ARC_SIM_MODE=0 เธซเธฃเธทเธญ toggle เธเนเธฒเธ UI/API
import json as _json
from pathlib import Path as _Path
from .state_paths import artifact_path, runtime_state_path

SIM_SCAN_IMAGE = os.getenv(
    "ARC_SIM_SCAN_IMAGE",
    str(artifact_path("captures", "scan_capture.png")),
)
SIM_MODE_FILE = runtime_state_path("sim_mode.json")

_SIM_TRUE = {"1", "true", "yes", "on"}
_SIM_FALSE = {"0", "false", "no", "off"}


def _load_sim_mode() -> bool:
    """เธญเนเธฒเธเนเธซเธกเธ” sim โ€” env var เธเธเธฐเธเนเธญเธ, เธ•เธฒเธกเธ”เนเธงเธขเนเธเธฅเนเธ—เธตเน toggle เนเธงเน, เนเธฅเนเธงเธเนเธญเธข default=True"""
    raw = os.getenv("ARC_SIM_MODE")
    if raw is not None:
        return raw.strip().lower() in _SIM_TRUE
    try:
        if SIM_MODE_FILE.exists():
            data = _json.loads(SIM_MODE_FILE.read_text(encoding="utf-8"))
            return bool(data.get("sim_mode", True))
    except (ValueError, OSError):
        pass
    return True


SIM_MODE = _load_sim_mode()


def sim_mode_env_locked() -> bool:
    """True เน€เธกเธทเนเธญ ARC_SIM_MODE เธ–เธนเธเธ•เธฑเนเธเนเธ env โ€” UI toggle เธ•เนเธญเธเนเธกเน override process เธเธตเน"""
    return os.getenv("ARC_SIM_MODE") is not None


def set_sim_mode(on: bool) -> bool:
    """เธ•เธฑเนเธเธเนเธฒเนเธซเธกเธ” sim เธ—เธฑเนเธเนเธ memory เนเธฅเธฐ persist เธฅเธเนเธเธฅเน (เนเธเนเนเธ”เธข /api/sim/toggle)

    เธเธทเธเธเนเธฒเนเธซเธกเธ”เนเธซเธกเน โ€” เธเธนเนเน€เธฃเธตเธขเธเธเธงเธฃเธชเธฃเนเธฒเธ FleetController เนเธซเธกเนเน€เธเธทเนเธญเนเธซเนเนเธเน Sim/MG400 เธ—เธตเนเธ–เธนเธเธ•เนเธญเธ
    เธ–เนเธฒ process เธ–เธนเธ lock เธ”เนเธงเธข ARC_SIM_MODE env เธเธฐเนเธกเนเน€เธเธฅเธตเนเธขเธเธเนเธฒ (เธเธฑเธ Toggle เธเธดเธ” sim เนเธ”เธขเนเธกเนเธ•เธฑเนเธเนเธ)
    """
    global SIM_MODE
    if sim_mode_env_locked():
        # Keep in-memory value aligned with env; still rewrite file for other tools.
        SIM_MODE = _load_sim_mode()
        try:
            SIM_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
            SIM_MODE_FILE.write_text(
                _json.dumps({"sim_mode": SIM_MODE}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
        return SIM_MODE

    SIM_MODE = bool(on)
    try:
        SIM_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SIM_MODE_FILE.write_text(
            _json.dumps({"sim_mode": SIM_MODE}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    return SIM_MODE


def active_robot_ids(mapping=None):
    source = mapping or ROBOT_FLEET_CONFIG
    if not ACTIVE_ROBOT_IDS:
        return list(source.keys())
    selected = [robot_id for robot_id in ACTIVE_ROBOT_IDS if robot_id in source]
    return selected or list(source.keys())


def active_config(mapping):
    active_ids = set(active_robot_ids(mapping))
    return {
        robot_id: value
        for robot_id, value in mapping.items()
        if robot_id in active_ids
    }


# ==========================================
# ๐ SIM / lab LLM (OpenAI-compatible chat completions)
# Used by Sim web chat + ai_fleet interactive runtime.
# Override with env; defaults match the Jetson LiteLLM on this LAN.
# ==========================================
def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def normalize_openai_base_url(url: str) -> str:
    text = (url or "").strip().rstrip("/")
    if text.lower().endswith("/v1"):
        text = text[:-3].rstrip("/")
    return text


JETSON_PRIMARY_HOST = (os.getenv("ARC_JETSON_HOST") or "").strip()
JETSON_FALLBACK_HOST = (os.getenv("ARC_JETSON_FALLBACK_HOST") or "").strip()

SIM_LLM_BASE_URL = normalize_openai_base_url(
    os.getenv("ARC_SIM_LLM_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or "http://127.0.0.1:4000"
)
SIM_LLM_API_KEY = (
    os.getenv("ARC_SIM_LLM_API_KEY")
    or os.getenv("OPENAI_API_KEY")
    or "local-dev-key"
)
SIM_LLM_MODEL = (
    os.getenv("ARC_SIM_LLM_MODEL")
    or os.getenv("OPENAI_MODEL")
    or "local-llama"
)
# 1 = Sim chat เนเธเน LLM เน€เธเนเธเธซเธฅเธฑเธ (keyword เน€เธเนเธ fallback)
# 0 = keyword เธญเธขเนเธฒเธเน€เธ”เธตเธขเธง
SIM_LLM_ENABLED = _env_flag("ARC_SIM_LLM", True)

JETSON_SIDECAR_BASE_URL = normalize_openai_base_url(
    os.getenv("ARC_JETSON_SIDECAR_URL") or "http://127.0.0.1:9100"
)
JETSON_CONDITION_ID = (os.getenv("ARC_JETSON_CONDITION_ID") or "").strip()
JETSON_MARKERS_ENABLED = _env_flag("ARC_JETSON_MARKERS", True)
JETSON_WAIT_BUNDLE = _env_flag("ARC_JETSON_WAIT_BUNDLE", False)
JETSON_WAIT_BUNDLE_S = float(os.getenv("ARC_JETSON_WAIT_BUNDLE_S") or "15")


def llm_base_candidates() -> list:
    primary = SIM_LLM_BASE_URL
    out = [primary]
    if (JETSON_PRIMARY_HOST and JETSON_PRIMARY_HOST in primary) or (JETSON_FALLBACK_HOST and JETSON_FALLBACK_HOST in primary):
        for host in (JETSON_PRIMARY_HOST, JETSON_FALLBACK_HOST):
            alt = f"http://{host}:4000"
            if alt not in out:
                out.append(alt)
    return out


def sidecar_base_candidates() -> list:
    primary = JETSON_SIDECAR_BASE_URL
    out = [primary]
    if (JETSON_PRIMARY_HOST and JETSON_PRIMARY_HOST in primary) or (JETSON_FALLBACK_HOST and JETSON_FALLBACK_HOST in primary):
        for host in (JETSON_PRIMARY_HOST, JETSON_FALLBACK_HOST):
            alt = f"http://{host}:9100"
            if alt not in out:
                out.append(alt)
    return out

# ==========================================
# ๐ฌ Sim motion animation profile
# เธเธณเธซเธเธ”เธเธงเธฒเธกเน€เธฃเนเธง/FPS เธเธญเธเธญเธเธดเน€เธกเธเธฑเธเธเนเธญเธ โ€” UI + SimRobot เนเธเนเธเนเธฒเน€เธ”เธตเธขเธงเธเธฑเธ
# ==========================================
def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


# เธเธงเธฒเธกเน€เธฃเนเธงเธเธฅเธฒเธขเนเธเธ (mm/s) โ€” เธเนเธฒเธฅเธเธเธดเธ”เน€เธเธทเนเธญเนเธซเนเธเนเธฒเธงเธชเธฑเนเธ เธ–เธตเน เธ•เธฒเนเธกเนเน€เธซเนเธเธงเธฒเธ
SIM_MOTION_SPEED_MM_S = _env_float("ARC_SIM_MOTION_SPEED_MM_S", 70.0)
# เน€เธเนเธฒเธซเธกเธฒเธข FPS เธเธญเธ sim pose update (โฅ25; default 60 เนเธซเนเธเนเธฒเธงเธ–เธตเน)
SIM_MOTION_FPS = max(25.0, _env_float("ARC_SIM_MOTION_FPS", 60.0))
SIM_MOTION_DT_S = 1.0 / SIM_MOTION_FPS
# เธฃเธฐเธขเธฐเน€เธงเธฅเธฒ move เธเธฑเนเธเธ•เนเธณ/เธชเธนเธเธชเธธเธ” (เธงเธดเธเธฒเธ—เธต)
SIM_MOTION_MIN_S = _env_float("ARC_SIM_MOTION_MIN_S", 1.0)
SIM_MOTION_MAX_S = _env_float("ARC_SIM_MOTION_MAX_S", 8.0)
# linear = เธเธงเธฒเธกเน€เธฃเนเธงเธเธเธ—เธตเน; smoothstep = เธเธฐเธฅเธญเธซเธฑเธงเธ—เนเธฒเธข (เธ”เธนเธเธธเนเธกเธเธถเนเธ)
SIM_MOTION_EASING = (os.getenv("ARC_SIM_MOTION_EASING") or "smoothstep").strip().lower()

ROBOT_LAYOUT = {
    "coordinate_frame": "shared_robot_plane",
    "arms_face_each_other": True,
    "home_spacing_mm": 290.0,
    "home_axis": "shared",
    "notes": "Arm1 and Arm2 face each other and use the safe driver home pose: x=250, y=0, z=50. Their global tool positions are outside the shared zone.",
}

ROBOT_SAFETY_CONFIG = {
    "enabled": True,
    "max_arm_length_mm": 440.0,
    "min_reach_mm": 130.0,
    # Safety geometry is derived from ARM_FRAME_TRANSFORMS by default because
    # those transforms are the calibrated runtime source used by camera mapping.
    "distance_between_robots_mm": None,
    "shared_zone": {
        "enabled": True,
        "zone_start_mm": None,
        "zone_end_mm": None,
    },
    "collision": {
        "enabled": True,
        "block_if_other_pose_unavailable": True,
    },
    "arm_global_frames": {},
}

CAMERA_CONFIG = {
    "Cam1": {
        "project_root": MVS_PYTHON_ROOT,
        "dll_dir": MVS_RUNTIME_DIR,
        "config_file": os.getenv(
            "ARC_MVS_CONFIG_FILE",
            os.path.join(MVS_PYTHON_ROOT, "camera_mode_config.json"),
        ),
        "mode": "rgb",
        "frame_timeout_ms": 1000,
        "min_object_area": 1500,
        "workspace_min_area": 200000,
        "max_object_area_ratio": 0.2,
        "saturation_threshold": 40,
        "color_object_detection_enabled": True,
        "legacy_saturation_detection_enabled": False,
        "detectable_object_colors": ["red", "green", "blue"],
        "object_color_hsv_ranges": {
            "red": [
                {"lower": [0, 70, 25], "upper": [18, 255, 255]},
                {"lower": [160, 70, 25], "upper": [179, 255, 255]},
            ],
            "green": [
                {"lower": [30, 25, 20], "upper": [95, 255, 255]},
            ],
            "blue": [
                {"lower": [85, 35, 20], "upper": [145, 255, 255]},
            ],
        },
        "min_object_circularity": 0.45,
        "min_object_diameter_px": 35,
        "max_object_diameter_px": 190,
        "max_object_area_px": 45000,
        "min_object_aspect": 0.55,
        "max_object_aspect": 1.75,
        "object_duplicate_distance_px": 65,
        "color_morph_open_kernel": 5,
        "color_morph_close_kernel": 7,
        "color_hough_round_enabled": False,
        "edge_reject_margin_px": 20,
        "morph_open_kernel": 5,
        "morph_close_kernel": 7,
        "pixel_format": 17301512,
        "workspace_width_mm": 600.0,
        "workspace_height_mm": 400.0,
        "fixed_workspace_corners_px": [
            [1017.0, 27.0],
            [2320.0, 22.0],
            [1024.0, 1914.0],
            [2329.0, 1920.0],
        ],
        "params": {
            "rgb": {
                "ExposureTime": 52000.0,
                "Gain": 10.0,
                "Gamma": 0.7,
                "AcquisitionFrameRate": 60.0,
                "Sharpness": 50,
            },
            "grayscale": {
                "ExposureTime": 20000.0,
                "Gain": 5.0,
                "Gamma": 1.0,
                "AcquisitionFrameRate": 60.0,
                "Sharpness": 30,
            },
        },
        "calibration_arm": "Arm1",
        "calibration": {
            "camera_points": [
                [1156.0, 529.0],
                [2222.0, 446.0],
                [2200.0, 820.0],
                [1672.0, 998.0],
            ],
            "robot_points": [
                [255.087890625, -158.87002563476562],
                [226.88121032714844, 168.60813903808594],
                [343.013427734375, 161.34739685058594],
                [396.6991882324219, 3.6865625381469727],
            ],
        },
        "arm_calibrations": {
            "Arm1": {
                "camera_points": [
                    [1156.0, 529.0],
                    [2222.0, 446.0],
                    [2200.0, 820.0],
                    [1672.0, 998.0],
                ],
                "robot_points": [
                    [255.087890625, -158.87002563476562],
                    [226.88121032714844, 168.60813903808594],
                    [343.013427734375, 161.34739685058594],
                    [396.6991882324219, 3.6865625381469727],
                ],
                "session_id": "calibration_session_20260526_141910",
            },
            "Arm2": {
                "camera_points": [
                    [1122.0, 1552.0],
                    [1133.0, 1161.0],
                    [2217.0, 1519.0],
                    [1672.0, 998.0],
                ],
                "robot_points": [
                    [222.18836975097656, 175.17343139648438],
                    [341.513671875, 170.22409057617188],
                    [230.45314025878906, -164.18984985351562],
                    [390.9952392578125, 2.0249946117401123],
                ],
                "session_id": "calibration_session_20260526_141910",
            },
        },
    }
}

ARM_FRAME_TRANSFORMS = {
    "Arm1": {"a": 1.0, "b": 0.0, "tx": 0.0, "c": 0.0, "d": 1.0, "ty": 0.0},
    "Arm2": {"a": -1.0, "b": 0.0, "tx": 790.0, "c": 0.0, "d": -1.0, "ty": 0.0, "verified": False},
}

ARM_REACH_ZONES = {
    "Arm1": [
        [100.0, -250.0],
        [420.0, -250.0],
        [420.0, 220.0],
        [100.0, 220.0],
    ],
    "Arm2": [
        [100.0, -250.0],
        [420.0, -250.0],
        [420.0, 220.0],
        [100.0, 220.0],
    ],
}

ARM_PICK_CONFIG = {
    "Arm1": {
        "hover_z": 83.41,
        "pick_z": -151.0,
        "tool_do_index": 1,
        "tool_settle_s": 0.5,
        "motion_settle_s": 1.0,
        "post_pick_rescan_delay_s": 1.0,
        "post_pick_position_tolerance_mm": 35.0,
    },
    "Arm2": {
        "hover_z": 83.41,
        "pick_z": -152.0,
        "surface_z_plane": {
            "a": 0.00979012,
            "b": 0.00109043,
            "c": -154.17717827,
            "offset": 0.0,
            "source": "calibration_session_20260526_141910",
        },
        "tool_do_index": 1,
        "tool_settle_s": 0.5,
        "post_suction_on_delay_s": 0.3,
        "motion_settle_s": 1.0,
        "post_pick_rescan_delay_s": 1.0,
        "post_pick_position_tolerance_mm": 35.0,
    },
}

ARM_NAMED_TARGETS = {
    "Arm1": {
        "home": {
            "x": 250.0,
            "y": 0.0,
            "hover_z": 50.0,
            "place_z": 50.0,
            "r": 0.0,
        },
        "storage_zone": {
            "x": 187.41,
            "y": -311.67,
            "hover_z": 83.41,
            "place_z": -90.0,
            "r": 0.0,
        },
        "handoff_zone": {
            "x": 405.0967241615137,
            "y": 18.31009550736929,
            "hover_z": 83.41,
            "place_z": -149.87738037109375,
            "r": 57.85194396972656,
            "role": "shared_zone_station",
            "calibration_source": "runtime_state/handoff_pose_captures/latest.json:Arm1",
        },
        "drop_zone": {
            "x": 187.41,
            "y": -311.67,
            "hover_z": 83.41,
            "place_z": -90.0,
            "r": 0.0,
            "deprecated_alias_of": "storage_zone",
        },
    },
    "Arm2": {
        "home": {
            "x": 250.0,
            "y": 0.0,
            "hover_z": 50.0,
            "place_z": 50.0,
            "r": 0.0,
        },
        "drop_zone": {
            "x": 187.41,
            "y": 311.67,
            "hover_z": 83.41,
            "place_z": -113.61,
            "r": 0.0,
            "verified": False,
        },
        "handoff_zone": {
            "x": 402.5205573725671,
            "y": -5.832066826770127,
            "hover_z": 83.41,
            "place_z": -146.82452392578125,
            "r": 43.89799499511719,
            "role": "shared_zone_station",
            "calibration_source": "runtime_state/handoff_pose_captures/latest.json:Arm2",
        },
    },
}
