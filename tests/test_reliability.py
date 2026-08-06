from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factor_backend.config import Settings, get_settings, validate_runtime_settings
from factor_backend.db.models import reset_engine_for_tests
from factor_backend.services.factor_library import (
    _library_dir,
    upsert_job_factors_to_workspace,
)
from factor_backend.services.llm_config import upsert_llm_config
from factor_backend.services.pipeline import PipelineRunner
from factor_backend.services.storage import get_storage, reset_storage_singleton
from factor_backend.services import worker as worker_mod


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WORKER_ENABLED", "false")
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setenv("REPORT_COLLECTOR_ENABLED", "false")
    monkeypatch.setenv("NEWS_SUMMARIZE_ENABLED", "false")
    get_settings.cache_clear()
    reset_engine_for_tests(db_url)
    reset_storage_singleton()
    upsert_llm_config({"use_mock": True, "api_key": "", "enabled": True})

    from factor_backend.main import app

    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()
    reset_storage_singleton()


def test_production_rejects_insecure_defaults(monkeypatch):
    get_settings.cache_clear()
    s = Settings(
        app_env="production",
        auth_disabled=True,
        api_token="",
        strict_production=True,
    )
    with pytest.raises(RuntimeError, match="AUTH_DISABLED"):
        validate_runtime_settings(s)


def test_production_rejects_placeholder_token():
    s = Settings(
        app_env="production",
        auth_disabled=False,
        api_token="please-change-me",
        strict_production=True,
    )
    with pytest.raises(RuntimeError, match="占位"):
        validate_runtime_settings(s)


def test_production_ok_with_token():
    s = Settings(
        app_env="production",
        auth_disabled=False,
        api_token="a-strong-random-token-value",
        strict_production=True,
        cors_origins="http://localhost:5174",
        llm_mock=False,
    )
    warnings = validate_runtime_settings(s)
    assert not any("AUTH_DISABLED" in w for w in warnings)


def test_health_exposes_warnings(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "warnings" in body
    assert any("AUTH_DISABLED" in w for w in body["warnings"])
    assert "worker" in body


def test_enqueue_job_wakes_and_validates(client, monkeypatch):
    r = client.post(
        "/api/v1/reports/text",
        json={"title": "wake", "content": "换手率与动量因子"},
    )
    report_id = r.json()["report_id"]
    r = client.post("/api/v1/jobs", json={"report_id": report_id, "max_round": 1})
    job_id = r.json()["job_id"]

    worker_mod._wake.clear()
    worker_mod.enqueue_job(job_id)
    assert worker_mod._wake.is_set()

    with pytest.raises(FileNotFoundError):
        worker_mod.enqueue_job("job_does_not_exist")


def test_factor_library_atomic_write(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "factor_backend.services.factor_library.repo_root",
        lambda: tmp_path,
    )
    pack_dir = tmp_path / "data" / "factor_library"
    pack_dir.mkdir(parents=True)

    saved = [
        {
            "factor_id": "F1",
            "name_zh": "测试因子",
            "formula_or_rule": "rank(close/open)",
            "final_score": 88,
            "reason": "ok",
        }
    ]
    out = upsert_job_factors_to_workspace(job_id="job_t1", saved=saved)
    assert out["count"] == 1
    path = pack_dir / "workspace.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["factors"][0]["origin_factor_id"] == "F1"
    # 不应残留临时文件
    assert not list(pack_dir.glob(".workspace.*.tmp"))


def test_library_write_failure_surfaces_on_job(client, monkeypatch):
    def boom(**_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(
        "factor_backend.graph.nodes.upsert_job_factors_to_workspace",
        boom,
    )

    r = client.post(
        "/api/v1/reports/text",
        json={"title": "lib fail", "content": "换手率因子与估值PE分位数"},
    )
    report_id = r.json()["report_id"]
    r = client.post("/api/v1/jobs", json={"report_id": report_id, "max_round": 1})
    job_id = r.json()["job_id"]

    asyncio.run(PipelineRunner(storage=get_storage()).run_job(job_id))

    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "succeeded"
    assert job.get("error") and "library_write_failed" in job["error"]
    assert "disk full" in job["error"]

    steps = client.get(f"/api/v1/jobs/{job_id}/steps").json()
    persist = next(s for s in steps if s["step_type"] == "persist")
    assert persist["status"] == "warning"
    detail = client.get(f"/api/v1/jobs/{job_id}/steps/{persist['step_id']}").json()
    assert detail["payload"]["library_write_ok"] is False
