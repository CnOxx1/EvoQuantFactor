from qfactor.db.models import init_db
from qfactor.db.repo import Database


def test_db_roundtrip(tmp_path):
    db_file = tmp_path / "t.sqlite3"
    url = f"sqlite:///{db_file.as_posix()}"
    from qfactor.db import models

    models.get_engine.cache_clear()
    init_db(url)
    db = Database(url)
    import pandas as pd

    cal = pd.DataFrame({"cal_date": ["20240102"], "is_open": [1]})
    assert db.replace_calendar(cal) == 1
    univ = pd.DataFrame({"trade_date": ["20240102"], "ts_code": ["600519.SH"], "weight": [1.0]})
    assert db.replace_universe("csi100", univ) == 1
    bars = pd.DataFrame(
        {
            "ts_code": ["600519.SH"],
            "trade_date": ["20240102"],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "close_adj": [1.0],
            "ret_1d": [0.0],
        }
    )
    assert db.upsert_bars(bars) == 1
    loaded = db.load_bars()
    assert len(loaded) == 1
    db.upsert_factor({"name": "f1", "status": "candidate", "summary": {"rank_ic_mean": 0.02}})
    assert db.list_factors()[0]["name"] == "f1"
    st = db.status()
    assert st["n_bars"] == 1
    assert st["n_factors"] == 1


def test_generated_trial_count_accumulates_across_experiments(tmp_path):
    db_file = tmp_path / "trials.sqlite3"
    url = f"sqlite:///{db_file.as_posix()}"
    from qfactor.db import models

    models.get_engine.cache_clear()
    db = Database(url)
    windows = {
        "windows": {
            "discovery_start": "20190101",
            "discovery_end": "20221231",
        }
    }
    for exp in ("exp_1", "exp_2"):
        db.create_experiment(
            exp,
            {
                "experiment_id": exp,
                "state": "completed",
                "data_version": "data-v1",
                "date_partitions": windows,
            },
        )
        db.save_experiment_trial(
            exp,
            {
                "trial_id": f"{exp}:1",
                "stage": "generated",
                "outcome": "generated",
                "mechanism": "momentum",
            },
        )
    assert (
        db.count_generated_trials_scope(
            data_version="data-v1",
            discovery_start="20190101",
            discovery_end="20221231",
            mechanism="momentum",
        )
        == 2
    )
