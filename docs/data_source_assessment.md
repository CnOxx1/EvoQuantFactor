# 非 Tushare 数据源核查与覆盖边界

**核查日期：** 2026-08-15（UTC）  
**适用范围：** EvoQuantFactor 的中证100日频量价因子生产合同。

## 结论

公开来源已经能够补齐一部分**研究层**数据，但当前没有任何已连接的专业数据服务能够提供完整、可审计的中证100 PIT 成分、明确涨跌停价、公司行动、点时行业和风险暴露。因此，公开数据导入后可改善研究诊断与后续供应商交叉核验，但不能解除 `production`、`sealed acceptance` 或 `active release` 的 fail-closed 阻断。

| 数据类别 | 已验证来源 | 实际可用字段 | 生产级限制 | 处理方式 |
|---|---|---|---|---|
| 日频状态与换手 | BaoStock 公共 API | 换手率、交易状态、ST 标记、成交量、成交额 | 不提供显式日涨跌停价；状态来源不等同于经审核交易所执行数据 | 导出为 `research_only` 证据 |
| 流通股本/市值与 ADV | BaoStock 公共 API 衍生 | 由成交量/换手率推导的流通股本、推导流通市值、20日 ADV | 推导值不是供应商认证的日频流通市值 | 仅作 reconciliation，不标为 `archive_daily_basic` |
| CSI100 当前成分 | 中证指数官方 XLS | 当前成分和权重快照 | 文件通常为最新快照，不能回填历史 | 保持 snapshot/research 用途 |
| PIT 历史成分 | 中证指数调整公告/经授权供应商 | 未找到可自动、完整下载的历史时间序列 | 最新成分与调整公告不能单独证明每个交易日的完整宇宙 | 仍为硬缺口 |
| 公司行动 | BaoStock 复权因子接口/交易所公告 | 可获取部分复权因子记录 | 不能形成完整逐日事件或 `none` 覆盖 | 仍为硬缺口 |
| PIT 行业及风险暴露 | 专业风险模型或版本化供应商导出 | 无已连接来源 | 静态行业图不符合 PIT 合同 | 仍为硬缺口 |

## 已实际导出的公共研究证据

在云电脑上，脚本 `scripts/export_baostock_research_evidence.py` 已针对当前 100 个证券导出 2024-01-02 至 2026-06-30 的数据：

- `data/raw/research/baostock_execution/daily_basic_baostock_research.parquet`
- `data/raw/research/baostock_execution/security_status_baostock_research.parquet`
- `data/raw/research/baostock_execution/provenance.json`

导出器以 `research_only` 标记所有记录，并把无显式涨跌停价、无 PIT 成分历史、无认证市值、无完整公司行动、无 PIT 行业/风险暴露写入 provenance。它不会覆盖 `data/raw/providers/` 下的生产归档。

> 不能将由成交量与换手率反推的流通股本，或由价格数据推断的涨跌停，伪装为供应商点时证据。这样会把数据缺口隐藏起来，破坏后续密封验收和交易 release 的可追溯性。

## 生产解锁的最小外部交付

生产升级仍需一个可审计的数据包，至少包括：历史中证100成员生效/失效记录、日频供应商流通市值和股本、ST/停牌/日涨跌停价、公司行动事件和复权链、点时行业及风险暴露。文件格式及覆盖阈值见 [`production_data_contract.md`](production_data_contract.md)。

## 参考来源

1. [BaoStock PyPI 项目页](https://pypi.org/project/baostock/)：其 `query_history_k_data_plus` 示例列出 `turn`、`tradestatus` 与 `isST` 字段。
2. [中证指数当前成分文件端点](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/000903cons.xls)：用于当前快照，不代表历史 PIT 宇宙。
3. [AKShare 指数数据文档](https://akshare.akfamily.xyz/data/index/index.html)：记录公开指数/成分数据接口范围。

## 可满足生产合同的替代 API 候选

### RQData / RiceQuant

官方 RQData 文档显示，该服务需要先申请试用或订阅并配置 license。其数据接口覆盖生产合同所需的多数核心字段：`get_price` 支持历史 `limit_up`、`limit_down`、成交额与成交量；`is_suspended` 与 `is_st_stock` 支持停牌和 ST 判断；`get_shares` 提供流通 A 股；`get_dividend` 与 `get_split` 提供公司行动；并提供行业分类、历史行情和指数成分相关接口。基于这些公开接口说明，RQData 是当前最短的非 Tushare 生产级替代候选，但需要用户提供合法 license。

### JoinQuant / JQData

聚宽的官方 `get_index_stocks(index_symbol, date=None)` 文档说明可按指定日期获得指数成分股，适合构建 PIT 宇宙；但当前云电脑的海外出口被聚宽官网阻断，且当前会话没有用户提供的 JQData 凭证。因此不能在未经授权的情况下调用或将其作为已验证数据源。

| 提供方 | PIT 成分 | 涨跌停价 | ST/停牌 | 流通股本/换手 | 公司行动 | 行业 | 所需条件 |
|---|---:|---:|---:|---:|---:|---:|---|
| RQData | 是（需用户 license 验证） | 是 | 是 | 是 | 是 | 是 | 用户提供合法 license/试用授权 |
| JQData | 是（按 date 查询） | 需凭证验证 | 需凭证验证 | 需凭证验证 | 需凭证验证 | 需凭证验证 | 用户提供 JQData 凭证，且网络可访问 |
| BaoStock 公共 API | 否 | 否 | 部分 | 推导值 | 不完整 | 当前静态 | 无凭证；仅研究证据 |
| 中证指数公开 XLS | 仅当前 | 否 | 否 | 否 | 否 | 否 | 无凭证；仅当前快照 |

### 补充参考来源

4. [RQData/RQAlpha 扩展 API 文档](https://rqalpha.readthedocs.io/zh-cn/latest/api/extend_api.html)：明确列出 `limit_up`、`limit_down`、停牌、ST、流通股本、行业、拆分等接口，并说明 RQDatac 需申请 license。
5. [RiceQuant 数据查询接口](https://www.ricequant.com/doc/rqalpha-plus/api/data-api)：说明其中国市场合约和历史行情数据范围。
6. [JoinQuant JQData 文档](https://www.joinquant.com/help/api/doc?name=JQDatadoc&id=9958)：`get_index_stocks(index_symbol, date=None)` 的官方接口入口；本次云电脑出口返回地区访问限制页面。

## 公开 PIT 重建线索：官方调样附件

2025-05-30 的证券时报报道嵌入了中证指数有限公司公告截图，显示“关于沪深300、中证500、中证1000、中证A500等指数定期调整结果的公告”，并明确提示相关指数的“部分指数样本调整名单”见附件；报道正文确认中证A100在 2025-06-16 生效的定期调整中更换 5 只样本。页面正文没有可直接抽取的附件超链接，需从页面内嵌图片/原始公告继续定位附件。该线索可用于获取正式调入/调出名单，再从版本化快照反向重建 PIT 宇宙；在未取得所有变更期的完整名单之前，不能将重建结果标记为完整 PIT。

来源： [证券时报，2025-05-30](https://stcn.com/article/detail/1852878.html)。

## 中证指数官网技术核查

中证指数官网前端脚本公开显示指数详情页面具备“相关资料”“样本调整名单”“指数快照”等功能文案；官网当前成分 XLS 仍可直接下载。前端是单页应用，直接页面渲染在云端浏览器为空，需继续从前端 API 路由或公告附件定位对应期次的正式名单。旧 AKShare `index_stock_hist` 依赖的金融界页面 `stock.jrj.com.cn/share,sh000903.shtml` 当前返回 404，不能作为数据源。

来源： [中证指数官网](https://www.csindex.com.cn/)；[AKShare 变更记录](https://akshare.akfamily.xyz/changelog.html)（`index_stock_hist` 已移除）；[金融界旧端点](https://stock.jrj.com.cn/share,sh000903.shtml)（本次探测返回 404）。

## 已验证的中证指数官方 API

通过官网单页应用静态资源反查，已验证下列公开端点：

- `GET https://www.csindex.com.cn/csindex-home/indexInfo/index-basic-info/000903` 返回中证A100（代码 `000903`）的正式基本资料，包含指数全称、发布日期、基日和半年调样频率。
- `POST https://www.csindex.com.cn/csindex-home/indexInfo/index-sample-information` 是官网样本表接口；在未知完整请求字段时返回空分页结构，尚未获得历史样本。不能据此将当前成分误标为 PIT。
- 官网前端公开存在 `indexInfo/index-details-data`、`exportExcel/index-sample-information-excel/`、`announcement/selectNoticeRe` 以及资料下载相关路由，表明正式资料与样本下载功能在官网中存在，正在进一步定位参数与附件 ID。

官网中证A100详情页：<https://www.csindex.com.cn/#/indices/family/detail?indexCode=000903>。

## 官网数据服务范围核验

中证指数官网的“证券所属指数检索”页面用于按证券代码查询其当前所属指数及“上月末数据”的权重；页面没有历史日期选择器。因此该公开服务不能替代逐日 PIT 成分历史。中证A100详情页的 `indexInfo/index-nicons` 接口当前返回 `拟生效文件=null` 与 `拟生效历史文件=null`，说明在本次抓取时官网未对该指数公开可下载的历史拟生效样本文件。

## 新浪财经成分历史核查

新浪财经提供中证A100的“最新成分”和“历史成分”页面：

- 最新成分页列出当前 100 只成分及每只证券的纳入日期；可作为与中证指数当前官方快照的公开交叉核对。
- 历史成分页提供历史纳入/剔除日期，但本次可获取内容最后更新到 2012 年，未包含 2024–2026 年调出名单，不能单独重建研究窗口内的 PIT 成分。
- 互联网档案馆中该页面的可用快照仅为 2011、2015、2022 年，未覆盖所需的 2024–2026 年度截面。

来源：[新浪中证A100最新成分](http://vip.stock.finance.sina.com.cn/corp/go.php/vII_NewestComponent/indexid/000903.phtml)，[新浪中证A100历史成分](http://vip.stock.finance.sina.com.cn/corp/go.php/vII_HistoryComponent/indexid/000903.phtml)，[Internet Archive CDX](https://web.archive.org/cdx/search/cdx?url=vip.stock.finance.sina.com.cn/corp/go.php/vII_NewestComponent/indexid/000903.phtml&output=json&filter=statuscode:200&fl=timestamp,original,statuscode,digest&collapse=digest)。

## BaoStock 正式公司行动数据

实际探测显示 BaoStock 的 `query_dividend_data()` 提供除权除息的预披露、方案、登记、除权和派息日期及现金/送转字段；`query_adjust_factor()` 提供逐次除权日的前复权、后复权和复权因子。该公开数据可升级公司行动的**研究证据**，但不自动证明指数成员、点时行业或完整代码变更，并仍受公共服务的可用性与字段覆盖限制约束。
