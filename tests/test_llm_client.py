from __future__ import annotations

import asyncio
import json

import httpx

from factor_backend.llm.client import LlmClient
from factor_backend.services.llm_config import LlmRuntimeConfig


def _cfg(**kwargs) -> LlmRuntimeConfig:
    base = dict(
        enabled=True,
        use_mock=False,
        api_format="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        model_step1="gpt-4o",
        model_review="gpt-4o-mini",
        timeout_sec=30.0,
        max_retries=0,
    )
    base.update(kwargs)
    return LlmRuntimeConfig(**base)


def test_openai_chat_completions_path(monkeypatch):
    captured: dict = {}

    async def fake_post(self, url, headers=None, json=None):  # noqa: A002
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ping": true}'}}]},
            request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = LlmClient(_cfg(api_format="openai"))
    result = asyncio.run(client.chat_json(system="sys", user="user"))
    assert result == {"ping": True}
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"].startswith("Bearer ")
    assert captured["json"]["response_format"]["type"] == "json_object"


def test_anthropic_messages_path(monkeypatch):
    captured: dict = {}

    async def fake_post(self, url, headers=None, json=None):  # noqa: A002
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": '{"ping": true}'},
                ]
            },
            request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = LlmClient(
        _cfg(
            api_format="anthropic",
            base_url="https://api.anthropic.com/v1",
            model_step1="claude-opus-4-20250514",
        )
    )
    result = asyncio.run(
        client.chat_json(system="Return JSON only.", user='{"ping": true}')
    )
    assert result == {"ping": True}
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-test"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in captured["headers"]
    assert captured["json"]["system"] == "Return JSON only."
    assert captured["json"]["messages"] == [{"role": "user", "content": '{"ping": true}'}]
    assert captured["json"]["max_tokens"] == 8192
    assert "response_format" not in captured["json"]


def test_test_connection_skips_json_mode(monkeypatch):
    captured: dict = {}

    async def fake_post(self, url, headers=None, json=None):  # noqa: A002
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "pong"}}]},
            request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = LlmClient(_cfg(api_format="openai"))
    result = asyncio.run(client.test_connection())
    assert result["ok"] is True
    assert "pong" in result["sample"]
    assert "response_format" not in captured["json"]


def test_build_request_openai_vs_anthropic():
    oai = LlmClient(_cfg(api_format="openai"))
    url, headers, payload = oai._build_request(system="s", user="u", model=None, temperature=0.1)
    assert url.endswith("/chat/completions")
    assert "Bearer" in headers["Authorization"]

    ant = LlmClient(_cfg(api_format="anthropic", base_url="https://api.anthropic.com/v1"))
    url, headers, payload = ant._build_request(
        system="s", user="u", model="claude-opus-4", temperature=0.1
    )
    assert url.endswith("/messages")
    assert headers["x-api-key"] == "sk-test"
    assert payload["model"] == "claude-opus-4"
    assert json.dumps(payload)  # serializable


def test_cursor_cloud_agents_chat_json(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_post(self, url, headers=None, json=None):  # noqa: A002
        calls.append(("POST", url))
        req = httpx.Request("POST", url)
        assert url.endswith("/v1/agents")
        assert headers["Authorization"] == "Bearer sk-test"
        assert json["prompt"]["text"]
        assert json["model"]["id"] == "composer-2.5"
        return httpx.Response(
            200,
            json={
                "agent": {"id": "bc-1", "latestRunId": "run-1"},
                "run": {"id": "run-1", "status": "RUNNING"},
            },
            request=req,
        )

    async def fake_get(self, url, headers=None):
        calls.append(("GET", url))
        req = httpx.Request("GET", url)
        assert "/v1/agents/bc-1/runs/run-1" in url
        return httpx.Response(
            200,
            json={
                "id": "run-1",
                "agentId": "bc-1",
                "status": "FINISHED",
                "result": '{"ping": true, "source": "cursor"}',
                "durationMs": 1200,
            },
            request=req,
        )

    async def fake_delete(self, url, headers=None):
        calls.append(("DELETE", url))
        req = httpx.Request("DELETE", url)
        return httpx.Response(200, json={"id": "bc-1"}, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "delete", fake_delete)

    client = LlmClient(
        _cfg(
            api_format="cursor",
            base_url="https://api.cursor.com",
            model_step1="composer-2.5",
            model_review="composer-2.5",
        )
    )
    result = asyncio.run(client.chat_json(system="sys", user="user"))
    assert result == {"ping": True, "source": "cursor"}
    assert any(m == "POST" and u.endswith("/v1/agents") for m, u in calls)
    assert any(m == "GET" for m, u in calls)


def test_cursor_test_connection(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):  # noqa: A002
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "agent": {"id": "bc-2"},
                "run": {"id": "run-2", "status": "FINISHED", "result": "pong"},
            },
            request=req,
        )

    async def fake_get(self, url, headers=None):
        req = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={"id": "run-2", "status": "FINISHED", "result": "pong"},
            request=req,
        )

    async def fake_delete(self, url, headers=None):
        req = httpx.Request("DELETE", url)
        return httpx.Response(204, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "delete", fake_delete)

    client = LlmClient(
        _cfg(api_format="cursor", base_url="https://api.cursor.com", model_review="composer-2.5")
    )
    result = asyncio.run(client.test_connection())
    assert result["ok"] is True
    assert "pong" in result["sample"]
    assert result["api_format"] == "cursor"
