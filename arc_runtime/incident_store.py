import json
from pathlib import Path
from typing import Optional

from .state_paths import runtime_state_path


class IncidentStore:
    def __init__(self, base_dir: Optional[Path] = None):
        root = base_dir or runtime_state_path("incidents")
        self.base_dir = root
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _incident_path(self, robot_id: str) -> Path:
        safe_id = str(robot_id).strip().replace("/", "_").replace("\\", "_")
        return self.base_dir / f"{safe_id}.json"

    def save(self, robot_id: str, incident: dict):
        path = self._incident_path(robot_id)
        path.write_text(json.dumps(incident, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, robot_id: str):
        path = self._incident_path(robot_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def clear(self, robot_id: str):
        path = self._incident_path(robot_id)
        if path.exists():
            path.unlink()
