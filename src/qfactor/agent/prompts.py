from __future__ import annotations

import json
from typing import Any


SYSTEM_IDEATION = """你是 A 股量化研究员。当前只允许开发中证100、日频、量价因子。
必须遵守：
1. 只能使用提供的字段字典
2. 禁止未来函数；默认 trade_lag=1
3. 输出严格 JSON，不要 markdown
"""

SYSTEM_CODER = """你是量化工程师。把因子假设实现为 Python 代码。
要求：
1. 定义 build() -> Factor
2. 继承 qfactor.factor.base.Factor
3. compute(self, ctx) 返回 DataFrame (index=trade_date, columns=ts_code)
4. 只用 ctx.panel(field) 与 ctx.shift_safe
5. 只输出 JSON: {"factor_py": "...", "spec": {...}}
"""

SYSTEM_REVIEWER = """你是量化研究审稿人。根据回测指标给出修改建议。
只输出 JSON: {"action":"revise|accept|reject","reason":"...","revision_hints":["..."]}
"""


def ideation_user_prompt(
    theme: str,
    fields: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> str:
    allowed = [
        f
        for f in fields
        if f.get("family") == "price_volume" and f.get("name") not in {"trade_date", "ts_code"}
    ]
    return json.dumps(
        {
            "theme": theme,
            "universe": "csi100",
            "frequency": "daily",
            "allowed_fields": allowed,
            "existing_factors": existing,
            "output_schema": {
                "name": "snake_case",
                "hypothesis": "str",
                "category": "momentum|reversal|liquidity|volatility|volume_price",
                "required_fields": ["close_adj"],
                "lookback": 20,
                "horizon": 5,
                "formula_draft": "str",
                "params": {},
            },
        },
        ensure_ascii=False,
        indent=2,
    )


FACTOR_CODE_TEMPLATE = '''from __future__ import annotations

import pandas as pd

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import rank, winsorize, zscore


class GeneratedFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="{name}",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="{category}",
            required_fields={required_fields},
            lookback={lookback},
            horizon={horizon},
            params={params},
            tags=["llm"],
            hypothesis="""{hypothesis}""",
            entry_gate="research",
        )

    def compute(self, ctx) -> pd.DataFrame:
        # TODO: implement formula: {formula_draft}
        close = ctx.panel("close_adj")
        raw = -close.pct_change({lookback})
        return zscore(winsorize(raw))


def build() -> Factor:
    return GeneratedFactor()
'''