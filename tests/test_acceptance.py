from pathlib import Path

import pytest

from qfactor.factor.acceptance import AcceptanceService
from qfactor.factor.base import FactorSpec


class _Cfg:
    def __init__(self, root: Path):
        self.root = root
        self.project = {}
        self.eval = {"eval": {}}


class _Registry:
    def __init__(self, root: Path):
        self.root = root
        self.spec = FactorSpec(
            name="frozen_factor",
            expression="ma(ret_1d,5)",
            mechanism="reversal",
            params={"experiment_id": "exp_1"},
        )
        d = self.factor_dir(self.spec.name)
        d.mkdir(parents=True)
        (d / "factor.py").write_text("FACTOR = None\n", encoding="utf-8")

    def factor_dir(self, name: str) -> Path:
        return self.root / "factor_lib" / "factors" / name

    def load_spec(self, name: str) -> FactorSpec:
        assert name == self.spec.name
        return self.spec


class _DB:
    pass


def test_freeze_definition_is_idempotent_but_rejects_definition_change(tmp_path):
    registry = _Registry(tmp_path)
    service = AcceptanceService(_Cfg(tmp_path), registry=registry, db=_DB())

    first = service.freeze_definition("frozen_factor")
    second = service.freeze_definition("frozen_factor")
    assert first["definition_hash"] == second["definition_hash"]
    assert first["experiment_id"] == "exp_1"

    registry.spec.expression = "std(ret_1d,5)"
    with pytest.raises(RuntimeError, match="Definition changed"):
        service.freeze_definition("frozen_factor")
