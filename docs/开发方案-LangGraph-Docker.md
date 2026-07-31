# 开发方案：LangGraph + Docker

> 技术选型已锁定：**编排框架 = LangGraph**，**部署 = Docker / Compose**。  
> 本文是可执行的开发计划，与 [因子提取评审流程设计](./因子提取评审流程设计.md)、[一键部署与打包](./一键部署与打包.md) 配套。

---

## 1. 目标与边界

### 1.1 目标

在多台服务器上以同一镜像运行「研报因子提取 → 六角色并行隔离评审 → 门槛入库 / 回灌修订」流水线；行情数据通过 MCP 按需调用。

### 1.2 非目标（本期不做）

- 不在 Cursor IDE Agent 上做生产调度
- 不做完整回测引擎 / 组合优化
- 不自研行情主数据（由外部 MCP 提供）
- 不做 K8s（有集群需求时另开阶段）

### 1.3 固定约束

| 项 | 约定 |
|----|------|
| 框架 | LangGraph（Python） |
| 部署 | Docker Compose；多服务器 = 同镜像 + 不同 `.env` |
| 提示词 | `prompts/*.json`，编排只加载不硬编码长文 |
| Step3 计分 | **纯代码**（`mean/median/veto`），禁止模型心算 |
| 保存门槛 | `mean≥80` 且 `median≥75` 且 `veto==false` |
| 修订范围 | Step1 只改低分因子；Step2 只重评 `ChangedIds` |
| 隔离 | Step2 六角色独立 LLM 调用，不共享中间结论与 MCP 结果 |

---

## 2. 总体架构

```text
                    ┌─────────────────────────────┐
  HTTP / CLI ──────▶│  FastAPI（app/main.py）       │
                    │  POST /v1/analyze            │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │  LangGraph Pipeline          │
                    │  step1 → fanout R1..R6       │
                    │       → merge → code_gate    │
                    │       → save | revise_loop   │
                    └───────┬───────────┬─────────┘
                            │           │
                   ┌────────▼──┐   ┌────▼─────────┐
                   │ LLM Gateway│   │ Market MCP   │
                   │ OpenAI兼容 │   │ K线/成交量…  │
                   └───────────┘   └──────────────┘
                            │
                   ┌────────▼────────┐
                   │ data/saved 等    │
                   │ （或后续 DB）    │
                   └─────────────────┘
```

**多服务器：** 每台只跑相同 `factor-api` 镜像；差异全部进 `.env`（LLM、MCP、门槛覆盖）。

---

## 3. 目标目录结构

```text
因子/
├── app/
│   ├── main.py                 # FastAPI 入口（已有骨架）
│   ├── settings.py             # 配置
│   ├── router_logic.py         # Step3 确定性门槛（已有）
│   ├── prompts_loader.py       # 加载 prompts JSON + shared MCP 拼接
│   ├── llm/
│   │   ├── client.py           # OpenAI 兼容客户端
│   │   └── roles.py            # 单角色调用封装（隔离）
│   ├── mcp/
│   │   ├── client.py           # MCP 客户端（可先 stub）
│   │   └── tools.py            # get_kline 等工具声明
│   ├── graph/
│   │   ├── state.py            # GraphState TypedDict
│   │   ├── nodes_step1.py
│   │   ├── nodes_review.py     # R1..R6 + fan-out/fan-in
│   │   ├── nodes_gate.py       # 调 router_logic + 组回灌包
│   │   └── build.py            # 编译 StateGraph
│   └── storage/
│       └── factors.py          # SAVE 落盘
├── config/default.yaml
├── prompts/                    # 已有，保持契约稳定
├── tests/
├── docker-compose.yml
├── Dockerfile
└── docs/
    └── 开发方案-LangGraph-Docker.md   # 本文
```

---

## 4. LangGraph 设计

### 4.1 状态（GraphState）

```text
GraphState:
  report: str
  meta: { market, symbols_hint, date_range_hint, ... }
  round: int
  max_round: int
  factors: list[Factor]
  frozen_ids: list[str]
  changed_ids: list[str]
  scorecards: dict[factor_id, { R1..R6: RoleReview }]
  revise_packet: object | null
  batch: { save_ids, revise_ids, drop_ids }
  saved_payloads: list
  errors: list[str]
  mcp_enabled: bool
```

### 4.2 图拓扑

```text
START
  → step1_extract_or_revise
  → review_fanout          # 并行调用 R1..R6（首轮全量 / 其后仅 ChangedIds）
  → review_merge           # 增量合并旧分
  → code_gate              # router_logic 判定 SAVE/REVISE/DROP
  → route:
       ├─ has_revise && round < max_round → 写回 revise_packet, round+=1 → step1_extract_or_revise
       ├─ anti_noop（无 ChangedIds）→ force_drop → END
       └─ else → persist_saved → END
```

### 4.3 节点职责

| 节点 | 类型 | 说明 |
|------|------|------|
| `step1_extract_or_revise` | LLM | 加载 `step1_extract.json`；首轮提取 / 有 packet 则只改低分 |
| `review_r1` … `review_r6` | LLM 并行 | 各加载对应 prompts；独立 session；可绑 MCP 工具 |
| `review_merge` | 代码 | `merge_scorecards` |
| `code_gate` | 代码 | `decide_action` + 组装 `step1_revise_packet` |
| `persist_saved` | 代码 | 写 `data/saved`（或 DB） |

### 4.4 并行与隔离实现要点

- 使用 LangGraph 的并行边，或 `asyncio.gather` 包在单个 `review_fanout` 节点内（二选一，推荐 **一节点内 gather**，状态更好控）。
- 每个角色：`Chat`/`ainvoke` **新建消息列表**，只注入「待评因子 + 研报概览」，禁止注入其他角色输出。
- MCP：按角色 `prefer_tools` 挂载；工具返回只进入该角色上下文。

### 4.5 Step3 禁止事项

- 不要把六份分数丢给 LLM 让它“综合打分”
- `prompts/step3_router.json` 仅作文档/可选文案模板；**生产路径以 `router_logic.py` 为准**

---

## 5. API 与配置契约

### 5.1 HTTP API（开发交付）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 探活（已有） |
| GET | `/config/summary` | 配置与提示词清单（已有） |
| POST | `/v1/decide` | 单测门槛（已有） |
| POST | `/v1/analyze` | **主入口**：研报 → 流水线结果 |
| GET | `/v1/runs/{run_id}` | （可选）查询一次运行结果 |

**`POST /v1/analyze` 请求草案：**

```json
{
  "report": "研报正文...",
  "meta": {
    "market": "CN",
    "symbols_hint": ["600000.SH"],
    "date_range_hint": ["2020-01-01", "2024-12-31"]
  },
  "options": {
    "max_round": 3,
    "mcp_enabled": true
  }
}
```

**响应草案：**

```json
{
  "run_id": "uuid",
  "saved": [ { "factor_id": "F01", "final_score": 83.5, "median_score": 82.0 } ],
  "dropped": [ { "factor_id": "F02", "reason": "..." } ],
  "rounds_used": 2,
  "artifacts": { "factors_final": [], "scorecards": {} }
}
```

### 5.2 环境变量（多服务器差异面）

| 变量 | 含义 |
|------|------|
| `LLM_API_KEY` / `LLM_BASE_URL` | 模型网关 |
| `LLM_MODEL_STEP1` / `LLM_MODEL_REVIEW` | 模型名 |
| `MCP_ENABLED` / `MCP_MARKET_URL` | 行情 MCP |
| `SAVE_MEAN_MIN` / `SAVE_MEDIAN_MIN` / `MAX_ROUND` | 门槛 |
| `APP_PORT` | 对外端口 |

---

## 6. 分阶段开发计划

### Phase 0 — 基线（已完成）

- [x] 流程设计文档 + 提示词 JSON
- [x] Docker Compose / Dockerfile / bootstrap
- [x] FastAPI 骨架 + `router_logic` 单测
- [x] 技术选型锁定：LangGraph + Docker

**退出标准：** 任意机器 `bootstrap` 后 `/health` 为 ok。

---

### Phase 1 — 提示词加载与 LLM 通路（约 3–5 人日）

**任务**

1. 实现 `prompts_loader.py`：读 JSON、拼接 `_shared_mcp.json`
2. 实现 OpenAI 兼容 `llm/client.py`（超时、重试、JSON mode / 结构化解析）
3. 用最小脚本：对 `step1` 跑通「假研报 → JSON 因子」
4. `pyproject.toml` 启用 `orch` 依赖：`langgraph`、`langchain-openai`（或官方推荐组合）

**退出标准**

- 本地与 Docker 内均可完成 1 次 Step1 调用
- 解析失败有明确错误，不静默吞掉

---

### Phase 2 — LangGraph 主干（无 MCP）（约 5–8 人日）

**任务**

1. 定义 `GraphState`，实现 `build.py` 编译图
2. Step1 节点 + 六角色并行节点 + merge + code_gate + 回灌边
3. 接上 `POST /v1/analyze`
4. 落盘 `data/runs/{run_id}.json` 与 `data/saved/`
5. 单测：门槛、合并、回灌轮次上限、防空转

**退出标准**

- 给定样例研报，能跑完 ≥1 轮全流程
- 六角色调用互不注入对方输出（代码审查 + 日志断言）
- Docker 镜像内可复现

---

### Phase 3 — MCP 按需接入（约 3–5 人日，可与行情团队并行）

**任务**

1. MCP client stub → 真实 SSE/HTTP（按最终协议）
2. 工具：`get_kline` / `get_volume` 等按 `prefer_tools` 挂到角色
3. `MCP_ENABLED=false` 时全链路仍可用（`data_unavailable`）
4. Compose `full` profile 指向真实/模拟 MCP 镜像

**退出标准**

- 开关 MCP 不改代码只改配置
- 证据写入各角色 `mcp_evidence`，且不泄漏到其他角色

---

### Phase 4 — 多服务器硬化（约 2–4 人日）

**任务**

1. 完善健康检查、就绪检查、结构化日志（run_id）
2. 资源与超时：角色并发上限、单角色超时、整 run 超时
3. `.env` 模板按环境分：`env.dev` / `env.prod.example`（仍不提交密钥）
4. 离线包脚本：`scripts/export-image.sh`（save/load 说明写入部署文档）
5. （可选）简单鉴权：`API_TOKEN`

**退出标准**

- 两台不同 LLM_BASE_URL 的服务器用同一镜像跑通
- 文档可让非开发同学按 README 独立部署

---

### Phase 5 — 增强（排期可选）

- 持久化改为 Postgres / 对象存储
- 运行队列（Redis）与异步 job
- 管理端：查看 saved 因子、重跑某 run
- 可观测：OpenTelemetry / Prometheus
- K8s Helm（仅当运维标准要求）

---

## 7. 测试策略

| 层级 | 内容 |
|------|------|
| 单元 | `decide_action`、`merge_scorecards`、prompts 加载、JSON 解析 |
| 契约 | 各 Agent `output_schema` 字段校验（pydantic 模型） |
| 集成 | Mock LLM：固定返回，断言回灌与 SAVE |
| 容器 | `docker compose up` 后 curl `/health`、`/v1/analyze`（mock 模式） |
| 隔离回归 | 断言 review 请求 payload 不含其他角色 score/comment |

开发期提供 `LLM_MOCK=true`：不走外网，便于 CI。

---

## 8. 开发环境与日常命令

```bash
# 本地
python -m venv .venv
pip install -e ".[orch,dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8080

# 测试
pytest -q

# 容器（与服务器一致）
./scripts/bootstrap.ps1    # 或 bootstrap.sh
```

**原则：** 本地可以无 Docker 开发；**合并进主分支的行为必须以容器路径验证一次。**

---

## 9. 里程碑与 Definition of Done

| 里程碑 | DoD |
|--------|-----|
| M1 通路 | Step1 真实/ Mock LLM 出结构化因子 |
| M2 闭环 | LangGraph 全图 + 门槛 + 回灌 + 落盘 |
| M3 可部署 | 任意服务器 Compose 一键起，换 `.env` 即换环境 |
| M4 可插拔行情 | MCP 开关可用，证据可追溯 |
| M5 生产就绪 | 超时/日志/鉴权/双机验证通过 |

单功能 DoD：有测试、有日志字段 `run_id`、有配置项、不破坏 Docker 一键启动。

---

## 10. 风险与对策

| 风险 | 对策 |
|------|------|
| 六角色成本高、耗时长 | 增量只重评 ChangedIds；review 用较小模型；设并发与超时 |
| 模型 JSON 不稳定 | JSON schema / 重试 / 校验失败回灌「补全输出」 |
| 角色串味 | 代码层禁止共享；单测锁 payload |
| MCP 延期 | 默认关闭，不堵主链路 |
| 多服务器模型差异 | 统一走公司网关；版本记入 run 元数据 |
| Step3 被改回 LLM | Code review 门禁：gate 节点不得调 LLM |

---

## 11. 人员与分工建议

| 角色 | 负责 |
|------|------|
| 后端 A | LangGraph 图、API、落盘 |
| 后端 B | LLM 客户端、prompts 加载、JSON 校验 |
| 数据/行情 | MCP 服务与契约 |
| 量化/研究 | 提示词迭代、样例研报验收 |
| 运维 | 镜像仓库、服务器 `.env`、网络放行 |

若 1 人：按 Phase 1 → 2 → 4 → 3 顺序（先闭环再 MCP）。

---

## 12. 近期两周排期（示例）

| 天 | 产出 |
|----|------|
| D1–D2 | prompts_loader + LLM client + Mock |
| D3–D5 | GraphState + step1 + gate + 单轮 analyze |
| D6–D8 | 六角色并行 + 回灌循环 + 落盘 |
| D9–D10 | Docker 回归、双配置验证、补测试与文档 |

---

## 13. 相关文档

- [因子提取评审流程设计](./因子提取评审流程设计.md)
- [一键部署与打包](./一键部署与打包.md)
- 根目录 [README.md](../README.md)
- 提示词索引：`prompts/index.json`

---

*版本：v1.0 | 选型：LangGraph + Docker*
