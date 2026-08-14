from qfactor.eval.gate import apply_gate, route_library_status


def _research_metrics(**overrides):
    metrics = {
        "rank_ic_mean": 0.03,
        "icir": 0.08,
        "icir_ann": 1.3,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 0.5,
        "years_consistent": True,
        "no_lookahead": True,
    }
    metrics.update(overrides)
    return metrics


def _research_thresholds():
    return {
        "min_rank_ic_mean": 0.01,
        "min_abs_rank_ic_mean": 0.01,
        "min_icir": 1.0,
        "min_coverage": 0.7,
        "max_corr_existing": 0.8,
        "min_layered_monotonic_score": 0.5,
        "max_daily_turnover": 1.5,
        "require_no_lookahead": True,
    }


def _production_thresholds():
    return {
        "min_rank_ic_mean": 0.02,
        "min_abs_rank_ic_mean": 0.02,
        "min_icir": 1.5,
        "min_coverage": 0.7,
        "max_corr_existing": 0.7,
        "min_layered_monotonic_score": 0.75,
        "max_daily_turnover": 1.2,
        "require_no_lookahead": True,
        "require_recent_ic_positive": True,
        "require_oos": True,
        "min_oos_ic_mean": 0.01,
        "min_oos_pos_folds": 2,
        "require_cost_ls_positive": True,
        "require_years_same_sign": True,
    }


def test_gate_pass_research_is_screened():
    out = apply_gate(_research_metrics(), _research_thresholds(), mode="research")
    assert out["status"] == "screened"
    assert out["status"] != "candidate"


def test_research_soft_pass_is_screened():
    metrics = _research_metrics(years_consistent=False, daily_turnover=3.0)
    out = apply_gate(metrics, _research_thresholds(), mode="research")
    assert out["passed"] is False
    assert out["status"] == "screened"


def test_research_soft_pass_rejects_negative_oos():
    thresholds = dict(_research_thresholds())
    thresholds["require_oos"] = True
    thresholds["min_oos_ic_mean"] = 0.0
    thresholds["min_oos_pos_folds"] = 1
    metrics = _research_metrics(
        years_consistent=False,
        oos_ic_mean=-0.02,
        oos_pos_folds=0,
    )
    out = apply_gate(metrics, thresholds, mode="research")
    assert out["checks"]["oos"] is False
    assert out["status"] == "reject"


def test_research_soft_pass_keeps_nonneg_oos():
    thresholds = dict(_research_thresholds())
    thresholds["require_oos"] = True
    thresholds["min_oos_ic_mean"] = 0.0
    thresholds["min_oos_pos_folds"] = 1
    metrics = _research_metrics(
        years_consistent=False,
        daily_turnover=3.0,
        oos_ic_mean=0.005,
        oos_pos_folds=2,
    )
    out = apply_gate(metrics, thresholds, mode="research")
    assert out["passed"] is False
    assert out["status"] == "screened"


def test_research_rejects_without_lookahead():
    metrics = _research_metrics(no_lookahead=False)
    out = apply_gate(metrics, _research_thresholds(), mode="research")
    assert out["status"] == "reject"


def test_production_requires_oos():
    metrics = {
        "rank_ic_mean": 0.04,
        "icir": 0.12,
        "icir_ann": 1.9,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 0.5,
        "years_consistent": True,
        "no_lookahead": True,
        "recent_rank_ic_mean": 0.03,
        "oos_ic_mean": 0.005,
        "oos_pos_folds": 1,
        "cost_adjusted_ls": 0.01,
    }
    out = apply_gate(metrics, _production_thresholds(), mode="production")
    assert out["status"] == "reject"
    assert out["checks"]["oos"] is False


def test_production_pass_is_candidate():
    metrics = {
        "rank_ic_mean": 0.04,
        "icir": 0.12,
        "icir_ann": 1.9,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 0.5,
        "years_consistent": True,
        "no_lookahead": True,
        "recent_rank_ic_mean": 0.03,
        "oos_ic_mean": 0.02,
        "oos_pos_folds": 3,
        "cost_adjusted_ls": 0.01,
    }
    out = apply_gate(metrics, _production_thresholds(), mode="production")
    assert out["status"] == "candidate"
    assert out["passed"] is True


def test_recent_ic_rejects_negative_after_orientation():
    metrics = {
        "rank_ic_mean": 0.04,
        "icir_ann": 1.9,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 0.5,
        "years_consistent": True,
        "no_lookahead": True,
        "recent_rank_ic_mean": -0.04,
        "oos_ic_mean": 0.02,
        "oos_pos_folds": 3,
        "cost_adjusted_ls": 0.01,
    }
    out = apply_gate(metrics, _production_thresholds(), mode="production")
    assert out["checks"]["recent_ic"] is False
    assert out["status"] == "reject"


def test_oos_rejects_negative_mean():
    metrics = {
        "rank_ic_mean": 0.04,
        "icir_ann": 1.9,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 0.5,
        "years_consistent": True,
        "no_lookahead": True,
        "recent_rank_ic_mean": 0.03,
        "oos_ic_mean": -0.02,
        "oos_pos_folds": 3,
        "cost_adjusted_ls": 0.01,
    }
    out = apply_gate(metrics, _production_thresholds(), mode="production")
    assert out["checks"]["oos"] is False


def test_oos_pos_folds_defaults_to_one():
    thresholds = dict(_research_thresholds())
    thresholds["require_oos"] = True
    thresholds["min_oos_ic_mean"] = 0.0
    thresholds.pop("min_oos_pos_folds", None)
    metrics = _research_metrics(oos_ic_mean=0.01, oos_pos_folds=1)
    out = apply_gate(metrics, thresholds, mode="research")
    assert out["checks"]["oos"] is True


def test_production_freeze_sign_oos():
    thresholds = dict(_production_thresholds())
    thresholds["oos_mode"] = "freeze_sign"
    ok = {
        "rank_ic_mean": 0.04,
        "icir": 0.12,
        "icir_ann": 1.9,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 0.5,
        "years_consistent": True,
        "no_lookahead": True,
        "recent_rank_ic_mean": 0.03,
        "freeze_sign_ok": True,
        "oos_ic_mean": 0.04,
        "oos_pos_folds": 1,
        "cost_adjusted_ls": 0.01,
    }
    assert apply_gate(ok, thresholds, mode="production")["status"] == "candidate"
    bad = dict(ok)
    bad["freeze_sign_ok"] = False
    out = apply_gate(bad, thresholds, mode="production")
    assert out["checks"]["oos"] is False
    assert out["status"] == "reject"


def test_production_window_icir_not_annualized():
    thresholds = dict(_production_thresholds())
    thresholds["icir_annualized"] = False
    thresholds["min_holdout_icir"] = 0.10
    thresholds["oos_mode"] = "freeze_sign"
    metrics = {
        "rank_ic_mean": 0.04,
        "icir": 0.08,
        "icir_ann": 1.9,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 0.5,
        "years_consistent": True,
        "no_lookahead": True,
        "recent_rank_ic_mean": 0.03,
        "freeze_sign_ok": True,
        "cost_adjusted_ls": 0.01,
    }
    out = apply_gate(metrics, thresholds, mode="production")
    assert out["checks"]["icir"] is False
    metrics["icir"] = 0.20
    out = apply_gate(metrics, thresholds, mode="production")
    assert out["checks"]["icir"] is True


def test_production_newey_west_icir_gate():
    thresholds = dict(_production_thresholds())
    thresholds["icir_annualized"] = False
    thresholds["icir_mode"] = "newey_west"
    thresholds["min_holdout_icir"] = 0.07
    thresholds["oos_mode"] = "freeze_sign"
    metrics = {
        "rank_ic_mean": 0.04,
        "icir": 0.20,
        "icir_nw": 0.04,
        "icir_ann": 3.0,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 0.5,
        "years_consistent": True,
        "no_lookahead": True,
        "recent_rank_ic_mean": 0.03,
        "freeze_sign_ok": True,
        "cost_adjusted_ls": 0.01,
    }
    out = apply_gate(metrics, thresholds, mode="production")
    assert out["checks"]["icir"] is False
    metrics["icir_nw"] = 0.10
    out = apply_gate(metrics, thresholds, mode="production")
    assert out["checks"]["icir"] is True


def test_production_resid_icir_is_window_when_not_annualized():
    thresholds = dict(_production_thresholds())
    thresholds["icir_annualized"] = False
    thresholds["min_holdout_icir"] = 0.10
    thresholds["require_residual_ic"] = True
    thresholds["min_resid_ic_mean"] = 0.01
    thresholds["min_resid_icir"] = 0.10
    thresholds["oos_mode"] = "freeze_sign"
    metrics = {
        "rank_ic_mean": 0.04,
        "icir": 0.20,
        "icir_ann": 1.9,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 0.5,
        "years_consistent": True,
        "no_lookahead": True,
        "recent_rank_ic_mean": 0.03,
        "freeze_sign_ok": True,
        "cost_adjusted_ls": 0.01,
        "resid_ic_mean": 0.02,
        "resid_icir": 0.05,
        "resid_icir_ann": 2.0,
    }
    out = apply_gate(metrics, thresholds, mode="production")
    assert out["checks"]["resid_ic"] is False
    metrics["resid_icir"] = 0.15
    out = apply_gate(metrics, thresholds, mode="production")
    assert out["checks"]["resid_ic"] is True


def test_production_resid_icir_uses_newey_west():
    thresholds = dict(_production_thresholds())
    thresholds["icir_annualized"] = False
    thresholds["icir_mode"] = "newey_west"
    thresholds["min_holdout_icir"] = 0.07
    thresholds["require_residual_ic"] = True
    thresholds["min_resid_ic_mean"] = 0.01
    thresholds["min_resid_icir"] = 0.07
    thresholds["oos_mode"] = "freeze_sign"
    metrics = {
        "rank_ic_mean": 0.04,
        "icir": 0.20,
        "icir_nw": 0.12,
        "icir_ann": 1.9,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 0.5,
        "years_consistent": True,
        "no_lookahead": True,
        "recent_rank_ic_mean": 0.03,
        "freeze_sign_ok": True,
        "cost_adjusted_ls": 0.01,
        "resid_ic_mean": 0.02,
        "resid_icir": 0.20,
        "resid_icir_nw": 0.04,
        "resid_icir_ann": 2.0,
    }
    out = apply_gate(metrics, thresholds, mode="production")
    assert out["checks"]["resid_ic"] is False
    metrics["resid_icir_nw"] = 0.10
    out = apply_gate(metrics, thresholds, mode="production")
    assert out["checks"]["resid_ic"] is True


def test_production_peers_exclude_screened():
    from qfactor.eval.gate import KEEP_STATUSES, USABLE_STATUSES

    assert USABLE_STATUSES == ("candidate", "approved")
    assert "screened" in KEEP_STATUSES
    assert "screened" not in USABLE_STATUSES


def test_production_requires_residual_ic():
    metrics = {
        "rank_ic_mean": 0.04,
        "icir_ann": 1.9,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 0.5,
        "years_consistent": True,
        "no_lookahead": True,
        "recent_rank_ic_mean": 0.03,
        "oos_ic_mean": 0.02,
        "oos_pos_folds": 2,
        "cost_adjusted_ls": 0.01,
        "resid_ic_mean": 0.002,
        "resid_icir_ann": 0.2,
    }
    thresholds = dict(_production_thresholds())
    thresholds["require_residual_ic"] = True
    thresholds["min_resid_ic_mean"] = 0.01
    thresholds["min_resid_icir"] = 1.0
    out = apply_gate(metrics, thresholds, mode="production")
    assert out["checks"]["resid_ic"] is False
    assert out["status"] == "reject"


def test_route_library_status():
    assert route_library_status("research", "screened") == "screened"
    assert route_library_status("production", "candidate") == "candidate"
    assert route_library_status("research", "reject") == "draft"
    assert route_library_status("production", "reject", current="screened") == "screened"
    assert route_library_status("production", "reject", current="candidate") == "screened"
    assert route_library_status("production", "reject", current="draft") == "draft"
    assert route_library_status("default", "reject", current="screened") == "draft"
