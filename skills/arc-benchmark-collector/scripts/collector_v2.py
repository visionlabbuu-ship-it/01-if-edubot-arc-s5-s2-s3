"""Launch the repository-owned extended ARC collector."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def workspace() -> Path:
    configured = os.getenv("ARC_ROBOT_WORKSPACE")
    if configured:
        return Path(configured).resolve()
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "benchmark" / "automation" / "collector_v2.py").is_file():
            return candidate
    raise SystemExit("ARC workspace not found; set ARC_ROBOT_WORKSPACE")


if __name__ == "__main__":
    ws = workspace()
    raise SystemExit(subprocess.call([sys.executable, "-m", "benchmark.automation.collector_v2", *sys.argv[1:]], cwd=ws))
