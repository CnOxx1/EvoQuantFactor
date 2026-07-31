from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factor_backend.config import get_settings


ROLE_FILES = {
    "R1": "step2_r1_quant.json",
    "R2": "step2_r2_pm.json",
    "R3": "step2_r3_risk.json",
    "R4": "step2_r4_sellside.json",
    "R5": "step2_r5_data.json",
    "R6": "step2_r6_head.json",
}


class PromptLoader:
    def __init__(self, prompts_dir: str | Path | None = None) -> None:
        settings = get_settings()
        self.dir = Path(prompts_dir or settings.prompts_dir)

    def load(self, filename: str) -> dict[str, Any]:
        path = self.dir / filename
        if not path.exists():
            raise FileNotFoundError(str(path))
        return json.loads(path.read_text(encoding="utf-8"))

    def shared_mcp_append(self) -> str:
        try:
            data = self.load("_shared_mcp.json")
            return data.get("system_append", "")
        except FileNotFoundError:
            return ""

    def step1_system(self) -> str:
        data = self.load("step1_extract.json")
        system = data.get("system", "")
        if data.get("mcp", {}).get("append_shared"):
            system = system + "\n\n" + self.shared_mcp_append()
        return system

    def role_prompt(self, role_code: str) -> dict[str, Any]:
        filename = ROLE_FILES[role_code]
        data = self.load(filename)
        system = data.get("system", "")
        if data.get("mcp", {}).get("append_shared"):
            system = system + "\n\n" + self.shared_mcp_append()
        return {
            "role_code": role_code,
            "system": system,
            "user_template": data.get("user_template", ""),
            "scoring": data.get("scoring", {}),
            "name": data.get("name", role_code),
        }

    def index(self) -> dict[str, Any]:
        path = self.dir / "index.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
