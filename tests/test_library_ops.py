from types import SimpleNamespace

from qfactor.factor.ops import LibraryOps, screened_promotion_key


def test_screened_promotion_key_prefers_train_resid_oos():
    loud = {"icir_ann": 9.0, "oos_ic_mean": 0.05, "train_rank_ic_mean": 0.005}
    honest = {
        "icir_ann": 1.2,
        "train_rank_ic_mean": 0.03,
        "resid_icir_nw": 0.12,
        "oos_min_fold_ic": 0.02,
    }
    assert screened_promotion_key(honest) > screened_promotion_key(loud)


def test_cap_usable_per_mechanism_keeps_one(monkeypatch):
    ops = LibraryOps()
    rows = [
        {
            "name": "amp_hi",
            "status": "candidate",
            "category": "amplitude",
            "summary": {"train_rank_ic_mean": 0.04, "resid_ic_mean": 0.03},
        },
        {
            "name": "amp_lo",
            "status": "candidate",
            "category": "amplitude",
            "summary": {"train_rank_ic_mean": 0.02, "resid_ic_mean": 0.01},
        },
        {
            "name": "liq",
            "status": "candidate",
            "category": "liquidity",
            "summary": {"train_rank_ic_mean": 0.03},
        },
    ]
    specs = {
        "amp_hi": SimpleNamespace(mechanism="amplitude", category="amplitude"),
        "amp_lo": SimpleNamespace(mechanism="amplitude", category="amplitude"),
        "liq": SimpleNamespace(mechanism="liquidity", category="liquidity"),
    }
    demoted: list[tuple[str, str]] = []

    class _Reg:
        def list_factors(self):
            return rows

        def load_spec(self, name):
            return specs[name]

        def update_status(self, name, status):
            demoted.append((name, status))
            for row in rows:
                if row["name"] == name:
                    row["status"] = status

    ops.registry = _Reg()  # type: ignore[assignment]
    monkeypatch.setattr(ops, "_log_op", lambda *a, **k: None)
    out = ops.cap_usable_per_mechanism(1)
    names = {d["name"] for d in out["demoted"]}
    assert names == {"amp_lo"}
    assert demoted == [("amp_lo", "screened")]
    kept = {k["name"] for k in out["kept"]}
    assert kept == {"amp_hi", "liq"}
