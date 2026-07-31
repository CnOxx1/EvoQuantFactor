from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from factor_backend.config import get_settings
from factor_backend.db.models import reset_engine_for_tests
from factor_backend.services.pipeline import PipelineRunner
from factor_backend.services.storage import get_storage, reset_storage_singleton
from factor_backend.services.llm_config import upsert_llm_config


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("WORKER_ENABLED", "false")
    monkeypatch.setenv("LLM_MOCK", "true")
    get_settings.cache_clear()
    reset_engine_for_tests(db_url)
    reset_storage_singleton()
    upsert_llm_config({"use_mock": True, "api_key": "", "enabled": True})

    # avoid lifespan worker; import app after env
    from factor_backend.main import app

    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()
    reset_storage_singleton()


def test_health_and_meta(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "langgraph"
    assert body["storage"] == "sqlalchemy"

    r = client.get("/api/v1/meta")
    assert r.status_code == 200
    assert "llm" in r.json()
    assert r.json()["endpoints"]["llm_config"] == "/api/v1/llm/config"


def test_llm_config_api(client):
    r = client.get("/api/v1/llm/config")
    assert r.status_code == 200
    assert r.json()["use_mock"] is True
    assert r.json().get("api_format", "openai") == "openai"

    r = client.put(
        "/api/v1/llm/config",
        json={
            "use_mock": True,
            "api_format": "cursor",
            "base_url": "https://api.cursor.com",
            "model_step1": "composer-2.5",
            "model_review": "composer-2.5",
            "api_key": "sk-test-key-123456",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["api_format"] == "cursor"
    assert data["base_url"] == "https://api.cursor.com"
    assert data["api_key_set"] is True
    assert "sk-" in data["api_key_masked"] or "*" in data["api_key_masked"]
    assert data["api_key_masked"] != "sk-test-key-123456"

    r = client.get("/api/v1/llm/config")
    assert r.status_code == 200
    assert r.json()["api_format"] == "cursor"

    r = client.post("/api/v1/llm/test")
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "Mock" in r.json()["message"] or "mock" in r.json()["message"].lower()

    r = client.post(
        "/api/v1/llm/test",
        json={
            "use_mock": False,
            "api_format": "anthropic",
            "base_url": "https://api.anthropic.com/v1",
            "api_key": "sk-test-key-123456",
            "model_review": "claude-sonnet-4-20250514",
        },
    )
    assert r.status_code == 200
    # 无真实外网调用成功时，应返回明确失败信息（而非 500）
    body = r.json()
    assert "ok" in body
    assert isinstance(body["message"], str)


def test_auth_required_when_enabled(tmp_path, monkeypatch):
    db_url = f"sqlite:///{(tmp_path / 'auth.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("WORKER_ENABLED", "false")
    get_settings.cache_clear()
    reset_engine_for_tests(db_url)
    reset_storage_singleton()

    from factor_backend.main import app

    with TestClient(app) as c:
        r = c.get("/api/v1/llm/config")
        assert r.status_code == 401
        r = c.get("/api/v1/llm/config", headers={"Authorization": "Bearer secret-token"})
        assert r.status_code == 200

    get_settings.cache_clear()
    reset_storage_singleton()


def test_job_flow_db_workerless(client):
    r = client.post(
        "/api/v1/reports/text",
        json={
            "title": "db flow",
            "content": "讨论换手率因子与估值PE分位数，以及ROE质量。",
        },
    )
    assert r.status_code == 200
    report_id = r.json()["report_id"]

    r = client.post("/api/v1/jobs", json={"report_id": report_id, "max_round": 2})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "queued"

    asyncio.run(PipelineRunner(storage=get_storage()).run_job(job_id))

    r = client.get(f"/api/v1/jobs/{job_id}")
    assert r.json()["status"] == "succeeded"

    r = client.get(f"/api/v1/jobs/{job_id}/factors")
    assert r.status_code == 200
    assert len(r.json()) >= 1

    r = client.get(f"/api/v1/jobs/{job_id}/steps")
    assert len(r.json()) >= 3
    # 并行评审后应有权重字段
    detail_id = next(s["step_id"] for s in r.json() if s["step_type"] == "step2_review")
    d = client.get(f"/api/v1/jobs/{job_id}/steps/{detail_id}").json()
    reviews = d["payload"]["reviews"]
    first = next(iter(reviews.values()))
    assert "weights" in first or "total_score" in first


def test_prompts_weights_api(client):
    r = client.get("/api/v1/prompts")
    assert r.status_code == 200
    keys = {x["key"] for x in r.json()}
    assert "R1" in keys and "step1_extract" in keys

    r = client.put(
        "/api/v1/prompts/R1",
        json={"weights": {"Logic": 50, "Implementability": 50}, "name": "R1自定义"},
    )
    assert r.status_code == 200
    assert r.json()["source"] == "db_override"
    assert r.json()["weights"]["Logic"] == 50

    r = client.post("/api/v1/prompts/R1/reset")
    assert r.status_code == 200
    assert r.json()["source"] == "file"


def test_cancel_queued_job(client):
    r = client.post(
        "/api/v1/reports/text",
        json={"title": "c", "content": "换手率与估值因子"},
    )
    report_id = r.json()["report_id"]
    r = client.post("/api/v1/jobs", json={"report_id": report_id})
    job_id = r.json()["job_id"]
    r = client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_batch_multi_reports(client):
    r = client.post(
        "/api/v1/batches",
        json={
            "title": "multi",
            "items": [
                {"title": "a", "content": "换手率动量因子研究"},
                {"title": "b", "content": "估值PE分位数与ROE质量"},
            ],
            "max_round": 1,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["jobs"]) == 2
    batch_id = body["batch_id"]

    for job in body["jobs"]:
        asyncio.run(PipelineRunner(storage=get_storage()).run_job(job["job_id"]))

    r = client.get(f"/api/v1/batches/{batch_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["counts"]["succeeded"] == 2
    assert data["percent"] == 100
    assert data["status"] == "succeeded"
