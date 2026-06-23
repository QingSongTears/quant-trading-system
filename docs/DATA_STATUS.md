# 数据状态报告 — 2026-06-23

> 目的：盘点数据库当前实际状态 + 缺口 + 后续工作

---

## 1. 当前数据库清单（实际数据）

| 表 | 行数 | 状态 | CSV 源 | 大小 |
|---|---|---|---|---|
| `daily_price` | 4,181,854 | ✅ 满 | `raw/kline_daily/*.csv` | 290 MB |
| `stock_basic` | 5,209 | ✅ 满 | `raw/reference/tencent_quotes.csv` | 0.7 MB |
| `technical_indicators` | **3,016,976** | ✅ **新** | `raw/technical_indicators/*.csv` (3 个，跳过 part1/2 冗余) | 343 MB |
| `block_trade` | **54,700** | ✅ **新** | `raw/reference/block_trade.csv` | 9.2 MB |
| `dividend` | **40,377** | ✅ **新** | `raw/reference/dividend.csv` | 1.6 MB |
| `announcements` | **4,616** | ✅ **新** | `raw/reference/announcements.csv` | 0.7 MB |
| `backtest_result` | 0 | ⚠️ 表已建（事务层）| - | - |
| `strategy_config` | 0 | ⚠️ 表已建（事务层）| - | - |

**总计 7.3M 行数据**，**DB 大小 ~1.2 GB**（实际 894 MB）。

---

## 2. 缺失的数据（无 CSV 源）

| 表 | 现状 | 原因 | 解决方案 |
|---|---|---|---|
| `fund_flow_data` | 空（表已建）| **CSV 源不在仓库** | 需要从 tdrive 拷贝或采集（fund_flow*.csv）|
| `finance_summary` | 空（表已建）| **CSV 源不在仓库** | 同上（finance_summary.csv 84 MB）|
| `benchmark_data` | 空（表已建）| **CSV 源不在仓库** | 同上（沪深300/中证500 历史）|
| `stock_profile` | 空（表已建）| **CSV 源不在仓库** | tencent_quotes.csv 没有 industry 字段，需独立采集 |

**重要发现**：
- `tencent_quotes.csv`（已 import 到 stock_basic）**没有 industry/sector 字段**
- 之前 TODO.md 提到的 "stock_profile.csv 5066 只 + industry/sector" **本项目仓库里没有这个 CSV**
- 之前 audit 报告的 "fund_flow 268K 行" "finance_summary 4709 行" **本项目 DB 里都是空的**

---

## 3. 已完成的 import 工具

| 脚本 | 用途 | 状态 |
|---|---|---|
| `scripts/build_db.py` | kline_daily + stock_basic + fund_flow + benchmark + technical_indicators + finance_summary → DB | ✅ 在用 |
| `scripts/build_parquet.py` | kline_daily → 10232 个 parquet 文件（vnpy 风格）| ✅ 新（commit 22c3f76）|
| `scripts/import_more_csv.py` | technical_indicators + block_trade + dividend + announcements → DB | ✅ 新（本次）|

---

## 4. 修复的真实 bug（伴随本次 import）

| Bug | 修复 |
|---|---|
| `tech_indicators_2025.csv` 与 `tech_indicators_2025_part1.csv` + `part2.csv` 冗余 | 跳过 part1/part2，只 import 2025.csv |

---

## 5. vnpy 借鉴后的数据架构

```
market_data/                              # Git LFS 源
├── raw/
│   ├── kline_daily/                      (4 CSV, 283 MB)  → daily_price
│   ├── technical_indicators/             (5 CSV, 343 MB)  → technical_indicators [skip part1/2]
│   └── reference/                        (11 CSV, 12.5 MB) → stock_basic + block_trade + dividend + announcements
└── parquet/                              # Gitignore (vnpy 风格派生层)
    └── daily/                            (10232 parquet, 223 MB)

database/
└── quant.db                              # Gitignore (本地 SQLite, ~1.2 GB)
    ├── daily_price         (行情)
    ├── stock_basic         (合约)
    ├── technical_indicators (技术指标)  ← 新
    ├── block_trade         (大宗交易)   ← 新
    ├── dividend            (分红)        ← 新
    ├── announcements       (公告)       ← 新
    ├── backtest_result     (回测结果 - 事务层)
    └── strategy_config     (策略配置 - 事务层)
```

---

## 6. 后续工作（按 ROI）

| 优先级 | 任务 | 价值 |
|---|---|---|
| 🔴 P0 | **找源：fund_flow / finance_summary / benchmark / stock_profile CSV** | 补齐 8 维评分 + alpha 因子 |
| 🟡 P1 | **V6 OOS 重跑**（用真实数据通路）| 验证 V6 是否仍 OOS 失败 |
| 🟡 P1 | **V龙头 OOS 跑**（看 XGBoost v4 在真实数据上）| 验证 ML 策略可行性 |
| 🟢 P2 | 把 technical_indicators 也转 parquet（vnpy 风格）| - |

---

*最后更新: 2026-06-23*