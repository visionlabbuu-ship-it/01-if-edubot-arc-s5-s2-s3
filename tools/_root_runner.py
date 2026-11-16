from __future__ import annotations

import runpy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_root_script(script_name: str) -> None:
    runpy.run_path(str(PROJECT_ROOT / script_name), run_name="__main__")
