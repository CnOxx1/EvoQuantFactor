# Backend API（前端对接）

基址默认：`http://localhost:8080`

## 业务流程

```text
前端上传研报
  → POST /api/v1/reports
  → POST /api/v1/jobs          （用 report_id 创建任务，后台跑流水线）
  → 轮询 GET /api/v1/jobs/{job_id}
  → 完成后来：
       GET /api/v1/jobs/{job_id}/factors   （最终因子公式）
       GET /api/v1/jobs/{job_id}/steps     （每一步记录，供时间线展示）
```

也支持一步创建：`POST /api/v1/jobs/from-upload`（multipart 上传并直接开跑）。

## 接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/api/v1/meta` | 门槛、角色、提示词等元信息 |
| POST | `/api/v1/reports` | 上传研报（文件或纯文本） |
| GET | `/api/v1/reports/{report_id}` | 研报元信息 |
| GET | `/api/v1/reports/{report_id}/content` | 原文 + 资讯摘要 |
| GET | `/api/v1/reports?suitability=` | 列表；`factor` / `news_only` |
| POST | `/api/v1/reports/collect/run` | 手动触发多源采集 |
| GET | `/api/v1/reports/collect/status` | 采集状态（含按源统计、摘要队列） |
| POST | `/api/v1/reports/titles/backfill` | 坏标题回填 |
| GET | `/api/v1/reports/summarize/status` | 摘要队列状态 |
| POST | `/api/v1/reports/summarize/backfill` | 未摘要批量入队 |
| POST | `/api/v1/jobs` | 基于 report_id / 文本创建分析任务 |
| POST | `/api/v1/jobs/from-upload` | 上传并创建任务 |
| GET | `/api/v1/jobs` | 任务列表 |
| GET | `/api/v1/jobs/{job_id}` | 任务状态与摘要 |
| GET | `/api/v1/jobs/{job_id}/factors` | 因子公式（SAVE + 淘汰，默认含 candidates） |
| GET | `/api/v1/jobs/{job_id}/steps` | **逐步记录列表** |
| GET | `/api/v1/jobs/{job_id}/steps/{step_id}` | 单步详情 |
| GET | `/api/v1/jobs/{job_id}/result` | 完整结果包（因子+步骤摘要+落选） |
| POST | `/api/v1/jobs/{job_id}/rerun` | 再次分析 |
| GET | `/api/v1/factor-library/packs` | 因子库包：alpha101 / workspace / dropped |
| GET | `/api/v1/factor-library/{pack_id}/factors` | 因子库分页搜索 |

## 任务状态

`queued` → `running` → `succeeded` | `failed`

`progress` 示例：`{"phase":"review","round":1,"message":"六角色并行评审"}`

## 步骤类型（steps）

| step_type | 含义 |
|-----------|------|
| `ingest` | 研报入库 |
| `step1_extract` | 因子提取 |
| `step2_review` | 某角色评审（含 role_code） |
| `step2_merge` | 评分合并 |
| `step3_gate` | 门槛裁决 |
| `revise_loop` | 回灌修订开始 |
| `persist` | 结果落盘 |
| `error` | 失败信息 |

前端时间线可按 `seq` / `created_at` 排序展示。

## 本地启动

```bash
cd backend
pip install -e ..
# 或在仓库根目录
pip install -e .
uvicorn factor_backend.main:app --app-dir backend --reload --port 8080
```

仓库根目录推荐：

```bash
pip install -e .
uvicorn factor_backend.main:app --reload --port 8080
```

（`factor_backend` 包位于 `backend/factor_backend`。）

## 资讯采集

后端启动时若 `REPORT_COLLECTOR_ENABLED=true`，会按 `REPORT_COLLECTOR_INTERVAL_SEC`（默认 600s）定时拉取多源资讯；也可 `POST /api/v1/reports/collect/run`。入库后自动资讯摘要，不自动跑因子图。

详见仓库 [docs/资讯分析与采集.md](../docs/资讯分析与采集.md)。
