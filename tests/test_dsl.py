from qfactor.dsl.parser import parse_expression, skeleton
from qfactor.dsl.validate import validate_expression


def test_parse_and_validate_ok():
    expr = "neg(roc(close_adj, 5))"
    v = validate_expression(expr)
    assert v["ok"] is True
    assert "close_adj" in v["fields"]


def test_reject_bad_window():
    v = validate_expression("ma(close_adj, 7)")
    assert v["ok"] is False


def test_skeleton_blinds_windows():
    a = parse_expression("ma(close_adj, 5)")
    b = parse_expression("ma(close_adj, 20)")
    assert skeleton(a) == skeleton(b)