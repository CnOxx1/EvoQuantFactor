from pathlib import Path
from types import SimpleNamespace
import json

from qfactor.factor.base import FactorSpec
from qfactor.factor.ops import LibraryOps, screened_library_key, screened_promotion_key


def test_screened_promotion_key_prefers_train_resid_oos():
    loud = {"icir_ann": 9.0, "oos_ic_mean": 0.05, "train_rank_ic_mean": 0.005}
    honest = {
        "icir_ann": 1.2,
        "train_rank_ic_mean": 0.03,
        "resid_icir_nw": 0.12,
        "oos_min_fold_ic": 0.02,
    }
    assert screened_promotion_key(honest) > screened_promotion_key(loud)


def test_screened_library_key_ignores_annualized_icir_noise():
    noisy = {
        "icir_ann": 12.0,
        "train_rank_ic_mean": 0.005,
        "resid_ic_mean": 0.002,
        "oos_min_fold_ic": 0.001,
        "coverage": 0.99,
    }
    robust = {
        "icir_ann": 1.0,
        "train_rank_ic_mean": 0.03,
        "resid_ic_mean": 0.02,
        "oos_min_fold_ic": 0.015,
        "coverage": 0.8,
    }
    assert screened_library_key(robust) > screened_library_key(noisy)


def test_promote_screened_skips_when_data_contract_is_blocked(monkeypatch):
    ops = LibraryOps()

    def _blocked(_cfg):
        raise RuntimeError("universe_not_pit")

    monkeypatch.setattr("qfactor.factor.ops.require_candidate_contract", _blocked)
    monkeypatch.setattr(
        "qfactor.factor.ops.EvalService",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not evaluate")),
    )

    out = ops.promote_screened(["screened_factor"])

    assert out["state"] == "blocked"
    assert out["promoted"] == []
    assert out["held_screened"] == []
    assert "universe_not_pit" in out["reason"]


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


class _QualityRegistry:
    def __init__(self, root: Path, rows: list[dict]):
        self.root = root
        self.rows = rows

    def list_factors(self):
        return self.rows

    def factor_dir(self, name: str) -> Path:
        return self.root / name

    def load_spec(self, name: str) -> FactorSpec:
        row = next(r for r in self.rows if r["name"] == name)
        return FactorSpec(
            name=name,
            mechanism=str(row.get("mechanism") or "amplitude"),
            expression=str(row.get("expression") or "ma(close,5)"),
        )


class _QualityEval:
    def __init__(self, _cfg):
        self.data = SimpleNamespace(data_version=lambda: "data-v1")


def _write_latest(root: Path, name: str, *, mode: str, status: str, passed: bool, metrics: dict):
    report_dir = root / name / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "latest.json").write_text(
        json.dumps(
            {
                "gate": {"mode": mode, "status": status, "passed": passed},
                "metrics": metrics,
            }
        ),
        encoding="utf-8",
    )


def test_quality_library_exports_pit_research_keep_not_candidates(tmp_path, monkeypatch):
    pit_metrics = {
        "data_version": "data-v1",
        "universe_mode": "pit",
        "circ_mv_source": "archive_daily_basic",
        "daily_basic_coverage": 0.9,
        "industry_pit_coverage": 1.0,
        "rank_ic_mean": 0.015,
        "resid_ic_mean": 0.014,
        "icir_ann": 1.6,
        "coverage": 0.93,
        "max_corr": 0.1,
        "n_peers": 2,
        "n_independent": 40,
        "oos_ic_mean": 0.01,
        "daily_turnover": 0.4,
    }
    rows = [
        {
            "name": "amp_keep",
            "status": "screened",
            "source": "llm",
            "mechanism": "amplitude",
            "expression": "zscore(div(amplitude, turnover_rate))",
            "params": {"research_cohort": "clean_discovery"},
            "summary": {
                "data_version": "data-v1",
                "universe_mode": "pit",
                "circ_mv_source": "archive_daily_basic",
            },
        },
        {
            "name": "snap_legacy",
            "status": "screened",
            "source": "llm",
            "mechanism": "amplitude",
            "params": {"research_cohort": "clean_discovery"},
            "summary": {
                "data_version": "data-v1",
                "universe_mode": "snapshot",
                "circ_mv_source": "estimated",
            },
        },
        {
            "name": "unverified",
            "status": "screened",
            "source": "llm",
            "mechanism": "liquidity",
            "params": {},
            "summary": {
                "data_version": "data-v1",
                "universe_mode": "pit",
                "circ_mv_source": "archive_daily_basic",
            },
        },
        {
            "name": "seed_tpl",
            "status": "screened",
            "source": "seed",
            "mechanism": "reversal",
            "params": {},
            "summary": {"data_version": "data-v1"},
        },
        {
            "name": "other_panel",
            "status": "screened",
            "source": "llm",
            "mechanism": "reversal",
            "params": {"research_cohort": "clean_discovery"},
            "summary": {
                "data_version": "old-panel",
                "universe_mode": "pit",
                "circ_mv_source": "archive_daily_basic",
            },
        },
    ]
    _write_latest(
        tmp_path,
        "amp_keep",
        mode="research",
        status="screened",
        passed=True,
        metrics=pit_metrics,
    )
    _write_latest(
        tmp_path,
        "snap_legacy",
        mode="research",
        status="screened",
        passed=True,
        metrics={**pit_metrics, "universe_mode": "snapshot", "circ_mv_source": "estimated"},
    )
    _write_latest(
        tmp_path,
        "unverified",
        mode="research",
        status="screened",
        passed=True,
        metrics=pit_metrics,
    )
    _write_latest(
        tmp_path,
        "seed_tpl",
        mode="research",
        status="screened",
        passed=True,
        metrics=pit_metrics,
    )
    _write_latest(
        tmp_path,
        "other_panel",
        mode="research",
        status="screened",
        passed=True,
        metrics={**pit_metrics, "data_version": "old-panel"},
    )

    from qfactor.settings import get_project_config

    ops = object.__new__(LibraryOps)
    ops.cfg = get_project_config()
    ops.registry = _QualityRegistry(tmp_path, rows)
    monkeypatch.setattr("qfactor.factor.ops.EvalService", _QualityEval)

    quality = ops.quality_library()
    assert quality["contract_version"] == "quality-library-v1"
    assert quality["tradable"] is False
    assert quality["usage"] == "price_volume_research_library"
    assert [row["name"] for row in quality["factors"]] == ["amp_keep"]
    assert quality["factors"][0]["layer"] == "mining_quality"
    assert quality["factors"][0]["tradable"] is False
    reasons = {row["name"]: row["reasons"] for row in quality["excluded"]}
    assert "snapshot_or_estimated_size" in reasons["snap_legacy"]
    assert "missing_clean_experiment_provenance" in reasons["unverified"]
    assert "seed_template_not_mining_output" in reasons["seed_tpl"]
    assert "data_version_mismatch" in reasons["other_panel"]

    inventory = ops.multifactor_inventory()
    assert inventory["n_eligible"] == 0
    assert all(row["name"] != "amp_keep" for row in inventory["factors"])

    exported = ops.export_quality_library(output=str(tmp_path / "quality_library.json"))
    assert Path(exported["path"]).exists()
    blob = json.loads(Path(exported["path"]).read_text(encoding="utf-8"))
    assert blob["n_eligible"] == 1
    assert blob["factors"][0]["name"] == "amp_keep"

