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
    """仅负责从 prompts/ 读 JSON；DB 覆盖与拼接见 prompt_config。"""

    def __init__(self, prompts_dir: str | Path | None = None) -> None:
        settings = get_settings()
        self.dir = Path(prompts_dir or settings.prompts_dir)

    def load(self, filename: str) -> dict[str, Any]:
        path = self.dir / filename
        if not path.exists():
            raise FileNotFoundError(str(path))
        return json.loads(path.read_text(encoding="utf-8"))

    def index(self) -> dict[str, Any]:
        path = self.dir / "index.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
