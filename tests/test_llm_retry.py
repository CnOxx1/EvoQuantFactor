from __future__ import annotations

import asyncio

import pytest

from factor_backend.llm.errors import LlmError
from factor_backend.services.llm_retry import is_retryable_llm_error, retry_async


def test_retry_async_succeeds():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise LlmError("模型返回非合法 JSON: boom")
        return {"ok": True}

    result = asyncio.run(
        retry_async(flaky, attempts=3, label="demo", retryable=is_retryable_llm_error, backoff_sec=0.01)
    )
    assert result == {"ok": True}
    assert calls["n"] == 3


def test_retry_gives_up():
    async def always_fail():
        raise LlmError("bad json")

    with pytest.raises(LlmError, match="已重试 2 次"):
        asyncio.run(
            retry_async(
                always_fail,
                attempts=2,
                label="R1",
                retryable=is_retryable_llm_error,
                backoff_sec=0.01,
            )
        )


def test_non_retryable():
    async def cfg_err():
        raise LlmError("未配置 LLM API Key")

    with pytest.raises(LlmError, match="未配置"):
        asyncio.run(
            retry_async(
                cfg_err,
                attempts=3,
                label="Step1",
                retryable=is_retryable_llm_error,
                backoff_sec=0.01,
            )
        )
