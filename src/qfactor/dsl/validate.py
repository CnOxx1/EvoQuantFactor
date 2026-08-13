from __future__ import annotations

from typing import Any

from qfactor.dsl.parser import Expr, parse_expression, skeleton


ALLOWED_FIELDS = {
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "close_adj",
    "vol",
    "amount",
    "ret_1d",
    "turnover_rate",
    "amplitude",
    "overnight",
    "upper_shadow",
    "lower_shadow",
}

ALLOWED_WINDOWS = {3, 5, 10, 20, 40, 60}


def _depth(node: Expr | str | int | float) -> int:
    if isinstance(node, Expr):
        return 1 + max((_depth(a) for a in node.args), default=0)
    return 0


def _nodes(node: Expr | str | int | float) -> int:
    if isinstance(node, Expr):
        return 1 + sum(_nodes(a) for a in node.args)
    return 1


def _collect_fields(node: Expr | str | int | float, out: set[str]) -> None:
    if isinstance(node, str):
        out.add(node)
    elif isinstance(node, Expr):
        for a in node.args:
            _collect_fields(a, out)


def _check_windows(node: Expr | str | int | float, errors: list[str]) -> None:
    if not isinstance(node, Expr):
        return
    if node.op in {"ma", "std", "delta", "delay", "sum", "max", "min", "roc"}:
        if len(node.args) < 2 or not isinstance(node.args[1], (int, float)):
            errors.append(f"{node.op} requires numeric window")
        else:
            w = int(node.args[1])
            if w not in ALLOWED_WINDOWS:
                errors.append(f"window {w} not allowed; use {sorted(ALLOWED_WINDOWS)}")
    for a in node.args:
        _check_windows(a, errors)


def validate_expression(text: str) -> dict[str, Any]:
    errors: list[str] = []
    try:
        expr = parse_expression(text)
    except ValueError as e:
        return {"ok": False, "errors": [str(e)]}

    fields: set[str] = set()
    _collect_fields(expr, fields)
    unknown = sorted(fields - ALLOWED_FIELDS)
    if unknown:
        errors.append(f"unknown fields: {unknown}")
    _check_windows(expr, errors)
    d = _depth(expr)
    n = _nodes(expr)
    if d < 1:
        errors.append("expression too shallow")
    if d > 6:
        errors.append(f"expression too deep: {d}")
    if n > 24:
        errors.append(f"too many nodes: {n}")
    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "expr": expr.to_str(),
        "fields": sorted(fields),
        "depth": d,
        "nodes": n,
        "skeleton": skeleton(expr),
    }