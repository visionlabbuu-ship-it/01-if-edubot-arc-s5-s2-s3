import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent


def project_path(*parts):
    return PROJECT_ROOT.joinpath(*parts)


def runtime_state_dir():
    override = os.getenv("ARC_RUNTIME_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return project_path("runtime_state")


def runtime_state_path(*parts):
    return runtime_state_dir().joinpath(*parts)


def artifact_path(*parts):
    return project_path("artifacts", *parts)


def arc_py_path():
    override = os.getenv("ARC_PY_PATH")
    if override:
        return Path(override).expanduser()
    return project_path("arc.py")
