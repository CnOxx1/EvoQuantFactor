from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

import yaml

from qfactor.agent.coldstart import (
    DSL_SEEDS,
    cold_start_cfg,
    collect_fields_windows,
    field_window_prior,
    is_cold_start,
    weighted_sample,
)
from qfactor.agent.diversity import (
    active_skeleton_bans,
    blocked_mechanisms,
    eligible_mechanisms,
    expression_fingerprint,
    is_banned_expression,
    library_diversity_index,
    merge_bans,
    keep_mechanism_coverage,
    pick_theme_with_lessons,
    unique_factor_name,
    usable_mechanism_coverage,
    weak_mechanisms,
)
from qfactor.agent.llm import LLMClient
from qfactor.dsl.parser import (
    Expr,
    all_expr_paths,
    expr_at,
    nested_expr_paths,
    parse_expression,
    replace_expr_at,
    skeleton as skel_of,
)
from qfactor.eval.gate import KEEP_STATUSES, USABLE_STATUSES
from qfactor.settings import ProjectConfig, get_project_config

DSL_OPS = "ma,std,delta,delay,sum,max,min,roc,rank,zscore,abs,neg,log,add,sub,mul,div"
DSL_FIELDS = (
    "open,high,low,close,close_adj,vol,amount,ret_1d,turnover_rate,"
    "amplitude,overnight,upper_shadow,lower_shadow"
)
DSL_WINDOWS = "3,5,10,20,40,60"
_DSL_FIELD_SET = {f.strip() for f in DSL_FIELDS.split(",") if f.strip()}
_CATALOG_SKIP_AT = 20

# Hand-written conditional / volume-price / residual templates (priority emit).
_PRIORITY_SPECS: list[tuple[str, str]] = [
    ("reversal", "mul(rank(turnover_rate),neg(roc(close_adj,{w})))"),
    ("reversal", "mul(rank(vol),neg(delta(close_adj,{w})))"),
    ("reversal", "mul(rank(turnover_rate),neg(ma(ret_1d,{w})))"),
    ("reversal", "mul(rank(amount),neg(roc(open,{w})))"),
    ("volume_price", "sub(rank(roc(close_adj,{w})),rank(roc(turnover_rate,{w})))"),
    ("volume_price", "mul(rank(neg(roc(close_adj,{w}))),rank(roc(vol,{w})))"),
    ("volume_price", "div(rank(roc(close_adj,{w})),rank(roc(turnover_rate,{w})))"),
    ("volume_price", "sub(zscore(roc(close_adj,{w})),zscore(roc(amount,{w})))"),
    ("volume_price", "mul(rank(turnover_rate),sub(rank(roc(close_adj,{w})),rank(roc(vol,{w}))))"),
    ("liquidity", "mul(rank(turnover_rate),div(vol,ma(vol,{w})))"),
    ("liquidity", "div(abs(ret_1d),ma(turnover_rate,{w}))"),
    ("liquidity", "mul(rank(amount),neg(roc(close_adj,{w})))"),
    ("liquidity", "div(ma(abs(ret_1d),{w}),ma(turnover_rate,{w}))"),
    ("shadow", "mul(rank(turnover_rate),ma(upper_shadow,{w}))"),
    ("shadow", "div(ma(upper_shadow,{w}),ma(turnover_rate,{w}))"),
    ("shadow", "mul(rank(vol),div(upper_shadow,amplitude))"),
    ("shadow", "sub(rank(upper_shadow),rank(roc(close_adj,{w})))"),
    ("shadow", "mul(rank(turnover_rate),div(upper_shadow,ma(amplitude,{w})))"),
    ("volatility", "div(overnight,std(ret_1d,{w}))"),
    ("volatility", "div(std(overnight,{w}),std(ret_1d,{w}))"),
    ("volatility", "mul(rank(turnover_rate),std(ret_1d,{w}))"),
    ("momentum", "sub(roc(close_adj,{w}),ma(roc(close_adj,{w}),{w2}))"),
    ("momentum", "mul(rank(turnover_rate),roc(close_adj,{w}))"),
    ("overnight", "div(overnight,std(amplitude,{w}))"),
    ("overnight", "sub(overnight,mul(div(std(overnight,{w}),std(amplitude,{w})),amplitude))"),
]

# Seed templates; _build_compose_catalog() expands these into a larger unique-skeleton set.
_COMPOSE_SPECS: list[tuple[str, str]] = _PRIORITY_SPECS + [
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


def extra_templates_path(cfg: ProjectConfig | None = None) -> Path:
    cfg = cfg or get_project_config()
    return cfg.path("runs") / "extra_templates.yaml"


def load_extra_templates(cfg: ProjectConfig | None = None) -> list[tuple[str, str]]:
    path = extra_templates_path(cfg)
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    rows = data.get("templates") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        mech = str(row.get("mechanism") or "").strip()
        tmpl = str(row.get("tmpl") or row.get("template") or "").strip()
        if not mech or not tmpl or tmpl in seen:
            continue
        seen.add(tmpl)
        out.append((mech, tmpl))
    return out


def _build_compose_catalog(
    extra: list[tuple[str, str]] | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Unique-skeleton DSL templates; windows filled at emit time.

    Returns (all, unary, priority-only) so compose can emit hand templates first.
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    unary: list[tuple[str, str]] = []
    priority: list[tuple[str, str]] = []
    priority_tmpls = {t for _m, t in _PRIORITY_SPECS}

    def _add(mech: str, tmpl: str) -> None:
        try:
            expr = tmpl.format(w=20, w2=60)
            sk = skel_of(parse_expression(expr))
            fields, _ = collect_fields_windows(expr)
        except Exception:
            return
        if sk in seen:
            return
        seen.add(sk)
        item = (mech, tmpl)
        out.append(item)
        if tmpl in priority_tmpls:
            priority.append(item)
        if len(fields) <= 1:
            unary.append(item)

    extra_rows = extra if extra is not None else load_extra_templates()
    extra_tmpls = {t for _m, t in extra_rows}
    priority_tmpls |= extra_tmpls

    for row in _COMPOSE_SPECS:
        _add(*row)
    for row in extra_rows:
        _add(*row)
    for field, mech in _FIELD_MECH.items():
        for tmpl in _UNARY_TMPLS:
            _add(mech, tmpl.format(f=field, w="{w}", w2="{w2}"))
    for mech, a, b in _PAIR_FIELDS:
        for tmpl in _PAIR_TMPLS:
            _add(mech, tmpl.format(a=a, b=b, w="{w}"))
    return out, unary, priority


_COMPOSE_CATALOG, _COMPOSE_UNARY, _COMPOSE_PRIORITY = _build_compose_catalog()
_CATALOG_SKELETONS: set[str] = set()
for _mech, _tmpl in _COMPOSE_CATALOG:
    try:
        _CATALOG_SKELETONS.add(skel_of(parse_expression(_tmpl.format(w=20, w2=60))))
    except Exception:
        pass

_CATALOG_EXPAND_EVERY = 20
_CATALOG_EXPAND_UNUSED_LT = 0
_CATALOG_EXPAND_MAX = 10
_WIN_NUM_RE = re.compile(r"\b(?:3|5|10|20|40|60)\b")


def _catalog_skeletons_of(catalog: list[tuple[str, str]]) -> set[str]:
    out: set[str] = set()
    for _mech, tmpl in catalog:
        try:
            out.add(skel_of(parse_expression(tmpl.format(w=20, w2=60))))
        except Exception:
            continue
    return out


def rebuild_compose_catalog(
    cfg: ProjectConfig | None = None,
    extra: list[tuple[str, str]] | None = None,
) -> dict[str, int]:
    """Reload module catalog from hand specs + extra_templates.yaml."""
    global _COMPOSE_CATALOG, _COMPOSE_UNARY, _COMPOSE_PRIORITY, _CATALOG_SKELETONS
    if extra is None:
        extra = load_extra_templates(cfg)
    cat, unary, pri = _build_compose_catalog(extra)
    _COMPOSE_CATALOG = cat
    _COMPOSE_UNARY = unary
    _COMPOSE_PRIORITY = pri
    _CATALOG_SKELETONS = _catalog_skeletons_of(cat)
    return {
        "n_catalog": len(cat),
        "n_unary": len(unary),
        "n_priority": len(pri),
        "n_extra": len(extra),
        "n_skeletons": len(_CATALOG_SKELETONS),
    }


def should_expand_compose_catalog(
    unused_compose: int,
    rounds_done: int,
    last_expand_round: int | None = None,
    *,
    every: int = _CATALOG_EXPAND_EVERY,
    unused_lt: int = _CATALOG_EXPAND_UNUSED_LT,
) -> bool:
    """Periodic catalog refill while mining. unused_lt<=0 means do not wait for empty."""
    if int(unused_lt) > 0 and int(unused_compose) >= int(unused_lt):
        return False
    if int(rounds_done) < 1:
        return False
    if last_expand_round is None or int(last_expand_round) < 0:
        return int(rounds_done) >= int(every)
    return int(rounds_done) - int(last_expand_round) >= int(every)


def normalize_compose_template(tmpl: str) -> str | None:
    """Require {w}; optionally {w2}. Bare window numbers become placeholders."""
    t = str(tmpl or "").strip()
    if not t or "import" in t or "lambda" in t or "\n" in t:
        return None
    if "{w}" not in t:
        nums = _WIN_NUM_RE.findall(t)
        if not nums:
            return None
        t = _WIN_NUM_RE.sub("{w}", t, count=1)
        if "{w2}" not in t and _WIN_NUM_RE.search(t):
            t = _WIN_NUM_RE.sub("{w2}", t, count=1)
    if "{w}" not in t:
        return None
    try:
        t.format(w=20, w2=60)
    except Exception:
        return None
    return t


def validate_compose_template(
    mech: str,
    tmpl: str,
    *,
    mechanism_ids: set[str],
    known_skeletons: set[str],
) -> tuple[str | None, str]:
    """Return (normalized_tmpl, error). error empty means accept."""
    from qfactor.dsl.validate import validate_expression

    mid = str(mech or "").strip()
    if mid not in mechanism_ids:
        return None, "unknown_mechanism"
    norm = normalize_compose_template(tmpl)
    if not norm:
        return None, "bad_template"
    try:
        expr = norm.format(w=20, w2=60)
    except Exception:
        return None, "format_error"
    v = validate_expression(expr)
    if not v.get("ok"):
        return None, "validate:" + ",".join(str(e) for e in (v.get("errors") or [])[:3])
    try:
        sk = skel_of(parse_expression(expr))
    except Exception:
        return None, "parse_error"
    if sk in known_skeletons:
        return None, "duplicate_skeleton"
    return norm, ""


def save_extra_templates(
    rows: list[dict[str, Any]],
    cfg: ProjectConfig | None = None,
) -> Path:
    path = extra_templates_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_extra_templates(cfg)
    merged: list[dict[str, Any]] = []
    seen_tmpl: set[str] = set()
    seen_sk: set[str] = set()
    for mech, tmpl in existing:
        try:
            sk = skel_of(parse_expression(tmpl.format(w=20, w2=60)))
        except Exception:
            continue
        if tmpl in seen_tmpl or sk in seen_sk:
            continue
        seen_tmpl.add(tmpl)
        seen_sk.add(sk)
        merged.append({"mechanism": mech, "tmpl": tmpl, "skeleton": sk})
    for row in rows:
        mech = str(row.get("mechanism") or "").strip()
        tmpl = str(row.get("tmpl") or "").strip()
        if not mech or not tmpl or tmpl in seen_tmpl:
            continue
        try:
            sk = str(row.get("skeleton") or skel_of(parse_expression(tmpl.format(w=20, w2=60))))
        except Exception:
            continue
        if sk in seen_sk:
            continue
        seen_tmpl.add(tmpl)
        seen_sk.add(sk)
        merged.append({"mechanism": mech, "tmpl": tmpl, "skeleton": sk})
    payload = {"templates": merged}
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path

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
    "not_like 必须点名 library_cards 里已有 candidate 不是同一件事（禁止只改窗口）。"
    "禁止提出 catalog_skeletons / banned_skeletons 已有的结构。"
)

SYSTEM_TEMPLATE = (
    "你是A股量价因子研究员。只输出JSON。"
    f"只能使用DSL函数: {DSL_OPS}。"
    f"字段仅限: {DSL_FIELDS}。"
    "输出 templates 数组，每条 {mechanism, tmpl}。"
    "tmpl 必须是带 {w} 的模板（双窗用 {w2}），不要写死窗口数字当创新。"
    "mechanism 必须是给定 id 之一。"
    "每条 tmpl 展开 w=20,w2=60 后必须是新骨架，不得与 catalog_skeletons 相同。"
    "不要写 Python，不要 if/where，不要新字段。"
    "不要把当轮 JSON 当成已入库模板复述。"
)


def _production_llm_cfg(cfg: ProjectConfig) -> dict[str, Any]:
    prod = cfg.project.get("production", {}) or {}
    llm = prod.get("llm", {}) or {}
    div = prod.get("diversity", {}) or {}
    return {
        "llm_ratio": float(llm.get("llm_ratio", 0.7)),
        "llm_batch_size": int(llm.get("llm_batch_size", 8)),
        "llm_retries": int(llm.get("llm_retries", 3)),
        "perturb_ratio": float(llm.get("perturb_ratio", 0.15)),
        "llm_review_ratio": float(llm.get("llm_review_ratio", 0.0)),
        "llm_decide_theme": bool(llm.get("llm_decide_theme", True)),
        "llm_library_mutate_slots": int(llm.get("llm_library_mutate_slots", 0)),
        "catalog_expand_every": int(llm.get("catalog_expand_every", _CATALOG_EXPAND_EVERY)),
        "catalog_expand_max": int(llm.get("catalog_expand_max", _CATALOG_EXPAND_MAX)),
        "catalog_expand_unused_lt": int(llm.get("catalog_expand_unused_lt", _CATALOG_EXPAND_UNUSED_LT)),
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
    library_mutate_slots: int = 0,
    has_parents: bool = False,
    cold_start: bool = False,
    fresh_ratio: float = 0.30,
) -> dict[str, Any]:
    """Catalog stays the miner while unused >= skip; thin catalog uses fresh/crossover/mutate.

    Empty catalog (unused < 5), n=8: fresh 3, crossover 3+, mutate 1.
    Mid (5 <= unused < skip): compose fills the rest; fresh and crossover at least 1.
    Thick: skip LLM unless cold start or forced library mutate.
    """
    n = max(0, int(n))
    ratio = min(1.0, max(0.0, float(ratio)))
    fresh_ratio = min(1.0, max(0.0, float(fresh_ratio)))
    catalog_thick = unused_compose >= catalog_skip_at
    mutate_slots = max(0, int(library_mutate_slots))
    force = catalog_thick and n_usable > 0 and mutate_slots > 0

    def _pack(
        *,
        skip_llm: bool,
        n_fresh: int,
        n_mutate: int,
        n_crossover: int,
        n_template: int,
        force_library_mutate: bool = False,
    ) -> dict[str, Any]:
        n_fresh = max(0, n_fresh)
        n_mutate = max(0, n_mutate)
        n_crossover = max(0, n_crossover)
        n_template = max(0, n_template)
        return {
            "skip_llm": skip_llm,
            "force_library_mutate": force_library_mutate,
            "n_llm": n_fresh + n_mutate,
            "n_mutate": n_mutate,
            "n_fresh": n_fresh,
            "n_crossover": n_crossover,
            "n_template": n_template,
        }

    if cold_start:
        n_fresh = int(round(n * fresh_ratio))
        n_fresh = max(n_fresh, 1) if fresh_ratio > 0 and n > 0 else 0
        n_fresh = min(n_fresh, n)
        rest = n - n_fresh
        n_mutate = 0
        if has_parents and rest:
            n_mutate = 1 if rest == 1 else max(1, rest // 3)
            n_mutate = min(n_mutate, rest)
        return _pack(
            skip_llm=n_fresh == 0 and n_mutate == 0,
            n_fresh=n_fresh,
            n_mutate=n_mutate,
            n_crossover=0,
            n_template=max(0, n - n_fresh - n_mutate),
            force_library_mutate=n_mutate > 0,
        )

    if catalog_thick and not force:
        return _pack(
            skip_llm=True,
            n_fresh=0,
            n_mutate=0,
            n_crossover=0,
            n_template=n,
        )
    if force:
        cap = min(mutate_slots, n)
        if n > 1:
            cap = min(cap, n - 1)
        n_mutate = cap
        return _pack(
            skip_llm=n_mutate == 0,
            n_fresh=0,
            n_mutate=n_mutate,
            n_crossover=0,
            n_template=max(0, n - n_mutate),
            force_library_mutate=n_mutate > 0,
        )

    # Thin catalog: do not follow llm_ratio 50/50 mutate.
    if unused_compose < 5:
        n_fresh = int(round(n * 3 / 8)) if n else 0
        n_fresh = max(1, n_fresh) if n >= 2 else n
        n_mutate = 1 if has_parents and n >= 4 else 0
        n_fresh = min(n_fresh, max(0, n - n_mutate))
        n_crossover = max(0, n - n_fresh - n_mutate)
        return _pack(
            skip_llm=n_fresh == 0 and n_mutate == 0,
            n_fresh=n_fresh,
            n_mutate=n_mutate,
            n_crossover=n_crossover,
            n_template=0,
        )

    n_fresh = 1 if n >= 2 else 0
    n_crossover = 1 if n >= 3 else 0
    n_mutate = 1 if has_parents and n >= 6 else 0
    used = n_fresh + n_crossover + n_mutate
    if used > n:
        n_mutate = 0
        used = n_fresh + n_crossover
    return _pack(
        skip_llm=n_fresh == 0,
        n_fresh=n_fresh,
        n_mutate=n_mutate,
        n_crossover=n_crossover,
        n_template=max(0, n - used),
    )


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
        pool = getattr(self, "_eligible_mechs", None) or self.mechanisms
        if theme:
            for m in pool:
                if m["id"] == theme or theme in m["id"]:
                    return m
        ranked = sorted(pool, key=lambda m: coverage.get(m["id"], 0))
        return ranked[0] if ranked else self.mechanisms[0]

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
        if is_cold_start(existing, self.cfg):
            field_w, _ = field_window_prior(lessons, existing)
            if field_w:
                top_field = max(field_w, key=lambda k: field_w[k])
                mapped = _FIELD_MECH.get(str(top_field))
                if mapped:
                    return mapped
            return pick_theme_with_lessons(
                self.mechanisms,
                coverage,
                lessons,
                forced=forced_theme,
                soft_switch_after=int(self.llm_cfg.get("soft_switch_after", 3)),
                recent_themes=recent_themes,
                hard_rotate=False,
                usable_coverage={},
            )
        usable_cov = usable_mechanism_coverage(existing)
        pool = eligible_mechanisms(self.mechanisms, usable_cov)
        soft = pick_theme_with_lessons(
            self.mechanisms,
            coverage,
            lessons,
            forced=forced_theme,
            soft_switch_after=int(self.llm_cfg.get("soft_switch_after", 3)),
            recent_themes=recent_themes,
            hard_rotate=bool(self.llm_cfg.get("hard_rotate", True)),
            usable_coverage=usable_cov,
        )
        if not self.llm_cfg["llm_decide_theme"]:
            return soft
        try:
            data = self._llm_json_with_retry(
                SYSTEM_DSL
                + " 本轮只选择一个机制方向。输出JSON: {theme:str, reason:str}。"
                " theme 必须是给定 mechanisms 的 id 之一。"
                " 不得选择已有 candidate 的机制。"
                " 优先 keep_count 低、recent_failures 少、且不在 recent_themes 里的机制。",
                json.dumps(
                    {
                        "mechanisms": [
                            {
                                "id": m["id"],
                                "desc": m["desc"],
                                "keep_count": coverage.get(m["id"], 0),
                                "recent_failures": weak_mechanisms(lessons).get(m["id"], 0),
                            }
                            for m in pool
                        ],
                        "suggested": soft,
                        "blocked_mechanisms": sorted(blocked_mechanisms(usable_cov)),
                        "forced_theme": forced_theme,
                        "recent_themes": list(recent_themes or [])[-5:],
                        "lessons_tail": lessons[-12:],
                        "existing_sample": existing[:12],
                    },
                    ensure_ascii=False,
                ),
            )
            theme = str(data.get("theme", "")).strip()
            ids = {m["id"] for m in pool}
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

    def _blocked_field_set(self) -> set[str]:
        blocked = getattr(self, "_blocked_mechs", set()) or set()
        return {f for f, m in _FIELD_MECH.items() if m in blocked}

    def _expr_has_blocked_fields(self, expr: str) -> bool:
        skip = self._blocked_field_set()
        if not skip:
            return False
        fields, _ = collect_fields_windows(expr)
        return bool(fields & skip)

    def _unused_compose_count(self, bans: dict[str, set[str]]) -> int:
        """Unused catalog templates on eligible mechanisms, ignoring blocked fields."""
        skip_mechs = getattr(self, "_blocked_mechs", set()) or set()
        skip_fields = self._blocked_field_set()
        n = 0
        for mech, tmpl in _COMPOSE_CATALOG:
            if mech in skip_mechs:
                continue
            try:
                expr = tmpl.format(w=20, w2=60)
            except Exception:
                continue
            if skip_fields:
                fields, _ = collect_fields_windows(expr)
                if fields & skip_fields:
                    continue
            banned, _ = is_banned_expression(expr, bans)
            if not banned:
                n += 1
        return n

    def expand_compose_catalog_via_llm(
        self,
        *,
        n_ask: int = 12,
        max_accept: int | None = None,
        blocked: set[str] | None = None,
    ) -> dict[str, Any]:
        """Low-frequency catalog ammo. Not a per-round candidate source."""
        cap = int(max_accept if max_accept is not None else self.llm_cfg.get(
            "catalog_expand_max", _CATALOG_EXPAND_MAX
        ))
        cap = max(0, min(cap, _CATALOG_EXPAND_MAX))
        mech_ids = {str(m["id"]) for m in self.mechanisms}
        blocked = set(blocked if blocked is not None else getattr(self, "_blocked_mechs", set()) or set())
        allowed = [m for m in self.mechanisms if m["id"] not in blocked] or list(self.mechanisms)
        allowed_ids = {str(m["id"]) for m in allowed}
        known = set(_CATALOG_SKELETONS)
        result: dict[str, Any] = {
            "attempted": True,
            "asked": n_ask,
            "accepted": [],
            "rejected": [],
            "n_accepted": 0,
            "path": str(extra_templates_path(self.cfg)),
            "n_catalog_before": len(_COMPOSE_CATALOG),
        }
        if cap <= 0:
            result["attempted"] = False
            result["reason"] = "max_accept=0"
            return result
        user = {
            "task": "catalog_templates",
            "count": n_ask,
            "mechanisms": [{"id": m["id"], "desc": m["desc"]} for m in allowed],
            "blocked_mechanisms": sorted(blocked),
            "catalog_skeletons": sorted(known)[:40],
            "instruction": (
                f"提出最多 {n_ask} 条互异 tmpl。"
                "只用 allowed mechanisms。"
                "骨架不得出现在 catalog_skeletons。"
            ),
        }
        try:
            data = self._llm_json_with_retry(
                SYSTEM_TEMPLATE + f" 请提出最多 {n_ask} 条新模板。输出 templates 数组。",
                json.dumps(user, ensure_ascii=False),
            )
        except Exception as e:
            result["ok"] = False
            result["error"] = str(e)
            return result
        items = data.get("templates")
        if not isinstance(items, list) or not items:
            items = [data] if isinstance(data, dict) and data.get("tmpl") else []
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if len(accepted) >= cap:
                break
            mech = str(item.get("mechanism") or "").strip()
            tmpl = str(item.get("tmpl") or item.get("template") or item.get("expression") or "")
            if mech not in allowed_ids:
                rejected.append({"tmpl": tmpl, "reason": "blocked_or_unknown_mechanism"})
                continue
            norm, err = validate_compose_template(
                mech, tmpl, mechanism_ids=mech_ids, known_skeletons=known
            )
            if err or not norm:
                rejected.append({"tmpl": tmpl, "reason": err or "bad_template"})
                continue
            expr = norm.format(w=20, w2=60)
            sk = skel_of(parse_expression(expr))
            known.add(sk)
            accepted.append({"mechanism": mech, "tmpl": norm, "skeleton": sk})
        if accepted:
            save_extra_templates(accepted, self.cfg)
            sizes = rebuild_compose_catalog(self.cfg)
            result["n_catalog_after"] = sizes["n_catalog"]
        else:
            result["n_catalog_after"] = result["n_catalog_before"]
        result["ok"] = True
        result["accepted"] = accepted
        result["rejected"] = rejected[:20]
        result["n_accepted"] = len(accepted)
        result["n_rejected"] = len(rejected)
        return result

    def _compose_one(
        self,
        theme: str | None,
        bans: dict[str, set[str]],
        coverage: dict[str, int],
    ) -> dict[str, Any] | None:
        """Emit a structurally new DSL tree when templates/LLM are exhausted."""
        del coverage  # theme/eligible pool already encode coverage
        windows = [5, 10, 20, 40, 60]
        curriculum = bool(getattr(self, "_curriculum", False))
        field_prior: dict[str, float] = getattr(self, "_field_prior", {}) or {}
        window_prior: dict[int, float] = getattr(self, "_window_prior", {}) or {}
        blocked: set[str] = getattr(self, "_blocked_mechs", set()) or set()
        skip_fields = self._blocked_field_set()
        specs = list(_COMPOSE_UNARY if curriculum else _COMPOSE_CATALOG)
        if blocked:
            filtered = [s for s in specs if s[0] not in blocked]
            if filtered:
                specs = filtered
        if skip_fields:
            keep: list[tuple[str, str]] = []
            for item in specs:
                try:
                    fields, _ = collect_fields_windows(item[1].format(w=20, w2=60))
                except Exception:
                    fields = set()
                if not (fields & skip_fields):
                    keep.append(item)
            if keep:
                specs = keep
        pri_set = {t for _m, t in _COMPOSE_PRIORITY}
        priority = [s for s in specs if s[1] in pri_set]
        rest_specs = [s for s in specs if s[1] not in pri_set]

        def _order(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
            items = list(items)
            if field_prior:
                def _tmpl_score(item: tuple[str, str]) -> float:
                    try:
                        fields, _ = collect_fields_windows(item[1].format(w=20, w2=60))
                    except Exception:
                        fields = set()
                    return sum(field_prior.get(f, 0.05) for f in fields) or 0.05

                items.sort(key=_tmpl_score, reverse=True)
            else:
                random.shuffle(items)
            return items

        if theme and any(s[0] == theme for s in specs):
            themed_pri = _order([s for s in priority if s[0] == theme])
            themed_rest = _order([s for s in rest_specs if s[0] == theme])
            other_pri = _order([s for s in priority if s[0] != theme])
            other_rest = _order([s for s in rest_specs if s[0] != theme])
            specs = themed_pri + themed_rest + other_pri + other_rest
        else:
            specs = _order(priority) + _order(rest_specs)
        for mech_id, tmpl in specs:
            if window_prior:
                w = int(weighted_sample(window_prior, windows))
                shuffled = [w] + [x for x in windows if x != w]
            else:
                shuffled = list(windows)
                random.shuffle(shuffled)
            for w in shuffled:
                others = [x for x in windows if x != w] or windows
                w2 = int(weighted_sample(window_prior, others)) if window_prior else random.choice(others)
                expr = tmpl.format(w=w, w2=w2)
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

    def _crossover_trees(self, expr_a: str, expr_b: str) -> str | None:
        """Replace one nested subtree of A with a subtree from B."""
        from qfactor.dsl.validate import validate_expression

        try:
            tree_a = parse_expression(expr_a)
            tree_b = parse_expression(expr_b)
        except Exception:
            return None
        recv = nested_expr_paths(tree_a)
        recv = [p for p in recv if p] or recv
        donors = all_expr_paths(tree_b)
        if not recv or not donors:
            return None
        parent_sk = {skel_of(tree_a), skel_of(tree_b)}
        pairs = [(ra, db) for ra in recv for db in donors]
        random.shuffle(pairs)
        for path_a, path_b in pairs:
            try:
                donor = expr_at(tree_b, path_b)
                if not isinstance(donor, Expr):
                    continue
                child = replace_expr_at(tree_a, path_a, donor)
                if not isinstance(child, Expr):
                    continue
                text = child.to_str()
                sk = skel_of(child)
                if sk in parent_sk or sk in _CATALOG_SKELETONS:
                    continue
                v = validate_expression(text)
                if not v.get("ok"):
                    continue
                return text
            except Exception:
                continue
        return None

    def _crossover_one(
        self,
        parents: list[dict[str, Any]],
        bans: dict[str, set[str]],
        theme: str | None = None,
    ) -> dict[str, Any] | None:
        """Swap a subtree across two different-mechanism parents. No LLM."""
        by_mech: dict[str, dict[str, Any]] = {}
        blocked: set[str] = getattr(self, "_blocked_mechs", set()) or set()
        skip_fields = self._blocked_field_set()
        for p in parents:
            mid = str(p.get("mechanism") or p.get("category") or "").strip()
            expr = str(p.get("expression") or "")
            if not mid or not expr or mid in by_mech or mid in blocked:
                continue
            by_mech[mid] = p
        mechs = list(by_mech)
        if len(mechs) < 2:
            return None
        random.shuffle(mechs)
        for i, m1 in enumerate(mechs):
            for m2 in mechs[i + 1 :]:
                pa, pb = by_mech[m1], by_mech[m2]
                child_expr = self._crossover_trees(str(pa["expression"]), str(pb["expression"]))
                if not child_expr:
                    child_expr = self._crossover_trees(
                        str(pb["expression"]), str(pa["expression"])
                    )
                if not child_expr:
                    continue
                if skip_fields and self._expr_has_blocked_fields(child_expr):
                    continue
                banned, _ = is_banned_expression(child_expr, bans)
                if banned:
                    continue
                try:
                    sk = expression_fingerprint(child_expr)["skeleton"]
                    pa_sk = expression_fingerprint(str(pa["expression"]))["skeleton"]
                    pb_sk = expression_fingerprint(str(pb["expression"]))["skeleton"]
                except Exception:
                    continue
                if sk in {pa_sk, pb_sk}:
                    continue
                child_mech = theme or m1
                if child_mech in blocked:
                    child_mech = m2 if m2 not in blocked else m1
                if child_mech in blocked:
                    continue
                return {
                    "name": unique_factor_name(str(child_mech), "xover"),
                    "mechanism": child_mech,
                    "expression": child_expr,
                    "hypothesis": f"crossover {m1} x {m2}",
                    "source": "crossover",
                    "lookback": int(pa.get("lookback") or pb.get("lookback") or 20),
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
        skip_fields = self._blocked_field_set()
        field_swaps = [
            pair
            for pair in (
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
            if pair[1] not in skip_fields
        ]
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
            if self._expr_has_blocked_fields(new_expr):
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
        if self._expr_has_blocked_fields(expr):
            return None
        idea = data.get("idea") if isinstance(data.get("idea"), dict) else {}
        claim = str(idea.get("claim") or data.get("claim") or "").strip()
        hypothesis = str(data.get("hypothesis") or claim or mech["desc"])
        known = {str(m["id"]) for m in self.mechanisms}
        mid = str(data.get("mechanism") or idea.get("mechanism") or mech["id"]).strip()
        if mid not in known:
            mid = str(mech["id"])
        return {
            "name": unique_factor_name(mid, source[:6]),
            "mechanism": mid,
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
        del lessons
        cards: list[dict[str, Any]] = []
        for e in existing:
            if str(e.get("status") or "") not in USABLE_STATUSES:
                continue
            card = self._factor_card(e)
            if card:
                cards.append(card)
            if len(cards) >= 8:
                break
        compile_bans = {
            "hashes": set(bans.get("hashes") or []),
            "skeletons": set(bans.get("skeletons") or []) | set(_CATALOG_SKELETONS),
        }
        for c in cards:
            if c.get("skeleton"):
                compile_bans["skeletons"].add(str(c["skeleton"]))
        modes = [
            "not_like must name the candidate cards — not a window tweak",
            "do not reuse catalog_skeletons or candidate skeletons",
            "prefer turnover/shadow/reversal/volume_price over amplitude",
        ]
        ideas = self._llm_idea_batch(mech, cards, n, modes)
        self._last_idea_n = len(ideas)
        if not ideas:
            return []
        return self._llm_compile_ideas(
            mech, ideas, cards, compile_bans, source="llm"
        )

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
            "catalog_skeletons": sorted(_CATALOG_SKELETONS)[:40],
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
        source: str = "llm_mutate",
    ) -> list[dict[str, Any]]:
        parent_skels = {p.get("skeleton") for p in cards if p.get("skeleton")}
        banned = set(bans.get("skeletons") or []) | set(_CATALOG_SKELETONS)
        user = {
            "task": "compile",
            "ideas": ideas,
            "library_cards": cards,
            "parent_skeletons": sorted(str(s) for s in parent_skels if s),
            "banned_skeletons": sorted(str(s) for s in banned)[:40],
            "catalog_skeletons": sorted(_CATALOG_SKELETONS)[:40],
            "instruction": (
                "把每个 idea 译成一棵新骨架 DSL 树。"
                "只用 idea.fields 与白名单算子/窗口。"
                "子代 skeleton 不得与 parent_skeletons / banned_skeletons / catalog_skeletons 相同。"
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
                    payload, mech, source, bans, parent_skeleton=parent_skel
                )
                if cand:
                    sk = expression_fingerprint(cand["expression"])["skeleton"]
                    if sk in parent_skels or sk in _CATALOG_SKELETONS:
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
        for seed in DSL_SEEDS:
            expr = seed["expression"]
            if expr in seen:
                continue
            seen.add(expr)
            uniq.append(
                {
                    "name": seed["name"],
                    "expression": expr,
                    "mechanism": seed["mechanism"],
                    "hypothesis": seed["hypothesis"],
                    "status": "draft",
                    "summary": {},
                }
            )

        def _rank(p: dict[str, Any]) -> tuple[int, float, float]:
            st = str(p.get("status") or "draft")
            tier = {"approved": 0, "candidate": 1, "screened": 2, "draft": 3}.get(st, 4)
            summary = p.get("summary") if isinstance(p.get("summary"), dict) else {}
            resid = abs(float(summary.get("resid_ic_mean") or 0))
            ic = abs(float(summary.get("rank_ic_mean") or 0))
            return (tier, -resid, -ic)

        uniq.sort(key=_rank)
        usable = [p for p in uniq if str(p.get("status")) in USABLE_STATUSES]
        screened = [p for p in uniq if str(p.get("status")) == "screened"]
        screened.sort(key=_rank)
        cs = cold_start_cfg(self.cfg)
        top_n = int(cs["parent_top_screened"])
        cold = is_cold_start(uniq, self.cfg) or is_cold_start(
            [p for p in uniq if str(p.get("status")) in KEEP_STATUSES], self.cfg
        )
        if cold:
            drafts = [p for p in uniq if str(p.get("status")) == "draft"]
            drafts.sort(key=_rank)
            return usable + screened + drafts
        blocked = blocked_mechanisms(usable_mechanism_coverage(uniq))
        skip_fields = {f for f, m in _FIELD_MECH.items() if m in blocked}
        by_mech: dict[str, list[dict[str, Any]]] = {}
        for p in screened:
            mid = str(p.get("mechanism") or p.get("category") or "").strip()
            if not mid or mid in blocked:
                continue
            expr = str(p.get("expression") or "")
            if skip_fields and expr:
                fields, _ = collect_fields_windows(expr)
                if fields & skip_fields:
                    continue
            by_mech.setdefault(mid, []).append(p)
        per = max(1, top_n // max(len(by_mech), 1))
        screened_keep: list[dict[str, Any]] = []
        for rows in by_mech.values():
            rows.sort(key=_rank)
            screened_keep.extend(rows[:per])
        mixed = usable + screened_keep
        return mixed if mixed else uniq

    def _parents_by_mechanism(
        self,
        parents: list[dict[str, Any]],
        *,
        exclude: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Keep at most one parent per mechanism, already ranked by _parent_pool."""
        skip = exclude or set()
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for p in parents:
            mid = str(p.get("mechanism") or p.get("category") or "").strip()
            if not mid or mid in seen or mid in skip:
                continue
            seen.add(mid)
            out.append(p)
        return out

    def _refresh_field_window_prior(
        self,
        lessons: list[dict[str, Any]],
        existing: list[dict[str, Any]],
        *,
        cold: bool,
        round_idx: int,
        every: int,
    ) -> bool:
        last = getattr(self, "_prior_at_round", None)
        if round_idx > 0 and every > 0 and last is not None and (round_idx - last) < every:
            return False
        if cold:
            field_p, win_p = field_window_prior(lessons, existing)
        else:
            blocked_fields = {
                f for f, m in _FIELD_MECH.items() if m in self._blocked_mechs
            }
            field_p, win_p = field_window_prior(
                lessons,
                existing,
                blocked_mechanisms=self._blocked_mechs,
                blocked_fields=blocked_fields,
                prefer_oos=True,
            )
        self._field_prior = field_p
        self._window_prior = win_p
        if round_idx > 0:
            self._prior_at_round = round_idx
        return True

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
        round_idx: int = 0,
    ) -> list[dict[str, Any]]:
        self.llm.require_enabled()
        existing = list(existing or [])
        if not existing:
            try:
                from qfactor.factor.registry import FactorRegistry

                existing = FactorRegistry(self.cfg).existing_summaries()
            except Exception:
                existing = []
        coverage = dict(coverage) if coverage else keep_mechanism_coverage(existing)
        lessons = list(lessons or [])
        out: list[dict[str, Any]] = []

        index = library_diversity_index(self.cfg)
        cs_cfg = cold_start_cfg(self.cfg)
        cold = is_cold_start(existing, self.cfg)
        self._curriculum = bool(cs_cfg["curriculum"] and cold)
        usable_cov = usable_mechanism_coverage(existing)
        if cold:
            self._blocked_mechs = set()
            self._eligible_mechs = list(self.mechanisms)
        else:
            self._blocked_mechs = blocked_mechanisms(usable_cov)
            self._eligible_mechs = eligible_mechanisms(self.mechanisms, usable_cov)
            if theme and theme in self._blocked_mechs:
                theme = None
        prior_refreshed = self._refresh_field_window_prior(
            lessons,
            existing,
            cold=cold,
            round_idx=int(round_idx or 0),
            every=int(cs_cfg["prior_update_every"]),
        )
        bans = merge_bans(index, extra_banned_hashes, extra_banned_skeletons)
        bans["skeletons"] = active_skeleton_bans(
            self.cfg,
            extra=list(extra_banned_skeletons or []),
            cold_start=bool(cs_cfg["disable_fsa"] and cold),
        )

        unused_compose = self._unused_compose_count(bans)
        ratio = self.llm_cfg["llm_ratio"] if llm_ratio is None else float(llm_ratio)
        parents = self._parent_pool(existing)
        usable = [p for p in parents if str(p.get("status")) in USABLE_STATUSES]
        plan = llm_slot_plan(
            n,
            unused_compose=unused_compose,
            n_usable=len(usable),
            ratio=ratio,
            catalog_skip_at=_CATALOG_SKIP_AT,
            library_mutate_slots=int(self.llm_cfg.get("llm_library_mutate_slots", 0)),
            has_parents=bool(parents),
            cold_start=cold,
            fresh_ratio=float(cs_cfg["llm_fresh_ratio"]),
        )
        skip_llm = bool(plan["skip_llm"])
        n_llm = int(plan["n_llm"])
        n_mutate = int(plan["n_mutate"])
        n_fresh = int(plan["n_fresh"])
        n_xover = int(plan.get("n_crossover") or 0)
        n_tmpl = int(plan["n_template"])
        thin = unused_compose < _CATALOG_SKIP_AT and not cold
        blocked_excl = set(self._blocked_mechs)
        xover_parents = [
            p
            for p in self._parents_by_mechanism(parents, exclude=blocked_excl)
            if not self._expr_has_blocked_fields(str(p.get("expression") or ""))
        ]
        mutate_parents = [
            p
            for p in self._parents_by_mechanism(
                parents, exclude=blocked_excl | {"amplitude"}
            )
            if not self._expr_has_blocked_fields(str(p.get("expression") or ""))
        ]
        candidate_skels: set[str] = set()
        for p in usable:
            try:
                candidate_skels.add(expression_fingerprint(str(p["expression"]))["skeleton"])
            except Exception:
                continue
        batch_sz = max(1, self.llm_cfg["llm_batch_size"])
        perturb_ratio = self.llm_cfg["perturb_ratio"]
        stats = {
            "n_requested": n,
            "n_llm": n_llm,
            "n_mutate": n_mutate,
            "n_fresh": n_fresh,
            "n_crossover": n_xover,
            "n_template": n_tmpl,
            "llm_fresh_ok": 0,
            "llm_fresh_empty": 0,
            "llm_mutate_ok": 0,
            "llm_mutate_empty": 0,
            "crossover_ok": 0,
            "llm_errors": 0,
            "hint_fallback": 0,
            "compose_fallback": 0,
            "structure_perturb": 0,
            "unused_compose": unused_compose,
            "llm_skipped": skip_llm,
            "force_library_mutate": bool(plan["force_library_mutate"]),
            "n_usable": len(usable),
            "llm_ideas": 0,
            "cold_start": cold,
            "curriculum": self._curriculum,
            "blocked_mechanisms": sorted(self._blocked_mechs),
            "keep_coverage": dict(coverage),
            "prior_refreshed": prior_refreshed,
        }

        def _accept(cand: dict[str, Any] | None) -> bool:
            if not cand:
                return False
            banned, _ = is_banned_expression(cand["expression"], bans)
            if banned:
                return False
            fp = expression_fingerprint(cand["expression"])
            if fp["skeleton"] in bans.get("skeletons", set()):
                return False
            src = str(cand.get("source") or "")
            if src in {"llm", "llm_mutate", "crossover", "structure_perturb"}:
                if self._expr_has_blocked_fields(cand["expression"]):
                    return False
            if src in {"llm", "llm_mutate", "crossover"}:
                if fp["skeleton"] in _CATALOG_SKELETONS:
                    return False
                if fp["skeleton"] in candidate_skels:
                    return False
            out.append(cand)
            bans["hashes"].add(fp["expr_hash"])
            if not cold:
                bans.setdefault("skeletons", set()).add(fp["skeleton"])
            mid = cand.get("mechanism", "unknown")
            coverage[mid] = coverage.get(mid, 0) + 1
            return True

        def _fill_crossover(need: int) -> int:
            filled_x = 0
            guard_x = 0
            while filled_x < need and guard_x < need * 8:
                guard_x += 1
                cand = self._crossover_one(xover_parents, bans, theme)
                if _accept(cand):
                    filled_x += 1
                    stats["crossover_ok"] += 1
                elif cand is None:
                    break
                else:
                    try:
                        bans["hashes"].add(
                            expression_fingerprint(cand["expression"])["expr_hash"]
                        )
                    except Exception:
                        pass
            return filled_x

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
                if thin:
                    filled += _fill_crossover(n_fresh - filled)
                if unused_compose > 0:
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

        filled += _fill_crossover(n_xover)

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
                if not thin:
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
            for m in self._eligible_mechs:
                if _accept(self._from_hint(m, bans)):
                    stats["hint_fallback"] += 1
                    break

        if len(out) >= 1 and perturb_ratio > 0:
            n_pert = max(0, int(round(min(len(out), n) * perturb_ratio)))
            idxs = random.sample(range(len(out)), min(n_pert, len(out)))
            for i in idxs:
                pert = self._perturb_structure(out[i], bans)
                if not pert:
                    continue
                banned, _ = is_banned_expression(pert["expression"], bans)
                if banned:
                    continue
                try:
                    fp = expression_fingerprint(pert["expression"])
                except Exception:
                    continue
                if fp["skeleton"] in bans.get("skeletons", set()):
                    continue
                if len(out) < n:
                    if _accept(pert):
                        stats["structure_perturb"] += 1
                    continue

        guard = 0
        while len(out) < n and guard < n * 8:
            guard += 1
            if thin:
                if _fill_crossover(1) == 0:
                    break
                continue
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
