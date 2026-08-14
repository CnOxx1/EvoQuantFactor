from __future__ import annotations

import importlib.util
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.provenance import definition_hash
from qfactor.settings import ProjectConfig, get_project_config


class FactorRegistry:
    def __init__(self, cfg: ProjectConfig | None = None):
        self.cfg = cfg or get_project_config()
        self.root = self.cfg.path("factor_lib")
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.root / "catalog.json"
        if not self.catalog_path.exists():
            self._write_catalog({"factors": [], "updated_at": None})

    def _read_catalog(self) -> dict[str, Any]:
        return json.loads(self.catalog_path.read_text(encoding="utf-8"))

    def _write_catalog(self, data: dict[str, Any]) -> None:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.catalog_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def factor_dir(self, name: str) -> Path:
        return self.root / "factors" / name

    def list_factors(self) -> list[dict[str, Any]]:
        return list(self._read_catalog().get("factors", []))

    def save_factor_files(
        self,
        spec: FactorSpec,
        code: str,
        source: str = "human",
        report: dict[str, Any] | None = None,
    ) -> Path:
        fdir = self.factor_dir(spec.name)
        frozen_path = fdir / "acceptance" / "frozen_definition.json"
        if frozen_path.exists():
            frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
            code_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()
            if (
                frozen.get("definition_hash") != definition_hash(spec)
                or frozen.get("code_sha256") != code_sha256
            ):
                raise RuntimeError(
                    "Frozen definition cannot be overwritten. Create a new factor version and acceptance run."
                )
        fdir.mkdir(parents=True, exist_ok=True)
        (fdir / "reports").mkdir(exist_ok=True)
        (fdir / "runs").mkdir(exist_ok=True)
        (fdir / "spec.yaml").write_text(
            yaml.safe_dump(spec.model_dump(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        (fdir / "factor.py").write_text(code, encoding="utf-8")
        if report is not None:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            (fdir / "reports" / f"{ts}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (fdir / "reports" / "latest.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        summary = dict((report or {}).get("summary") or {})
        if summary:
            # `status` is the durable library state consumed downstream.  Preserve
            # the last numerical gate result separately so a later manual/correlation
            # demotion does not leave conflicting labels in the catalog.
            summary["gate_status"] = summary.get("gate_status", summary.get("status"))
            summary["library_status"] = spec.status
            summary["status"] = spec.status

        catalog = self._read_catalog()
        factors = catalog.get("factors", [])
        entry = {
            "name": spec.name,
            "version": spec.version,
            "status": spec.status,
            "family": spec.family,
            "category": spec.category,
            "source": source,
            "path": str(fdir.relative_to(self.cfg.root)).replace("\\", "/"),
            "summary": summary,
        }
        factors = [f for f in factors if f.get("name") != spec.name]
        factors.append(entry)
        catalog["factors"] = sorted(factors, key=lambda x: x["name"])
        self._write_catalog(catalog)
        try:
            from qfactor.db.repo import Database

            Database().upsert_factor(entry, spec.model_dump())
            if report is not None:
                Database().save_factor_report(spec.name, report)
        except Exception:
            pass
        return fdir

    def load_spec(self, name: str) -> FactorSpec:
        path = self.factor_dir(name) / "spec.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return FactorSpec.model_validate(data)

    def load_factor(self, name: str) -> Factor:
        path = self.factor_dir(name) / "factor.py"
        spec = self.load_spec(name)
        mod_name = f"qfactor_dynamic_{name}"
        module_spec = importlib.util.spec_from_file_location(mod_name, path)
        if module_spec is None or module_spec.loader is None:
            raise ImportError(f"Cannot load {path}")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        if hasattr(module, "build"):
            factor = module.build()
        elif hasattr(module, "FACTOR"):
            factor = module.FACTOR
        else:
            raise AttributeError(f"{path} must define build() or FACTOR")
        if not isinstance(factor, Factor):
            # allow duck typing if compute exists
            if not hasattr(factor, "compute"):
                raise TypeError("Loaded object is not a Factor")
        if not hasattr(factor, "spec"):
            factor.spec = spec  # type: ignore[attr-defined]
        return factor  # type: ignore[return-value]

    def update_status(self, name: str, status: str) -> None:
        spec = self.load_spec(name)
        spec.status = status
        fdir = self.factor_dir(name)
        (fdir / "spec.yaml").write_text(
            yaml.safe_dump(spec.model_dump(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        catalog = self._read_catalog()
        for f in catalog.get("factors", []):
            if f.get("name") == name:
                f["status"] = status
                summary = f.get("summary")
                if isinstance(summary, dict) and summary:
                    summary["gate_status"] = summary.get(
                        "gate_status", summary.get("status")
                    )
                    summary["library_status"] = status
                    summary["status"] = status
        self._write_catalog(catalog)
        try:
            from qfactor.db.repo import Database

            Database().upsert_factor(
                next(f for f in catalog["factors"] if f["name"] == name),
                spec.model_dump(),
            )
        except Exception:
            pass

    def existing_summaries(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for f in self.list_factors():
            row: dict[str, Any] = {
                "name": f.get("name"),
                "category": f.get("category"),
                "status": f.get("status"),
                "summary": f.get("summary", {}),
                "source": f.get("source"),
            }
            try:
                spec = self.load_spec(str(f["name"]))
                row["expression"] = spec.expression
                row["mechanism"] = spec.mechanism or spec.category
                row["hypothesis"] = spec.hypothesis
                row["params"] = spec.params
            except Exception:
                pass
            rows.append(row)
        return rows

    def remove_factor(self, name: str) -> None:
        fdir = self.factor_dir(name)
        if fdir.exists():
            shutil.rmtree(fdir)
        catalog = self._read_catalog()
        catalog["factors"] = [f for f in catalog.get("factors", []) if f.get("name") != name]
        self._write_catalog(catalog)
        try:
            from qfactor.db.repo import Database

            Database().delete_factor(name)
        except Exception:
            pass