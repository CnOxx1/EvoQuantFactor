from qfactor.eval.multiple_testing import familywise_ic_audit


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
