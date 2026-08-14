from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone, timedelta
from typing import Any, Literal

from qfactor.eval.service import EvalService
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import ProjectConfig, get_project_config


Status = Literal["draft", "screened", "candidate", "approved", "deprecated", "archived"]


class LibraryOps:
    """Factor library operating rules: archive / promote / demote / corr demote."""

    def __init__(self, cfg: ProjectConfig | None = None):
        self.cfg = cfg or get_project_config()
        self.registry = FactorRegistry(self.cfg)
        self.ops_cfg = (self.cfg.project.get("library_ops") or {})

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
        if to not in {"draft", "deprecated", "archived"}:
            raise ValueError("demote target must be draft|deprecated|archived")
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
            self.ops_cfg.get("auto_demote_corr", 0.85)
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
            self.registry.update_status(loser, "deprecated")
            self._log_op(loser, "auto_demote_corr", "deprecated", reason=f"corr={corr:.3f} vs {peer}")
            demoted.append({"name": loser, "corr": corr, "peer": peer if loser == name else name})
        return {"demoted": demoted, "threshold": max_corr}

    def reevaluate_and_route(
        self, name: str, gate_name: str = "production"
    ) -> dict[str, Any]:
        """Strict production gate for promotion path."""
        return EvalService(self.cfg).evaluate_and_save(
            name, gate_name=gate_name, promote=True
        )

    def promote_screened(self, names: list[str] | None = None) -> dict[str, Any]:
        """Re-run production gate on screened factors; passers become candidate."""
        if names is None:
            ranked: list[tuple[float, str]] = []
            for f in self.registry.list_factors():
                if f.get("status") != "screened":
                    continue
                summary = f.get("summary") or {}
                icir = abs(float(summary.get("icir_ann") or summary.get("icir") or 0))
                oos = float(summary.get("oos_ic_mean") or 0)
                ranked.append((icir * max(oos, 0.0), str(f["name"])))
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
        return {"promoted": promoted, "held_screened": held, "errors": errors}

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
        promo: dict[str, Any] = {"promoted": [], "held_screened": [], "errors": []}
        if include_screened:
            promo = self.promote_screened()
        promo["errors"] = (promo.get("errors") or []) + errors
        rerank = self.rerank_candidates_by_resid()
        return {
            "kept_candidates": kept,
            "demoted_candidates": demoted,
            "pruned_screened": prune.get("demoted") or [],
            "promoted": promo.get("promoted") or [],
            "held_screened": promo.get("held_screened") or [],
            "resid_rerank": rerank,
            "errors": promo.get("errors") or [],
        }

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
        buckets: dict[str, list[tuple[float, str]]] = {}
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
            summary = item.get("summary") or {}
            icir = abs(float(summary.get("icir_ann") or summary.get("icir") or 0))
            oos = float(summary.get("oos_ic_mean") or 0)
            score = icir * max(oos, 0.0)
            buckets.setdefault(sk, []).append((score, name))
        demoted: list[str] = []
        for _sk, rows in buckets.items():
            rows.sort(reverse=True)
            for _score, name in rows[max_per:]:
                self.demote(name, "draft", reason="skeleton_cap")
                demoted.append(name)
        return {"demoted": demoted, "max_per": max_per}

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
