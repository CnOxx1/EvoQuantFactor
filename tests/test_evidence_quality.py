from qfactor.data.evidence import evidence_quality


def test_evidence_quality_does_not_upgrade_estimated_size():
    out = evidence_quality(
        {
            "universe_mode": "snapshot",
            "circ_mv_source": "estimated",
            "security_status_coverage": 0.99,
            "security_status_provider": "baostock_daily_bars",
            "adv_20d_coverage": 0.95,
        }
    )
    assert out["universe"]["level"] == "official"
    assert out["universe"]["candidate_eligible"] is False
    assert out["circ_mv"]["level"] == "estimated"
    assert out["circ_mv"]["candidate_eligible"] is False
    assert out["security_status"]["level"] == "verified"
    assert out["adv_20d"]["level"] == "derived"
