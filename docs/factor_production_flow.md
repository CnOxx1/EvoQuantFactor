# 因子生产逻辑与服务器启动顺序

`loop` 默认仍不下载行情。生产前的获取、保持和检查由 `DataPrepareService`（`qfactor prepare-data` / `produce` / supervisor）负责：对照配置窗口检查覆盖，缺了再 `sync`，再跑三层合同。窗口没覆盖或 research 没过，就不能开挖。缺哪一层证据，就停在哪一层，不会为了出因子而降门槛。

## 1. 总流程

```mermaid
flowchart TD
    A[服务器 git pull main] --> B[配置 .env 中的 OPENAI_API_KEY]
    B --> C["qfactor prepare-data / supervisor 启动"]
    C --> C1{目标窗口已覆盖?}
    C1 -->|否| C2[PIT sync, 失败才 snapshot 回拉]
    C1 -->|是| C3[跳过下载, 只检查]
    C2 --> D[生成或复用 data_version.json]
    C3 --> D
    D --> D1[派生 ADV + 给旧 meta 盖戳]
    D1 --> E[三层合同]
    E --> F{research 合同}
    F -->|bars 缺失| C
    F -->|discovery 分区未配| G[停止: discovery_partitions_unconfigured]
    F -->|窗口超出已同步数据| H[停止: discovery_window_before/after_data]
    F -->|通过| I{LLM key}
    I -->|缺失| J[停止: 不调用模型]
    I -->|有 key| K[干净实验 discovery]
    K --> L[research 闸]
    L -->|未过| M[reject / 写入 trial ledger]
    L -->|通过| N[入库 screened]
    N --> O{candidate 合同}
    O -->|快照宇宙 / 估算市值 / 无 PIT 行业 / 无 selection| P[保持 screened<br/>candidate 仍为 0]
    O -->|PIT + 供应商 circ_mv + selection 通过| Q[production 闸]
    Q -->|通过| R[candidate]
    R --> S[freeze 定义]
    S --> T[sealed OOS]
    T --> U[simulate-tradability]
    U --> V{release 执行合同}
    V -->|ST/涨跌停/ADV 等不足| W[tradability_blocked]
    V -->|通过| X[active release]
```

## 2. 数据同步在做什么

`qfactor sync-data` 仍是底层拉数命令。`prepare-data` 在窗口已覆盖时不会重下；supervisor 只在启动或覆盖不完整时调用它。PIT 宇宙会先试；只有配置允许时才用 snapshot 做研究回拉，且不会把 snapshot 标成 PIT。

```mermaid
flowchart TD
    A["sync-data --start --end --source baostock"] --> B[拉交易日历]
    B --> C{宇宙策略}
    C -->|pit 且 archive/Tushare 覆盖窗口起点| D[按历史调样取并集代码]
    C -->|pit 但没有 2020 起点的调样| E[失败, 不会用今日名单冒充 PIT]
    C -->|--allow-snapshot-universe| F[用最新中证100 + 调样事件代码]
    D --> G[逐只拉日 K]
    F --> G
    G --> H{已有 parquet 是否覆盖整个窗口}
    H -->|只有 2024-2026 切片| I[重新下载缺的 2020 前缀]
    H -->|已覆盖 start-end| J[复用]
    I --> K[估算 circ_mv / 保留 isST tradestatus]
    J --> K
    K --> L[写入 bars.parquet + SQLite daily_bars]
    L --> M[写入 data_version 与 quality_reports]
    M --> N[宇宙仍可能是 snapshot<br/>circ_mv 仍可能是 estimated]
```

同步完成后才能谈“数据完整”。完整分三层，见下一节。

## 3. 合同检查：什么叫完整

`qfactor data-contract-readiness` 一次返回三层。`prepare-data` 会先看目标窗口覆盖，再复用这三层。`loop --prepare-data` / `produce` / supervisor 在调用 LLM 之前还要 `mining_allowed`。`loop` 本身仍会再跑 `require_discovery_contract`；晋升 candidate 前会再跑 `require_candidate_contract`。

```mermaid
flowchart LR
    subgraph research["research 合同 才能开挖"]
        R1[日行情 has_bars]
        R2[discovery_start / discovery_end 已配置]
        R3[discovery 窗口落在已同步数据范围内]
    end
    subgraph candidate["candidate 合同 才能晋升生产因子"]
        C1[universe_mode = pit]
        C2[供应商 circ_mv]
        C3[PIT 行业覆盖 >= 95%]
        C4[selection 分区已冻结]
        C5[选择偏差审计]
        C6[独立观测 >= 60]
        C7[至少 3 年同号]
    end
    subgraph release["release 合同 才能交易"]
        L1[ST / 停牌 / 涨跌停 >= 98%]
        L2[ADV / 公司行动]
        L3[风险暴露]
        L4[密封 OOS + 可交易账本]
    end
    research --> candidate --> release
```

快照成分 + 估算市值可以通过 research，**不能**通过 candidate。这是设计，不是故障。

## 4. 单次 discovery 怎么生产 screened

只有 research 合同通过后，`FactorLoop` / supervisor 才会进入这一段。

```mermaid
flowchart TD
    A[require_discovery_contract] --> B[创建 experiment 账本]
    B --> C[干净实验: 忽略 legacy 父本 / 旧 checkpoint / extra templates]
    C --> D[decide: 选机制主题]
    D --> E[generate: LLM 提案或 compose DSL]
    E --> F[解析表达式]
    F -->|非法| G[记 trial: invalid]
    F -->|合法| H[在 discovery 窗口上算因子]
    H --> I[research 闸: IC / ICIR / 覆盖 / 换手 / 两年同号 / 弱 OOS]
    I -->|未过| J[reject + lesson]
    I -->|通过| K[写入 factor_lib 状态=screened]
    K --> L[cohort=clean_discovery]
    J --> M{还有剩余 rounds?}
    L --> M
    M -->|有| D
    M -->|无| N[结束: 只产出 screened]
```

循环**不会**自动 `promote_screened`。supervisor 每个周期都会看 candidate 合同；不过就明确 `recheck_screened=blocked`。

## 5. 工厂 supervisor 一个周期

```mermaid
flowchart TD
    A["run-forever / run-once"] --> P[prepare: 检查覆盖, 缺了再拉]
    P --> B[读 data_version]
    B --> C[research 合同]
    B --> D[candidate 合同]
    P -->|mining_allowed=false| E0[research_discovery = blocked]
    C -->|未过| E[research_discovery = blocked]
    C -->|通过且本周期该开挖| F[FactorLoop 1 round / batch 2]
    C -->|通过但未到 cadence| G[research_discovery = skipped]
    D -->|未过| H[recheck_screened = blocked]
    D -->|通过| I[refresh_candidates / promote_screened]
    F --> J[export-trading-releases]
    E0 --> J
    E --> J
    G --> J
    H --> J
    I --> J
    J --> K[library-export-candidates]
    K --> L[library-reconcile]
    L --> M[写 status.json / events.jsonl]
```

默认 `discovery_every=12`。`run-forever --start-cycle 12` 会在第 12 周期先挖一轮，之后每 12 个周期再挖。这只影响研究 discovery，不影响 candidate 挡板。

## 6. 服务器上必须按这个顺序

直接 `loop`（不加 `--prepare-data`）仍可能用仓库里的 2024–2026 切片开挖，因为当前 discovery 窗口就在这段数据里。`prepare-data` / `produce` / 默认 supervisor 会先对照 `data_prepare.start`（默认 `20200101`）检查覆盖，不够就拉，不够且拉失败就挡住 mining。

```bash
# 推荐：一条命令按顺序 pull → prepare-data → 启动工厂
scripts/start_factory.sh
scripts/start_factory.sh status
scripts/start_factory.sh stop

# 等价手工步骤
git fetch origin main && git checkout main && git pull origin main
.venv/bin/qfactor prepare-data
.venv/bin/qfactor data-contract-readiness
.venv/bin/qfactor produce --rounds 5 --batch-size 8 --gate research
# 或
.venv/bin/python -m qfactor.agent.supervisor run-forever --start-cycle 12
```

长历史进库并生成**新** `data_version` 之后，才能把 `eval.partitions.discovery_start` 改成 `20200102`。如果先改分区再拉数，`discovery_window_before_data` 会挡住 LLM，这是正确的。

2026 行情可以留在库里，但不要写进 discovery。candidate 在 PIT 成分、供应商市值和 selection 分区补齐之前必须保持为 0。

## 7. 三层优化：先诚实，再补证据，最后扩窗口

不要放宽闸门，也不要再开一轮只为了“多挖 screened”的 LLM。优化按这三层走，不能跳层。

### 第一层：代码诚实（本仓库即可）

`prepare-data` 在已有行情上会调用 `enrich_derived_evidence`：用已完成成交额填派生 `adv_20d`，并给旧 `data_version.json` 盖上 `universe_mode` / `circ_mv_source`。这**不会**把 snapshot 升成 PIT，也**不会**把估算市值升成供应商市值。

评估中性化只在 candidate 级证据上做：

- 行业残差只用日期对齐的 `industry_pit`。今日静态行业图会把当前分类灌进历史，造成前视。
- 市值残差只在 `circ_mv_source` 以 `_daily_basic` 结尾时做。`amount/turnover` 估算市值上的 residual IC 不是生产声明。
- `eval.neutralize_require_vendor_circ_mv` 默认 `true`。

这一层只停止假残差。`candidate` 仍为 0。

### 第二层：供应商 / archive 证据（在你的服务器上）

把覆盖窗口起点的文件放进 `data/raw/providers/`，或配置 `TUSHARE_TOKEN`：

| 文件 | 用途 |
|---|---|
| `csi100_members.parquet` | 点时中证100调样 |
| `daily_basic.parquet` | 供应商流通市值 |
| `industry_history.parquet` | 点时行业 |
| `security_status.parquet` | ST / 停牌 / 涨跌停 |
| `corporate_actions.parquet` | 公司行动 |

不要把中证官网最新快照和历史 xls 在缺口大于 240 个交易日时拼接成假 PIT。官方 archive 目前只有最新快照时，重建必须停，而不是用今日名单盖满 2020。

拉数命令仍是：

```bash
git fetch origin main && git checkout main && git pull origin main
scripts/start_factory.sh
# 或
.venv/bin/qfactor prepare-data
```

不要在没有 token / archive 的环境里再开一小时 BaoStock 下载指望它变成 candidate。

### 第三层：新 data_version 之后再扩 discovery

只有第二层写出**新** `data_version`，并且 `universe_mode=pit`、`circ_mv_source` 为供应商 `*_daily_basic` 之后，才改：

- `eval.partitions.discovery_start` → `20200102`（不要写进 2026）
- 配置并冻结 `selection_start` / `selection_end`
- 对已有 screened 做 `library-reeval-screened`，让真正过生产闸的因子进入 candidate

旧 bootstrap 报告不能升级。新宇宙和新市值来源必须重算。
