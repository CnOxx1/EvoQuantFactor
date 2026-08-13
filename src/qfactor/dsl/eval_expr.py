from __future__ import annotations

import numpy as np
import pandas as pd

from qfactor.dsl.parser import Expr
from qfactor.factor.context import FactorContext
from qfactor.factor.transforms import rank, zscore


def _derived_panels(ctx: FactorContext) -> dict[str, pd.DataFrame]:
    close = ctx.panel("close")
    open_ = ctx.panel("open")
    high = ctx.panel("high")
    low = ctx.panel("low")
    pre = ctx.panel("pre_close") if "pre_close" in ctx._bars.columns else close.shift(1)
    amp = (high - low) / close.replace(0, np.nan)
    overnight = open_ / pre.replace(0, np.nan) - 1.0
    upper = (high - pd.DataFrame(np.maximum(open_.to_numpy(), close.to_numpy()), index=open_.index, columns=open_.columns)) / close.replace(0, np.nan)
    lower = (pd.DataFrame(np.minimum(open_.to_numpy(), close.to_numpy()), index=open_.index, columns=open_.columns) - low) / close.replace(0, np.nan)
    return {
        "amplitude": amp,
        "overnight": overnight,
        "upper_shadow": upper,
        "lower_shadow": lower,
    }


def evaluate_expression(expr: Expr | str | int | float, ctx: FactorContext) -> pd.DataFrame:
    derived = _derived_panels(ctx)

    def ev(node: Expr | str | int | float) -> pd.DataFrame | float:
        if isinstance(node, (int, float)):
            return float(node)
        if isinstance(node, str):
            if node in derived:
                return derived[node]
            return ctx.panel(node)
        assert isinstance(node, Expr)
        op = node.op
        args = node.args
        if op == "ma":
            x, n = ev(args[0]), int(args[1])  # type: ignore[arg-type]
            return x.rolling(n).mean()  # type: ignore[union-attr]
        if op == "std":
            x, n = ev(args[0]), int(args[1])  # type: ignore[arg-type]
            return x.rolling(n).std()  # type: ignore[union-attr]
        if op == "sum":
            x, n = ev(args[0]), int(args[1])  # type: ignore[arg-type]
            return x.rolling(n).sum()  # type: ignore[union-attr]
        if op == "max":
            x, n = ev(args[0]), int(args[1])  # type: ignore[arg-type]
            return x.rolling(n).max()  # type: ignore[union-attr]
        if op == "min":
            x, n = ev(args[0]), int(args[1])  # type: ignore[arg-type]
            return x.rolling(n).min()  # type: ignore[union-attr]
        if op == "delay":
            x, n = ev(args[0]), int(args[1])  # type: ignore[arg-type]
            return x.shift(n)  # type: ignore[union-attr]
        if op == "delta":
            x, n = ev(args[0]), int(args[1])  # type: ignore[arg-type]
            return x - x.shift(n)  # type: ignore[operator, union-attr]
        if op == "roc":
            x, n = ev(args[0]), int(args[1])  # type: ignore[arg-type]
            return x.pct_change(n)  # type: ignore[union-attr]
        if op == "rank":
            return rank(ev(args[0]))  # type: ignore[arg-type]
        if op == "zscore":
            return zscore(ev(args[0]))  # type: ignore[arg-type]
        if op == "abs":
            return ev(args[0]).abs()  # type: ignore[union-attr]
        if op == "neg":
            return -ev(args[0])  # type: ignore[operator]
        if op == "log":
            x = ev(args[0])
            return np.log(x.clip(lower=1e-12))  # type: ignore[union-attr]
        if op in {"add", "sub", "mul", "div"}:
            a, b = ev(args[0]), ev(args[1])
            if op == "add":
                return a + b  # type: ignore[operator]
            if op == "sub":
                return a - b  # type: ignore[operator]
            if op == "mul":
                return a * b  # type: ignore[operator]
            return a / b.replace(0, np.nan) if isinstance(b, pd.DataFrame) else a / (b if b != 0 else np.nan)  # type: ignore[operator]
        raise ValueError(f"Unknown op {op}")

    out = ev(expr)
    if not isinstance(out, pd.DataFrame):
        raise ValueError("Expression must evaluate to a panel DataFrame")
    return out