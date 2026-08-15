from qfactor.eval.multiple_testing import (
    familywise_ic_audit,
    research_selection_bias_preview,
)


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
