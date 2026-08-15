from pathlib import Path

from qfactor.agent.supervisor import FactoryRuntime


class _Data:
    def status(self):
        return {"data_version": "data-v1", "meta": {}}


class _Ops:
    def __init__(self):
        self.refresh_calls = 0

    def refresh_production(self, include_screened=False):
        assert include_screened is False
        self.refresh_calls += 1
        return {"kept_candidates": [], "demoted_candidates": []}

    def promote_screened(self):
        raise AssertionError("screened recheck should not run off cadence")

    def multifactor_inventory(self):
        return {"n_eligible": 0}

    def reconcile_state(self):
        return {"state": "consistent", "n_drift": 0}


class _Release:
    def export_active(self):
        return {"n_active": 0}


def test_runtime_cycle_is_fail_closed_and_auditable(tmp_path: Path):
    runtime = object.__new__(FactoryRuntime)
    runtime.runtime_dir = tmp_path
    runtime.status_path = tmp_path / "status.json"
    runtime.events_path = tmp_path / "events.jsonl"
    runtime.stop_path = tmp_path / "STOP"
    runtime.data = _Data()
    runtime.ops = _Ops()
    runtime.release = _Release()
    runtime.discovery_every = 1
    runtime.screened_every = 10
    runtime.llm_ratio = 0.0
    runtime._discovery_contract = lambda: {"state": "blocked", "reason": "universe_not_pit"}
    runtime._candidate_contract = lambda: {"state": "blocked", "reason": "universe_not_pit"}
    snapshots = iter(
        [
            {"total": 3, "screened": 3, "candidate": 0, "active_release": 0},
            {"total": 3, "screened": 3, "candidate": 0, "active_release": 0},
        ]
    )
    runtime.lifecycle_counts = lambda: next(snapshots)

    result = runtime.run_cycle(1)

    assert result["state"] == "ok"
    assert result["actions"]["research_discovery"]["state"] == "blocked"
    assert result["actions"]["refresh_candidates"]["demoted_candidates"] == []
    assert result["actions"]["recheck_screened"]["state"] == "blocked"
    assert result["actions"]["trading_releases"]["n_active"] == 0
    assert runtime.status_path.exists()
    assert "universe_not_pit" in runtime.events_path.read_text(encoding="utf-8")


def test_runtime_does_not_recheck_screened_when_contract_is_blocked(tmp_path: Path):
    runtime = object.__new__(FactoryRuntime)
    runtime.runtime_dir = tmp_path
    runtime.status_path = tmp_path / "status.json"
    runtime.events_path = tmp_path / "events.jsonl"
    runtime.stop_path = tmp_path / "STOP"
    runtime.data = _Data()
    runtime.ops = _Ops()
    runtime.release = _Release()
    runtime.discovery_every = 1
    runtime.screened_every = 1
    runtime.llm_ratio = 0.0
    runtime._discovery_contract = lambda: {"state": "blocked", "reason": "universe_not_pit"}
    runtime._candidate_contract = lambda: {"state": "blocked", "reason": "universe_not_pit"}
    snapshots = iter(
        [
            {"total": 3, "screened": 3, "candidate": 0, "active_release": 0},
            {"total": 3, "screened": 3, "candidate": 0, "active_release": 0},
        ]
    )
    runtime.lifecycle_counts = lambda: next(snapshots)

    result = runtime.run_cycle(1)

    assert result["actions"]["recheck_screened"] == {
        "state": "blocked",
        "reason": "universe_not_pit",
    }
