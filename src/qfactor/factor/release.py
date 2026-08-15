from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from qfactor.data.dataset import DataService
from qfactor.db.repo import Database
from qfactor.eval.service import EvalService
from qfactor.eval.trading import simulate_non_overlapping_long_short
from qfactor.factor.acceptance import AcceptanceService
from qfactor.factor.provenance import definition_hash
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import ProjectConfig, get_project_config


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _release_contract(cfg: ProjectConfig) -> dict[str, Any]:
    """Prefer the release layer; retain legacy production config compatibility."""
    return cfg.eval.get("release") or cfg.eval.get("production") or {}


def _execution_contract_reasons(meta: dict[str, Any], release: dict[str, Any]) -> list[str]:
    """Return fail-closed data-contract violations for a tradable release."""
    if not release.get("require_execution_data", False):
        return []
    requirements = {
        "security_status_coverage": "min_security_status_coverage",
        "limit_price_coverage": "min_limit_price_coverage",
        "adv_20d_coverage": "min_adv_20d_coverage",
        "corporate_action_coverage": "min_corporate_action_coverage",
        "industry_pit_coverage": "min_industry_pit_coverage",
        "risk_exposures_coverage": "min_risk_exposures_coverage",
    }
    reasons: list[str] = []
    for metric, threshold in requirements.items():
        value = float(meta.get(metric, 0.0) or 0.0)
        minimum = float(release.get(threshold, 1.0))
        if value < minimum:
            reasons.append(f"{metric}_below_contract")
    return reasons


class TradabilityService:
    """Create a conservative readiness report for a future execution simulator.

    This is intentionally fail-closed. A report can diagnose data/execution gaps,
    but only a dedicated order-level simulator may write `tradability_passed`.
    """

    def __init__(self, cfg: ProjectConfig | None = None, registry: FactorRegistry | None = None):
        self.cfg = cfg or get_project_config()
        self.registry = registry or FactorRegistry(self.cfg)

    def assess_readiness(self, name: str) -> dict[str, Any]:
        spec = self.registry.load_spec(name)
        data = DataService(self.cfg).status()
        meta = data.get("meta") or {}
        production = self.cfg.eval.get("production") or {}
        release = _release_contract(self.cfg)
        reasons: list[str] = []
        universe = str((meta.get("members_provider") or {}).get("provider") or "").lower()
        circ_mv = str((meta.get("quality") or {}).get("circ_mv_source") or meta.get("circ_mv_source") or "").lower()
        limitations = [str(x) for x in (meta.get("limitations") or [])]
        if any("snapshot" in x.lower() for x in limitations) or universe in {"csindex", "csindex_latest"}:
            reasons.append("universe_not_pit")
        allowed = {str(x).lower() for x in production.get("allowed_circ_mv_sources", [])}
        if allowed and circ_mv not in allowed:
            reasons.append("circ_mv_not_vendor")
        reasons.extend(_execution_contract_reasons(meta, release))
        reasons.append("missing_order_level_execution_simulator")
        report = {
            "schema_version": 1,
            "name": name,
            "definition_hash": definition_hash(spec),
            "data_version": data.get("data_version"),
            "state": "tradability_blocked",
            "created_at": _utc_now(),
            "reasons": reasons,
            "requirements": {
                "signal_timing": "T signal / T+1 executable order",
                "constraints": ["suspension", "limit_up_down", "ST", "lot_size", "ADV_participation"],
                "costs": ["fees", "slippage", "impact"],
                "required_outputs": ["gross_net_pnl", "turnover", "fill_rate", "capacity_curve"],
            },
            "data_meta": {
                "universe_provider": universe,
                "circ_mv_source": circ_mv,
                "security_status_coverage": meta.get("security_status_coverage"),
                "limit_price_coverage": meta.get("limit_price_coverage"),
                "adv_20d_coverage": meta.get("adv_20d_coverage"),
                "corporate_action_coverage": meta.get("corporate_action_coverage"),
                "industry_pit_coverage": meta.get("industry_pit_coverage"),
                "risk_exposures_coverage": meta.get("risk_exposures_coverage"),
                "limitations": limitations,
            },
        }
        root = self.registry.factor_dir(name) / "tradability"
        root.mkdir(parents=True, exist_ok=True)
        (root / "latest.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    def simulate(self, name: str) -> dict[str, Any]:
        """Run the non-overlapping execution ledger; never upgrades missing data."""
        factor = self.registry.load_factor(name)
        evaluator = EvalService(self.cfg)
        signal = factor.compute(evaluator._context())
        bars = DataService(self.cfg).load_bars().copy()
        bars["trade_date"] = bars["trade_date"].astype(str)
        def panel(field: str) -> Any:
            if field not in bars.columns:
                return None
            return bars.pivot(index="trade_date", columns="ts_code", values=field).sort_index()
        open_px = panel("open")
        close_px = panel("close")
        pre_close = panel("pre_close")
        amount = panel("amount")
        is_st = panel("is_st")
        is_suspended = panel("is_suspended")
        limit_up = panel("limit_up")
        limit_down = panel("limit_down")
        adv_20d = panel("adv_20d")
        free_float_shares = panel("free_float_shares")
        if open_px is None or close_px is None or pre_close is None:
            raise RuntimeError("Execution simulation requires open, close, and pre_close panels")
        ev = self.cfg.eval.get("eval") or {}
        execution = simulate_non_overlapping_long_short(
            signal,
            open_px,
            close_px,
            pre_close,
            amount,
            is_st=is_st,
            is_suspended=is_suspended,
            limit_up=limit_up,
            limit_down=limit_down,
            adv_20d=adv_20d,
            free_float_shares=free_float_shares,
            trade_lag=int(ev.get("trade_lag", 1)),
            hold_days=int(ev.get("signal_hold_days", 5)),
            quantiles=int(ev.get("n_quantiles", 5)),
            cost_bps=float(ev.get("cost_bps", 10)),
        )
        readiness = self.assess_readiness(name)
        reasons = [r for r in readiness.get("reasons", []) if r != "missing_order_level_execution_simulator"]
        reasons.extend(execution.get("limitations") or [])
        if int(execution.get("n_filled") or 0) < 20:
            reasons.append("insufficient_non_overlapping_rebalances")
        if float(execution.get("net_long_short_mean") or 0.0) <= 0.0:
            reasons.append("net_execution_return_not_positive")
        state = "tradability_passed" if not reasons else "tradability_blocked"
        report = {
            "schema_version": 3,
            "name": name,
            "definition_hash": definition_hash(factor.spec),
            "data_version": DataService(self.cfg).data_version(),
            "state": state,
            "created_at": _utc_now(),
            "reasons": sorted(set(reasons)),
            "execution": execution,
            "note": "Only a PIT/ST/limit/capacity-complete execution report may become tradability_passed.",
        }
        root = self.registry.factor_dir(name) / "tradability"
        root.mkdir(parents=True, exist_ok=True)
        (root / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    def latest(self, name: str) -> dict[str, Any] | None:
        path = self.registry.factor_dir(name) / "tradability" / "latest.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


class ReleaseService:
    """Publish only versioned, evidence-complete factor releases for downstream use."""

    def __init__(
        self,
        cfg: ProjectConfig | None = None,
        *,
        registry: FactorRegistry | None = None,
        acceptance: AcceptanceService | None = None,
        tradability: TradabilityService | None = None,
        db: Database | None = None,
    ):
        self.cfg = cfg or get_project_config()
        self.registry = registry or FactorRegistry(self.cfg)
        self.acceptance = acceptance or AcceptanceService(self.cfg, registry=self.registry)
        self.tradability = tradability or TradabilityService(self.cfg, registry=self.registry)
        self.db = db or Database()

    def eligibility(self, name: str) -> dict[str, Any]:
        spec = self.registry.load_spec(name)
        latest_report = self._load_json(self.registry.factor_dir(name) / "reports" / "latest.json")
        acceptance = self.acceptance.latest(name)
        tradability = self.tradability.latest(name)
        data_status = DataService(self.cfg).status()
        data_version = data_status.get("data_version")
        reasons: list[str] = _execution_contract_reasons(
            (data_status.get("meta") or {}), _release_contract(self.cfg)
        )
        if latest_report is None:
            reasons.append("missing_production_report")
        else:
            gate = latest_report.get("gate") or {}
            metrics = latest_report.get("metrics") or {}
            if gate.get("mode") != "production" or not gate.get("passed"):
                reasons.append("latest_report_not_passing_production_gate")
            if data_version and metrics.get("data_version") != data_version:
                reasons.append("stale_data_version")
        if acceptance is None:
            reasons.append("missing_sealed_acceptance")
        elif acceptance.get("state") != "sealed_oos_passed":
            reasons.append("sealed_acceptance_not_passed")
        elif acceptance.get("definition_hash") != definition_hash(spec):
            reasons.append("definition_changed_after_acceptance")
        elif data_version and acceptance.get("data_version") != data_version:
            reasons.append("sealed_acceptance_stale_data_version")
        elif not bool((acceptance.get("selection_bias_audit") or {}).get("passed")):
            reasons.append("selection_bias_audit_not_passed")
        if tradability is None:
            reasons.append("missing_tradability_report")
        elif tradability.get("state") != "tradability_passed":
            reasons.append("tradability_not_passed")
        elif tradability.get("definition_hash") != definition_hash(spec):
            reasons.append("definition_changed_after_tradability")
        elif data_version and tradability.get("data_version") != data_version:
            reasons.append("tradability_stale_data_version")
        return {
            "name": name,
            "definition_hash": definition_hash(spec),
            "data_version": data_version,
            "eligible": not reasons,
            "reasons": reasons,
            "acceptance": acceptance,
            "tradability": tradability,
        }

    def publish(self, name: str) -> dict[str, Any]:
        check = self.eligibility(name)
        if not check["eligible"]:
            return {"state": "release_blocked", **check}
        spec = self.registry.load_spec(name)
        release_id = f"rel_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:10]}"
        root = self.cfg.path("factor_lib") / "releases" / release_id
        root.mkdir(parents=True, exist_ok=False)
        factor_dir = self.registry.factor_dir(name)
        code = (factor_dir / "factor.py").read_bytes()
        payload = {
            "schema_version": 1,
            "release_id": release_id,
            "name": name,
            "version": spec.version,
            "state": "active",
            "created_at": _utc_now(),
            "definition_hash": check["definition_hash"],
            "code_sha256": hashlib.sha256(code).hexdigest(),
            "data_version": check["data_version"],
            "acceptance_id": (check["acceptance"] or {}).get("acceptance_id"),
            "experiment_id": (check["acceptance"] or {}).get("experiment_id"),
            "factor_path": str(factor_dir.relative_to(self.cfg.root)),
            "artifacts": {
                "spec": "spec.yaml",
                "code": "factor.py",
                "production_report": "production_report.json",
                "sealed_acceptance": "sealed_acceptance.json",
                "tradability": "tradability.json",
            },
        }
        (root / "spec.yaml").write_text((factor_dir / "spec.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        (root / "factor.py").write_bytes(code)
        for out_name, src in (
            ("production_report.json", factor_dir / "reports" / "latest.json"),
            ("sealed_acceptance.json", factor_dir / "acceptance" / "latest.json"),
            ("tradability.json", factor_dir / "tradability" / "latest.json"),
        ):
            (root / out_name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        (root / "release_manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.db.save_release(release_id, payload)
        return payload

    def export_active(self, output: str | None = None) -> dict[str, Any]:
        releases = self.db.list_releases(state="active")
        data_version = DataService(self.cfg).status().get("data_version")
        active = [r for r in releases if r.get("data_version") == data_version]
        inventory = {
            "contract_version": "trading-factor-release-v1",
            "data_version": data_version,
            "n_active": len(active),
            "releases": active,
        }
        path = Path(output) if output else self.cfg.path("factor_lib") / "trading_factor_releases.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**inventory, "path": str(path)}

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
