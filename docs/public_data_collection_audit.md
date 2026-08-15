# 公开数据采集与生产合同审计

**采集时间：** 2026-08-15 UTC
**覆盖窗口：** 2024-01-02 至 2026-06-30
**范围：** 中证A100（中证100，`000903`）现有研究面板中的 100 只证券。

## 已实际获取的公开数据

| 数据类别 | 来源与方法 | 实测结果 | 证据等级 | 是否可解除 production gate |
|---|---|---:|---|---|
| 官方指数快照 | 中证指数官网 `index-details-data` API 下载样本与权重 XLS | 样本 100 行；权重 100 行；已记录 SHA-256 | 官方**当前**快照 | 否；不能回填历史日期 |
| 换手、交易状态、ST、推导容量 | BaoStock 公共 API | 60,100 行；100 只证券；无请求失败 | 研究 | 否；无供应商认证市值与显式涨跌停价 |
| 20 日 ADV | 按 BaoStock 成交额滚动计算 | 覆盖率 96.47% | 研究 | 否；容量输入为派生值 |
| 公司行动候选 | 原始与前复权收盘价比率的材料变化 | 60,100 行；323 个材料变化候选 | 研究 | 否；不是正式公司行动事件账本 |
| 行业 | BaoStock 当前行业快照按日期复制 | 60,100 行；字段非空覆盖 100% | 研究 | 否；不是 PIT 行业历史 |
| 风险暴露 | 基于公开日收益计算 60 日波动率与市场 beta | 60,000 行；波动率覆盖 93.50%；beta 覆盖 93.40% | 透明内部研究模型 | 否；使用非 PIT 研究宇宙 |

> **关键控制：** 所有新增文件均写入 `data/raw/research/`，没有写入 `data/raw/providers/`，也没有更新可被 production adapter 消费的 archive 配置。因此它们不会错误解除 PIT、公司行动、行业、风险或 release 合同。

## 生产合同检查结果

当前项目的数据版本仍为 `20260812T095118Z`。其已知限制为：中证指数文件是最新快照而不是历史重建，流通市值由成交额/换手估算。新增公开研究证据没有也不应改变这些限制。

| 必要条件 | 当前状态 | 阻断原因 |
|---|---|---|
| PIT 历史中证100成分 | 未满足 | 官网公开当前样本与权重；旧金融界历史端点已失效；官网当前未提供该指数的拟生效历史文件。 |
| 供应商验证的日频流通市值 | 未满足 | BaoStock 的流通股本/市值由公开成交与换手推导。 |
| 显式涨跌停价 | 未满足 | BaoStock 公共接口未返回逐日 `limit_up` / `limit_down`。 |
| 完整公司行动事件账本 | 未满足 | 仅有价格比率派生的候选事件。 |
| PIT 行业 | 未满足 | 当前行业快照不可证明历史时点分类。 |
| PIT 风险暴露 | 未满足 | 内部滚动估计基于非 PIT 研究宇宙。 |
| 三段时间分区 | 未满足 | 现有数据版本未创建完整 discovery / selection / sealed 分区证据。 |

因此，**Candidate、sealed、tradability 和 active release 必须继续为零**。这是预期的 fail-closed 行为，而非采集脚本失败。

## 来源与可复核链接

1. [中证指数官网中证A100详情](https://www.csindex.com.cn/#/indices/family/detail?indexCode=000903)，及其公开资料 API：`https://www.csindex.com.cn/csindex-home/indexInfo/index-details-data?fileLang=2&indexCode=000903`。
2. [中证A100官方样本 XLS](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/000903cons.xls)。
3. [BaoStock Python API](https://pypi.org/project/baostock/)。
4. [中证指数官网数据服务](https://www.csindex.com.cn/#/dataService/indexConstituent)。该页面提供当前证券所属指数及上月末权重查询，不提供历史日期选择。

## 后续自动化策略

公开采集脚本会保留在仓库中，可持续刷新当前样本、执行状态、研究 ADV、派生公司行动和内部风险诊断。只有取得可复核的 PIT 历史成分与对应的正式执行/公司行动/行业证据后，才将经过验证的文件提升到 `data/raw/providers/` 并重新同步生产数据版本。
