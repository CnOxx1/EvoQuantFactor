# qfactor

中证100（中证A100 / `000903.SH`）日频量价因子工厂。用 DSL 表达式生成因子，经研究闸 / 生产闸两级评估入库；LLM 只负责提案，过闸由解析器与数值闸门决定。

目标不是堆 `screened`，而是产出**非振幅族**的 `candidate`。`candidate` 只表示过了本机 production 闸，**不是**已验收的实盘因子。

| 项 | 值 |
|---|---|
| 包名 | `qfactor` 0.1.0 |
| Python | ≥ 3.11 |
| 宇宙 | 中证100，日频，时区 `Asia/Shanghai` |
| 信号 | T 日算因子，T+1 交易；持有 / 前瞻收益默认 5 日 |
| 远程仓库 | https://github.com/CnOxx1/EvoQuantFactor.git |

---

## 目录结构

```
configs/                 项目、数据源、评估闸
src/qfactor/
  cli.py                 Typer CLI
  settings.py            配置加载
  agent/                 挖矿循环：LangGraph + 生成器 + LLM
  data/                  同步、成分、Baostock / Tushare
  dsl/                   表达式解析与求值
  eval/                  IC / OOS / 分层 / 相关 / 闸门
  factor/                因子注册、变换、库运营
  db/                    SQLite 双写
  api/                   FastAPI + Web UI
skills/mechanisms.yaml   八个机制族与提示骨架
factor_lib/factors/      每个因子一个目录（spec.yaml + factor.py + reports/）
tests/                   pytest
runs/_hour_mine.py       按墙钟预算连续挖矿
data/                    parquet / SQLite
```

安装：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env   # 填 TUSHARE_TOKEN、OPENAI_API_KEY
```

密钥只放在本地 `.env`，**不要提交**。挖矿需要 `OPENAI_API_KEY`；点时成分需要 `TUSHARE_TOKEN`。

```bash
# 全程建议
set PYTHONPATH=src
python -m qfactor.cli --help
```

---

## 因子生命周期

```
draft ──research闸──► screened ──production闸──► candidate ──人工──► approved
                │                         │
                └──────── reject ─────────┘
```

| 状态 | 含义 | 用途 |
|---|---|---|
| `draft` | 刚生成或未过研究闸 | 不进 KEEP |
| `screened` | 过研究闸（训练窗宽松） | 研究堆；**禁止当生产库存** |
| `candidate` | 过生产闸（holdout + 残差 + 成本等） | 可用库存 USABLE |
| `approved` | 人工确认后的 candidate | 当前库为 0 |
| `reject` / `deprecated` | 失败或下架 | 高相关 reject 不进父本 |

KEEP = `screened` + `candidate` + `approved`。  
USABLE = `candidate` + `approved`。  
冷启动：KEEP 父本少于 `production.cold_start.min_parents`（默认 8）时放开主题封锁与骨架冻结。

每个因子目录：

- `spec.yaml`：名称、状态、DSL `expression`、机制、假设
- `factor.py`：由表达式编译出的计算代码
- `reports/`：历次评估 JSON；`latest.json` 为最近一次

---

## 数据

默认 SQLite：`data/qfactor.sqlite3`。`sync-data` / 因子入库会双写数据库；计算优先读 DB，失败再回退 Parquet。

| 用途 | 来源 | 说明 |
|---|---|---|
| 成分 / 权重 | Tushare `index_weight` / `index_member` | `universe_policy.mode: pit` 时必须有 `TUSHARE_TOKEN` |
| 行情 / 换手 / 行业 | Baostock | 日 K、换手、`query_stock_industry` |
| 流通市值 | `amount / turnover` 估算 | 仅诊断与市值中性；**不是** Tushare `circ_mv` |
| 最新成分 xls | 中证官网 | 仅 `mode: snapshot` |

`mode: pit` 且没有 token 时，同步会失败，而不会把最新名单盖满历史。没有 Tushare 就不要声称点时样本。磁盘上若仍是 snapshot 成分，评估数字不能当 PIT 结论。

```bash
python -m qfactor.cli db-init
python -m qfactor.cli db-import
python -m qfactor.cli db-status

python -m qfactor.cli sync-data --start 20240101 --end 20260630 --source baostock
python -m qfactor.cli sync-universe --start 20240101 --end 20260630
python -m qfactor.cli data-status
```

表：行情 `daily_bars`、成分 `universe_members`、行业 `industry_map`、因子 `factors` / `factor_reports`、任务 `jobs`、checkpoint `loop_checkpoints`。

---

## 评估闸

配置：`configs/eval_thresholds.yaml`。研究窗 `train_end: 20251231`；生产闸在此日期之后打分。交易滞后 1 日、前瞻 5 日、分层 5 档、成本 10bp、行业 + 市值中性。

**不要放松 production 闸。** 不要加大 `llm_ratio` / `llm_batch_size`，不要把冷启动阈值开到 KEEP≈96。

### research（挖矿 → screened）

- `|Rank IC| ≥ 0.01`，覆盖 ≥ 0.60，相关 ≤ 0.85
- 要求 OOS，但门槛弱（OOS IC 均值 ≥ 0）
- 不强制成本后多空为正、不强制残差 IC、不强制年份同号

### production（screened → candidate）

- `|Rank IC| ≥ 0.02`，覆盖 ≥ 0.70，相关 ≤ 0.70
- Newey-West ICIR：全样本门槛与 holdout `min_holdout_icir: 0.07`
- 残差 IC：`min_resid_ic_mean: 0.01`，残差 NW ICIR ≥ 0.07
- 分层单调 ≥ 0.75；成本后多空为正
- `freeze_sign`：训练窗与 holdout 同号，且 holdout IC ≥ `min_oos_ic_mean`
- 年份同号；近期 IC 为正；日换手 ≤ 1.20

### 多因子上游质量合同

后续多因子策略**不能直接读取** `screened` 或全部 `candidate`。它只能读取
`library-export-multifactor` 生成的库存；该库存只保留满足以下条件的条目：

- 当前状态为 `candidate` 或 `approved`，且最近一次报告是通过的 production 闸；
- 报告的数据版本等于当前数据版本；
- 宇宙为 PIT，中性化使用供应商 `daily_basic` 流通市值，且供应商覆盖率 ≥ 80%；
- 5 日持有期下独立观测数 ≥ 60；
- 导出同时保留 holdout IC、NW ICIR、残差 IC、最低 OOS 折、成本后多空、换手和库相关等质量元数据，供多因子层做二次择优与组合约束。

若当前是快照宇宙、估算市值或样本不足，导出结果为零是**预期的保护行为**，不是应通过放宽阈值解决的问题。

```bash
python -m qfactor.cli eval-factor NAME --gate research
python -m qfactor.cli library-reeval-screened
python -m qfactor.cli library-refresh-production
python -m qfactor.cli library-promote NAME --gate production
python -m qfactor.cli library-export-multifactor
```

---

## 挖矿循环

LangGraph：`decide → generate → review_validate → persist`。循环只产 `screened`，**不会**在图结束时自动 `promote_screened`。晋升用 `library-reeval-screened` / `library-promote`。LLM 卡片只含训练窗 `train_ic`，不含 holdout。LLM 无离线回退。

```bash
python -m qfactor.cli loop --rounds 5 --batch-size 8 --llm-ratio 0.45 --gate research
python runs/_hour_mine.py              # 默认 3600 秒
python runs/_hour_mine.py 1800 half_hour
```

`_hour_mine.py` 用 `graph_rounds_for_budget` 把剩余时间打包进一次 invoke（最多 10 回合），让字段/窗口 prior 缓存跨回合存活。

### 生成槽位（`llm_slot_plan`）

目录仍厚（未用 compose 模板多）时以 compose 为主。目录空时固定：

| 槽 | 空目录 |
|---|---|
| LLM fresh | 3（假设 → 编译，禁止已有 catalog 骨架） |
| AST 交叉 | ≥ 3 |
| LLM mutate | 1（父本排除振幅） |
| compose | 剩余 |

失败**不会**回退成 compose 灌振幅近亲。主题硬排除已有 `candidate` 的机制族。因子名用落地 `mechanism`，不用当轮 theme。

### 机制封锁与父本

已有 USABLE 的机制（振幅 / 流动性 / 隔夜等）不再当主题，交叉/mutate 父本也跳过这些机制。表达式含封锁字段（`amplitude` / `high` / `low` / `overnight` / `turnover_rate` 等）的 screened 不进交叉池。

热库交叉父本不再取「全局 IC 最高的 12 条 screened」（会被振幅占满），而是：USABLE + **每个未封锁机制**若干 screened（预算 `parent_top_screened` 均分）。

热库 `field_window_prior` 用残差 / holdout 加权，封锁族字段不进 prior；每 20 轮刷新。decide 用 `keep_mechanism_coverage`，不用全库 `mechanism_hits`。

### 目录扩容

每 20 轮 LLM 最多合并 10 条新骨架到 `runs/extra_templates.yaml`（该文件 gitignore）。`catalog_expand_unused_lt: 0` 表示不等目录耗尽也扩。

### 机制族

`skills/mechanisms.yaml`：reversal、momentum、volatility、liquidity、overnight、amplitude、volume_price、shadow。

---

## CLI 一览

| 命令 | 作用 |
|---|---|
| `sync-data` / `sync-universe` / `data-status` | 行情与成分 |
| `install-seeds` | 写入种子因子 |
| `list-factors` / `eval-factor` / `promote` | 单因子 |
| `mine` / `loop` | 挖矿（需 API key） |
| `library-archive` / `library-demote-corr` / `library-cap-usable` | 归档、高相关降权、每机制 1 条 candidate |
| `library-reeval-screened` | screened 上生产闸 |
| `library-refresh-production` | 重打 candidate |
| `library-promote` / `library-demote` | 升降级 |
| `library-export-multifactor` | 导出严格、数据版本固定的多因子策略输入库存 |
| `db-init` / `db-import` / `db-status` | SQLite |
| `serve` | Web UI `http://127.0.0.1:8000/ui/{sync,loop,factors}` |
| `show-config` | 打印根路径与宇宙 |

---

## 配置要点

`configs/project.yaml`：

- `universe_policy.mode`: `pit`（默认）\| `freeze_start` \| `snapshot`
- `defaults.trade_lag: 1`，`adj_type: qfq`
- `production.llm.llm_ratio: 0.45`，`llm_batch_size: 4`，`llm_review_ratio: 0`（LLM 不否决）
- `llm_decide_theme: false`（本地轮转主题，省一次 LLM）
- `diversity.max_per_skeleton: 2`，`max_corr_ban: 0.90`
- `cold_start.min_parents: 8`，`prior_update_every: 20`，`cheap_ic_min: 0.008`

`configs/data_sources.yaml`：指数代码 `000903`；成分优先 Tushare。

---

## 当前库（2026-08-14）

约 **3** 条 `candidate`（振幅 / 流动性 / 隔夜各 1），无 `approved`。同机制上限 1。PIT + `daily_basic` 重评后数字才会改口径。

| 名称 | 机制 | 表达式 |
|---|---|---|
| `amplitude_c10_163144_8115` | amplitude | `div(std(high,10),std(low,10))` |
| `overnight_c40_164135_4925` | overnight | `div(std(overnight,40),std(amplitude,40))` |
| `liquidity_c60_143458_3465` | liquidity | `div(abs(ret_1d),ma(turnover_rate,60))` |

已降为 screened：`amplitude_c40_171144_1084`、`amplitude_c10_105336_3673`、`momentum_llm_143848_8537`。

有 candidate 的族会被主题封锁，后续搜索应偏向 reversal / momentum / volatility / volume_price / shadow。

---

## 测试

```bash
python -m pytest tests -q
```

覆盖 DSL、闸门、宇宙、生成槽位、交叉父本、目录扩容、冷启动、多样性。不要为了过测试而放宽 production 阈值。

---

## 已知限制

1. **PIT**：没有 Tushare 成分与 `circ_mv` 时，不能把当前 IC 说成点时中证100结果。
2. **candidate ≠ 实盘**：未做多重检验收缩、容量、冲击；holdout 仍可能被多轮搜索看见。
3. **振幅占优**：高 IC screened 曾占满交叉父本；现已按未封锁机制均分，但振幅/流动性/隔夜字段仍禁止进入新交叉子代。
4. **LLM 只提案**：不提高 `llm_ratio`、不加新 Agent、不用 LLM 否决过闸。
5. **运行产物**：`runs/loop_*`、`*_mine.log`、SQLite `-wal/-shm`、`.env` 不入库。密钥用 `.env.example` 复制。

更细的问题清单见 `项目缺点.md`（部分条目已落地，部分仍是研究债）。
