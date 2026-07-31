from factor_backend.services.scoring import ensure_subscores, weighted_total


def test_weighted_total_basic():
    weights = {"Logic": 20, "Edge": 80}
    subs = {"Logic": 100, "Edge": 50}
    # (100*20 + 50*80) / 100 = 60
    assert weighted_total(subs, weights) == 60


def test_info_insufficient_cap():
    weights = {"Logic": 100}
    assert weighted_total({"Logic": 90}, weights, info_insufficient=True, info_insufficient_cap=65) == 65


def test_ensure_subscores_fallback():
    weights = {"A": 50, "B": 50}
    subs = ensure_subscores({"total_score": 80}, weights)
    assert subs["A"] == 80 and subs["B"] == 80
    assert weighted_total(subs, weights) == 80
