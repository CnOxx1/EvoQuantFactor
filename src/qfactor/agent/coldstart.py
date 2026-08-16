from __future__ import annotations

from collections import Counter
from typing import Any

from qfactor.dsl.parser import Expr, parse_expression
from qfactor.eval.gate import KEEP_STATUSES
from qfactor.factor.base import FactorSpec
from qfactor.settings import ProjectConfig, get_project_config

TS_OPS = {"ma", "std", "delta", "delay", "sum", "max", "min", "roc"}

DEFAULTS = {
    "min_parents": 8,
    "disable_fsa": True,
    "llm_fresh_ratio": 0.30,
    "curriculum": True,
    "prior_update_every": 20,
    "cheap_ic_min": 0.008,
    "parent_top_screened": 12,
}

# Classic A-share price-volume priors, expressed in the same DSL the miner mutates.
DSL_SEEDS: list[dict[str, str]] = [
    {
        "name": "seed_overnight_ma_20",
        "mechanism": "overnight",
        "expression": "ma(overnight,20)",
        "hypothesis": "隔夜收益的中期均值；A股跳空溢价先验",
    },
    {
        "name": "seed_overnight_vs_amp_20",
        "mechanism": "overnight",
        "expression": "sub(ma(overnight,20),ma(amplitude,20))",
        "hypothesis": "跳空均值相对振幅均值的背离",
    },
    {
        "name": "seed_overnight_amp_div_20",
        "mechanism": "overnight",
        "expression": "div(overnight,ma(amplitude,20))",
        "hypothesis": "隔夜收益相对近期振幅的标准化",
    },
    {
        "name": "seed_reversal_5",
        "mechanism": "reversal",
        "expression": "neg(roc(close_adj,5))",
        "hypothesis": "5日收益反转",
    },
    {
        "name": "seed_reversal_10",
        "mechanism": "reversal",
        "expression": "neg(roc(close_adj,10))",
        "hypothesis": "10日收益反转",
    },
    {
        "name": "seed_amihud_20",
        "mechanism": "liquidity",
        "expression": "ma(div(abs(ret_1d),amount),20)",
        "hypothesis": "Amihud非流动性",
    },
    {
        "name": "seed_amplitude_20",
        "mechanism": "amplitude",
        "expression": "ma(amplitude,20)",
        "hypothesis": "20日平均振幅",
    },
    {
        "name": "seed_realized_vol_20",
        "mechanism": "volatility",
        "expression": "std(ret_1d,20)",
        "hypothesis": "已实现波动",
    },
    {
        "name": "seed_turnover_20",
        "mechanism": "liquidity",
        "expression": "ma(turnover_rate,20)",
        "hypothesis": "换手率均值",
    },
    {
        "name": "seed_lower_shadow_20",
        "mechanism": "shadow",
        "expression": "ma(lower_shadow,20)",
        "hypothesis": "下影线均值",
    },
    {
        "name": "seed_volume_shock_20",
        "mechanism": "liquidity",
        "expression": "div(vol,ma(vol,20))",
        "hypothesis": "成交量相对20日均值",
    },
]


def cold_start_cfg(cfg: ProjectConfig | None = None) -> dict[str, Any]:
    cfg = cfg or get_project_config()
    raw = ((cfg.project.get("production") or {}).get("cold_start") or {})
    out = dict(DEFAULTS)
    out.update({k: raw[k] for k in raw if k in DEFAULTS})
    out["min_parents"] = int(out["min_parents"])
    out["disable_fsa"] = bool(out["disable_fsa"])
    out["llm_fresh_ratio"] = float(out["llm_fresh_ratio"])
    out["curriculum"] = bool(out["curriculum"])
    out["prior_update_every"] = int(out["prior_update_every"])
    out["cheap_ic_min"] = float(out["cheap_ic_min"])
    out["parent_top_screened"] = int(out["parent_top_screened"])
    return out


def parent_count(
    existing: list[dict[str, Any]] | None,
    current_data_version: str | None = None,
) -> int:
    """Count KEEP-status parents that are eligible on the live data version.

    Legacy snapshot / unverified screened rows do not heat the library. Missing
    ``parent_eligible`` is classified rather than treated as a production parent.
    """
    from qfactor.factor.cohort import apply_parent_eligibility

    n = 0
    for item in existing or []:
        if str(item.get("status") or "") not in KEEP_STATUSES:
            continue
        if apply_parent_eligibility(item, current_data_version).get("parent_eligible"):
            n += 1
    return n


def is_cold_start(
    existing: list[dict[str, Any]] | None,
    cfg: ProjectConfig | None = None,
    *,
    current_data_version: str | None = None,
) -> bool:
    return parent_count(existing, current_data_version) < int(cold_start_cfg(cfg)["min_parents"])


def _walk_fields_windows(node: Expr | str | int | float, fields: set[str], windows: list[int]) -> None:
    if isinstance(node, str):
        fields.add(node)
        return
    if not isinstance(node, Expr):
        return
    if node.op in TS_OPS and len(node.args) >= 2 and isinstance(node.args[-1], (int, float)):
        windows.append(int(node.args[-1]))
    for a in node.args:
        _walk_fields_windows(a, fields, windows)


def collect_fields_windows(expr_text: str) -> tuple[set[str], list[int]]:
    fields: set[str] = set()
    windows: list[int] = []
    try:
        _walk_fields_windows(parse_expression(expr_text), fields, windows)
    except Exception:
        return set(), []
    return fields, windows


def _abs_ic(summary: dict[str, Any] | None, prefer_oos: bool) -> float:
    """Prefer residual/holdout IC on the hot path so train-fit does not steer search."""
    src = summary if isinstance(summary, dict) else {}
    keys = (
        ("resid_ic_mean", "holdout_ic_mean", "oos_ic_mean", "rank_ic_mean")
        if prefer_oos
        else ("rank_ic_mean",)
    )
    for key in keys:
        raw = src.get(key)
        if raw is None or raw == "":
            continue
        try:
            return abs(float(raw))
        except (TypeError, ValueError):
            continue
    return 0.0


def field_window_prior(
    lessons: list[dict[str, Any]] | None,
    existing: list[dict[str, Any]] | None,
    *,
    blocked_mechanisms: set[str] | None = None,
    blocked_fields: set[str] | None = None,
    prefer_oos: bool = False,
) -> tuple[dict[str, float], dict[int, float]]:
    """IC-weighted field/window prior. Failures still count (CICC first adaptation).

    Hot library: pass blocked production families/fields and prefer_oos so amplitude
    candidates cannot keep boosting amplitude/overnight templates.
    """
    field_w: Counter[str] = Counter()
    win_w: Counter[int] = Counter()
    blocked_mechs = {str(m) for m in (blocked_mechanisms or set())}
    skip_fields = {str(f) for f in (blocked_fields or set())}

    def _add(expr: str | None, ic: float, base: float) -> None:
        if not expr:
            return
        fields, windows = collect_fields_windows(str(expr))
        w = base + 10.0 * abs(float(ic))
        for f in fields:
            if f in skip_fields:
                continue
            field_w[f] += w
        for n in windows:
            win_w[n] += w

    for item in existing or []:
        mid = str(item.get("mechanism") or item.get("category") or "").strip()
        if mid and mid in blocked_mechs:
            continue
        expr = item.get("expression")
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        ic = _abs_ic(summary, prefer_oos)
        status = str(item.get("status") or "")
        base = 2.0 if status in KEEP_STATUSES else 0.5
        _add(expr if isinstance(expr, str) else None, ic, base)
    for lesson in lessons or []:
        mid = str(lesson.get("mechanism") or "").strip()
        if mid and mid in blocked_mechs:
            continue
        detail = lesson.get("detail") if isinstance(lesson.get("detail"), dict) else {}
        ic = _abs_ic(detail, prefer_oos)
        _add(lesson.get("expression"), ic, 0.4)
    return dict(field_w), dict(win_w)


def weighted_sample(weights: dict[Any, float], options: list[Any]) -> Any:
    if not options:
        raise ValueError("no options")
    scored = []
    for opt in options:
        scored.append((max(float(weights.get(opt, 0.0)), 0.05), opt))
    total = sum(s for s, _ in scored)
    import random

    r = random.random() * total
    acc = 0.0
    for s, opt in scored:
        acc += s
        if r <= acc:
            return opt
    return scored[-1][1]


def dsl_factor_source(name: str, expression: str, mechanism: str, hypothesis: str) -> str:
    return f'''from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="{name}",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="{mechanism}",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={{"expression": """{expression}"""}},
            tags=["dsl", "seed"],
            hypothesis="""{hypothesis}""",
            entry_gate="research",
            expression="""{expression}""",
            mechanism="{mechanism}",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
'''


def ensure_dsl_seeds(cfg: ProjectConfig | None = None) -> list[str]:
    """Write missing DSL seed factors so they can be mutated. Does not overwrite."""
    from qfactor.factor.registry import FactorRegistry

    cfg = cfg or get_project_config()
    reg = FactorRegistry(cfg)
    have = {str(f.get("name")) for f in reg.list_factors()}
    saved: list[str] = []
    for seed in DSL_SEEDS:
        name = seed["name"]
        if name in have:
            continue
        spec = FactorSpec(
            name=name,
            status="draft",
            family="price_volume",
            category=seed["mechanism"],
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            tags=["dsl", "seed"],
            hypothesis=seed["hypothesis"],
            entry_gate="research",
            expression=seed["expression"],
            mechanism=seed["mechanism"],
        )
        code = dsl_factor_source(
            name, seed["expression"], seed["mechanism"], seed["hypothesis"]
        )
        reg.save_factor_files(spec, code, source="seed")
        saved.append(name)
    return saved
