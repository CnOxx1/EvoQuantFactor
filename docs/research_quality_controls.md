# 价量研究库质量控制

研究层的目标是保留少量、可追溯的价量假说，而不是把 `screened` 解释为交易输入。
本项目不降低 production 或 active release 门槛；以下控制均为研究诊断或运维保护。

## 多重检验预警

每次 discovery 已记录全部生成、拒绝和保存试验。研究报告会按当前实验的实际
trial 数附带 Bonferroni/Newey-West `research_selection_bias_preview`。该预警失败
不会改变 research gate 的 `screened` 状态；冻结后的 sealed acceptance 仍是具有
约束力的家族错误率检查。

## 成本压力情景

报告同时展示 5、10、20 bps 成本情景。`eval.cost_bps: 10` 仍是 production gate
使用的唯一合同情景；压力表只用于识别对摩擦过于敏感的研究假说，不能替代生产门。

## 状态对账

```bash
qfactor library-reconcile
```

命令只读检查 `catalog.json`、`spec.yaml`、`reports/latest.json` 和 SQLite。发现
漂移时返回明细，但绝不自动修复或晋升/降级因子；状态修复必须由人工确认来源后进行。

## 数据就绪

```bash
qfactor data-contract-readiness
```

命令分别汇总 research、candidate 和 active-release 三层缺口。research 只要求
行情和 discovery 分区；candidate 要求 PIT/中性化/selection；release 要求完整
执行与风险证据。任一层只阻断自己的下一次状态转换。数据补齐流程见
`docs/production_data_contract.md`。

## 干净实验与旧库隔离

历史 snapshot/estimated-size 报告会动态归类为
`legacy_snapshot_research`，保留审计但不再作为父本。启动新的研究周期时使用：

```bash
qfactor loop --clean-experiment --no-resume
```

干净实验忽略旧 checkpoint、lesson、额外模板和 legacy 因子，只使用固定 DSL seed
以及本次 experiment 新产生的 screened。生成的因子写入 experiment/cohort 来源，
后续多重检验可按数据版本、discovery 窗口和机制累计。

当前仓库默认启用 bootstrap discovery `20240102–20251231`，并在 Cloud Agent
启动时运行 clean factory。该窗口只能生产 `screened`；snapshot universe 与
estimated `circ_mv` 仍使 candidate 保持为零。2026 数据不被 discovery 读取。
补齐长 PIT 历史后必须创建新 data version 和新分区，不能把 bootstrap 报告升级。
