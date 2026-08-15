import json
from types import SimpleNamespace

from qfactor.factor.reconcile import reconcile_library_state


class _Registry:
    def __init__(self, root):
        self.root = root

    def list_factors(self):
        return [
            {
                "name": "factor_a",
                "status": "screened",
                "summary": {"library_status": "screened"},
            }
        ]

    def load_spec(self, name):
        assert name == "factor_a"
        return SimpleNamespace(status="screened")

    def factor_dir(self, name):
        return self.root / name


class _DB:
    def __init__(self, status="screened"):
        self.status = status

    def list_factors(self):
        return [{"name": "factor_a", "status": self.status}]

    def get_latest_report(self, name):
        return {"gate": {"status": "screened"}}


def test_reconcile_reports_consistent_state(tmp_path):
    root = tmp_path / "factor_a" / "reports"
    root.mkdir(parents=True)
    (root / "latest.json").write_text(
        json.dumps({"gate": {"status": "screened"}}),
        encoding="utf-8",
    )
    out = reconcile_library_state(registry=_Registry(tmp_path), db=_DB())
    assert out["state"] == "consistent"
    assert out["repair_performed"] is False


def test_reconcile_detects_db_status_drift_without_repair(tmp_path):
    out = reconcile_library_state(registry=_Registry(tmp_path), db=_DB(status="candidate"))
    assert out["state"] == "drift_detected"
    assert out["repair_performed"] is False
    assert "catalog_db_status_mismatch" in {row["kind"] for row in out["drift"]}
