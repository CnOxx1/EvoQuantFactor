# EvoQuantFactor

研报 / 因子库 → LangGraph 流水线提取与六角色评审 → 门槛入库 / 回灌优化。

仓库：https://github.com/CnOxx1/EvoQuantFactor

## 功能概览

- **资讯分析**：多源定时/手动采集入库 → LLM 资讯摘要（非因子）；可看原文；人工点「因子」开跑流水线  
  → 详见 [资讯分析与采集](docs/资讯分析与采集.md)
- **研报分析**：上传/粘贴研报文本，提取可落地因子公式
- **因子层门禁**（现网）：六角色 **单因子** 评审 → SAVE/REVISE/DROP → 任务入库 / 淘汰库
- **因子库三分区**：Alpha101（公开公式）/ 任务入库（SAVE）/ 淘汰库（DROP + 原因）
- **因子库优化**：从上述分区勾选因子，走评估与修订闭环（`mode=evaluate`，仍为逐因子）
- **策略层**（规划中）：多因子合成与策略级评估，见 [多因子策略层设计](docs/多因子策略层设计.md)；**不改造**现有单因子主链路
- **提示词可配**：前端「提示词管理」（含分类 **资讯分析**）热更新 system/user 模板
- **任务回放**：逐步记录 Step1 → Review → Gate → Persist

## 更新记录

近期变更请看专题模块（按时间倒序追加）：

→ **[docs/更新记录.md](docs/更新记录.md)**

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
data/factor_library/     # Alpha101 + workspace/dropped 运行时库
docs/                    # 设计 / API / 更新记录
tests/                   # 后端测试
scripts/                 # 一键脚本
```

## 主流程 API

```text
单份：POST /api/v1/reports → POST /api/v1/jobs → 轮询 job
优化：POST /api/v1/jobs  { mode: evaluate, factors: [...] }
多份：POST /api/v1/batches 或 /batches/from-upload → 轮询 batch
```

- 因子结果：`GET /api/v1/jobs/{id}/factors`（含 SAVE 与淘汰）
- 步骤回放：`GET /api/v1/jobs/{id}/steps`
- 再次分析：`POST /api/v1/jobs/{id}/rerun`
- 因子库包：`GET /api/v1/factor-library/packs`

## 文档

| 文档 | 说明 |
|------|------|
| [更新记录](docs/更新记录.md) | **主项目更新模块（changelog）** |
| [资讯分析与采集](docs/资讯分析与采集.md) | 多源采集 / 定时器 / 摘要 / 环境变量 |
| [API-前端对接](docs/API-前端对接.md) | 鉴权 / LLM / 任务 / 资讯 / 因子库 API |
| [开发方案 LangGraph+Docker](docs/开发方案-LangGraph-Docker.md) | 架构与部署方案 |
| [流程设计](docs/因子提取评审流程设计.md) | 因子层：提取 → 评审 → 门禁 |
| [因子优化流程](因子优化流程设计.md) | evaluate 模式与因子库交互 |
| [多因子策略层设计](docs/多因子策略层设计.md) | 策略层 MVP 实现计划（未编码） |
| [一键部署](docs/一键部署与打包.md) | Compose / 打包 |

## 安全说明

- 不要提交 `.env` 与 `data/factor.db`
- 生产请关闭 `AUTH_DISABLED`，并设置 `API_TOKEN`
