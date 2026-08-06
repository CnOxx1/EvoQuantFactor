from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from factor_backend.config import Settings, get_settings
from factor_backend.db.models import get_engine, reset_engine_for_tests
from factor_backend.services.agents import run_role_reviews
from factor_backend.services.llm_config import upsert_llm_config
from factor_backend.services.prompt_config import (
    get_prompt_config,
    invalidate_prompt_cache,
    upsert_prompt_config,
)
from factor_backend.services.storage import reset_storage_singleton


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    db_url = f"sqlite:///{(tmp_path / 'opt.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WORKER_ENABLED", "false")
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setenv("REPORT_COLLECTOR_ENABLED", "false")
    monkeypatch.setenv("NEWS_SUMMARIZE_ENABLED", "false")
    monkeypatch.setenv("REVIEW_CONCURRENCY", "2")
    get_settings.cache_clear()
    reset_engine_for_tests(db_url)
    reset_storage_singleton()
    upsert_llm_config({"use_mock": True, "api_key": "", "enabled": True})
    invalidate_prompt_cache()
    yield
    get_settings.cache_clear()
    reset_storage_singleton()
    invalidate_prompt_cache()


def test_default_sources_and_side_workers_off():
    assert "eastmoney_report" in Settings.model_fields["report_collector_sources"].default
    assert "luobo" not in Settings.model_fields["report_collector_sources"].default
    assert Settings.model_fields["report_collector_enabled"].default is False
    assert Settings.model_fields["news_summarize_enabled"].default is False
    assert Settings.model_fields["news_summarize_workers"].default == 2
    assert Settings.model_fields["review_concurrency"].default == 3


def test_sqlite_wal_enabled(db_env):
    engine = get_engine()
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        busy = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert str(mode).lower() == "wal"
    assert int(busy) >= 5000


def test_prompt_cache_invalidates_on_upsert(db_env):
    before = get_prompt_config("R1")
    assert before["source"] in {"file", "db_override"}
    upsert_prompt_config("R1", {"name": "R1-cache-test"})
    after = get_prompt_config("R1")
    assert after["name"] == "R1-cache-test"
    assert after["source"] == "db_override"


def test_review_concurrency_respected(db_env, monkeypatch):
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def fake_mock(*, role_code, role_name, targets, meta=None):
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.05)
        async with lock:
            active -= 1
        out = {}
        for f in targets:
            out[f["factor_id"]] = {
                "role_code": role_code,
                "role_name": role_name,
                "factor_id": f["factor_id"],
                "total_score": 90,
                "comment": "ok",
                "suggestions": [],
                "veto": False,
                "veto_reason": None,
                "mcp_evidence": [],
                "data_unavailable": False,
                "subscores": {"Logic": 90},
                "weights": {"Logic": 100},
            }
        return role_code, out

    monkeypatch.setattr("factor_backend.services.agents._review_one_role_mock", fake_mock)
    factors = [
        {
            "factor_id": "F1",
            "name_zh": "t",
            "definition": {"formula_or_rule": "rank(close)", "inputs": ["close"]},
        }
    ]
    cards = asyncio.run(run_role_reviews(factors=factors, factor_ids=["F1"]))
    assert "F1" in cards
    assert len(cards["F1"]) == 6
    assert peak <= 2


def test_steps_include_payload_flag(db_env, monkeypatch):
    from fastapi.testclient import TestClient
    from factor_backend.main import app
    from factor_backend.services.pipeline import PipelineRunner
    from factor_backend.services.storage import get_storage

    monkeypatch.setenv("REPORT_COLLECTOR_ENABLED", "false")
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/reports/text",
            json={"title": "opt", "content": "换手率动量与估值PE分位数因子"},
        )
        rid = r.json()["report_id"]
        jid = client.post("/api/v1/jobs", json={"report_id": rid, "max_round": 1}).json()["job_id"]
        asyncio.run(PipelineRunner(storage=get_storage()).run_job(jid))
        full = client.get(f"/api/v1/jobs/{jid}/steps").json()
        light = client.get(f"/api/v1/jobs/{jid}/steps", params={"include_payload": False}).json()
        assert full and light
        assert any(s.get("payload") for s in full)
        assert all(s.get("payload") == {} for s in light)


def test_list_reports_job_count_batch(db_env):
    from fastapi.testclient import TestClient
    from factor_backend.main import app

    with TestClient(app) as client:
        rids = []
        for i in range(3):
            rid = client.post(
                "/api/v1/reports/text",
                json={"title": f"r{i}", "content": f"换手率因子研究样本 {i}"},
            ).json()["report_id"]
            rids.append(rid)
        client.post("/api/v1/jobs", json={"report_id": rids[0], "max_round": 1})
        client.post("/api/v1/jobs", json={"report_id": rids[0], "max_round": 1})
        client.post("/api/v1/jobs", json={"report_id": rids[1], "max_round": 1})
        items = client.get("/api/v1/reports", params={"limit": 20}).json()["items"]
        by_id = {x["report_id"]: x["job_count"] for x in items}
        assert by_id[rids[0]] == 2
        assert by_id[rids[1]] == 1
        assert by_id[rids[2]] == 0


def test_metrics_endpoint(db_env):
    from fastapi.testclient import TestClient
    from factor_backend.main import app
    from factor_backend.services import metrics

    metrics.reset_for_tests()
    metrics.incr("jobs_claimed_total", 2)
    with TestClient(app) as client:
        body = client.get("/metrics").json()
        assert body["counters"]["jobs_claimed_total"] == 2
        assert "worker" in body
        assert "news_summarize" in body
        assert body["config"]["review_concurrency"] >= 1
