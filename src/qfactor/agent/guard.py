from __future__ import annotations

import ast
import re
from typing import Any


FORBIDDEN_PATTERNS = [
    r"shift\s*\(\s*-\s*\d+",
    r"pct_change\s*\(\s*-\s*\d+",
    r"__import__",
    r"os\.system",
    r"subprocess",
    r"open\s*\(",
    r"Path\s*\(",
    r"requests\.",
    r"httpx\.",
]


def guard_factor_code(code: str, allowed_fields: set[str]) -> dict[str, Any]:
    errors: list[str] = []
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, code):
            errors.append(f"Forbidden pattern: {pat}")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"ok": False, "errors": [f"SyntaxError: {e}"]}

    used_fields: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "panel" and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    used_fields.add(arg0.value)
    unknown = sorted(used_fields - allowed_fields)
    if unknown:
        errors.append(f"Unknown fields: {unknown}")
    if "build" not in code and "FACTOR" not in code:
        errors.append("Missing build() or FACTOR")
    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "used_fields": sorted(used_fields),
    }