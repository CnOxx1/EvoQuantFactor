# EvoQuantFactor

研报 / 因子库 → LangGraph 流水线提取与六角色评审 → 门槛入库 / 回灌优化。

仓库：https://github.com/CnOxx1/EvoQuantFactor

## 功能概览

- **资讯分析**：多源定时/手动采集入库 → LLM 资讯摘要（非因子）；可看原文；人工点「因子」开跑流水线  
  → 详见 [资讯分析与采集](docs/资讯分析与采集.md)（默认关闭采集/摘要，需显式开启）
- **研报分析**：上传/粘贴研报文本，提取可落地因子公式
- **因子层门禁**（现网）：六角色 **单因子** 评审 → SAVE/REVISE/DROP → 任务入库 / 淘汰库
- **因子库三分区**：Alpha101（公开公式）/ 任务入库（SAVE）/ 淘汰库（DROP + 原因）
- **因子库优化**：从上述分区勾选因子，走评估与修订闭环（`mode=evaluate`，仍为逐因子）
- **策略层**（规划中，未编码）：见 [归档 · 多因子策略层设计](docs/archive/多因子策略层设计.md)
- **提示词可配**：前端「提示词管理」热更新 system/user 模板
- **任务回放**：逐步记录 Step1 → Review → Gate → Persist

## 更新记录

→ **[docs/更新记录.md](docs/更新记录.md)**

## 快速开始

### 1. 后端

```bash
# Python >= 3.11
pip install -e ".[dev]"
cp .env.example .env
# 本地请设 APP_ENV=development、AUTH_DISABLED=true、LLM_MOCK=true

uvicorn factor_backend.main:app --host 127.0.0.1 --port 18081
# 或：python -m factor_backend api
```

- 健康检查：http://127.0.0.1:18081/health  
- 进程指标：http://127.0.0.1:18081/metrics  
- API 文档：http://127.0.0.1:18081/docs  

独立进程（可选）：

```bash
python -m factor_backend worker      # 任务队列
python -m factor_backend collector   # 资讯采集
python -m factor_backend summarize   # 资讯摘要
```

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
# 编辑 .env：非占位 API_TOKEN；LLM_MOCK=true 或配置 LLM_API_KEY
./scripts/bootstrap.sh
# 拆分 worker/collector：BOOTSTRAP_PROFILE=split ./scripts/bootstrap.sh
```

## 目录结构

```text
backend/factor_backend/   # FastAPI + LangGraph 流水线
frontend/                # Vue3 + Naive UI
prompts/                 # Agent 提示词 JSON
config/default.yaml      # 参考文档（运行时不加载）
data/factor_library/     # Alpha101 + workspace/dropped
docs/                    # 设计 / API / 更新记录
docs/archive/            # 过时或未编码规划
tests/
scripts/
```

## 配置真相源

1. **环境变量 / `.env`** → `factor_backend.config.Settings`（门槛、worker、采集开关等）
2. **SQLite DB** → LLM / 提示词覆盖（前端配置页）
3. `config/default.yaml` **仅作参考，不生效**

## 主流程 API

```text
单份：POST /api/v1/reports → POST /api/v1/jobs → 轮询 job
优化：POST /api/v1/jobs  { mode: evaluate, factors: [...] }
多份：POST /api/v1/batches → 轮询 batch
```

## 文档

| 文档 | 说明 |
|------|------|
| [更新记录](docs/更新记录.md) | changelog |
| [资讯分析与采集](docs/资讯分析与采集.md) | 采集 / 摘要 |
| [API-前端对接](docs/API-前端对接.md) | API |
| [流程设计](docs/因子提取评审流程设计.md) | 因子层门禁 |
| [因子优化流程](docs/因子优化流程设计.md) | evaluate 模式 |
| [一键部署](docs/一键部署与打包.md) | Compose |
| [归档文档](docs/archive/README.md) | 过时/未编码方案 |

## 安全说明

- 不要提交 `.env` 与 `data/factor.db`
- 生产（`APP_ENV=production`）启动校验：`AUTH_DISABLED=false` + 非占位 `API_TOKEN`
- `/health` 返回 `warnings` 与 worker 存活信息
