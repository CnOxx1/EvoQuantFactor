"""Smoke-check new gates on the previously closest factors. Read-only."""
from time import time

from qfactor.eval.service import EvalService
from qfactor.factor.registry import FactorRegistry

NAMES = [
    "liquidity_40_4891",
    "realized_vol_20d",
    "ret_reversal_5d",
    "overnight_60_6081",
    "amplitude_20d",
]


def main() -> None:
    ev = EvalService()
    reg = FactorRegistry()
    t0 = time()
    for name in NAMES:
        t = time()
        fac = reg.load_factor(name)
        panel = fac.compute(ev._context())
        prod = ev.evaluate_panel(panel, name, gate_name="production")
        res = ev.evaluate_panel(panel, name, gate_name="research")
        m = prod["metrics"]
        pf = [k for k, v in prod["gate"]["checks"].items() if not v]
        rf = [k for k, v in res["gate"]["checks"].items() if not v]
        print(
            f"{name:24} IC={m['rank_ic_mean']:.4f} ICIRann={m['icir_ann']:.2f} "
            f"OOS={m['oos_ic_mean']:+.4f} mono={m['monotonic_score']:.2f} "
            f"recent={m['recent_rank_ic_mean']:+.4f} cost={m['cost_adjusted_ls']:+.5f} "
            f"years={m['years_consistent']} "
            f"res={res['gate']['status']}({rf or 'ok'}) "
            f"prod={prod['gate']['status']}({pf or 'ok'}) "
            f"{time()-t:.1f}s",
            flush=True,
        )
    print(f"total {time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
