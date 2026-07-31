from factor_backend.services.router_logic import decide_action, merge_scorecards


def test_save_gate():
    out = decide_action([82, 81, 80, 79, 85, 84], veto=False, round_idx=1, max_round=3)
    assert out["action"] == "SAVE"


def test_revise_when_below():
    out = decide_action([70, 72, 68, 71, 69, 73], veto=False, round_idx=1, max_round=3)
    assert out["action"] == "REVISE"


def test_merge_scorecards():
    prev = {"F01": {"R1": 80}, "F02": {"R1": 60}}
    new = {"F02": {"R1": 75}}
    merged = merge_scorecards(prev, new, ["F02"])
    assert merged["F01"]["R1"] == 80
    assert merged["F02"]["R1"] == 75
