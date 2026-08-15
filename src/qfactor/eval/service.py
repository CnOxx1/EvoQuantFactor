from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd

from qfactor.data.dataset import DataService
from qfactor.eval.oos import cost_layered, holdout_oos, holdout_window, walk_forward_ic
from qfactor.eval.corr import max_corr_with_library
from qfactor.eval.gate import KEEP_STATUSES, USABLE_STATUSES, apply_gate, route_library_status
from qfactor.eval.ic import rank_ic, summarize_ic, yearly_ic_sign_consistency
from qfactor.eval.layered import layered_returns
from qfactor.eval.timing import apply_signal_hold, apply_trade_lag, drop_tail, forward_close_returns, slice_eval_index
from qfactor.eval.turnover import approx_daily_turnover
from qfactor.factor.base import Factor
from qfactor.factor.context import FactorContext
from qfactor.factor.registry import FactorRegistry
from qfactor.factor.transforms import (
    neutralize_groups,
    neutralize_numeric,
    residualize_on_peers,
    zscore,
)
from qfactor.settings import ProjectConfig, get_project_config


class EvalService:
    def __init__(self, cfg: ProjectConfig | None = None):
        self.cfg = cfg or get_project_config()
        self.data = DataService(self.cfg)
        self.registry = FactorRegistry(self.cfg)
        self._ctx: FactorContext | None = None
        self._peer_cache: dict[str, pd.DataFrame] = {}
        self._industry_map: pd.Series | pd.DataFrame | None = None

    def trade_lag(self) -> int:
        ev = self.cfg.eval.get("eval", {})
        if "trade_lag" in ev:
            return int(ev["trade_lag"])
        return int(self.cfg.project.get("defaults", {}).get("trade_lag", 1))

    def _context(self) -> FactorContext:
        if self._ctx is None:
            self._ctx = FactorContext.from_service(self.data)
        return self._ctx

    def _industry_groups(self) -> pd.Series | pd.DataFrame:
        if self._industry_map is None:
            # The daily bar panel carries archive-backed PIT industry when present.
            # Only use the legacy static map when no daily classification exists.
            try:
                panel = self._context().panel("industry")
                if isinstance(panel, pd.DataFrame) and not panel.empty and panel.notna().any().any():
                    self._industry_map = panel.where(panel.notna())
                    return self._industry_map
            except Exception:
                pass
            try:
                df = self.data.load_industry()
            except Exception:
                df = pd.DataFrame()
            if df is None or df.empty or "ts_code" not in df.columns:
                self._industry_map = pd.Series(dtype=object)
            else:
                col = "industry" if "industry" in df.columns else df.columns[-1]
                self._industry_map = (
                    df.dropna(subset=["ts_code"])
                    .drop_duplicates("ts_code")
                    .set_index("ts_code")[col]
                    .astype(str)
                )
        return self._industry_map

    def _prepare_eval_panel(self, factor_panel: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
        """Strip industry/size exposure then re-zscore so IC is closer to residual alpha."""
        ev = self.cfg.eval.get("eval", {}) or {}
        used: list[str] = []
        panel = factor_panel
        if bool(ev.get("neutralize_industry", True)):
            groups = self._industry_groups()
            if isinstance(groups, pd.DataFrame):
                # PIT classifications are date x security. Eligibility is based
                # on the largest valid cross-section, not DataFrame.nunique(),
                # which returns one count per security column.
                per_date = groups.nunique(axis=1, dropna=True)
                has_industry_groups = bool(len(per_date)) and int(per_date.max()) >= 2
            else:
                has_industry_groups = int(groups.nunique()) >= 2
            if has_industry_groups:
                panel = neutralize_groups(panel, groups)
                used.append("industry")
        if bool(ev.get("neutralize_size", True)):
            try:
                mv = self._context().panel("circ_mv")
                if float(mv.notna().mean().mean()) >= 0.3:
                    expo = np.log(mv.clip(lower=1.0))
                    panel = neutralize_numeric(panel, expo)
                    used.append("circ_mv")
            except Exception:
                pass
        if used:
            panel = zscore(panel)
        return panel, used

    def _forward_returns(self, horizon: int) -> pd.DataFrame:
        close = self._context().panel("close_adj")
        return forward_close_returns(close, horizon)

    def _peer_panels(
        self,
        name: str,
        exclude_names: set[str],
        statuses: tuple[str, ...] | None = None,
        hold: int = 0,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, pd.DataFrame]:
        lag = self.trade_lag()
        statuses = statuses or KEEP_STATUSES
        eligible = [
            item
            for item in self.registry.list_factors()
            if item["name"] != name
            and item["name"] not in exclude_names
            and item.get("status") in statuses
        ]
        others: dict[str, pd.DataFrame] = {}
        total = len(eligible)
        for ordinal, item in enumerate(eligible, 1):
            fname = item["name"]
            if on_progress is not None and (ordinal == 1 or ordinal % 10 == 0 or ordinal == total):
                on_progress(
                    {
                        "stage": "peer_cache",
                        "peer": fname,
                        "peer_index": ordinal,
                        "peer_total": total,
                        "cached": len(self._peer_cache),
                    }
                )
            key = f"{fname}:h{hold}" if hold > 1 else fname
            if key not in self._peer_cache:
                try:
                    fac = self.registry.load_factor(fname)
                    raw = fac.compute(self._context())
                    prepared, _ = self._prepare_eval_panel(raw)
                    if hold > 1:
                        prepared = apply_signal_hold(prepared, hold)
                    self._peer_cache[key] = apply_trade_lag(prepared, lag)
                except Exception:
                    continue
            if key in self._peer_cache:
                others[fname] = self._peer_cache[key]
        return others

    def _train_end(self) -> str:
        ev = self.cfg.eval.get("eval", {}) or {}
        partitions = ev.get("partitions") or {}
        # Once the production date contract is configured, discovery code may
        # only see dates through discovery_end; legacy train_end remains a
        # backward-compatible placeholder while partitions are unconfigured.
        return str(partitions.get("discovery_end") or ev.get("train_end") or "").strip()

    def _eval_split(self, gate_name: str) -> str:
        if not self._train_end():
            return "full"
        return "holdout" if gate_name == "production" else "train"

    def _universe_mode(self) -> str:
        try:
            meta = (self.data.status() or {}).get("meta") or {}
            mode = str(meta.get("universe_mode") or "").strip()
            if mode:
                return mode
            nested = meta.get("members_provider")
            if isinstance(nested, dict) and nested.get("universe_mode"):
                return str(nested["universe_mode"])
            blob = str(meta.get("limitations") or "")
            if "point-in-time" in blob.lower() or "reconstitution" in blob.lower():
                if "latest snapshot" in blob.lower():
                    return "snapshot"
                return "pit"
            if "snapshot" in blob.lower():
                return "snapshot"
        except Exception:
            pass
        return "unknown"

    def _circ_mv_source(self, neutralized: list[str]) -> str:
        if "circ_mv" not in neutralized:
            return "none"
        try:
            meta = (self.data.status() or {}).get("meta") or {}
            src = str(meta.get("circ_mv_source") or "").strip()
            if src:
                return src
        except Exception:
            pass
        return "estimated"

    def _daily_basic_coverage(self) -> float:
        """Return vendor daily-basic coverage recorded by the current data version."""
        return self._data_contract_value("daily_basic_coverage")

    def _data_contract_value(self, key: str) -> float:
        try:
            meta = (self.data.status() or {}).get("meta") or {}
            return float(meta.get(key) or 0.0)
        except Exception:
            return 0.0

    def _slice_panel(
        self, panel: pd.DataFrame, split: str, train_end: str | None = None
    ) -> pd.DataFrame:
        idx = slice_eval_index(panel.index, train_end or self._train_end() or None, split)
        return panel.loc[idx]

    @staticmethod
    def _slice_interval(panel: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
        mask = (panel.index.astype(str) >= str(start)) & (panel.index.astype(str) <= str(end))
        return panel.loc[mask]

    def evaluate_panel(
        self,
        factor_panel: pd.DataFrame,
        name: str,
        gate_name: str = "default",
        exclude_names: set[str] | None = None,
        interval_start: str | None = None,
        interval_end: str | None = None,
        sealed: bool = False,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        ev = self.cfg.eval.get("eval", {})
        thresholds = self.cfg.eval.get(gate_name, self.cfg.eval["default"])
        n_q = int(ev.get("n_quantiles", 5))
        lag = self.trade_lag()
        min_obs = int(ev.get("min_obs_per_day", 30))
        factor_panel, neutralized = self._prepare_eval_panel(factor_panel)
        is_prod = gate_name == "production"
        hold = int(ev.get("signal_hold_days", 0) or 0)
        horizon = int(ev.get("forward_horizon", 5))
        if hold > 1:
            horizon = hold
            factor_panel = apply_signal_hold(factor_panel, hold)
            neutralized = list(neutralized) + [f"hold{hold}"]
        tradable_full = apply_trade_lag(factor_panel, lag)
        no_lookahead = lag >= 1
        split = "sealed" if sealed else self._eval_split(gate_name)
        train_end = str(interval_start or self._train_end())
        if sealed and (not interval_start or not interval_end or interval_start > interval_end):
            raise ValueError("sealed evaluation requires inclusive interval_start and interval_end")

        fwd_full = self._forward_returns(horizon)
        fwd_1d_full = self._forward_returns(1)

        train_f = self._slice_panel(tradable_full, "train", train_end) if train_end else tradable_full
        train_fwd = self._slice_panel(fwd_full, "train", train_end) if train_end else fwd_full
        train_trim_days = int(horizon) if train_end else 0
        if train_trim_days > 0:
            # Last H train days' H-day forward includes holdout (or post-sample) returns.
            train_f = drop_tail(train_f, train_trim_days)
            train_fwd = drop_tail(train_fwd, train_trim_days)
        ic_train_raw = rank_ic(train_f, train_fwd, min_obs=min_obs)
        orient = 1
        if ic_train_raw.empty:
            # No train window: fall back to eval-window sign (legacy / missing train_end).
            eval_tmp = self._slice_panel(tradable_full, split)
            ic_tmp = rank_ic(eval_tmp, self._slice_panel(fwd_full, split), min_obs=min_obs)
            if not ic_tmp.empty and float(ic_tmp.mean()) < 0:
                orient = -1
        elif float(ic_train_raw.mean()) < 0:
            orient = -1

        tradable = self._slice_panel(tradable_full, split) if not sealed else tradable_full
        fwd = self._slice_panel(fwd_full, split) if not sealed else fwd_full
        fwd_1d = self._slice_panel(fwd_1d_full, split) if not sealed else fwd_1d_full
        if sealed:
            tradable = self._slice_interval(tradable, str(interval_start), str(interval_end))
            fwd = fwd.reindex(tradable.index)
            fwd_1d = fwd_1d.reindex(tradable.index)
            # H-day returns at the end of an acceptance interval may use prices
            # beyond that interval; remove them rather than letting sealed OOS peek.
            tradable = drop_tail(tradable, horizon)
            fwd = drop_tail(fwd, horizon)
            fwd_1d = drop_tail(fwd_1d, 1)
        signed = tradable * orient
        ic_raw = rank_ic(tradable, fwd, min_obs=min_obs)
        ic = ic_raw * orient
        nw_lags = max(0, int(horizon) - 1) if is_prod else 0
        ic_summary = summarize_ic(ic, nw_lags=nw_lags)
        train_raw_mean = float(ic_train_raw.mean()) if not ic_train_raw.empty else 0.0
        hold_raw_mean = float(ic_raw.mean()) if not ic_raw.empty else 0.0
        if is_prod and train_end and not ic_train_raw.empty:
            freeze_sign_ok = bool(train_raw_mean * hold_raw_mean > 0)
        else:
            freeze_sign_ok = (not ic.empty) and float(ic.mean()) > 0
        train_ic_oriented = ic_train_raw * orient if not ic_train_raw.empty else ic_train_raw
        train_rank_ic_mean = (
            float(train_ic_oriented.mean()) if not train_ic_oriented.empty else 0.0
        )

        recent_days = int(ev.get("recent_days", 120))
        min_years = int(thresholds.get("min_years_consistent", 2))
        recent_n = recent_days
        if is_prod and train_end and len(ic) <= recent_days:
            recent_n = max(40, len(ic) // 2)
        recent_ic = summarize_ic(ic.tail(recent_n), nw_lags=nw_lags)
        years_ic = ic_train_raw * orient if not ic_train_raw.empty else ic
        years = yearly_ic_sign_consistency(years_ic, min_years)
        if thresholds.get("require_years_same_sign", False):
            years_ok = bool(years["consistent"])
        else:
            years_ok = years["dominant_years"] >= min_years and years["n_years"] >= min_years

        layered_h = layered_returns(signed, fwd, n_quantiles=n_q, min_obs=min_obs)
        layered_1d = layered_returns(signed, fwd_1d, n_quantiles=n_q, min_obs=min_obs)

        coverage = float(signed.notna().mean().mean()) if not signed.empty else 0.0
        turnover = approx_daily_turnover(signed)

        min_abs = float(thresholds.get("min_abs_rank_ic_mean", 0.0))
        peer_status = USABLE_STATUSES if is_prod else KEEP_STATUSES
        peer_hold = hold if hold > 1 else 0
        others_full = {}
        corr = {"max_corr": 0.0, "max_corr_with": None, "skipped": True}
        # For production, always retain the full correlation path.  For the
        # research gate, perform an exact pre-check with max_corr=0.0.  If this
        # already rejects, a real correlation can only make the result worse, so
        # no candidate that could be screened is skipped.  This prevents loading
        # the entire peer library for obvious IC/OOS/turnover rejects.
        should_load_peers = True
        if not is_prod:
            pre_corr_metrics = {
                **ic_summary,
                "coverage": coverage,
                "daily_turnover": turnover,
                "monotonic_score": layered_h.get("monotonic_score", 0.0),
                "years_consistent": years_ok,
                "no_lookahead": no_lookahead,
                "oos_ic_mean": oos.get("oos_ic_mean", 0.0),
                "oos_pos_folds": oos.get("pos_folds", 0),
                "oos_min_fold_ic": oos.get("oos_min_fold_ic", oos.get("oos_ic_mean", 0.0)),
                "max_corr": 0.0,
            }
            should_load_peers = apply_gate(pre_corr_metrics, thresholds, mode="research")["status"] in KEEP_STATUSES
            if on_progress is not None and not should_load_peers:
                on_progress({"stage": "pre_gate_reject", "reason": "cannot_screen_before_correlation"})
        if should_load_peers:
            if on_progress is not None:
                on_progress({"stage": "peer_correlation_start"})
            others_full = self._peer_panels(
                name,
                exclude_names or set(),
                statuses=peer_status,
                hold=peer_hold,
                on_progress=on_progress,
            )
            others = {
                k: (
                    self._slice_interval(v, str(interval_start), str(interval_end))
                    if sealed
                    else self._slice_panel(v, split, train_end)
                )
                for k, v in others_full.items()
            }
            corr = max_corr_with_library(signed, others, min_obs=min_obs)
            corr["skipped"] = False
        else:
            others = {}

        if thresholds.get("require_residual_ic", False):
            resid_panel = residualize_on_peers(signed, others, min_obs=min_obs)
            resid_ic = rank_ic(resid_panel, fwd, min_obs=min_obs)
            resid_summary = summarize_ic(resid_ic, nw_lags=nw_lags)
        else:
            resid_summary = {
                "rank_ic_mean": 0.0,
                "icir": 0.0,
                "icir_ann": 0.0,
                "icir_nw": 0.0,
            }

        cost_bps = float(ev.get("cost_bps", 10))
        cost_horizon = hold if hold > 1 else 1
        cost_layered_src = layered_h if cost_horizon > 1 else layered_1d
        layered_cost = cost_layered(
            cost_layered_src, turnover, cost_bps, horizon=cost_horizon
        )
        layered_cost["horizon_layered"] = layered_h
        cost_ret = float(layered_cost.get("long_short_cost_adj", 0.0))

        if is_prod and sealed:
            oos = holdout_window(
                ic_raw,
                start=str(interval_start),
                end=str(interval_end),
                orientation=orient,
                min_days=int(ev.get("oos_min_days", 40)),
                nw_lags=nw_lags,
                n_folds=2,
            )
        elif is_prod and train_end:
            oos = holdout_oos(
                ic_raw,
                after=train_end,
                orientation=orient,
                min_days=int(ev.get("oos_min_days", 40)),
                nw_lags=nw_lags,
                n_folds=2,
            )
        else:
            oos = walk_forward_ic(
                tradable,
                fwd,
                n_folds=int(ev.get("oos_folds", 4)),
                min_days=int(ev.get("oos_min_days", 40)),
                min_obs=min_obs,
                ic=ic_raw,
            )

        eval_start = str(tradable.index.min()) if len(tradable.index) else None
        eval_end = str(tradable.index.max()) if len(tradable.index) else None

        metrics = {
            **ic_summary,
            "recent_rank_ic_mean": recent_ic.get("rank_ic_mean", 0.0),
            "coverage": coverage,
            "daily_turnover": turnover,
            "monotonic_score": layered_h.get("monotonic_score", 0.0),
            "monotonic_steps": layered_h.get("monotonic_steps", 0.0),
            "layered": layered_cost,
            "max_corr": corr["max_corr"],
            "max_corr_with": corr["max_corr_with"],
            "corr_skipped": bool(corr.get("skipped")),
            "resid_ic_mean": resid_summary.get("rank_ic_mean", 0.0),
            "resid_icir": resid_summary.get("icir", 0.0),
            "resid_icir_ann": resid_summary.get("icir_ann", 0.0),
            "resid_icir_nw": resid_summary.get("icir_nw", 0.0),
            "n_peers": len(others),
            "years": years,
            "years_consistent": years_ok,
            "cost_adjusted_ls": cost_ret,
            "oos_ic_mean": oos.get("oos_ic_mean", 0.0),
            "oos_icir": oos.get("oos_icir", 0.0),
            "oos_pos_folds": oos.get("pos_folds", 0),
            "oos_min_fold_ic": oos.get("oos_min_fold_ic", oos.get("oos_ic_mean", 0.0)),
            "oos": oos,
            "freeze_sign_ok": freeze_sign_ok,
            "train_rank_ic_mean": train_rank_ic_mean,
            "period_stability": oos.get("period_stability", {}),
            "orientation": orient,
            "orientation_source": "train" if train_end and not ic_train_raw.empty else "eval_window",
            "no_lookahead": no_lookahead,
            "trade_lag": lag,
            "horizon": horizon,
            "signal_hold_days": hold if hold > 1 else 0,
            "cost_horizon": cost_horizon,
            "recent_window": recent_n,
            "neutralized": neutralized,
            "circ_mv_source": self._circ_mv_source(neutralized),
            "daily_basic_coverage": self._daily_basic_coverage(),
            "security_status_coverage": self._data_contract_value("security_status_coverage"),
            "limit_price_coverage": self._data_contract_value("limit_price_coverage"),
            "adv_20d_coverage": self._data_contract_value("adv_20d_coverage"),
            "corporate_action_coverage": self._data_contract_value("corporate_action_coverage"),
            "industry_pit_coverage": self._data_contract_value("industry_pit_coverage"),
            "risk_exposures_coverage": self._data_contract_value("risk_exposures_coverage"),
            "eval_split": split,
            "train_end": train_end or None,
            "interval_start": interval_start,
            "interval_end": interval_end,
            "sealed": bool(sealed),
            "train_trim_days": train_trim_days,
            "icir_nw": ic_summary.get("icir_nw", 0.0),
            "n_independent": ic_summary.get("n_independent", ic_summary.get("n", 0)),
            "nw_lags": nw_lags,
            "eval_start": eval_start,
            "eval_end": eval_end,
            "universe_mode": self._universe_mode(),
            "data_version": self.data.data_version(),
        }
        gate_mode = "production" if is_prod else "research"
        gate = apply_gate(metrics, thresholds, mode=gate_mode)
        if on_progress is not None:
            on_progress(
                {
                    "stage": "gate_complete",
                    "status": gate.get("status"),
                    "rank_ic_mean": metrics.get("rank_ic_mean"),
                    "max_corr": metrics.get("max_corr"),
                }
            )
        summary = {
            "rank_ic_mean": metrics["rank_ic_mean"],
            "train_rank_ic_mean": train_rank_ic_mean,
            "icir": metrics["icir"],
            "icir_ann": metrics.get("icir_ann", 0.0),
            "icir_nw": metrics.get("icir_nw", 0.0),
            "n_independent": metrics.get("n_independent"),
            "train_trim_days": metrics.get("train_trim_days"),
            "coverage": coverage,
            "max_corr": corr["max_corr"],
            "resid_ic_mean": metrics["resid_ic_mean"],
            "oos_ic_mean": metrics["oos_ic_mean"],
            "oos_min_fold_ic": metrics.get("oos_min_fold_ic"),
            "cost_adjusted_ls": cost_ret,
            "status": gate["status"],
            "trade_lag": lag,
            "eval_split": split,
            "orientation_source": metrics.get("orientation_source"),
            "universe_mode": metrics.get("universe_mode"),
            "circ_mv_source": metrics.get("circ_mv_source"),
            "daily_basic_coverage": metrics.get("daily_basic_coverage", 0.0),
            "freeze_sign_ok": freeze_sign_ok,
            "horizon": horizon,
            "signal_hold_days": hold if hold > 1 else 0,
            "cost_horizon": cost_horizon,
        }
        return {
            "name": name,
            "gate_name": gate_name,
            "metrics": metrics,
            "gate": gate,
            "summary": summary,
        }

    def evaluate_dsl(
        self,
        expression: str,
        name: str,
        gate_name: str = "research",
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Evaluate a DSL expression in memory without writing the library."""
        from qfactor.dsl.eval_expr import evaluate_expression
        from qfactor.dsl.parser import parse_expression
        from qfactor.factor.transforms import winsorize, zscore

        raw = evaluate_expression(parse_expression(expression), self._context())
        panel = zscore(winsorize(raw))
        return self.evaluate_panel(panel, name, gate_name=gate_name, on_progress=on_progress)

    def quick_rank_ic(self, expression: str, recent_days: int = 120) -> float:
        """Cheap |train Rank IC| for cold-start pre-screen; no peers / OOS."""
        from qfactor.dsl.eval_expr import evaluate_expression
        from qfactor.dsl.parser import parse_expression
        from qfactor.factor.transforms import winsorize, zscore

        raw = evaluate_expression(parse_expression(expression), self._context())
        panel = zscore(winsorize(raw))
        ev = self.cfg.eval.get("eval", {}) or {}
        hold = int(ev.get("signal_hold_days", 0) or 0)
        if hold > 1:
            panel = apply_signal_hold(panel, hold)
        lag = self.trade_lag()
        tradable = apply_trade_lag(panel, lag)
        train_end = self._train_end()
        horizon = hold if hold > 1 else int(ev.get("forward_horizon", 5))
        min_obs = int(ev.get("min_obs_per_day", 30))
        fwd = self._forward_returns(horizon)
        if train_end:
            tradable = self._slice_panel(tradable, "train")
            fwd = self._slice_panel(fwd, "train")
        ic = rank_ic(tradable, fwd, min_obs=min_obs)
        if ic.empty:
            return 0.0
        tail = ic.tail(max(20, int(recent_days)))
        return abs(float(tail.mean())) if len(tail) else 0.0

    def evaluate_factor(
        self,
        factor: Factor,
        gate_name: str | None = None,
        interval_start: str | None = None,
        interval_end: str | None = None,
        sealed: bool = False,
    ) -> dict[str, Any]:
        gate_name = gate_name or factor.spec.entry_gate or "research"
        panel = factor.compute(self._context())
        report = self.evaluate_panel(
            panel,
            factor.spec.name,
            gate_name=gate_name,
            interval_start=interval_start,
            interval_end=interval_end,
            sealed=sealed,
        )
        report["spec"] = factor.spec.model_dump()
        return report

    def evaluate_and_save(
        self, name: str, gate_name: str = "research", promote: bool = True
    ) -> dict[str, Any]:
        factor = self.registry.load_factor(name)
        report = self.evaluate_factor(factor, gate_name=gate_name)
        code = (self.registry.factor_dir(name) / "factor.py").read_text(encoding="utf-8")
        spec = self.registry.load_spec(name)
        hold = int(self.cfg.eval.get("eval", {}).get("signal_hold_days", 0) or 0)
        if hold > 0:
            spec.signal_hold_days = hold
        if promote:
            spec.status = route_library_status(
                gate_name, report["gate"]["status"], current=spec.status
            )
        self.registry.save_factor_files(
            spec,
            code,
            source=next(
                (f.get("source", "human") for f in self.registry.list_factors() if f["name"] == name),
                "human",
            ),
            report=report,
        )
        for k in list(self._peer_cache):
            if k == name or k.startswith(f"{name}:"):
                self._peer_cache.pop(k, None)
        if spec.status in KEEP_STATUSES:
            lag = self.trade_lag()
            try:
                prepared, _ = self._prepare_eval_panel(factor.compute(self._context()))
                self._peer_cache[name] = apply_trade_lag(prepared, lag)
                hold = int(self.cfg.eval.get("eval", {}).get("signal_hold_days", 0) or 0)
                if hold > 1:
                    held = apply_signal_hold(prepared, hold)
                    self._peer_cache[f"{name}:h{hold}"] = apply_trade_lag(held, lag)
            except Exception:
                pass
        return report
