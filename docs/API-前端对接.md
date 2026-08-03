# 前端对接 API 说明（鉴权 / DB / LLM 配置）

基址：`http://<host>:8080`  
OpenAPI：`/docs`

## 鉴权

除 `/health`、`/api/v1/meta` 外，业务接口需要：

```http
Authorization: Bearer <API_TOKEN>
```

本地开发可在 `.env` 设 `AUTH_DISABLED=true`。  
生产：`AUTH_DISABLED=false` + 强随机 `API_TOKEN`。

## LLM 配置（前端配置入口）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/llm/config` | 读取配置（api_key 脱敏） |
| PUT | `/api/v1/llm/config` | 保存配置 |
| POST | `/api/v1/llm/test` | 探测当前配置是否可用 |

### PUT 示例

```json
{
  "enabled": true,
  "use_mock": false,
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-xxx",
  "model_step1": "gpt-4o",
  "model_review": "gpt-4o-mini",
  "timeout_sec": 120,
  "max_retries": 2
}
```

- `use_mock=true`：不调真实模型（联调）
- `use_mock=false` 且配置了 `api_key`：流水线走真实 LLM
- 再次 PUT 时 `api_key` 传 `***` 或不传，表示不修改原 Key

## 提示词与权重（前端可配）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/prompts` | 列表（含各角色 weights） |
| GET | `/api/v1/prompts/{key}` | 详情（system / template / weights） |
| PUT | `/api/v1/prompts/{key}` | 更新提示词与权重 |
| POST | `/api/v1/prompts/{key}/reset` | 恢复文件默认 |

`key`：`step1_extract` / `step1_optimize` / `R1`…`R6` / `_shared_mcp`

权重示例：

```json
{ "weights": { "Logic": 20, "Implementability": 25, "Edge": 15, "Robustness": 25, "Tradability": 5, "Novelty": 10 } }
```

服务端会按权重重算 `total_score`（不信任模型自报总分）。六角色评审使用 `asyncio.gather` 并行。

## 取消 / 超时

```http
POST /api/v1/jobs/{job_id}/cancel
```

- `queued`：立即 `cancelled`
- `running`：置 `cancel_requested`，worker 中断
- 超时：`JOB_TIMEOUT_SEC`（或创建任务时 `timeout_sec`）→ `timed_out`
- 多机：领取用 CAS（`status=queued` 条件更新）；僵死 running 会被 reclaim

## MCP

`MCP_ENABLED=true` 时角色可按 `prefer_tools` 调用 stub MCP（返回可追溯占位，不编造真实行情）。后续替换 `factor_backend/mcp/client.py` 即可接真服务。

## 批量处理多份研报

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/batches` | JSON：多份文本 / 多个 report_id 入队 |
| POST | `/api/v1/batches/from-upload` | multipart 一次上传多个文件 |
| GET | `/api/v1/batches/{batch_id}` | 批次进度（counts / percent / jobs） |
| GET | `/api/v1/batches` | 批次列表 |
| POST | `/api/v1/batches/{batch_id}/cancel` | 取消整批 |

```json
{
  "title": "本周研报包",
  "items": [
    { "title": "研报A", "content": "..." },
    { "title": "研报B", "content": "..." }
  ],
  "max_round": 3
}
```

或：`{ "report_ids": ["rpt_xxx", "rpt_yyy"] }`

并发：`WORKER_CONCURRENCY`（默认 3）控制同时跑几份。

## 资讯分析 / 自动采集

多源入库（定时默认 **10 分钟** + 手动同步）→ 自动 **资讯摘要**；因子流水线仍需人工点「因子」。

完整专题（流程、字段、状态、排障、已知限制）：[资讯分析与采集](资讯分析与采集.md)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/reports` | 列表：`q` / `source` / `suitability` / `limit` / `offset` |
| GET | `/api/v1/reports/{id}` | 元信息 |
| GET | `/api/v1/reports/{id}/content` | 原文 + `news_summary*` + `text_incomplete` + `pdf_url` |
| POST | `/api/v1/reports/{id}/summarize` | 重跑资讯摘要；`force` |
| POST | `/api/v1/reports/summarize/backfill` | 未摘要批量入队；`limit` / `only_missing` |
| GET | `/api/v1/reports/summarize/status` | 摘要队列背压统计 |
| POST | `/api/v1/reports/titles/backfill` | 坏标题回填；`limit` |
| POST | `/api/v1/reports/{id}/refetch-pdf` | 东财 PDF 重抓（优先 http） |
| POST | `/api/v1/reports` | 上传文件入库（入队摘要） |
| POST | `/api/v1/reports/text` | 纯文本入库（入队摘要） |
| POST | `/api/v1/reports/collect/run` | 手动触发一轮采集（同步、可能较久） |
| GET | `/api/v1/reports/collect/status` | 采集状态：`source_stats` / `luobo_*` / `summarize` 等 |

### 源与环境变量（摘要）

| 源 key | 入库 `source` | 说明 |
|--------|---------------|------|
| `eastmoney_report` | `eastmoney` | 东财研报 + PDF |
| `eastmoney_news` | `eastmoney_news` | 东财资讯栏目 |
| `wallstreetcn` / `sina` / `ths` / `jin10` | 同名 | 见闻 / 新浪 / 同花顺 / 金十（无需登录） |
| `luobo` | `luobo` | 萝卜投研（需 `LUOBO_CLOUD_SSO_TOKEN`） |

- 定时：`REPORT_COLLECTOR_ENABLED`、`REPORT_COLLECTOR_INTERVAL_SEC`（默认 600）
- 源列表：`REPORT_COLLECTOR_SOURCES`
- 去重/PDF/标题：`REPORT_COLLECTOR_FINGERPRINT_DEDUPE`、`REPORT_COLLECTOR_PDF_REFETCH_LIMIT`、`REPORT_COLLECTOR_TITLE_BACKFILL_ON_START`
- 摘要：`NEWS_SUMMARIZE_ENABLED`、`NEWS_SUMMARIZE_WORKERS`（默认 8）、`NEWS_SUMMARIZE_WORKERS_CAP`（默认 32）、`NEWS_SUMMARIZE_QUEUE_MAX`（默认 500）、`NEWS_SUMMARIZE_MAX_RETRIES`
- 提示词：`news_summarize`（提示词管理 → **资讯分析**）
- 列表因子粗筛：`suitability=factor|news_only`（宏观/晨报/正文不完整）

## 业务主流程

```text
1. PUT  /api/v1/llm/config          （前端配置 LLM，可一次）
2. 单份：POST /api/v1/reports → POST /api/v1/jobs
   优化：POST /api/v1/jobs { mode:evaluate, factors:[...] }
   多份：POST /api/v1/batches 或 /batches/from-upload
3. GET  /api/v1/jobs/{id} 或 /api/v1/batches/{batch_id}  轮询
4. GET  /api/v1/jobs/{id}/factors   因子公式（SAVE + 淘汰）
5. GET  /api/v1/jobs/{id}/steps     逐步记录
6. POST /api/v1/jobs/{id}/rerun     再次分析（可选）
```

## 因子库

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/factor-library/packs` | `alpha101` / `workspace`（任务入库）/ `dropped`（淘汰库） |
| GET | `/api/v1/factor-library/{pack_id}/factors` | 分页搜索：`q` / `limit` / `offset` |

落盘规则（任务 `persist`）：

- **SAVE** → `data/factor_library/workspace.json`
- **DROP**（有公式）→ `data/factor_library/dropped.json`（含 `reason`）
- Alpha101 只读，不被任务改写

`GET /api/v1/jobs/{id}/factors`：

- 默认 `include_candidates=true`，同时返回 SAVE 与淘汰因子
- 淘汰项 `status=DROP`（结果包字段名仍兼容 `candidates`）

## 存储与任务

- 存储：SQLAlchemy（默认 SQLite `data/factor.db`，可换 Postgres）
- 任务：DB 队列 + 后台 worker 线程领取 `queued` → `running`
- 步骤/结果均落库，前端可回放
- 因子库可变分区：`workspace.json` / `dropped.json`

## 本地启动

```bash
cp .env.example .env
# 开发可 AUTH_DISABLED=true
pip install -e ".[dev]"
uvicorn factor_backend.main:app --reload --port 18081
```

前端开发常用：`http://127.0.0.1:5174`（代理 `/api` → `18081`）。

更新历史：[更新记录.md](./更新记录.md)