from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Literal

from qfactor.agent.experiments import require_candidate_contract
from qfactor.eval.gate import KEEP_STATUSES
from qfactor.eval.service import EvalService
from qfactor.factor.cohort import apply_parent_eligibility
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import ProjectConfig, get_project_config


Status = Literal["draft", "screened", "candidate", "approved", "deprecated", "archived"]


def screened_promotion_key(summary: dict[str, Any] | None) -> tuple[float, float, float]:
    """Rank screened → production attempts by train / residual / OOS floor.

    Research icir_ann * oos_ic_mean over-weights overlapping annualized ICIR
    and ignores the production contract (train magnitude, residual, min fold).
    """
    s = summary if isinstance(summary, dict) else {}
    train = abs(float(s.get("train_rank_ic_mean") or 0))
    resid = abs(float(s.get("resid_icir_nw") or s.get("resid_ic_mean") or 0))
    oos = float(s.get("oos_min_fold_ic") or s.get("oos_ic_mean") or 0)
    return (train, resid, oos)


def screened_library_key(
    summary: dict[str, Any] | None,
) -> tuple[float, float, float, float]:
    """Rank research inventory by robust evidence, not overlapping annualized ICIR.

    Screened factors are retained as reusable price-volume research parents.  A
    high annualized ICIR from a short, overlapping series must not outrank a
    factor with stronger train, residual, and worst-fold evidence.
    """
    s = summary if isinstance(summary, dict) else {}
    train = abs(float(s.get("train_rank_ic_mean") or 0))
    resid = abs(float(s.get("resid_ic_mean") or 0))
    oos_floor = float(s.get("oos_min_fold_ic") or s.get("oos_ic_mean") or 0)
    coverage = float(s.get("coverage") or 0)
    return (train, resid, oos_floor, coverage)


class LibraryOps:
    """Factor library operating rules: archive / promote / demote / corr demote."""

    def __init__(self, cfg: ProjectConfig | None = None):
        self.cfg = cfg or get_project_config()
        self.registry = FactorRegistry(self.cfg)
        self.ops_cfg = (self.cfg.project.get("library_ops") or {})

    def reconcile_state(self) -> dict[str, Any]:
        """Read-only cross-store consistency check for supervisor heartbeats."""
        from qfactor.factor.reconcile import reconcile_library_state

        return reconcile_library_state(self.cfg, registry=self.registry)

    def archive_stale(
        self,
        draft_days: int | None = None,
        reject_days: int | None = None,
        force_rejects: bool = False,
    ) -> dict[str, Any]:
        draft_days = draft_days if draft_days is not None else int(
            self.ops_cfg.get("archive_draft_days", 14)
        )
        reject_days = reject_days if reject_days is not None else int(
            self.ops_cfg.get("archive_reject_days", 7)
        )
        now = datetime.now(timezone.utc)
        archived: list[str] = []
        archive_root = self.cfg.path("factor_lib") / "_archive"
        archive_root.mkdir(parents=True, exist_ok=True)

        for item in self.registry.list_factors():
            name = item["name"]
            status = item.get("status", "draft")
            summary = item.get("summary") or {}
            source = item.get("source")
            if source == "seed":
                continue
            fdir = self.registry.factor_dir(name)
            if not fdir.exists():
                continue
            latest = fdir / "reports" / "latest.json"
            mtime = datetime.fromtimestamp(fdir.stat().st_mtime, tz=timezone.utc)
            if latest.exists():
                mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
            age = now - mtime
            should = False
            if status == "draft" and summary.get("status") == "reject":
                if force_rejects or age > timedelta(days=reject_days):
                    should = True
            elif status == "draft" and not summary and age > timedelta(days=draft_days):
                should = True
            elif status == "draft" and age > timedelta(days=draft_days * 2):
                should = True
            elif status == "deprecated" and (force_rejects or age > timedelta(days=reject_days)):
                should = True
            if not should:
                continue
            dest = archive_root / f"{name}_{now.strftime('%Y%m%dT%H%M%SZ')}"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(fdir), str(dest))
            catalog = self.registry._read_catalog()
            catalog["factors"] = [f for f in catalog.get("factors", []) if f.get("name") != name]
            self.registry._write_catalog(catalog)
            try:
                from qfactor.db.repo import Database

                Database().delete_factor(name)
                Database().log_library_op(name, "archive", "archived")
            except Exception as e:
                print(f"[library] db archive cleanup failed for {name}: {e}", flush=True)
            archived.append(name)
        return {"archived": archived, "n": len(archived)}

    def promote(self, name: str, to: Status = "approved") -> dict[str, str]:
        if to not in {"screened", "candidate", "approved"}:
            raise ValueError("promote target must be screened|candidate|approved")
        self.registry.update_status(name, to)
        self._log_op(name, "promote", to)
        return {"name": name, "status": to}

    def demote(self, name: str, to: Status = "draft", reason: str = "") -> dict[str, str]:
        if to not in {"draft", "screened", "deprecated", "archived"}:
            raise ValueError("demote target must be draft|screened|deprecated|archived")
        if to == "archived":
            self.archive_named(name)
        else:
            self.registry.update_status(name, to)
        self._log_op(name, "demote", to, reason=reason)
        return {"name": name, "status": to, "reason": reason}

    def archive_named(self, name: str) -> None:
        fdir = self.registry.factor_dir(name)
        archive_root = self.cfg.path("factor_lib") / "_archive"
        archive_root.mkdir(parents=True, exist_ok=True)
        dest = archive_root / f"{name}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        if fdir.exists():
            shutil.move(str(fdir), str(dest))
        catalog = self.registry._read_catalog()
        catalog["factors"] = [f for f in catalog.get("factors", []) if f.get("name") != name]
        self.registry._write_catalog(catalog)
        try:
            from qfactor.db.repo import Database

            Database().delete_factor(name)
            Database().log_library_op(name, "archive", "archived")
        except Exception as e:
            print(f"[library] db archive cleanup failed for {name}: {e}", flush=True)

    def demote_high_corr(self, max_corr: float | None = None) -> dict[str, Any]:
        """If two candidates/approved are too correlated, keep higher |IC| and demote the other."""
        max_corr = max_corr if max_corr is not None else float(
            self.ops_cfg.get("auto_demote_corr", 0.70)
        )
        kept = [
            f
            for f in self.registry.list_factors()
            if f.get("status") in {"candidate", "approved"}
        ]
        demoted: list[dict[str, Any]] = []
        # Use summary max_corr_with when available from latest report
        for item in kept:
            name = item["name"]
            latest = self.registry.factor_dir(name) / "reports" / "latest.json"
            if not latest.exists():
                continue
            report = json.loads(latest.read_text(encoding="utf-8"))
            metrics = report.get("metrics") or {}
            corr = float(metrics.get("max_corr") or 0)
            peer = metrics.get("max_corr_with")
            if corr < max_corr or not peer:
                continue
            peer_item = next((x for x in kept if x["name"] == peer), None)
            if peer_item is None:
                continue
            my_ic = abs(float((item.get("summary") or {}).get("rank_ic_mean") or 0))
            peer_ic = abs(float((peer_item.get("summary") or {}).get("rank_ic_mean") or 0))
            loser = name if my_ic < peer_ic else peer
            if any(d["name"] == loser for d in demoted):
                continue
            self.registry.update_status(loser, "screened")
            self._log_op(loser, "auto_demote_corr", "screened", reason=f"corr={corr:.3f} vs {peer}")
            demoted.append({"name": loser, "corr": corr, "peer": peer if loser == name else name})
        return {"demoted": demoted, "threshold": max_corr}

    def reevaluate_and_route(
        self, name: str, gate_name: str = "production"
    ) -> dict[str, Any]:
        """Strict production gate for promotion path."""
        if gate_name == "production":
            require_candidate_contract(self.cfg)
        return EvalService(self.cfg).evaluate_and_save(
            name, gate_name=gate_name, promote=True
        )

    def promote_screened(self, names: list[str] | None = None) -> dict[str, Any]:
        """Re-run production gate on screened factors when its data contract is ready.

        A screen sweep is expensive and cannot honestly produce a candidate from
        snapshot data.  Preflight before touching reports so the research library
        remains stable until the PIT/time contract is complete.
        """
        try:
            require_candidate_contract(self.cfg)
        except Exception as exc:
            return {
                "state": "blocked",
                "reason": str(exc),
                "promoted": [],
                "held_screened": [],
                "errors": [],
                "corr_demoted": [],
                "mech_capped": [],
            }
        if names is None:
            ranked: list[tuple[tuple[float, float, float], str]] = []
            for f in self.registry.list_factors():
                if f.get("status") != "screened":
                    continue
                ranked.append((screened_promotion_key(f.get("summary")), str(f["name"])))
            ranked.sort(reverse=True)
            names = [n for _s, n in ranked]
        promoted: list[str] = []
        held: list[str] = []
        errors: list[dict[str, str]] = []
        ev = EvalService(self.cfg)
        for name in names:
            try:
                report = ev.evaluate_and_save(name, gate_name="production", promote=True)
                if report.get("gate", {}).get("status") == "candidate":
                    promoted.append(name)
                else:
                    held.append(name)
            except Exception as e:
                errors.append({"name": name, "error": str(e)})
        corr = self.demote_high_corr()
        cap = self.cap_usable_per_mechanism()
        return {
            "state": "ok",
            "promoted": promoted,
            "held_screened": held,
            "errors": errors,
            "corr_demoted": corr.get("demoted") or [],
            "mech_capped": cap.get("demoted") or [],
        }

    def refresh_production(self, include_screened: bool = False) -> dict[str, Any]:
        """Re-score candidates under the current production gate.

        By default does not sweep the screened pile — that pile was admitted
        under research gates and would dump redundant amplitude into production.
        Pass include_screened=True (CLI library-reeval-screened) to try promotions.
        """
        candidates = [
            str(f["name"])
            for f in self.registry.list_factors()
            if f.get("status") == "candidate"
        ]
        ev = EvalService(self.cfg)
        kept: list[str] = []
        demoted: list[str] = []
        errors: list[dict[str, str]] = []
        for name in candidates:
            try:
                report = ev.evaluate_and_save(name, gate_name="production", promote=True)
                if report.get("gate", {}).get("status") == "candidate":
                    kept.append(name)
                else:
                    demoted.append(name)
            except Exception as e:
                errors.append({"name": name, "error": str(e)})
        prune = self.prune_redundant_screened()
        corr = self.demote_high_corr()
        cap = self.cap_usable_per_mechanism()
        promo: dict[str, Any] = {"promoted": [], "held_screened": [], "errors": []}
        if include_screened:
            promo = self.promote_screened()
            cap2 = self.cap_usable_per_mechanism()
            cap["demoted"] = list(cap.get("demoted") or []) + list(cap2.get("demoted") or [])
        promo["errors"] = (promo.get("errors") or []) + errors
        rerank = self.rerank_candidates_by_resid()
        return {
            "kept_candidates": kept,
            "demoted_candidates": demoted,
            "pruned_screened": prune.get("demoted") or [],
            "corr_demoted": corr.get("demoted") or [],
            "mech_capped": cap.get("demoted") or [],
            "screened_promotion_state": promo.get("state", "not_requested"),
            "screened_promotion_reason": promo.get("reason"),
            "promoted": promo.get("promoted") or [],
            "held_screened": promo.get("held_screened") or [],
            "resid_rerank": rerank,
            "errors": promo.get("errors") or [],
        }

    def cap_usable_per_mechanism(self, max_per: int | None = None) -> dict[str, Any]:
        """Keep at most N candidate/approved factors per mechanism; rest → screened."""
        max_per = int(
            max_per
            if max_per is not None
            else self.ops_cfg.get("cap_usable_per_mechanism", 1)
        )
        if max_per <= 0:
            return {"demoted": [], "max_per": max_per, "skipped": True}

        def _score(item: dict[str, Any]) -> tuple[float, float, float]:
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            train = abs(float(summary.get("train_rank_ic_mean") or 0))
            resid = abs(float(summary.get("resid_ic_mean") or 0))
            ic = abs(float(summary.get("rank_ic_mean") or 0))
            return (train, resid, ic)

        buckets: dict[str, list[tuple[tuple[float, float, float], str]]] = {}
        for item in self.registry.list_factors():
            if item.get("status") not in {"candidate", "approved"}:
                continue
            name = str(item["name"])
            mid = str(item.get("category") or "").strip()
            try:
                spec = self.registry.load_spec(name)
                mid = str(spec.mechanism or spec.category or mid).strip()
            except Exception:
                pass
            if not mid:
                mid = "unknown"
            buckets.setdefault(mid, []).append((_score(item), name))
        demoted: list[dict[str, str]] = []
        kept: list[dict[str, str]] = []
        for mid, rows in buckets.items():
            rows.sort(reverse=True)
            for _score, name in rows[:max_per]:
                kept.append({"name": name, "mechanism": mid})
            for _score, name in rows[max_per:]:
                self.registry.update_status(name, "screened")
                self._log_op(name, "cap_usable_mechanism", "screened", reason=f"mech={mid}")
                demoted.append({"name": name, "mechanism": mid})
        return {"kept": kept, "demoted": demoted, "max_per": max_per}

    def rerank_candidates_by_resid(self) -> dict[str, Any]:
        """Leave-one-out residual IC vs other candidates; demote those that fail.

        The first name into the library has resid_ic = ic (no peers yet). After
        the set grows, re-score everyone against the rest so the order of
        promotion does not decide who stays.
        """
        names = [
            str(f["name"])
            for f in self.registry.list_factors()
            if f.get("status") == "candidate"
        ]
        if len(names) <= 1:
            return {"kept": names, "demoted": [], "skipped": True}
        ev = EvalService(self.cfg)
        scored: list[tuple[float, str, dict[str, Any]]] = []
        errors: list[dict[str, str]] = []
        for name in names:
            try:
                report = ev.evaluate_and_save(name, gate_name="production", promote=False)
                resid = abs(float((report.get("metrics") or {}).get("resid_ic_mean") or 0))
                scored.append((resid, name, report))
            except Exception as e:
                errors.append({"name": name, "error": str(e)})
        scored.sort(reverse=True)
        demoted: list[str] = []
        kept: list[str] = []
        for _resid, name, report in scored:
            gate = report.get("gate") or {}
            if gate.get("passed"):
                kept.append(name)
            else:
                self.registry.update_status(name, "screened")
                self._log_op(name, "demote", "screened", reason="resid_rerank")
                demoted.append(name)
        for name in kept:
            try:
                ev.evaluate_and_save(name, gate_name="production", promote=True)
            except Exception as e:
                errors.append({"name": name, "error": str(e)})
        return {"kept": kept, "demoted": demoted, "errors": errors}

    def prune_redundant_screened(self, max_per: int | None = None) -> dict[str, Any]:
        """Keep the best screened factors per skeleton; demote extras to draft.

        Staging should not hoard window variants that block new structures on corr.
        """
        from qfactor.agent.diversity import expression_fingerprint

        div = (self.cfg.project.get("production") or {}).get("diversity") or {}
        max_per = int(max_per if max_per is not None else div.get("max_per_skeleton", 2))
        buckets: dict[str, list[tuple[tuple[float, float, float, float], str]]] = {}
        for item in self.registry.list_factors():
            if item.get("status") != "screened":
                continue
            name = str(item["name"])
            try:
                spec = self.registry.load_spec(name)
                expr = spec.expression or (spec.params or {}).get("expression")
                if not expr:
                    continue
                sk = expression_fingerprint(str(expr))["skeleton"]
            except Exception:
                continue
            buckets.setdefault(sk, []).append((screened_library_key(item.get("summary")), name))
        demoted: list[str] = []
        for _sk, rows in buckets.items():
            rows.sort(reverse=True)
            for _score, name in rows[max_per:]:
                self.demote(name, "draft", reason="skeleton_cap")
                demoted.append(name)
        return {"demoted": demoted, "max_per": max_per}

    def quality_library(self) -> dict[str, Any]:
        """Mining output: PIT research-gate KEEP factors on the live panel.

        This factory's job is a high-quality price-volume library, not live
        trading. Later research modules should read this inventory
        (``tradable=false``). Snapshot / unverified / other-panel rows stay out.
        Candidate promotion is a separate, still-blocked contract.
        """
        production = self.cfg.eval.get("production") or {}
        data_version = EvalService(self.cfg).data.data_version()
        expected_universe = {
            str(x).strip().lower()
            for x in production.get("allowed_universe_modes", ["pit"])
        }
        expected_circ_mv = {
            str(x).strip().lower()
            for x in production.get("allowed_circ_mv_sources", ["tushare_daily_basic"])
        }
        min_daily_basic = float(production.get("min_daily_basic_coverage", 0.0))
        factors: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for item in self.registry.list_factors():
            name = str(item.get("name") or "")
            if not name:
                continue
            status = str(item.get("status") or "")
            if status not in KEEP_STATUSES:
                continue
            if str(item.get("source") or "") == "seed":
                excluded.append({"name": name, "reasons": ["seed_template_not_mining_output"]})
                continue
            meta = apply_parent_eligibility(item, data_version)
            if not meta.get("parent_eligible"):
                excluded.append(
                    {
                        "name": name,
                        "reasons": [str(meta.get("reason") or "parent_ineligible")],
                    }
                )
                continue
            latest = self.registry.factor_dir(name) / "reports" / "latest.json"
            if not latest.exists():
                excluded.append({"name": name, "reasons": ["missing_latest_report"]})
                continue
            try:
                report = json.loads(latest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                excluded.append({"name": name, "reasons": [f"invalid_latest_report:{exc}"]})
                continue
            gate = report.get("gate") if isinstance(report.get("gate"), dict) else {}
            metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
            reasons: list[str] = []
            # Research soft-pass can keep status=screened while passed is False.
            if not bool(gate.get("passed")) and str(gate.get("status") or "") != "screened":
                reasons.append("latest_report_not_passing_research_gate")
            if str(gate.get("mode") or "") not in {"research", "production"}:
                reasons.append("latest_report_not_research_or_production")
            if data_version and str(metrics.get("data_version") or "") != str(data_version):
                reasons.append("stale_data_version")
            if expected_universe and str(metrics.get("universe_mode") or "").lower() not in expected_universe:
                reasons.append("universe_not_pit")
            if expected_circ_mv and str(metrics.get("circ_mv_source") or "").lower() not in expected_circ_mv:
                reasons.append("circ_mv_not_vendor")
            if float(metrics.get("daily_basic_coverage") or 0.0) < min_daily_basic:
                reasons.append("daily_basic_coverage_below_contract")
            if production.get("require_industry_pit", False) and float(
                metrics.get("industry_pit_coverage") or 0.0
            ) < float(production.get("min_industry_pit_coverage", 1.0)):
                reasons.append("industry_pit_coverage_below_contract")
            if reasons:
                excluded.append({"name": name, "reasons": reasons})
                continue
            spec = self.registry.load_spec(name)
            factor_dir = self.registry.factor_dir(name)
            try:
                factor_path = str(factor_dir.relative_to(self.cfg.root))
            except ValueError:
                factor_path = str(factor_dir)
            factors.append(
                {
                    "name": name,
                    "status": status,
                    "layer": "mining_quality",
                    "cohort": meta.get("cohort"),
                    "mechanism": spec.mechanism or spec.category,
                    "expression": spec.expression or (spec.params or {}).get("expression"),
                    "data_version": metrics.get("data_version") or data_version,
                    "tradable": False,
                    "usage": "price_volume_research_library",
                    "factor_path": factor_path,
                    "quality": {
                        key: metrics.get(key)
                        for key in (
                            "rank_ic_mean",
                            "resid_ic_mean",
                            "icir_ann",
                            "coverage",
                            "max_corr",
                            "n_peers",
                            "n_independent",
                            "oos_ic_mean",
                            "daily_turnover",
                            "universe_mode",
                            "circ_mv_source",
                            "industry_pit_coverage",
                        )
                    },
                }
            )
        factors.sort(
            key=lambda x: (
                -abs(float((x.get("quality") or {}).get("rank_ic_mean") or 0)),
                str(x.get("mechanism") or ""),
                str(x.get("name") or ""),
            )
        )
        return {
            "contract_version": "quality-library-v1",
            "usage": "price_volume_research_library",
            "tradable": False,
            "data_version": data_version,
            "n_eligible": len(factors),
            "n_excluded": len(excluded),
            "factors": factors,
            "excluded": excluded,
            "notes": [
                "Mining deliverable on the live PIT panel. Not candidate and not a trading release.",
                "Do not invent selection dates to force candidate promotion.",
            ],
        }

    def export_quality_library(self, output: str | None = None) -> dict[str, Any]:
        """Persist the mining quality library without changing factor states."""
        inventory = self.quality_library()
        path = Path(output) if output else self.cfg.path("factor_lib") / "quality_library.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return {**inventory, "path": str(path)}

    def multifactor_inventory(self) -> dict[str, Any]:
        """Build a strict, data-version-pinned inventory for downstream strategies.

        This method deliberately does *not* promote factors.  It is a read-only
        quality boundary between the factor factory and any future multi-factor
        optimizer: only current `candidate`/`approved` records with a passing
        production report generated on the active data version are exported.
        Mining output lives in ``quality_library`` until a frozen selection
        window exists.
        """
        production = self.cfg.eval.get("production") or {}
        data_version = EvalService(self.cfg).data.data_version()
        expected_universe = {
            str(x).strip().lower()
            for x in production.get("allowed_universe_modes", ["pit"])
        }
        expected_circ_mv = {
            str(x).strip().lower()
            for x in production.get("allowed_circ_mv_sources", ["tushare_daily_basic"])
        }
        min_daily_basic = float(production.get("min_daily_basic_coverage", 0.0))
        min_independent = int(production.get("min_independent_observations", 0))
        factors: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []

        for item in self.registry.list_factors():
            name = str(item.get("name") or "")
            if not name:
                continue
            if item.get("status") not in {"candidate", "approved"}:
                continue
            reasons: list[str] = []
            latest = self.registry.factor_dir(name) / "reports" / "latest.json"
            if not latest.exists():
                excluded.append({"name": name, "reasons": ["missing_latest_report"]})
                continue
            try:
                report = json.loads(latest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                excluded.append({"name": name, "reasons": [f"invalid_latest_report:{exc}"]})
                continue

            gate = report.get("gate") if isinstance(report.get("gate"), dict) else {}
            metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
            if gate.get("mode") != "production" or not bool(gate.get("passed")):
                reasons.append("latest_report_not_passing_production_gate")
            if data_version and metrics.get("data_version") != data_version:
                reasons.append("stale_data_version")
            if expected_universe and str(metrics.get("universe_mode") or "").lower() not in expected_universe:
                reasons.append("universe_not_pit")
            if expected_circ_mv and str(metrics.get("circ_mv_source") or "").lower() not in expected_circ_mv:
                reasons.append("circ_mv_not_vendor")
            if float(metrics.get("daily_basic_coverage") or 0.0) < min_daily_basic:
                reasons.append("daily_basic_coverage_below_contract")
            if int(metrics.get("n_independent") or 0) < min_independent:
                reasons.append("insufficient_independent_observations")
            if production.get("require_industry_pit", False) and float(
                metrics.get("industry_pit_coverage") or 0.0
            ) < float(production.get("min_industry_pit_coverage", 1.0)):
                reasons.append("industry_pit_coverage_below_contract")
            if production.get("require_selection_bias_audit", False) and not bool(
                (report.get("selection_bias_audit") or {}).get("passed")
            ):
                reasons.append("selection_bias_audit_not_passed")
            if reasons:
                excluded.append({"name": name, "reasons": reasons})
                continue

            spec = self.registry.load_spec(name)
            factor_dir = self.registry.factor_dir(name)
            try:
                factor_path = str(factor_dir.relative_to(self.cfg.root))
            except ValueError:
                # A custom registry may be mounted outside the project root.
                factor_path = str(factor_dir)
            factors.append(
                {
                    "name": name,
                    "status": item.get("status"),
                    "mechanism": spec.mechanism or spec.category,
                    "expression": spec.expression or (spec.params or {}).get("expression"),
                    "data_version": metrics.get("data_version"),
                    "tradable": False,
                    "usage": "research_and_portfolio_optimization_only",
                    "factor_path": factor_path,
                    "quality": {
                        key: metrics.get(key)
                        for key in (
                            "train_rank_ic_mean",
                            "rank_ic_mean",
                            "icir_nw",
                            "resid_ic_mean",
                            "resid_icir_nw",
                            "oos_ic_mean",
                            "oos_min_fold_ic",
                            "coverage",
                            "daily_turnover",
                            "max_corr",
                            "cost_adjusted_ls",
                            "n_independent",
                            "signal_hold_days",
                            "trade_lag",
                            "industry_pit_coverage",
                            "cost_scenarios",
                        )
                    },
                    "selection_bias_audit": report.get("selection_bias_audit"),
                }
            )
        factors.sort(key=lambda x: (str(x["mechanism"]), str(x["name"])))
        return {
            "contract_version": "multifactor-alpha-input-v4-candidates",
            "usage": "research_and_portfolio_optimization_only",
            "tradable": False,
            "data_version": data_version,
            "n_eligible": len(factors),
            "n_excluded": len(excluded),
            "factors": factors,
            "excluded": excluded,
        }

    def export_multifactor_inventory(self, output: str | None = None) -> dict[str, Any]:
        """Persist the strict multi-factor input inventory without changing factor states."""
        inventory = self.multifactor_inventory()
        path = Path(output) if output else self.cfg.path("factor_lib") / "multifactor_inventory.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**inventory, "path": str(path)}

    def _log_op(self, name: str, action: str, to: str, reason: str = "") -> None:
        log_dir = self.cfg.path("runs") / "library_ops"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "ops.jsonl"
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "name": name,
            "action": action,
            "to": to,
            "reason": reason,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        try:
            from qfactor.db.repo import Database

            Database().log_library_op(name, action, to, reason)
        except Exception:
            pass
