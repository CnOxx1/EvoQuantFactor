from __future__ import annotations

import asyncio
from typing import Any

from factor_backend.config import Settings, get_settings
from factor_backend.graph.build import get_compiled_graph
from factor_backend.graph.state import GraphState
from factor_backend.models.schemas import JobProgress, JobStatus, StepType
from factor_backend.services.step_recorder import StepRecorder
from factor_backend.services.storage import Storage, get_storage


class PipelineRunner:
    """LangGraph 编排入口：对外 API 仍调用本类。"""

    def __init__(self, storage: Storage | None = None, settings: Settings | None = None) -> None:
        self.storage = storage or get_storage()
        self.settings = settings or get_settings()
        self.graph = get_compiled_graph()

    async def run_job(self, job_id: str) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        recorder = StepRecorder(self.storage, job_id)
        report_id = job.get("report_id")
        if not report_id:
            raise ValueError("job missing report_id")

        max_round = int(job.get("meta", {}).get("max_round") or self.settings.max_round)
        # 整任务失败重试次数（不含首次）
        failure_retries = int(
            (job.get("meta") or {}).get("failure_retries", self.settings.job_failure_retries)
        )
        attempts = max(1, failure_retries + 1)
        last_err: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                if self.storage.is_cancel_requested(job_id):
                    raise asyncio.CancelledError()

                self.storage.update_job(
                    job_id,
                    status=JobStatus.running.value,
                    error=None,
                    progress=JobProgress(
                        phase="ingest" if attempt == 1 else "retry",
                        round=0,
                        message=("LangGraph 启动" if attempt == 1 else f"任务失败后重试（{attempt}/{attempts}）"),
                        percent=1 if attempt == 1 else 3,
                    ).model_dump(),
                )
                if attempt > 1:
                    recorder.record(
                        StepType.error,
                        title=f"自动重试 ({attempt}/{attempts})",
                        summary=f"上次失败：{last_err}；开始第 {attempt} 次尝试",
                        payload={
                            "attempt": attempt,
                            "attempts": attempts,
                            "previous_error": str(last_err),
                            "engine": "langgraph",
                        },
                        status="retry",
                    )

                initial: GraphState = {
                    "job_id": job_id,
                    "report_id": report_id,
                    "meta": job.get("meta") or {},
                    "max_round": max_round,
                    "mean_min": float(self.settings.save_mean_min),
                    "median_min": float(self.settings.save_median_min),
                    "llm_mock": bool(self.settings.use_mock_llm),
                    "engine": "langgraph",
                    "attempt": attempt,
                }

                final_state = await self.graph.ainvoke(initial)
                result = final_state.get("result")
                if not result:
                    raise RuntimeError("LangGraph finished without result")
                return result

            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < attempts and not self.storage.is_cancel_requested(job_id):
                    self.storage.update_job(
                        job_id,
                        progress=JobProgress(
                            phase="retry",
                            round=0,
                            message=f"将重试：{e}",
                            percent=2,
                        ).model_dump(),
                    )
                    await asyncio.sleep(min(2.0 * attempt, 8.0))
                    continue

                recorder.record(
                    StepType.error,
                    title="任务失败",
                    summary=str(e),
                    payload={
                        "error": str(e),
                        "engine": "langgraph",
                        "attempt": attempt,
                        "attempts": attempts,
                    },
                    status="error",
                )
                self.storage.update_job(
                    job_id,
                    status=JobStatus.failed.value,
                    error=str(e),
                    progress=JobProgress(
                        phase="error", round=0, message=str(e), percent=100
                    ).model_dump(),
                )
                raise

        assert last_err is not None
        raise last_err


def start_job_background(job_id: str) -> None:
    """Sync entry for FastAPI BackgroundTasks."""
    runner = PipelineRunner()
    asyncio.run(runner.run_job(job_id))
