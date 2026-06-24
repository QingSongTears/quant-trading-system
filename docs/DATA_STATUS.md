# 数据状态报告 — 2026-06-24 11:30

> 目的：盘点 quant.db 当前实际状态 + CSV 源覆盖 + 待办
>
> **完整字段定义 / 索引 / 外键 / 示例查询见 [DATA_SCHEMA.md](DATA_SCHEMA.md)**
>
> 上次更新：2026-06-23

---

## 1. 当前 DB（5569 只股 + 6 指数 + 13 张业务表 + 3 张事务表）

| 表 | 行数 | 状态 | 来源 CSV |
|---|---|---|---|
| **daily_price** | 4,181,854 | ✅ 满 | `raw/kline_daily/*.csv`（2023-2026）|
| **technical_indicators** | 3,016,976 | ✅ 满 | `raw/technical_indicators/*.csv`（跳过 part1/2 冗余）|
| **fund_flow_data** | **435,092** | ✅ **新** | `fund_flow_120d.csv`（120 天滚动）|
| **stock_basic** | **5,569** | ✅ **新** | `raw/reference/tencent_quotes.csv` (5209) + westock 北交 (322) + 历史漏 (38) |
| **stock_profile** | **5,565** | ✅ **新** | westock profile 批量 53 批（含北交）|
| **block_trade** | 54,700 | ✅ 满 | `raw/reference/block_trade.csv` |
| **dividend** | 40,377 | ✅ 满 | `raw/reference/dividend.csv` |
| **announcements** | 4,616 | ✅ 满 | `raw/reference/announcements.csv` |
| **holder_num** | **5,521** | ✅ **新** | `holder_num.csv` |
| **benchmark_data** | 9,000 | ✅ 满 | westock kline（6 指数 × 1500 天）|
| **finance_summary** | **5,172** | ⚠️ 部分 | baostock profit_data（**仅 2024 Q4**，多年 BG 进行中）|
| **research_report** | **1,919** | ✅ **新** | `raw/reference/research_report.csv` |
| **em_global_news** | **101** | ✅ **新** | `raw/reference/em_global_news.csv` |
| **ths_hot_reason** | **138** | ✅ **新** | `raw/reference/ths_hot_reason.csv` |
| backtest_result | 0 | ⚠️ 事务层 | - |
| strategy_config | 0 | ⚠️ 事务层 | - |
| data_source_meta | 0 | ⚠️ 事务层 | - |

**总计：~7.9M 行 / 1581 MB**（不含事务表）

---

## 2. Schema 整合（2026-06-24）

消除冗余表，**单源真相**：
- ❌ DROP `benchmark_kline` → 合并到 `benchmark_data`（加 OHLCV 字段）
- ❌ DROP `finance_quarterly` → 合并到 `finance_summary`（加 baostock 扩展 10 列）
- ❌ DROP `stock_profile`（旧 schema 缺 `circulating_shares`） → 重建匹配 ORM + 加列

不破坏代码（保留原 schema 命名兼容 `repository.py`/`fundamental_scorer.py`/`pipeline.py` 等）。

---

## 3. 数据源分工（2026-06-24 实测）

| 数据源 | 网络 | 用于 | 调用方式 |
|---|---|---|---|
| **westock**（腾讯自选股 npm CLI）| ✅ 通 | profile / kline / 行情类 | `npx westock-data-clawhub@1.0.4 <subcmd> <codes> --flags`，支持逗号批量 |
| **baostock** 0.9.10 | ✅ 通 | 财务三表 + 行业 | `bs.query_*` Python SDK，单股循环（无 batch） |
| **akshare** 1.18.64 | ❌ RemoteDisconnected | (备用) 指数/资金流 | 2026-06-24 网络断，已不依赖 |
| **polars** 1.41.2 + **pyarrow** 24.0.0 | ✅ | parquet 派生层 | 10232 文件，223.6 MB |
| **fund_flow_120d.csv** (westock 历史导出) | ✅ | 主力资金流向（已存在）| 435K 行，120 天滚动 |

---

## 4. build_db.py 统一入口

```bash
python scripts/build_db.py              # 全量重建
python scripts/build_db.py --incremental # 增量
python scripts/build_db.py --incremental --table finance  # 单表
```

**14 个 importer**：stock_basic / daily_price / fund_flow / technical_indicators / block_trade / dividend / announcements / holder_num / benchmark / finance / stock_profile / research_report / em_global_news / ths_hot_reason

---

## 5. 数据质量（2026-06-24 11:00 校验）

### 外键一致性（除 benchmark_data 外）
| 表 | 孤儿股数 | 总股数 | 覆盖率 |
|---|---|---|---|
| daily_price | 0 | 5,206 | 100% |
| technical_indicators | 0 | 5,200 | 100% |
| fund_flow_data | 0 | 5,065 | 100% |
| block_trade | 0 | 4,887 | 100% |
| dividend | 0 | 5,131 | 100% |
| announcements | 0 | 462 | 100% |
| holder_num | 0 | 5,521 | 100% |
| finance_summary | 0 | 5,172 | 100% |

**benchmark_data 是指数代码（sh000300 等），与股票 code 不同 domain，外键检查跳过。**

### NaN/空值
- ✓ stock_profile.industry: 0/5565
- ✓ stock_basic.industry: 4/5569（westock 找不到 4 只，与之前报告一致）
- ⚠️ finance_summary.ROE: 18 空（**BG 完成后会填 0**）
- ✓ daily_price.close: 0/4.18M
- ⚠️ technical_indicators.rsi14: 67,600 空（2024-01-19 之前，**lookback 14 天技术正确**）

---

## 6. 后续工作

| 优先级 | 任务 | 状态 |
|---|---|---|
| 🟢 | 补 `stock_profile.circulating_shares`（fund_flow_scorer 期望）| 待 westock 拉 |
| 🟢 | finance 多年多季 (2015-2024 × 4Q) 填 finance_summary | **BG PID 24544 在跑**（cron 30min 检查）|
| 🟡 | 写 `docs/DATA_SCHEMA.md`（字段/索引/外键/示例查询）| ✅ 已写 |
| 🟡 | 修 `stock_basic.delist_date`（38 只历史退市股标记）| 待办 |
| 🟢 | 代码引用但 DB 缺表的检查（news_event_data / chip_distribution / dragon_tiger_data）| 无 CSV 源，**待定** |

---

## 7. cron 监控

- `bg_finance_check`（每 30min）：检查 finance 多年 BG 状态，完成后自动 import + commit + push

---

*最后更新: 2026-06-24 11:30*