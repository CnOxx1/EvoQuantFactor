# 因子工厂监控运行器

本运行器将 EvoQuantFactor 的日频因子生产、动态复评和下游 release 库存维护封装为一个**可监督的长期进程**。它以现有的生产合同为上限：数据不足时仅记录阻断状态，绝不降低 PIT、时间分区、LLM 预算、密封验收、可交易性或 `active release` 条件。

> 自动重启处理的是进程故障，不处理研究结论。某个因子被降级、某次 discovery 被数据合同阻断，都是正常且需要保留的审计结果，不会触发“放宽条件后重试”。

## 1. 生命周期与执行节奏

每个运行周期均记录数据版本、数据合同状态、操作结果、错误、前后因子数量和完成时间。默认周期为五分钟；候选因子每周期重新通过 production gate，screened 库存以较低频率重评，研究 discovery 仅在数据合同通过且满足调度频率时启动。

| 阶段 | 周期内行为 | 可能状态 | 是否可被交易模块使用 |
|---|---|---|---:|
| Discovery | 数据与日期合同通过时，运行一轮 research-only 搜索 | `draft` / `screened` | 否 |
| Dynamic production refresh | 每周期复评 `candidate` | 保留 `candidate` 或降回 `screened` | 否 |
| Screened recheck | 低频尝试 production gate | `screened` 或 `candidate` | 否 |
| Freeze / sealed acceptance | 人工冻结后才可消耗密封 OOS | `sealed_oos_passed` 或失败 | 否 |
| Tradability / release | PIT 执行账本、容量和 release 合同 | `tradability_passed` / `active release` | **仅 active release 可以** |

## 2. 运行与监控

在项目根目录中执行以下命令。生产环境应在持续在线的 Linux 主机上运行；临时环境仅适合烟雾测试，空闲时可能停止。

```bash
chmod 700 scripts/factor_factory_monitor.sh
scripts/factor_factory_monitor.sh start
scripts/factor_factory_monitor.sh status
scripts/factor_factory_monitor.sh stop
```

监控器使用 `runs/factory_monitor/monitor.pid` 保存自身 PID，使用 `worker.pid` 保存生产 worker PID。每 30 秒检查子进程是否存活；若子进程退出会立即按 10、20、40 秒直至 300 秒封顶的退避策略重启。若 `status.json` 心跳超过最大陈旧时间，监控器还会检查 worker 的 CPU tick 是否持续增长：仍在计算的长耗时因子评估不会被中断，只有**心跳陈旧且无 CPU 进展**时才会被认定为卡死并重启。全部重启记录写入 `runs/factory_monitor/restarts.jsonl`。

| 环境变量 | 默认值 | 含义 |
|---|---:|---|
| `FACTOR_INTERVAL_SECONDS` | `300` | 动态复评的周期秒数；生产日频任务不应设为高频轮询 |
| `FACTOR_DISCOVERY_EVERY` | `12` | discovery 的周期倍数；默认每小时最多一次 |
| `FACTOR_SCREENED_EVERY` | `72` | screened 全量重评的周期倍数；默认约每六小时一次 |
| `FACTOR_LLM_RATIO` | `0.25` | discovery 进入 LLM 发现的候选比例；仍受实验试验上限和数据合同约束 |
| `FACTOR_MAX_STALE_SECONDS` | `900` | 未更新心跳多久后认定 worker 卡死并重启 |

## 3. 审计与因子数量

运行状态保存在 `runs/factory_monitor/status.json`，逐周期事件保存在 `runs/factory_monitor/events.jsonl`。`counts_before` 与 `counts_after` 至少包括 `draft`、`screened`、`candidate`、`approved`、已冻结定义、已通过密封验收、已通过可交易性和 `active_release`。

当前数据若缺失 PIT 历史成分、供应商流通市值、ST/停牌/涨跌停、ADV、公司行动、点时行业、风险暴露或三段日期分区，`data_contract.state` 会是 `blocked`。在这一状态下可生产的 **active release 数量应为 0**；这是预期的 fail-closed 行为。

## 4. 长期主机部署建议

长期运行时，应让监控器由主机的服务管理器负责启动，并将项目目录、数据归档和 Python 环境固定在同一持久化卷中。监控脚本负责 worker 级自动重启；主机服务管理器负责监控脚本本身的开机启动和异常重启。停止时应先执行 `scripts/factor_factory_monitor.sh stop`，待 worker 写入 `stopped` 状态后再停止宿主服务。

不要同时启动多个监控器。多个 worker 会竞争写入 SQLite、因子 catalog、报告和发布库存，破坏审计顺序。启动前始终使用 `status` 确认已有 monitor PID 是否存活。
