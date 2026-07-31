from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from factor_backend.llm.errors import LlmError

T = TypeVar("T")


async def retry_async(
    factory: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    label: str,
    retryable: Callable[[Exception], bool] | None = None,
    backoff_sec: float = 1.5,
) -> T:
    """失败前自动重试。attempts 含首次，至少为 1。"""
    n = max(1, int(attempts))
    last: Exception | None = None
    for i in range(n):
        try:
            return await factory()
        except Exception as e:  # noqa: BLE001
            last = e
            if retryable is not None and not retryable(e):
                raise
            if i >= n - 1:
                break
            await asyncio.sleep(backoff_sec * (i + 1))
    assert last is not None
    if isinstance(last, LlmError):
        raise LlmError(f"{label} 已重试 {n} 次仍失败: {last}") from last
    raise LlmError(f"{label} 已重试 {n} 次仍失败: {last}") from last


def is_retryable_llm_error(exc: Exception) -> bool:
    """网络/限流/JSON 解析类错误可重试；配置类错误不重试。"""
    msg = str(exc).lower()
    if "use_mock=true" in msg or "未配置" in msg or "api key" in msg:
        return False
    if "未就绪" in msg:
        return False
    if "超时" in msg or "timeout" in msg:
        return False
    return True
