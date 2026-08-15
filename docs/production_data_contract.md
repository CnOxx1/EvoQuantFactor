# EvoQuantFactor 生产数据合同与归档导入清单

**适用范围：** 中证 100、日频、量价因子工厂。研究发现、统计 candidate
与可交易 release 使用分层合同：研究层只保留假说，candidate 要求 PIT 与选择
区间统计证据，active release 再要求完整执行/风险证据。

> **发布原则：** 只有在当前数据版本同时具备 PIT 成分、供应商日频流通市值、证券状态、涨跌停价、ADV、公司行动、点时行业和风险暴露，且通过密封验收及订单账本时，因子才可能成为 `active release`。缺少执行数据不阻止 research，但仍阻止交易 release。

## 1. 归档位置与提供方配置

归档文件由 `configs/data_sources.yaml` 中的 `archive` 路径注册，并通过独立 `providers` 角色读入。交易日、证券代码组成所有日频文件的唯一业务键；日期统一为 `YYYYMMDD` 或可无损转换的 `YYYY-MM-DD`，代码统一使用项目内的 `ts_code` 形式。

| 数据类别 | 默认归档路径 | 最小键 | 用途 | 生产性要求 |
|---|---|---|---|---|
| PIT 指数成分 | `data/raw/providers/csi100_members.parquet` | `trade_date`, `ts_code` | 当日可投宇宙 | 必须覆盖重构生效日期；不得用最新快照回填历史 |
| 日频估值与容量 | `data/raw/providers/daily_basic.parquet` | `trade_date`, `ts_code` | 流通市值中性化、流通股本、ADV | 供应商字段覆盖率至少 80%；ADV 覆盖率至少 95% |
| 证券状态与涨跌停 | `data/raw/providers/security_status.parquet` | `trade_date`, `ts_code` | T+1 订单可执行掩码 | 状态及涨跌停价覆盖率均至少 98% |
| 公司行动 | `data/raw/providers/corporate_actions.parquet` | `trade_date`, `ts_code` | 复权链审计、事件隔离 | 每日须以显式事件或 `none` 覆盖至少 98% |
| 点时行业 | `data/raw/providers/industry_history.parquet` | `trade_date`, `ts_code` | 每日行业中性化 | 至少 95% 覆盖；静态行业表不能替代 |
| 点时风险暴露 | `data/raw/providers/risk_exposures.parquet` | `trade_date`, `ts_code` | 后续多因子风险约束 | 至少 95% 覆盖；字段由风险模型版本定义 |

## 2. 文件字段规范

### 2.1 PIT 中证 100 成分文件

| 字段 | 类型 | 是否必需 | 规则 |
|---|---|---:|---|
| `trade_date` | string/date | 是 | 成分或权重生效的点时日期；同步层按实际交易日向前填充 |
| `ts_code` | string | 是 | 标准证券代码，例如 `000001.SZ` |
| `weight` | float | 否 | 指数权重；可为空但不得改变成员资格 |

### 2.2 日频估值与容量文件

| 字段 | 类型 | 是否必需 | 规则 |
|---|---|---:|---|
| `trade_date`, `ts_code` | string | 是 | 联合唯一键 |
| `circ_mv` | float | 是 | 供应商日频流通市值；单位与数据源声明一致且全程不变 |
| `turnover_rate` | float | 推荐 | 日换手率，百分比口径需在来源元数据中注明 |
| `free_float_shares` | float | 推荐 | 日频流通股本，用于容量和市值核验 |
| `adv_20d` | float | 是 | 截至当日收盘、仅使用过去 20 个已完成交易日的平均成交额；单位必须与行情 `amount` 一致 |

若供应商没有 `adv_20d`，同步层会从过去 20 个有效 `amount` 值透明派生；样本起始的前 19 个交易日仍为未知，不能在生产账本中当作容量证据。

### 2.3 证券状态与涨跌停文件

| 字段 | 类型 | 是否必需 | 规则 |
|---|---|---:|---|
| `trade_date`, `ts_code` | string | 是 | 联合唯一键 |
| `is_st` | bool | 是 | 点时 ST/*ST 标记；未知必须为 null，不得默认 false |
| `is_suspended` | bool | 是 | 点时停牌标记；未知必须为 null，不得由缺失行情替代 |
| `limit_up` | float | 是 | 当日有效涨停价 |
| `limit_down` | float | 是 | 当日有效跌停价 |

订单账本在开仓与平仓开盘都应用该掩码。它会排除 ST、停牌以及开盘触及涨跌停的证券；若文件缺失，则只能运行研究诊断，不能形成 `tradability_passed`。

### 2.4 公司行动、行业与风险暴露文件

| 文件 | 额外字段 | 合同解释 |
|---|---|---|
| `corporate_actions.parquet` | `corporate_action`, `adj_factor_vendor` | `corporate_action` 必须每日存在，非事件日填 `none`；`adj_factor_vendor` 用于与行情复权链进行审计 |
| `industry_history.parquet` | `industry` | 必须为日期键分类。同步层优先将其写入日频面板；最新静态行业仅保留为研究诊断回退 |
| `risk_exposures.parquet` | 至少一个风险字段，例如 `beta`、`size`、`liquidity` | 除键外的暴露列由风险模型版本约定；文件需另附模型版本、发布日期和计算口径 |

## 3. 数据交付前校验

数据提供方应先对每个文件执行键唯一性、日期连续性、代码映射、单位一致性和来源版本校验。特别是公司行动文件必须明确非事件日期；仅在发生事件日期提供一行会被系统识别为覆盖不足。

| 校验项目 | 阈值或判定 | 失败后果 |
|---|---|---|
| `(trade_date, ts_code)` 重复 | 必须为 0 | 同步或归档校验失败 |
| PIT 成分模式 | `universe_mode = pit` | LLM discovery、production gate 与 release 全部阻断 |
| 供应商流通市值覆盖 | `>= 80%` | 生产中性化合同失败 |
| ST 与停牌联合覆盖 | `>= 98%` | 订单账本与 release 阻断 |
| 涨跌停价覆盖 | `>= 98%` | 订单账本与 release 阻断 |
| ADV 20 日覆盖 | `>= 95%` | 容量合同失败 |
| 公司行动记录覆盖 | `>= 98%` | 复权审计合同失败 |
| PIT 行业与风险暴露 | 各 `>= 95%` | 生产门、LLM discovery 与 release 阻断 |

## 4. 导入与验收顺序

中证官方最新成分和调样附件可用 `qfactor fetch-archive-universe` 下载；缺失的半年定期调样工作簿会停止回溯，不会把旧调入调出接到后来的快照上。Wind / Choice / RQData 导出以及补齐的中证历史文件用 `qfactor ingest-archive --role <role> --source <file>` 归一到合同列（`trade_date` + `ts_code`），再用 `qfactor validate-archive --strict` 检查六类文件是否齐全。入库命令不从行情推断 ST、停牌、涨跌停、行业或流通市值。随后执行 `qfactor sync-data`（行情源可以是 BaoStock / AkShare；PIT 证据走 archive），检查生成的 `data/processed/data_version.json` 与 `data/quality_reports/`。覆盖率、来源和限制会写入不可变数据版本元数据。无 Tushare token 时，`providers.*.auto` 会在对应归档文件存在时解析为 `archive`。

接着运行研究发现或评估流程。LLM 调用前只验证行情与 discovery 分区；
screened→candidate 验证 PIT 成分、供应商流通市值、PIT 行业和 selection 分区；
密封验收只读取 sealed 分区；tradability/release 再验证完整执行与风险合同。
`qfactor library-export-candidates` 仅向非交易多因子研究提供 `tradable=false` 的
统计候选；交易模块只能读取 `qfactor export-trading-releases` 的 active release。

> **当前仓库状态：** 内置行情和现有快照数据未满足本合同。因此 `qfactor export-trading-releases` 与 `qfactor library-export-multifactor` 应继续输出零个可用因子。这是预期的 fail-closed 结果，不是可通过调低阈值解决的问题。

## 5. 不允许的替代做法

不能用今日 CSI100 成分回填历史，也不能以 BaoStock 行情缺失推断完整停牌、ST 或涨跌停状态。不能以当日成交额替代已知的 20 日 ADV，也不能以最新行业映射替代历史分类。任何此类替代仅可作为研究诊断字段，并必须在数据版本中披露，不能满足 production/release 合同。
