from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any


@dataclass
class Expr:
    op: str
    args: list[Any]  # Expr | str | int | float

    def to_str(self) -> str:
        parts = []
        for a in self.args:
            if isinstance(a, Expr):
                parts.append(a.to_str())
            elif isinstance(a, str):
                parts.append(a)
            else:
                parts.append(str(a))
        return f"{self.op}({', '.join(parts)})"


_ALLOWED_FUNCS = {
    "ma",
    "std",
    "delta",
    "delay",
    "sum",
    "max",
    "min",
    "roc",
    "rank",
    "zscore",
    "abs",
    "neg",
    "log",
    "add",
    "sub",
    "mul",
    "div",
}


def parse_expression(text: str) -> Expr:
    text = text.strip().rstrip(";")
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError as e:
        raise ValueError(f"Invalid expression syntax: {e}") from e
    return _convert(node)


def _convert(node: ast.AST) -> Expr | str | int | float:
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls allowed")
        op = node.func.id
        if op not in _ALLOWED_FUNCS:
            raise ValueError(f"Operator not allowed: {op}")
        if node.keywords:
            raise ValueError("Keyword args not allowed")
        return Expr(op=op, args=[_convert(a) for a in node.args])
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        # allow -5
        v = _convert(node.operand)
        if isinstance(v, (int, float)):
            return -v
        return Expr(op="neg", args=[v])
    raise ValueError(f"Unsupported syntax: {ast.dump(node)}")


def expr_hash(expr: Expr | str) -> str:
    s = expr.to_str() if isinstance(expr, Expr) else str(expr)
    import hashlib

    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def skeleton(expr: Expr | str | int | float) -> str:
    """Structure fingerprint with windows blanked — light dedup helper."""
    if isinstance(expr, Expr):
        inner = ",".join(skeleton(a) for a in expr.args)
        return f"{expr.op}({inner})"
    if isinstance(expr, str):
        return expr
    return "N"


def clone_expr(node: Expr | str | int | float) -> Expr | str | int | float:
    if isinstance(node, Expr):
        return Expr(op=node.op, args=[clone_expr(a) for a in node.args])
    return node


def nested_expr_paths(node: Expr | str | int | float, path: tuple[int, ...] = ()) -> list[tuple[int, ...]]:
    """Paths to Expr nodes that contain at least one child Expr (not field+window leaves)."""
    out: list[tuple[int, ...]] = []
    if not isinstance(node, Expr):
        return out
    if any(isinstance(a, Expr) for a in node.args):
        out.append(path)
    for i, a in enumerate(node.args):
        out.extend(nested_expr_paths(a, path + (i,)))
    return out


def all_expr_paths(node: Expr | str | int | float, path: tuple[int, ...] = ()) -> list[tuple[int, ...]]:
    """Paths to every Expr node, including the root (empty path)."""
    out: list[tuple[int, ...]] = []
    if not isinstance(node, Expr):
        return out
    out.append(path)
    for i, a in enumerate(node.args):
        out.extend(all_expr_paths(a, path + (i,)))
    return out


def expr_at(node: Expr | str | int | float, path: tuple[int, ...]) -> Expr | str | int | float:
    cur: Expr | str | int | float = node
    for i in path:
        if not isinstance(cur, Expr):
            raise ValueError("path does not point into an Expr")
        cur = cur.args[i]
    return cur


def replace_expr_at(
    node: Expr | str | int | float,
    path: tuple[int, ...],
    new: Expr | str | int | float,
) -> Expr | str | int | float:
    if not path:
        return clone_expr(new)
    if not isinstance(node, Expr):
        return node
    args = []
    for i, a in enumerate(node.args):
        if i == path[0]:
            args.append(replace_expr_at(a, path[1:], new))
        else:
            args.append(clone_expr(a) if isinstance(a, Expr) else a)
    return Expr(op=node.op, args=args)