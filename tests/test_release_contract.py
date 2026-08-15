import json
from pathlib import Path

from qfactor.factor.base import FactorSpec
from qfactor.factor.provenance import definition_hash
from qfactor.factor.release import ReleaseService, _execution_contract_reasons


class _Cfg:
    def __init__(self, root: Path):
        self.root = root
        self.project = {"paths": {"factor_lib": "factor_lib"}}
        self.eval = {"production": {}}

    def path(self, key: str) -> Path:
        assert key == "factor_lib"
        return self.root / "factor_lib"


class _Registry:
    def __init__(self, root: Path):
        self.root = root
        self.spec = FactorSpec(
            name="release_factor",
            version="1.0.0",
            expression="ma(ret_1d,5)",
            mechanism="reversal",
        )
        d = self.factor_dir(self.spec.name)
        (d / "reports").mkdir(parents=True)
        (d / "factor.py").write_text("FACTOR = None\n", encoding="utf-8")
        (d / "spec.yaml").write_text("name: release_factor\n", encoding="utf-8")
        (d / "reports" / "latest.json").write_text(
            json.dumps(
                {
                    "gate": {"mode": "production", "passed": True},
                    "metrics": {"data_version": "data_v1"},
                }
            ),
            encoding="utf-8",
        )

    def factor_dir(self, name: str) -> Path:
        assert name == self.spec.name
        return self.root / "factor_lib" / "factors" / name

    def load_spec(self, name: str) -> FactorSpec:
        assert name == self.spec.name
        return self.spec


class _Acceptance:
    def __init__(self, payload):
        self.payload = payload

    def latest(self, _name):
        return self.payload


class _Tradability:
    def __init__(self, payload):
        self.payload = payload

    def latest(self, _name):
        return self.payload


class _DB:
    def __init__(self):
        self.releases = []

    def save_release(self, release_id, payload):
        self.releases.append((release_id, payload))

    def list_releases(self, state=None):
        return [payload for _, payload in self.releases if state is None or payload["state"] == state]


class _DataService:
    def __init__(self, _cfg):
        pass

    def status(self):
        return {"data_version": "data_v1"}


def test_release_requires_sealed_and_tradability_evidence(monkeypatch, tmp_path):
    import qfactor.factor.release as release_module

    monkeypatch.setattr(release_module, "DataService", _DataService)
    cfg = _Cfg(tmp_path)
    registry = _Registry(tmp_path)
    d_hash = definition_hash(registry.spec)
    db = _DB()
    svc = ReleaseService(
        cfg,
        registry=registry,
        acceptance=_Acceptance(None),
        tradability=_Tradability(None),
        db=db,
    )
    blocked = svc.publish("release_factor")
    assert blocked["state"] == "release_blocked"
    assert "missing_sealed_acceptance" in blocked["reasons"]
    assert "missing_tradability_report" in blocked["reasons"]

    acceptance_payload = {
        "acceptance_id": "acc_1",
        "state": "sealed_oos_passed",
        "definition_hash": d_hash,
        "data_version": "data_v1",
        "experiment_id": "exp_1",
        "selection_bias_audit": {"passed": True, "state": "familywise_passed"},
    }
    tradability_payload = {
        "state": "tradability_passed",
        "definition_hash": d_hash,
        "data_version": "data_v1",
    }
    factor_dir = registry.factor_dir("release_factor")
    (factor_dir / "acceptance").mkdir()
    (factor_dir / "tradability").mkdir()
    (factor_dir / "acceptance" / "latest.json").write_text(
        json.dumps(acceptance_payload), encoding="utf-8"
    )
    (factor_dir / "tradability" / "latest.json").write_text(
        json.dumps(tradability_payload), encoding="utf-8"
    )
    svc = ReleaseService(
        cfg,
        registry=registry,
        acceptance=_Acceptance(acceptance_payload),
        tradability=_Tradability(tradability_payload),
        db=db,
    )
    released = svc.publish("release_factor")
    assert released["state"] == "active"
    assert released["acceptance_id"] == "acc_1"
    assert len(db.releases) == 1
    release_dir = cfg.path("factor_lib") / "releases" / released["release_id"]
    assert (release_dir / "release_manifest.json").exists()


def test_active_release_still_requires_execution_coverage():
    release = {
        "require_execution_data": True,
        "min_security_status_coverage": 0.98,
        "min_limit_price_coverage": 0.98,
        "min_adv_20d_coverage": 0.95,
        "min_corporate_action_coverage": 0.98,
        "min_industry_pit_coverage": 0.95,
        "min_risk_exposures_coverage": 0.95,
    }
    reasons = _execution_contract_reasons(
        {
            "security_status_coverage": 1.0,
            "limit_price_coverage": 0.0,
            "adv_20d_coverage": 1.0,
            "corporate_action_coverage": 1.0,
            "industry_pit_coverage": 1.0,
            "risk_exposures_coverage": 1.0,
        },
        release,
    )
    assert reasons == ["limit_price_coverage_below_contract"]
