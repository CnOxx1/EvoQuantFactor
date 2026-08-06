"""独立进程入口：python -m factor_backend <api|worker|collector|summarize>"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("factor_backend")


def _prepare(*, force_env: dict[str, str] | None = None) -> None:
    if force_env:
        os.environ.update(force_env)
    from factor_backend.config import get_settings, validate_runtime_settings
    from factor_backend.db.models import init_db

    get_settings.cache_clear()
    settings = get_settings()
    validate_runtime_settings(settings)
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    init_db()


def _run_api() -> None:
    from factor_backend.main import run

    run()


def _run_worker() -> None:
    from factor_backend.services.worker import start_worker, stop_worker

    _prepare(force_env={"WORKER_ENABLED": "true"})
    start_worker()
    logger.info("standalone job worker running; Ctrl+C to stop")
    _wait_forever(stop_worker)


def _run_collector() -> None:
    from factor_backend.services.news_summarize import start_news_summarize_workers, stop_news_summarize_workers
    from factor_backend.services.report_ingest.collector import start_report_collector, stop_report_collector

    # standalone collector 强制开启采集；摘要仍可由 NEWS_SUMMARIZE_ENABLED 控制
    _prepare(force_env={"REPORT_COLLECTOR_ENABLED": "true"})
    start_news_summarize_workers()
    start_report_collector()
    logger.info("standalone collector running; Ctrl+C to stop")

    def _stop() -> None:
        stop_report_collector()
        stop_news_summarize_workers()

    _wait_forever(_stop)


def _run_summarize() -> None:
    from factor_backend.services.news_summarize import start_news_summarize_workers, stop_news_summarize_workers

    _prepare(force_env={"NEWS_SUMMARIZE_ENABLED": "true"})
    start_news_summarize_workers()
    logger.info("standalone news summarize workers running; Ctrl+C to stop")
    _wait_forever(stop_news_summarize_workers)


def _wait_forever(on_stop) -> None:
    stop = False

    def _handle(signum, frame):  # noqa: ARG001
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    try:
        while not stop:
            time.sleep(0.5)
    finally:
        on_stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factor_backend", description="EvoQuantFactor 进程入口")
    parser.add_argument(
        "command",
        choices=["api", "worker", "collector", "summarize"],
        help="api=HTTP 服务；worker=任务队列；collector=资讯采集；summarize=资讯摘要",
    )
    args = parser.parse_args(argv)
    if args.command == "api":
        _run_api()
    elif args.command == "worker":
        _run_worker()
    elif args.command == "collector":
        _run_collector()
    elif args.command == "summarize":
        _run_summarize()
    return 0


if __name__ == "__main__":
    sys.exit(main())
