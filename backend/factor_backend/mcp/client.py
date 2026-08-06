from __future__ import annotations

from typing import Any

from factor_backend.config import get_settings


class McpClient:
    """行情 MCP 客户端。

    当前为 stub：`MCP_ENABLED=false` 时返回 data_unavailable；
    为 true 时也只返回可追溯的空序列，**不可当作真实行情**。
    无独立 market-mcp 容器依赖。
    """

    def __init__(self, base_url: str | None = None, enabled: bool | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.mcp_market_url).rstrip("/")
        self.enabled = settings.mcp_enabled if enabled is None else enabled

    async def call(self, tool: str, **params: Any) -> dict[str, Any]:
        if not self.enabled:
            return {
                "tool": tool,
                "ok": False,
                "data_unavailable": True,
                "reason": "MCP_ENABLED=false",
                "params": params,
            }
        # stub：返回可追溯的假结构，不编造真实行情数值用于交易
        return {
            "tool": tool,
            "ok": True,
            "stub": True,
            "params": params,
            "data": {
                "note": "MCP stub — 待接入真实行情服务",
                "series": [],
            },
            "conclusion": f"已按需请求 {tool}（stub），无真实行情序列。",
        }

    async def get_kline(self, **params: Any) -> dict[str, Any]:
        return await self.call("get_kline", **params)

    async def get_volume(self, **params: Any) -> dict[str, Any]:
        return await self.call("get_volume", **params)

    async def get_turnover(self, **params: Any) -> dict[str, Any]:
        return await self.call("get_turnover", **params)

    async def get_quote(self, **params: Any) -> dict[str, Any]:
        return await self.call("get_quote", **params)

    async def get_financials(self, **params: Any) -> dict[str, Any]:
        return await self.call("get_financials", **params)

    async def get_index(self, **params: Any) -> dict[str, Any]:
        return await self.call("get_index", **params)


async def collect_mcp_evidence(prefer_tools: list[str] | None, meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    client = McpClient()
    tools = prefer_tools or ["get_kline", "get_volume"]
    meta = meta or {}
    evidence = []
    for tool in tools[:3]:
        fn = getattr(client, tool, None)
        params = {
            "symbol": (meta.get("symbols_hint") or ["UNKNOWN"])[0]
            if isinstance(meta.get("symbols_hint"), list)
            else meta.get("symbol", "UNKNOWN"),
            "start": (meta.get("date_range_hint") or [None, None])[0],
            "end": (meta.get("date_range_hint") or [None, None])[1]
            if isinstance(meta.get("date_range_hint"), list) and len(meta.get("date_range_hint") or []) > 1
            else None,
        }
        if callable(fn):
            result = await fn(**{k: v for k, v in params.items() if v is not None})
        else:
            result = await client.call(tool, **params)
        evidence.append(
            {
                "tool": tool,
                "params_summary": str(params),
                "conclusion": result.get("conclusion") or result.get("reason") or "",
                "data_unavailable": bool(result.get("data_unavailable")),
                "stub": bool(result.get("stub")),
            }
        )
    return evidence
