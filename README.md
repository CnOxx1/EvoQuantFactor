# qfactor

中证100（中证A100 / 000903）日频量价因子库。Python + FastAPI Web UI。

## 数据库（后续模块统一读这里）

默认 SQLite：`data/qfactor.sqlite3`

```bash
python -m qfactor.cli db-init
python -m qfactor.cli db-import          # 把已有 parquet/因子库导入
python -m qfactor.cli db-status
```

表：行情 `daily_bars`、成分 `universe_members`、行业 `industry_map`、因子 `factors`/`factor_reports`、任务 `jobs`、checkpoint `loop_checkpoints`。  
`sync-data` / 因子入库会自动双写数据库；计算优先从 DB 读，失败再回退 Parquet。


| 用途 | 来源 |
|---|---|
| 成分股 / 权重 | **中证指数官网** xls（`000903cons/closeweight`） |
| 行情 / 换手 / 估值字段 | **Baostock** |
| 行业 | **Baostock** `query_stock_industry` |
| 流通市值 | 由 `amount / turnover` 估算（诊断用） |

限制：官网文件是**最新成分快照**，不是完整历史调样序列；比手写 30 只近似池正规得多，但仍需在报告中注明。

```bash
qfactor sync-data --start 20240101 --end 20260630 --source baostock
qfactor data-status
```

## 因子库运营

```bash
qfactor library-archive          # draft/reject 超期归档
qfactor library-demote-corr     # 高相关自动降权
qfactor library-promote NAME --gate production
qfactor library-demote NAME --to deprecated
```

## 评估

- `research`：挖矿宽松门禁
- `production`：要求 OOS、成本后分层、更严相关/ICIR

## 生产 Loop

默认 LLM 占比 0.45，强化机制覆盖与骨架去重（不做更重框架）。

```bash
qfactor loop --rounds 5 --batch-size 8 --llm-ratio 0.45 --gate research
```

## Web UI（三页）

```bash
qfactor serve
# http://127.0.0.1:8000/ui/sync
# http://127.0.0.1:8000/ui/loop
# http://127.0.0.1:8000/ui/factors
```
