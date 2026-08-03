# 因子提取前端（Vue 3 + Naive UI）

扁平化左菜单布局，对接后端 `factor_backend` API。

## 环境

需要 Node.js 18+（本机若未安装，请先安装 LTS）。

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

默认：http://127.0.0.1:5174  
开发代理：`/api` → `http://127.0.0.1:18081`（可在 `vite.config.ts` 修改）

## 页面

| 菜单 | 说明 |
|------|------|
| 工作台 | 概览与最近任务 |
| 资讯分析 | 定时/手动多源采集；LLM 摘要；查看原文；人工点「因子」 |
| 研报分析 | 单份上传/粘贴 → 任务列表/详情（因子+步骤） |
| 批量任务 | 多份研报批次 |
| 因子库 | Alpha101 / 任务入库(SAVE) / 淘汰库(DROP)；可优化因子 |
| LLM配置 | 前端配置模型 |
| 提示词管理 | Step1 / 六角色 / **资讯分析** 等分类；可恢复文件默认 |
| 系统设置 | API Token、健康检查 |

## 鉴权

若后端 `AUTH_DISABLED=false`，在「系统设置」填写 `API_TOKEN`。

## 设计色

- 侧栏：`#1e293b`
- 主色：`#0d9488`
- 背景：`#f8fafc`

主项目更新：[docs/更新记录.md](../docs/更新记录.md)
