from pathlib import Path

from qfactor.agent.experiments import ExperimentLedger


class _Cfg:
    def __init__(self, root: Path):
        self.root = root
        self.project = {
            "paths": {"runs": "runs"},
            "experiment": {"max_trials": 2},
            "production": {"llm": {"llm_ratio": 0.5}},
        }
        self.eval = {"eval": {"train_end": "20251231"}}
        self.universe = "csi100"

    def path(self, key: str) -> Path:
        assert key == "runs"
        return self.root / "runs"


class _DB:
    def __init__(self):
        self.manifests = []
        self.events = []

    def create_experiment(self, experiment_id, manifest):
        self.manifests.append((experiment_id, manifest))

    def update_experiment(self, experiment_id, manifest):
        self.manifests.append((experiment_id, manifest))

    def save_experiment_trial(self, experiment_id, event):
        self.events.append((experiment_id, event))


class _LLM:
    enabled = True
    model = "audit-model"
    reasoning_effort = "low"


def test_experiment_ledger_is_append_only_and_budgeted(tmp_path):
    cfg = _Cfg(tmp_path)
    db = _DB()
    ledger = ExperimentLedger(cfg, experiment_id="exp_test", db=db)
    manifest = ledger.start(
        run_id="run_test",
        data_version="data_v1",
        llm=_LLM(),
        search_config={"llm_ratio": 0.5},
        date_partitions={"discovery_end": "20251231"},
    )
    candidate = {"name": "f1", "expression": "ma(ret_1d,5)", "source": "llm_fresh"}
    ledger.record_trial(
        trial_id="exp_test:r1:1",
        stage="generated",
        outcome="generated",
        candidate=candidate,
    )
    ledger.record_trial(
        trial_id="exp_test:r1:1",
        stage="research_gate",
        outcome="rejected",
        candidate=candidate,
        detail={"reason": "weak_ic"},
    )
    closed = ledger.close(state="completed", summary={"screened": 0})

    assert manifest["research_only"] is True
    assert closed["trial_count"] == 1
    assert closed["state"] == "completed"
    assert len(db.events) == 2
    assert '"stage": "generated"' in ledger.trials_path.read_text(encoding="utf-8")


def test_experiment_ledger_rejects_generated_trials_above_budget(tmp_path):
    cfg = _Cfg(tmp_path)
    ledger = ExperimentLedger(cfg, experiment_id="exp_limit", db=_DB())
    ledger.start(
        run_id="run_limit",
        data_version="data_v1",
        llm=_LLM(),
        search_config={},
        date_partitions={},
    )
    for idx in range(2):
        ledger.record_trial(
            trial_id=f"exp_limit:{idx}",
            stage="generated",
            outcome="generated",
            candidate={"name": f"f{idx}", "source": "template"},
        )
    try:
        ledger.record_trial(
            trial_id="exp_limit:overflow",
            stage="generated",
            outcome="generated",
            candidate={"name": "overflow", "source": "template"},
        )
    except RuntimeError as exc:
        assert "exhausted" in str(exc)
    else:
        raise AssertionError("trial budget must prevent unrecorded search expansion")
