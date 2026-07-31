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

`key`：`step1_extract` / `R1`…`R6` / `_shared_mcp`

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

## 业务主流程

```text
1. PUT  /api/v1/llm/config          （前端配置 LLM，可一次）
2. 单份：POST /api/v1/reports → POST /api/v1/jobs
   多份：POST /api/v1/batches 或 /batches/from-upload
3. GET  /api/v1/jobs/{id} 或 /api/v1/batches/{batch_id}  轮询
4. GET  /api/v1/jobs/{id}/factors   最终因子公式
5. GET  /api/v1/jobs/{id}/steps     逐步记录
```
2. POST /api/v1/reports             上传研报
3. POST /api/v1/jobs                创建任务（进入 DB 队列，worker 领取）
4. GET  /api/v1/jobs/{id}           轮询
5. GET  /api/v1/jobs/{id}/factors   最终因子公式
6. GET  /api/v1/jobs/{id}/steps     逐步记录
```

## 存储与任务

- 存储：SQLAlchemy（默认 SQLite `data/factor.db`，可换 Postgres）
- 任务：DB 队列 + 后台 worker 线程领取 `queued` → `running`
- 步骤/结果均落库，前端可回放

## 本地启动

```bash
cp .env.example .env
# 开发可 AUTH_DISABLED=true
pip install -e ".[dev]"
uvicorn factor_backend.main:app --reload --port 18081
```
