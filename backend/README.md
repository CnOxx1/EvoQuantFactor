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
| POST | `/api/v1/jobs` | 基于 report_id / 文本创建分析任务 |
| POST | `/api/v1/jobs/from-upload` | 上传并创建任务 |
| GET | `/api/v1/jobs` | 任务列表 |
| GET | `/api/v1/jobs/{job_id}` | 任务状态与摘要 |
| GET | `/api/v1/jobs/{job_id}/factors` | **最终因子公式**（SAVE） |
| GET | `/api/v1/jobs/{job_id}/steps` | **逐步记录列表** |
| GET | `/api/v1/jobs/{job_id}/steps/{step_id}` | 单步详情 |
| GET | `/api/v1/jobs/{job_id}/result` | 完整结果包（因子+步骤摘要+落选） |

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
