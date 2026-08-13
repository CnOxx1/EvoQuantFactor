from __future__ import annotations

import json
from typing import Any

from qfactor.agent.llm import LLMClient
from qfactor.dsl.validate import validate_expression


class CandidateReviewer:
    """Rule-first review; optional LLM spot-check. LLM never decides final gate."""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    def review(
        self,
        candidate: dict[str, Any],
        tested_hashes: set[str],
        llm_spotcheck: bool = False,
    ) -> dict[str, Any]:
        v = validate_expression(candidate["expression"])
        if not v["ok"]:
            return {"ok": False, "stage": "rule", "errors": v["errors"], **v}

        from qfactor.dsl.parser import expr_hash, parse_expression

        expr = parse_expression(candidate["expression"])
        h = expr_hash(expr)
        if h in tested_hashes:
            return {"ok": False, "stage": "dedup", "errors": ["already tested"], "hash": h, **v}

        llm_note = None
        if llm_spotcheck and self.llm.enabled:
            try:
                llm_note = self.llm.chat_json(
                    "你是因子审查员，只做语义审查，输出JSON: {accept:bool, reason:str}",
                    json.dumps(
                        {
                            "expression": candidate["expression"],
                            "hypothesis": candidate.get("hypothesis"),
                        },
                        ensure_ascii=False,
                    ),
                )
            except Exception as e:
                llm_note = {"error": str(e)}
        # LLM accept=false is advisory only; parser/dedup already ran; apply_gate decides.

        return {
            "ok": True,
            "stage": "passed",
            "errors": [],
            "hash": h,
            "llm_note": llm_note,
            **v,
        }