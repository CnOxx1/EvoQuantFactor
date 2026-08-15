from qfactor.eval.multiple_testing import (
    familywise_ic_audit,
    research_selection_bias_preview,
)
from qfactor.eval.service import EvalService
from qfactor.factor.base import FactorSpec


def test_familywise_audit_passes_only_after_trial_count_correction():
    metrics = {
        "rank_ic_mean": 0.04,
        "se_nw": 0.01,
        "p_value_nw_one_sided": 0.001,
    }
    passed = familywise_ic_audit(metrics, n_trials=10, alpha=0.05)
    assert passed["passed"] is True
    assert passed["adjusted_p_value"] == 0.01

    failed = familywise_ic_audit(metrics, n_trials=100, alpha=0.05)
    assert failed["passed"] is False
    assert failed["state"] == "familywise_failed"


def test_research_preview_is_informational_when_familywise_fails():
    preview = research_selection_bias_preview(
        {
            "rank_ic_mean": 0.01,
            "se_nw": 0.02,
            "p_value_nw_one_sided": 0.2,
        },
        n_trials=50,
    )
    assert preview["passed"] is False
    assert preview["informational_only"] is True
    assert preview["research_status_unchanged"] is True


def test_candidate_selection_bias_audit_is_binding(monkeypatch):
    class _DB:
        def count_generated_trials_scope(self, **_kwargs):
            return 100

    class _Factor:
        spec = FactorSpec(
            name="f",
            mechanism="momentum",
            expression="ma(ret_1d,5)",
        )

        def compute(self, _ctx):
            return None

    svc = object.__new__(EvalService)
    svc.cfg = type(
        "_Cfg",
        (),
        {
            "eval": {
                "production": {"require_selection_bias_audit": True},
                "eval": {
                    "partitions": {
                        "discovery_start": "20190101",
                        "discovery_end": "20221231",
                    }
                },
            }
        },
    )()
    svc._context = lambda: None
    svc.evaluate_panel = lambda *_args, **_kwargs: {
        "metrics": {
            "data_version": "data-v1",
            "rank_ic_mean": 0.04,
            "se_nw": 0.01,
            "p_value_nw_one_sided": 0.001,
        },
        "gate": {"passed": True, "status": "candidate", "checks": {}},
        "summary": {"status": "candidate"},
    }
    monkeypatch.setattr("qfactor.eval.service.Database", _DB)

    report = svc.evaluate_factor(_Factor(), gate_name="production")

    assert report["selection_bias_audit"]["n_trials"] == 100
    assert report["gate"]["checks"]["selection_bias"] is False
    assert report["gate"]["status"] == "reject"
