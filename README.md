# EvoQuantFactor

研报 / 因子库 → LangGraph 流水线提取与六角色评审 → 门槛入库 / 回灌优化。

仓库：https://github.com/CnOxx1/EvoQuantFactor

## 功能概览

- **研报分析**：上传研报文本，提取可落地因子公式
- **因子库优化**：从 Alpha101 等因子库选中因子，走评估与修订闭环
- **六角色隔离评审**：量化 / 基本面 / 风控等角色独立打分后代码门槛裁决
- **提示词可配**：前端「提示词管理」热更新各步骤 system/user 模板
- **任务回放**：逐步记录 Step1 → Review → Gate → Persist，支持再次分析

## 快速开始

### 1. 后端

```bash
# Python >= 3.11
pip install -e ".[dev]"
cp .env.example .env

# 本地默认端口（若 8080 被占用可用 18081）
uvicorn factor_backend.main:app --host 127.0.0.1 --port 18081
```

- 健康检查：http://127.0.0.1:18081/health  
- API 文档：http://127.0.0.1:18081/docs  

开发默认可 `AUTH_DISABLED=true`。LLM 可在前端「LLM 配置」写入，或设 `LLM_MOCK=true` 联调。

### 2. 前端

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5174
```

浏览器打开 http://127.0.0.1:5174 （Vite 已将 `/api` 代理到后端）。

### 3. Docker（可选）

```bash
cp .env.example .env
# 编辑 .env，至少配置 LLM_API_KEY（或 LLM_MOCK=true）
./scripts/bootstrap.ps1   # 或 ./scripts/bootstrap.sh
```

## 目录结构

```text
backend/factor_backend/   # FastAPI + LangGraph 流水线
frontend/                # Vue3 + Naive UI
prompts/                 # Agent 提示词 JSON
config/                  # 默认业务配置
data/factor_library/     # 因子库（如 Alpha101）
docs/                    # 设计与 API 说明
tests/                   # 后端测试
scripts/                 # 一键脚本
```

## 主流程 API

```text
单份：POST /api/v1/reports → POST /api/v1/jobs → 轮询 job
优化：POST /api/v1/jobs  { mode: evaluate, seed_factors: [...] }
多份：POST /api/v1/batches 或 /batches/from-upload → 轮询 batch
```

- 因子结果：`GET /api/v1/jobs/{id}/factors`
- 步骤回放：`GET /api/v1/jobs/{id}/steps`
- 再次分析：`POST /api/v1/jobs/{id}/rerun`

## 文档

- [API-前端对接](docs/API-前端对接.md)
- [开发方案 LangGraph+Docker](docs/开发方案-LangGraph-Docker.md)
- [流程设计](docs/因子提取评审流程设计.md)
- [因子优化流程](因子优化流程设计.md)
- [一键部署](docs/一键部署与打包.md)

## 安全说明

- 不要提交 `.env` 与 `data/factor.db`
- 生产请关闭 `AUTH_DISABLED`，并设置 `API_TOKEN`
