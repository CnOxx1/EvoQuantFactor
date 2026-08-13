from __future__ import annotations

import json
import random
import re
from typing import Any

import yaml

from qfactor.agent.diversity import (
    active_skeleton_bans,
    expression_fingerprint,
    is_banned_expression,
    library_diversity_index,
    merge_bans,
    pick_theme_with_lessons,
    unique_factor_name,
    weak_mechanisms,
)
from qfactor.agent.llm import LLMClient
from qfactor.dsl.parser import parse_expression
from qfactor.settings import ProjectConfig, get_project_config

DSL_OPS = "ma,std,delta,delay,sum,max,min,roc,rank,zscore,abs,neg,log,add,sub,mul,div"
DSL_FIELDS = (
    "open,high,low,close,close_adj,vol,amount,ret_1d,turnover_rate,"
    "amplitude,overnight,upper_shadow,lower_shadow"
)
DSL_WINDOWS = "3,5,10,20,40,60"
_DSL_FIELD_SET = {f.strip() for f in DSL_FIELDS.split(",") if f.strip()}
_CATALOG_SKIP_AT = 20

# Seed templates; _build_compose_catalog() expands these into a larger unique-skeleton set.
_COMPOSE_SPECS: list[tuple[str, str]] = [
    ("reversal", "neg(ma(ret_1d,{w}))"),
    ("reversal", "neg(roc(open,{w}))"),
    ("reversal", "delta(neg(roc(close_adj,{w})),{w2})"),
    ("momentum", "ma(roc(open,{w}),{w2})"),
    ("momentum", "sub(roc(close_adj,{w}),roc(close_adj,{w2}))"),
    ("volatility", "div(std(ret_1d,{w}),ma(abs(ret_1d),{w}))"),
    ("volatility", "std(overnight,{w})"),
    ("volatility", "ma(std(ret_1d,{w}),{w2})"),
    ("liquidity", "div(roc(amount,{w}),std(ret_1d,{w}))"),
    ("liquidity", "ma(turnover_rate,{w})"),
    ("liquidity", "div(vol,ma(vol,{w}))"),
    ("overnight", "ma(abs(overnight),{w})"),
    ("overnight", "div(overnight,ma(amplitude,{w}))"),
    ("overnight", "delta(overnight,{w})"),
    ("amplitude", "div(amplitude,ma(amplitude,{w}))"),
    ("amplitude", "std(amplitude,{w})"),
    ("amplitude", "rank(delta(amplitude,{w}))"),
    ("volume_price", "div(rank(roc(close_adj,{w})),rank(roc(vol,{w})))"),
    ("volume_price", "mul(rank(roc(close_adj,{w})),rank(roc(amount,{w})))"),
    ("volume_price", "sub(rank(delta(close_adj,{w})),rank(roc(vol,{w})))"),
    ("shadow", "sub(ma(upper_shadow,{w}),ma(lower_shadow,{w}))"),
    ("shadow", "div(lower_shadow,ma(amplitude,{w}))"),
    ("shadow", "delta(upper_shadow,{w})"),
    ("shadow", "ma(div(lower_shadow,amplitude),{w})"),
]

_FIELD_MECH: dict[str, str] = {
    "close_adj": "momentum",
    "open": "momentum",
    "ret_1d": "reversal",
    "vol": "liquidity",
    "amount": "liquidity",
    "turnover_rate": "liquidity",
    "amplitude": "amplitude",
    "overnight": "overnight",
    "upper_shadow": "shadow",
    "lower_shadow": "shadow",
    "high": "amplitude",
    "low": "amplitude",
}

_UNARY_TMPLS = (
    "ma({f},{w})",
    "std({f},{w})",
    "roc({f},{w})",
    "delta({f},{w})",
    "sum({f},{w})",
    "max({f},{w})",
    "min({f},{w})",
    "delay({f},{w})",
    "rank({f})",
    "zscore({f})",
    "neg(roc({f},{w}))",
    "neg(delta({f},{w}))",
    "abs(delta({f},{w}))",
    "div({f},ma({f},{w}))",
    "div(std({f},{w}),ma(abs({f}),{w}))",
    "ma(abs({f}),{w})",
    "rank(delta({f},{w}))",
    "rank(roc({f},{w}))",
    "delta(ma({f},{w}),{w2})",
    "ma(roc({f},{w}),{w2})",
    "std(roc({f},{w}),{w2})",
    "delay(roc({f},{w}),{w2})",
)

_PAIR_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("volume_price", "close_adj", "vol"),
    ("volume_price", "close_adj", "amount"),
    ("volume_price", "close_adj", "turnover_rate"),
    ("volume_price", "open", "vol"),
    ("volume_price", "ret_1d", "amount"),
    ("overnight", "overnight", "amplitude"),
    ("shadow", "upper_shadow", "lower_shadow"),
    ("momentum", "close_adj", "open"),
    ("liquidity", "vol", "amount"),
    ("amplitude", "high", "low"),
    ("volatility", "ret_1d", "overnight"),
    ("reversal", "close_adj", "open"),
)

_PAIR_TMPLS = (
    "sub(rank(roc({a},{w})),rank(roc({b},{w})))",
    "mul(rank(roc({a},{w})),rank(roc({b},{w})))",
    "div(rank(roc({a},{w})),rank(roc({b},{w})))",
    "sub(rank(delta({a},{w})),rank(roc({b},{w})))",
    "div(std({a},{w}),std({b},{w}))",
    "sub(ma({a},{w}),ma({b},{w}))",
    "div(ma({a},{w}),ma({b},{w}))",
    "sub(rank({a}),rank({b}))",
)


def _build_compose_catalog() -> list[tuple[str, str]]:
    """Unique-skeleton DSL templates; windows filled at emit time."""
    from qfactor.dsl.parser import parse_expression, skeleton as skel_of

    seen: set[str] = set()
    out: list[tuple[str, str]] = []

    def _add(mech: str, tmpl: str) -> None:
        try:
            expr = tmpl.format(w=20, w2=60)
            sk = skel_of(parse_expression(expr))
        except Exception:
            return
        if sk in seen:
            return
        seen.add(sk)
        out.append((mech, tmpl))

    for row in _COMPOSE_SPECS:
        _add(*row)
    for field, mech in _FIELD_MECH.items():
        for tmpl in _UNARY_TMPLS:
            _add(mech, tmpl.format(f=field, w="{w}", w2="{w2}"))
    for mech, a, b in _PAIR_FIELDS:
        for tmpl in _PAIR_TMPLS:
            _add(mech, tmpl.format(a=a, b=b, w="{w}"))
    return out


_COMPOSE_CATALOG: list[tuple[str, str]] = _build_compose_catalog()

SYSTEM_DSL = (
    "你是A股量价因子研究员。只输出JSON。"
    f"只能使用DSL函数: {DSL_OPS}。"
    f"字段仅限: {DSL_FIELDS}。"
    f"窗口仅限 {DSL_WINDOWS}。"
    "表达式必须可被解析为单棵算子树，不要写Python。"
    "禁止只改窗口数字来伪创新；禁止复用 banned_skeletons / banned_expressions。"
    "name 字段可忽略，系统会强制生成唯一名。"
)

SYSTEM_IDEA = (
    "你是A股量价因子研究员。只输出JSON。不要写DSL表达式，不要写Python。"
    f"字段仅限: {DSL_FIELDS}。"
    "每个想法必须可被现有算子树表达（无 if / 截面分组 / 新字段）。"
    "claim 说明截面排序与未来5日收益的关系。"
    "why_t1 说明为何 T 日收盘后可见、T+1 可交易。"
    "fields 必须是白名单字段的非空列表。"
    "not_like 必须说明和 library_cards 不是同一件事（禁止只改窗口）。"
)


def _production_llm_cfg(cfg: ProjectConfig) -> dict[str, Any]:
    prod = cfg.project.get("production", {}) or {}
    llm = prod.get("llm", {}) or {}
    div = prod.get("diversity", {}) or {}
    return {
        "llm_ratio": float(llm.get("llm_ratio", 0.7)),
        "llm_mutate_share": float(llm.get("llm_mutate_share", 0.4)),
        "llm_batch_size": int(llm.get("llm_batch_size", 8)),
        "llm_retries": int(llm.get("llm_retries", 3)),
        "perturb_ratio": float(llm.get("perturb_ratio", 0.15)),
        "llm_review_ratio": float(llm.get("llm_review_ratio", 0.0)),
        "llm_decide_theme": bool(llm.get("llm_decide_theme", True)),
        "llm_library_mutate_slots": int(llm.get("llm_library_mutate_slots", 2)),
        "hard_rotate": bool(div.get("hard_rotate", True)),
        "soft_switch_after": int(div.get("soft_switch_after", 3)),
    }


def llm_slot_plan(
    n: int,
    *,
    unused_compose: int,
    n_usable: int,
    ratio: float,
    catalog_skip_at: int = _CATALOG_SKIP_AT,
    library_mutate_slots: int = 2,
    has_parents: bool = False,
) -> dict[str, Any]:
    """Catalog stays the miner; LLM only mutates when the usable library exists."""
    n = max(0, int(n))
    ratio = min(1.0, max(0.0, float(ratio)))
    catalog_thick = unused_compose >= catalog_skip_at
    force = catalog_thick and n_usable > 0
    if catalog_thick and not force:
        return {
            "skip_llm": True,
            "force_library_mutate": False,
            "n_llm": 0,
            "n_mutate": 0,
            "n_fresh": 0,
            "n_template": n,
        }
    if force:
        cap = min(max(0, int(library_mutate_slots)), n)
        if n > 1:
            cap = min(cap, n - 1)
        n_mutate = cap if cap > 0 else (1 if n >= 1 else 0)
        return {
            "skip_llm": False,
            "force_library_mutate": True,
            "n_llm": n_mutate,
            "n_mutate": n_mutate,
            "n_fresh": 0,
            "n_template": max(0, n - n_mutate),
        }
    n_llm = int(round(n * ratio))
    n_llm = max(n_llm, 1) if ratio > 0 else 0
    n_llm = min(n_llm, n)
    if has_parents and n_llm:
        n_mutate, n_fresh = n_llm, 0
    else:
        n_mutate, n_fresh = 0, n_llm
    return {
        "skip_llm": n_llm == 0,
        "force_library_mutate": False,
        "n_llm": n_llm,
        "n_mutate": n_mutate,
        "n_fresh": n_fresh,
        "n_template": n - n_llm,
    }


class CandidateGenerator:
    """
    LLM-first generator with diversity guards:
    unique names, skeleton/expression bans, mutate structural difference.
    """

    def __init__(self, cfg: ProjectConfig | None = None, llm: LLMClient | None = None):
        self.cfg = cfg or get_project_config()
        self.llm = llm or LLMClient()
        self.last_stats: dict[str, Any] = {}
        self.mechanisms = yaml.safe_load(
            (self.cfg.root / "skills" / "mechanisms.yaml").read_text(encoding="utf-8")
        )["mechanisms"]
        self.llm_cfg = _production_llm_cfg(self.cfg)

    def _pick_mechanism(self, theme: str | None, coverage: dict[str, int]) -> dict[str, Any]:
        if theme:
            for m in self.mechanisms:
                if m["id"] == theme or theme in m["id"]:
                    return m
        ranked = sorted(self.mechanisms, key=lambda m: coverage.get(m["id"], 0))
        return ranked[0]

    def decide_theme(
        self,
        coverage: dict[str, int],
        existing: list[dict[str, Any]],
        lessons: list[dict[str, Any]] | None = None,
        forced_theme: str | None = None,
        recent_themes: list[str] | None = None,
    ) -> str | None:
        """Decide mechanism using coverage + lessons + hard rotate."""
        self.llm.require_enabled()
        lessons = lessons or []
        soft = pick_theme_with_lessons(
            self.mechanisms,
            coverage,
            lessons,
            forced=forced_theme,
            soft_switch_after=int(self.llm_cfg.get("soft_switch_after", 3)),
            recent_themes=recent_themes,
            hard_rotate=bool(self.llm_cfg.get("hard_rotate", True)),
        )
        if not self.llm_cfg["llm_decide_theme"]:
            return soft
        try:
            data = self._llm_json_with_retry(
                SYSTEM_DSL
                + " 本轮只选择一个机制方向。输出JSON: {theme:str, reason:str}。"
                " theme 必须是给定 mechanisms 的 id 之一。"
                " 优先 hits 低、recent_failures 少、且不在 recent_themes 里的机制。",
                json.dumps(
                    {
                        "mechanisms": [
                            {
                                "id": m["id"],
                                "desc": m["desc"],
                                "hits": coverage.get(m["id"], 0),
                                "recent_failures": weak_mechanisms(lessons).get(m["id"], 0),
                            }
                            for m in self.mechanisms
                        ],
                        "suggested": soft,
                        "forced_theme": forced_theme,
                        "recent_themes": list(recent_themes or [])[-5:],
                        "lessons_tail": lessons[-12:],
                        "existing_sample": existing[:12],
                    },
                    ensure_ascii=False,
                ),
            )
            theme = str(data.get("theme", "")).strip()
            ids = {m["id"] for m in self.mechanisms}
            if theme in ids:
                if forced_theme and forced_theme in ids:
                    if weak_mechanisms(lessons).get(forced_theme, 0) < int(
                        self.llm_cfg.get("soft_switch_after", 3)
                    ):
                        return forced_theme
                recent = list(recent_themes or [])
                if (
                    self.llm_cfg.get("hard_rotate", True)
                    and theme in recent[-3:]
                    and soft
                    and soft not in recent[-3:]
                ):
                    return soft
                return theme
        except Exception:
            pass
        return soft

    def _llm_json_with_retry(self, system: str, user: str) -> dict[str, Any]:
        last: Exception | None = None
        retries = max(1, int(self.llm_cfg.get("llm_retries", 3)))
        for i in range(retries):
            try:
                return self.llm.chat_json(system, user)
            except Exception as e:
                last = e
                if i + 1 >= retries:
                    break
        assert last is not None
        raise last

    def _from_hint(
        self,
        mech: dict[str, Any],
        bans: dict[str, set[str]],
    ) -> dict[str, Any] | None:
        windows = [5, 10, 20, 40, 60]
        random.shuffle(windows)
        hints = list(mech["hints"])
        random.shuffle(hints)
        for hint in hints:
            for window in windows:
                expr = hint.replace(",n)", f",{window})")
                banned, _why = is_banned_expression(expr, bans)
                if banned:
                    continue
                try:
                    parse_expression(expr)
                except Exception:
                    continue
                return {
                    "name": unique_factor_name(mech["id"], f"t{window}"),
                    "mechanism": mech["id"],
                    "expression": expr,
                    "hypothesis": f"{mech['desc']}；模板启发表达式 {expr}",
                    "source": "template",
                    "lookback": window,
                }
        return None

    def _perturb_windows(
        self,
        candidate: dict[str, Any],
        bans: dict[str, set[str]],
    ) -> dict[str, Any] | None:
        expr = candidate["expression"]
        windows = [3, 5, 10, 20, 40, 60]
        random.shuffle(windows)
        parent_skel = None
        try:
            parent_skel = expression_fingerprint(expr)["skeleton"]
        except Exception:
            pass
        for w in windows:
            new_expr = re.sub(r"\b(?:3|5|10|20|40|60)\b", str(w), expr, count=1)
            banned, why = is_banned_expression(new_expr, bans, parent_skeleton=parent_skel)
            # window-only perturb keeps same skeleton — allow only if skeleton not FSA-banned
            if why == "same_parent_skeleton":
                banned, why = is_banned_expression(new_expr, bans, parent_skeleton=None)
            if banned:
                continue
            out = dict(candidate)
            out["expression"] = new_expr
            out["name"] = unique_factor_name(str(candidate.get("mechanism", "x")), "pert")
            out["source"] = "perturb"
            out["hypothesis"] = candidate.get("hypothesis", "") + " | window perturb"
            return out
        return None

    def _unused_structure_examples(
        self, bans: dict[str, set[str]], limit: int = 12
    ) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        specs = list(_COMPOSE_CATALOG)
        random.shuffle(specs)
        for mech, tmpl in specs:
            expr = tmpl.format(w=20, w2=60)
            banned, _ = is_banned_expression(expr, bans)
            if banned:
                continue
            out.append({"mechanism": mech, "example": expr})
            if len(out) >= limit:
                break
        return out

    def _unused_compose_count(self, bans: dict[str, set[str]]) -> int:
        n = 0
        for _mech, tmpl in _COMPOSE_CATALOG:
            try:
                expr = tmpl.format(w=20, w2=60)
            except Exception:
                continue
            banned, _ = is_banned_expression(expr, bans)
            if not banned:
                n += 1
        return n

    def _compose_one(
        self,
        theme: str | None,
        bans: dict[str, set[str]],
        coverage: dict[str, int],
    ) -> dict[str, Any] | None:
        """Emit a structurally new DSL tree when templates/LLM are exhausted."""
        windows = [5, 10, 20, 40, 60]
        specs = list(_COMPOSE_CATALOG)
        if theme:
            themed = [s for s in specs if s[0] == theme]
            rest = [s for s in specs if s[0] != theme]
            random.shuffle(themed)
            random.shuffle(rest)
            specs = themed + rest
        else:
            random.shuffle(specs)
        for mech_id, tmpl in specs:
            shuffled = list(windows)
            random.shuffle(shuffled)
            for w in shuffled:
                others = [x for x in windows if x != w] or windows
                expr = tmpl.format(w=w, w2=random.choice(others))
                banned, _ = is_banned_expression(expr, bans)
                if banned:
                    continue
                try:
                    parse_expression(expr)
                except Exception:
                    continue
                mech = next((m for m in self.mechanisms if m["id"] == mech_id), None)
                desc = mech["desc"] if mech else mech_id
                return {
                    "name": unique_factor_name(mech_id, f"c{w}"),
                    "mechanism": mech_id,
                    "expression": expr,
                    "hypothesis": f"{desc}；compose {expr}",
                    "source": "compose",
                    "lookback": w,
                }
        return None

    def _perturb_structure(
        self,
        candidate: dict[str, Any],
        bans: dict[str, set[str]],
    ) -> dict[str, Any] | None:
        """Change operators or fields — not just windows — to search new skeletons."""
        expr = str(candidate.get("expression") or "")
        if not expr:
            return None
        parent_skel = None
        try:
            parent_skel = expression_fingerprint(expr)["skeleton"]
        except Exception:
            pass
        field_swaps = (
            ("close_adj", "open"),
            ("close_adj", "ret_1d"),
            ("vol", "amount"),
            ("vol", "turnover_rate"),
            ("amount", "vol"),
            ("overnight", "amplitude"),
            ("amplitude", "overnight"),
            ("upper_shadow", "lower_shadow"),
            ("lower_shadow", "upper_shadow"),
            ("high", "low"),
            ("low", "high"),
            ("ret_1d", "overnight"),
            ("turnover_rate", "vol"),
        )
        op_swaps = (
            ("ma(", "std("),
            ("std(", "ma("),
            ("roc(", "delta("),
            ("delta(", "roc("),
            ("mul(", "sub("),
            ("mul(", "div("),
            ("sub(", "div("),
            ("div(", "sub("),
        )
        wraps = (
            "rank({e})",
            "neg({e})",
            "abs({e})",
            "delay({e},5)",
            "zscore({e})",
            "sum({e},20)",
            "ma({e},20)",
        )
        candidates: list[str] = []
        for old, new in field_swaps:
            if re.search(rf"\b{re.escape(old)}\b", expr):
                candidates.append(re.sub(rf"\b{re.escape(old)}\b", new, expr, count=1))
        for old, new in op_swaps:
            if old in expr:
                candidates.append(expr.replace(old, new, 1))
        for wrap in wraps:
            candidates.append(wrap.format(e=expr))
        random.shuffle(candidates)
        for new_expr in candidates:
            banned, _ = is_banned_expression(
                new_expr, bans, parent_skeleton=parent_skel
            )
            if banned:
                continue
            try:
                parse_expression(new_expr)
            except Exception:
                continue
            out = dict(candidate)
            out["expression"] = new_expr
            out["name"] = unique_factor_name(str(candidate.get("mechanism", "x")), "spert")
            out["source"] = "structure_perturb"
            out["hypothesis"] = str(candidate.get("hypothesis") or "") + " | structure perturb"
            return out
        return None

    def _normalize_llm_item(
        self,
        data: dict[str, Any],
        mech: dict[str, Any],
        source: str,
        bans: dict[str, set[str]],
        parent_skeleton: str | None = None,
    ) -> dict[str, Any] | None:
        from qfactor.dsl.validate import validate_expression

        expr = str(data.get("expression", "")).strip()
        banned, _why = is_banned_expression(expr, bans, parent_skeleton=parent_skeleton)
        if banned:
            return None
        v = validate_expression(expr)
        if not v.get("ok"):
            return None
        idea = data.get("idea") if isinstance(data.get("idea"), dict) else {}
        claim = str(idea.get("claim") or data.get("claim") or "").strip()
        hypothesis = str(data.get("hypothesis") or claim or mech["desc"])
        return {
            "name": unique_factor_name(mech["id"], source[:6]),
            "mechanism": data.get("mechanism") or idea.get("mechanism") or mech["id"],
            "expression": expr,
            "hypothesis": hypothesis,
            "source": source,
            "lookback": int(data.get("lookback") or 20),
            "idea": idea or None,
        }

    def _llm_fresh_batch(
        self,
        mech: dict[str, Any],
        existing: list[dict[str, Any]],
        n: int,
        bans: dict[str, set[str]],
        lessons: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        user = {
            "task": "fresh",
            "count": n,
            "mechanism": mech,
            "existing": [
                {"name": e.get("name"), "expression": e.get("expression"), "mechanism": e.get("mechanism")}
                for e in existing[:15]
                if e.get("expression")
            ],
            "banned_skeletons": sorted(list(bans.get("skeletons", set())))[:40],
            "lessons_tail": lessons[-8:],
            "rules": [
                "每个候选必须是新 skeleton（窗口数字不计）",
                "不要生成与 existing/banned 相同或仅改窗口的式子",
                "优先换算子或换字段，不要复用 banned_skeletons",
                "可参考 unused_structures，但不要原样复制同一骨架",
            ],
            "unused_structures": self._unused_structure_examples(bans),
            "example": "sub(ma(overnight,20),ma(amplitude,20))",
        }
        data = self._llm_json_with_retry(
            SYSTEM_DSL + f" 请一次生成 {n} 个互不相同的候选。输出 candidates 数组。",
            json.dumps(user, ensure_ascii=False),
        )
        items = data.get("candidates")
        if not isinstance(items, list) or not items:
            items = [data]
        out: list[dict[str, Any]] = []
        for item in items[: n * 2]:
            if not isinstance(item, dict):
                continue
            try:
                cand = self._normalize_llm_item(item, mech, "llm", bans)
            except Exception:
                cand = None
            if cand:
                out.append(cand)
            if len(out) >= n:
                break
        return out

    def _factor_card(self, parent: dict[str, Any]) -> dict[str, Any] | None:
        expr = parent.get("expression")
        if not expr and isinstance(parent.get("params"), dict):
            expr = parent["params"].get("expression")
        summary = parent.get("summary") if isinstance(parent.get("summary"), dict) else {}
        if not expr:
            expr = summary.get("expression")
        if not expr:
            return None
        try:
            sk = expression_fingerprint(str(expr))["skeleton"]
        except Exception:
            sk = None
        return {
            "expression": expr,
            "mechanism": parent.get("mechanism") or parent.get("category"),
            "skeleton": sk,
            "status": parent.get("status"),
            "holdout_ic": summary.get("rank_ic_mean"),
            "resid_ic": summary.get("resid_ic_mean"),
            "max_corr": summary.get("max_corr"),
            "cost_ls": summary.get("cost_adjusted_ls"),
            "icir": summary.get("icir"),
        }

    def _mutate_failure_modes(self, parents: list[dict[str, Any]]) -> list[str]:
        """5–8 live failure notes from this library, not generic overnight lore."""
        modes: list[str] = []
        seen: set[str] = set()

        def _add(msg: str) -> None:
            if msg and msg not in seen and len(modes) < 8:
                seen.add(msg)
                modes.append(msg)

        parent_skels = {
            str(p.get("skeleton")) for p in parents if p.get("skeleton")
        }
        try:
            from qfactor.factor.registry import FactorRegistry

            reg = FactorRegistry(self.cfg)
            for f in reg.list_factors():
                s = f.get("summary") or {}
                status = str(f.get("status") or "")
                try:
                    spec = reg.load_spec(str(f["name"]))
                    expr = spec.expression or ""
                    sk = expression_fingerprint(str(expr))["skeleton"] if expr else ""
                except Exception:
                    sk = ""
                corr = float(s.get("max_corr") or 0)
                resid = float(s.get("resid_ic_mean") or 0)
                cost = float(s.get("cost_adjusted_ls") or 0)
                if corr >= 0.70 and sk:
                    _add(f"high_corr skeleton {sk} corr={corr:.2f}")
                if status in {"screened", "draft"} and abs(resid) < 0.005 and sk:
                    _add(f"resid≈0 {sk}")
                if cost < 0 and sk:
                    _add(f"cost_ls_negative {sk}")
                if sk and sk in parent_skels and status == "screened":
                    _add(f"do_not_window_shop {sk}")
        except Exception:
            pass
        if not modes:
            _add("change operator/field/subtree; never window-only")
            _add("child skeleton must differ from parents and banned_skeletons")
            _add("avoid clones whose resid IC collapses to ~0 vs library")
        return modes[:8]

    def _validate_idea(self, raw: dict[str, Any], mech: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        claim = str(raw.get("claim") or "").strip()
        why_t1 = str(raw.get("why_t1") or "").strip()
        not_like = str(raw.get("not_like") or "").strip()
        fields = raw.get("fields")
        if not isinstance(fields, list):
            return None
        clean_fields = [str(f).strip() for f in fields if str(f).strip()]
        if len(claim) < 8 or len(why_t1) < 4 or len(not_like) < 4 or not clean_fields:
            return None
        if any(f not in _DSL_FIELD_SET for f in clean_fields):
            return None
        mid = str(raw.get("mechanism") or mech["id"]).strip() or mech["id"]
        return {
            "claim": claim,
            "why_t1": why_t1,
            "fields": clean_fields,
            "not_like": not_like,
            "mechanism": mid,
        }

    def _llm_idea_batch(
        self,
        mech: dict[str, Any],
        cards: list[dict[str, Any]],
        n: int,
        failure_modes: list[str],
    ) -> list[dict[str, Any]]:
        user = {
            "task": "ideas",
            "count": n,
            "mechanism": mech,
            "library_cards": cards,
            "failure_modes": failure_modes,
            "output_schema": {
                "ideas": [
                    {
                        "claim": "str",
                        "why_t1": "str",
                        "fields": ["whitelist field", "..."],
                        "not_like": "str",
                        "mechanism": "id",
                    }
                ]
            },
        }
        data = self._llm_json_with_retry(
            SYSTEM_IDEA + f" 请提出 {n} 个互不相同的想法。输出 ideas 数组。",
            json.dumps(user, ensure_ascii=False),
        )
        items = data.get("ideas")
        if not isinstance(items, list) or not items:
            items = [data]
        out: list[dict[str, Any]] = []
        for item in items:
            idea = self._validate_idea(item, mech)
            if idea:
                out.append(idea)
            if len(out) >= n:
                break
        return out

    def _llm_compile_ideas(
        self,
        mech: dict[str, Any],
        ideas: list[dict[str, Any]],
        cards: list[dict[str, Any]],
        bans: dict[str, set[str]],
    ) -> list[dict[str, Any]]:
        parent_skels = {p.get("skeleton") for p in cards if p.get("skeleton")}
        user = {
            "task": "compile",
            "ideas": ideas,
            "library_cards": cards,
            "parent_skeletons": sorted(str(s) for s in parent_skels if s),
            "banned_skeletons": sorted(list(bans.get("skeletons", set())))[:12],
            "instruction": (
                "把每个 idea 译成一棵新骨架 DSL 树。"
                "只用 idea.fields 与白名单算子/窗口。"
                "子代 skeleton 不得与 parent_skeletons / banned_skeletons 相同。"
                "严禁只改窗口。不要写Python。"
            ),
        }
        data = self._llm_json_with_retry(
            SYSTEM_DSL + f" 请把 {len(ideas)} 个想法编译为表达式。输出 candidates 数组。",
            json.dumps(user, ensure_ascii=False),
        )
        items = data.get("candidates")
        if not isinstance(items, list) or not items:
            items = [data]
        out: list[dict[str, Any]] = []
        parent_skel = next(iter(parent_skels), None)
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            idea = ideas[i] if i < len(ideas) else (ideas[0] if ideas else {})
            payload = dict(item)
            payload["idea"] = idea
            payload.setdefault("mechanism", idea.get("mechanism") or mech["id"])
            payload.setdefault(
                "hypothesis",
                f"{idea.get('claim','')}；unlike {idea.get('not_like','')}",
            )
            try:
                cand = self._normalize_llm_item(
                    payload, mech, "llm_mutate", bans, parent_skeleton=parent_skel
                )
                if cand:
                    sk = expression_fingerprint(cand["expression"])["skeleton"]
                    if sk in parent_skels:
                        cand = None
            except Exception:
                cand = None
            if cand:
                out.append(cand)
            if len(out) >= len(ideas):
                break
        return out

    def _llm_mutate_batch(
        self,
        mech: dict[str, Any],
        parents: list[dict[str, Any]],
        n: int,
        bans: dict[str, set[str]],
        lessons: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del lessons  # ideas use live library cards / failure modes, not lesson tails
        enriched = []
        for p in parents[:12]:
            card = self._factor_card(p)
            if card:
                enriched.append(card)
        if not enriched:
            return []
        modes = self._mutate_failure_modes(enriched)
        ideas = self._llm_idea_batch(mech, enriched, n, modes)
        self._last_idea_n = len(ideas)
        if not ideas:
            return []
        return self._llm_compile_ideas(mech, ideas, enriched, bans)

    def _parent_pool(self, existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Prefer candidate/approved parents; demote high-corr rejects."""
        pool: list[dict[str, Any]] = []
        for item in existing:
            expr = item.get("expression")
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            if not expr:
                expr = params.get("expression") or summary.get("expression")
            if not expr:
                continue
            status = str(item.get("status") or summary.get("status") or "draft")
            if status == "reject" and float(summary.get("max_corr") or 0) >= 0.9:
                continue
            row = dict(item)
            row["expression"] = expr
            row["status"] = status
            pool.append(row)
        try:
            from qfactor.factor.registry import FactorRegistry

            reg = FactorRegistry(self.cfg)
            for f in reg.list_factors():
                status = str(f.get("status") or "draft")
                if status in {"deprecated"}:
                    continue
                try:
                    spec = reg.load_spec(f["name"])
                    if spec.expression:
                        pool.append(
                            {
                                "name": spec.name,
                                "expression": spec.expression,
                                "mechanism": spec.mechanism or spec.category,
                                "hypothesis": spec.hypothesis,
                                "status": status or spec.status,
                                "summary": f.get("summary") or {},
                            }
                        )
                except Exception:
                    continue
        except Exception:
            pass
        seen: set[str] = set()
        uniq: list[dict[str, Any]] = []
        for p in pool:
            e = str(p.get("expression", ""))
            if e and e not in seen:
                seen.add(e)
                uniq.append(p)

        def _rank(p: dict[str, Any]) -> tuple[int, float, float]:
            st = str(p.get("status") or "draft")
            tier = {"approved": 0, "candidate": 1, "screened": 2, "draft": 3}.get(st, 4)
            summary = p.get("summary") if isinstance(p.get("summary"), dict) else {}
            resid = abs(float(summary.get("resid_ic_mean") or 0))
            ic = abs(float(summary.get("rank_ic_mean") or 0))
            return (tier, -resid, -ic)

        uniq.sort(key=_rank)
        usable = [p for p in uniq if str(p.get("status")) in {"candidate", "approved"}]
        if len(usable) >= 2:
            return usable
        screened = [p for p in uniq if str(p.get("status")) == "screened"]
        screened.sort(key=_rank)
        mixed = usable + screened
        return mixed if mixed else uniq

    def generate_batch(
        self,
        n: int = 8,
        theme: str | None = None,
        coverage: dict[str, int] | None = None,
        existing: list[dict[str, Any]] | None = None,
        llm_ratio: float | None = None,
        lessons: list[dict[str, Any]] | None = None,
        extra_banned_skeletons: list[str] | None = None,
        extra_banned_hashes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        self.llm.require_enabled()
        coverage = dict(coverage or {})
        existing = existing or []
        lessons = list(lessons or [])
        out: list[dict[str, Any]] = []

        index = library_diversity_index(self.cfg)
        bans = merge_bans(index, extra_banned_hashes, extra_banned_skeletons)
        bans["skeletons"] = active_skeleton_bans(
            self.cfg, extra=list(extra_banned_skeletons or [])
        )

        unused_compose = self._unused_compose_count(bans)
        ratio = self.llm_cfg["llm_ratio"] if llm_ratio is None else float(llm_ratio)
        parents = self._parent_pool(existing)
        usable = [p for p in parents if str(p.get("status")) in {"candidate", "approved"}]
        plan = llm_slot_plan(
            n,
            unused_compose=unused_compose,
            n_usable=len(usable),
            ratio=ratio,
            catalog_skip_at=_CATALOG_SKIP_AT,
            library_mutate_slots=int(self.llm_cfg.get("llm_library_mutate_slots", 2)),
            has_parents=bool(parents),
        )
        skip_llm = bool(plan["skip_llm"])
        n_llm = int(plan["n_llm"])
        n_mutate = int(plan["n_mutate"])
        n_fresh = int(plan["n_fresh"])
        n_tmpl = int(plan["n_template"])
        mutate_parents = usable if usable else parents
        batch_sz = max(1, self.llm_cfg["llm_batch_size"])
        perturb_ratio = self.llm_cfg["perturb_ratio"]
        stats = {
            "n_requested": n,
            "n_llm": n_llm,
            "n_mutate": n_mutate,
            "n_fresh": n_fresh,
            "n_template": n_tmpl,
            "llm_fresh_ok": 0,
            "llm_fresh_empty": 0,
            "llm_mutate_ok": 0,
            "llm_mutate_empty": 0,
            "llm_errors": 0,
            "hint_fallback": 0,
            "compose_fallback": 0,
            "structure_perturb": 0,
            "unused_compose": unused_compose,
            "llm_skipped": skip_llm,
            "force_library_mutate": bool(plan["force_library_mutate"]),
            "n_usable": len(usable),
            "llm_ideas": 0,
        }

        def _accept(cand: dict[str, Any] | None) -> bool:
            if not cand:
                return False
            banned, _ = is_banned_expression(cand["expression"], bans)
            if banned:
                return False
            fp = expression_fingerprint(cand["expression"])
            # One skeleton per batch: force structural search, not window shopping.
            if fp["skeleton"] in bans.get("skeletons", set()):
                return False
            out.append(cand)
            bans["hashes"].add(fp["expr_hash"])
            bans.setdefault("skeletons", set()).add(fp["skeleton"])
            mid = cand.get("mechanism", "unknown")
            coverage[mid] = coverage.get(mid, 0) + 1
            return True

        filled = 0
        attempts = 0
        while filled < n_fresh and attempts < n_fresh * 3:
            attempts += 1
            mech = self._pick_mechanism(theme, coverage)
            take = min(batch_sz, n_fresh - filled)
            try:
                got = self._llm_fresh_batch(mech, existing + out, take, bans, lessons)
            except Exception as e:
                stats["llm_errors"] += 1
                print(f"[generate] llm_fresh failed: {e}", flush=True)
                got = []
            if got:
                stats["llm_fresh_ok"] += 1
            else:
                stats["llm_fresh_empty"] += 1
                # LLM reused banned skeletons — drain the local catalog instead of retrying.
                while filled < n_fresh:
                    accepted = _accept(
                        self._compose_one(theme or mech["id"], bans, coverage)
                    )
                    if not accepted:
                        accepted = _accept(self._compose_one(None, bans, coverage))
                    if not accepted:
                        break
                    stats["compose_fallback"] += 1
                    filled += 1
                break
            for cand in got:
                if _accept(cand):
                    filled += 1
                if filled >= n_fresh:
                    break

        filled_m = 0
        attempts = 0
        while filled_m < n_mutate and attempts < n_mutate * 3:
            attempts += 1
            mech = self._pick_mechanism(theme, coverage)
            take = min(batch_sz, n_mutate - filled_m)
            try:
                got = self._llm_mutate_batch(mech, mutate_parents, take, bans, lessons)
            except Exception as e:
                stats["llm_errors"] += 1
                print(f"[generate] llm_mutate failed: {e}", flush=True)
                got = []
            if got:
                stats["llm_mutate_ok"] += 1
                stats["llm_ideas"] = int(getattr(self, "_last_idea_n", 0) or 0)
            else:
                stats["llm_mutate_empty"] += 1
                while filled_m < n_mutate:
                    accepted = _accept(
                        self._compose_one(theme or mech["id"], bans, coverage)
                    )
                    if not accepted:
                        accepted = _accept(self._compose_one(None, bans, coverage))
                    if not accepted:
                        break
                    stats["compose_fallback"] += 1
                    filled_m += 1
                break
            for cand in got:
                if _accept(cand):
                    filled_m += 1
                if filled_m >= n_mutate:
                    break

        for _ in range(max(0, n_tmpl)):
            if _accept(self._compose_one(theme, bans, coverage)):
                stats["compose_fallback"] += 1
                continue
            mech = self._pick_mechanism(theme, coverage)
            if _accept(self._from_hint(mech, bans)):
                stats["hint_fallback"] += 1
                continue
            for m in self.mechanisms:
                if _accept(self._from_hint(m, bans)):
                    stats["hint_fallback"] += 1
                    break

        if len(out) >= 1 and perturb_ratio > 0:
            n_pert = max(0, int(round(len(out) * perturb_ratio)))
            idxs = random.sample(range(len(out)), min(n_pert, len(out)))
            for i in idxs:
                pert = self._perturb_structure(out[i], bans)
                if _accept(pert):
                    stats["structure_perturb"] += 1

        # pad with unused templates, then structurally new compose trees
        guard = 0
        while len(out) < n and guard < n * 8:
            guard += 1
            mech = self._pick_mechanism(theme, coverage)
            if _accept(self._compose_one(theme, bans, coverage)):
                stats["compose_fallback"] += 1
                continue
            if _accept(self._from_hint(mech, bans)):
                stats["hint_fallback"] += 1
                continue
            if _accept(self._compose_one(None, bans, coverage)):
                stats["compose_fallback"] += 1

        stats["n_out"] = len(out[:n])
        sources: dict[str, int] = {}
        for c in out[:n]:
            src = str(c.get("source", "unknown"))
            sources[src] = sources.get(src, 0) + 1
        stats["sources"] = sources
        self.last_stats = stats
        return out[:n]
