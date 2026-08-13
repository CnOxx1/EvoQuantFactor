from qfactor.agent.guard import guard_factor_code


def test_guard_rejects_negative_shift():
    code = """
def build():
    x = df.shift(-1)
    return x
"""
    g = guard_factor_code(code, {"close_adj"})
    assert g["ok"] is False


def test_guard_accepts_panel_close():
    code = """
from qfactor.factor.base import Factor

class F(Factor):
    def compute(self, ctx):
        return ctx.panel("close_adj")

def build():
    return F()
"""
    g = guard_factor_code(code, {"close_adj"})
    assert g["ok"] is True
    assert "close_adj" in g["used_fields"]