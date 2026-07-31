from __future__ import annotations

import uuid
from typing import Any

from factor_backend.models.schemas import StepType, utc_now_iso
from factor_backend.services.storage import Storage


class StepRecorder:
    def __init__(self, storage: Storage, job_id: str) -> None:
        self.storage = storage
        self.job_id = job_id

    def record(
        self,
        step_type: StepType,
        *,
        title: str,
        summary: str = "",
        payload: dict[str, Any] | None = None,
        round: int = 0,
        role_code: str | None = None,
        status: str = "ok",
    ) -> dict[str, Any]:
        seq = self.storage.next_step_seq(self.job_id)
        step = {
            "step_id": f"stp_{uuid.uuid4().hex[:10]}",
            "seq": seq,
            "step_type": step_type.value,
            "title": title,
            "round": round,
            "role_code": role_code,
            "status": status,
            "created_at": utc_now_iso(),
            "summary": summary,
            "payload": payload or {},
        }
        return self.storage.append_step(self.job_id, step)
