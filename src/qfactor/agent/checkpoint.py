from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from qfactor.db.repo import Database
from qfactor.settings import ProjectConfig, get_project_config


class CheckpointStore:
    """Resume state persisted to SQLite (JSON file kept as backup)."""

    def __init__(self, name: str = "loop_csi100", cfg: ProjectConfig | None = None):
        self.cfg = cfg or get_project_config()
        self.name = name
        self.path = self.cfg.path("runs") / "checkpoints" / f"{name}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = Database()

    def load(self) -> dict[str, Any]:
        row = self.db.load_checkpoint(self.name)
        if row:
            return row
        if not self.path.exists():
            return {
                "iteration": 0,
                "tested_hashes": [],
                "saved_factors": [],
                "mechanism_hits": {},
                "history": [],
            }
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, state: dict[str, Any]) -> None:
        state = dict(state)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.db.save_checkpoint(self.name, state)
        text = json.dumps(state, ensure_ascii=False, indent=2)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            tmp.replace(self.path)
        except OSError:
            self.path.write_text(text, encoding="utf-8")
            tmp.unlink(missing_ok=True)
