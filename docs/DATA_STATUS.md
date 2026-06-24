# 数据状态报告 — 2026-06-24

> 目的：盘点数据库当前实际状态 + CSV 源覆盖 + 缺口 + 后续工作
>
> 上次更新：2026-06-23（import 4 个新表 + bug 修复）

---

## 1. 当前数据库清单（实际数据）

| 表 | 行数 | 状态 | CSV / 数据源 | 大小 |
|---|---|---|---|---|
| `daily_price` | 4,181,854 | ✅ 满 | `raw/kline_daily/*.csv` | 290 MB |
| `stock_basic` | 5,209 | ✅ 满 | `raw/reference/tencent_quotes.csv` | 0.7 MB |
| `technical_indicators` | 3,016,976 | ✅ 满 | `raw/technical_indicators/*.csv` (3 个，跳过 part1/2 冗余) | 343 MB |
| `block_trade` | 54,700 | ✅ 满 | `raw/reference/block_trade.csv` | 9.2 MB |
| `dividend` | 40,377 | ✅ 满 | `raw/reference/dividend.csv` | 1.6 MB |
| `announcements` | 4,616 | ✅ 满 | `raw/reference/announcements.csv` | 0.7 MB |
| `backtest_result` | 0 | ⚠️ 表已建（事务层）| - | - |
| `strategy_config` | 0 | ⚠️ 表已建（事务层）| - | - |

**总计 7.3M 行数据**，**DB 大小 ~1.2 GB**（实际 894 MB）。

---

## 2. CSV / Parquet 派生层覆盖

| 文件 | 行数 | 字段数 | 来源 | 备注 |
|---|---|---|---|---|
| `market_data/stock_profile.csv` | **5,205** | 15 | westock profile 批量 | **新 (2026-06-24)**, 含 industry/sector/regCapital/listedDate |
| `market_data/benchmark_data.csv` | **9,000** | 9 | westock kline 6 指数 × 1500 天 | **新 (2026-06-24)**, 沪深300/中证500/上证50/中证1000/科创50/创业板指 |
| `market_data/finance_summary.csv` | 采集中 | - | baostock profit_data loop | **新 (2026-06-24, 后台跑)**, year=2024 Q4 起步 |
| `market_data/fund_flow_120d.csv` | 435,093 | 10 | 已存在 (westock asfund 历史导出) | **已覆盖** (本次纠错 — 之前标为缺失是错的) |
| `market_data/holder_num.csv` | 5,522 | 6 | 已存在 (westock shareholder 历史导出) | 股东数据 |
| `market_data/parquet/daily/*.parquet` | 10232 文件 | 10 | `scripts/build_parquet.py` | vnpy 风格派生层 (gitignore) |

**关键纠错**：
- 之前 `fund_flow_data` 标"CSV 源不在仓库"是**错的** —— `fund_flow_120d.csv` 早已存在（435K 行，覆盖全市场 120 天滚动），是 westock asfund 历史导出
- tencent_quotes.csv 没有 industry/sector，但 westock profile 补齐（5,205 只全市场）

---

## 3. 数据源分工（2026-06-24 实测）

| 数据源 | 网络状态 | 用于 | 调用方式 |
|---|---|---|---|
| **westock** (腾讯自选股) | ✅ 通 (3-10s/批) | profile / kline / 行情类实时数据 | `npx westock-data-clawhub@1.0.4 <subcommand> <codes> --flags`, 支持逗号批量 |
| **baostock** 0.9.10 | ✅ 通 | 财务三表 (profit/balance/cashflow) + 行业分类 | `bs.query_*` Python SDK, 单股循环 (无 batch API) |
| **akshare** 1.18.64 | ❌ RemoteDisconnected (2026-06-24) | (备用) 指数/资金流/财报 | `ak.*` Python SDK, 当前网络断开 |
| **parquet** (本地) | ✅ 通 | 历史行情衍生 / 全市场特征工程 | polars 1.41.2 lazy API |

**说明**：
- AKShare 当前 RemoteDisconnected，import 脚本已**不依赖 AKShare**
- 主力资金流向已有 `fund_flow_120d.csv` 覆盖，无需 AKShare 补
- westock 批量上限经实测 BATCH=100 100% 成功率，BATCH=200/500 待压测

---

## 4. 已完成的 import 工具

| 脚本 | 用途 | 状态 |
|---|---|---|
| `scripts/build_db.py` | kline_daily + stock_basic + fund_flow + benchmark + technical_indicators + finance_summary → DB | ✅ 在用 |
| `scripts/build_parquet.py` | kline_daily → 10232 个 parquet 文件（vnpy 风格）| ✅ commit 22c3f76 |
| `scripts/import_more_csv.py` | technical_indicators + block_trade + dividend + announcements → DB | ✅ commit e3bf2fb |
| `scripts/import_from_westock_baostock_akshare.py` | **新** — stock_profile (westock) + finance_summary (baostock) + benchmark (westock) → CSV | ✅ 本次 |

---

## 5. 修复的真实 bug（伴随本次 import）

| Bug | 修复 |
|---|---|
| `tech_indicators_2025.csv` 与 `part1/2` 冗余 | 跳过 part1/2，只 import 2025.csv |
| `fund_flow_data` 标记缺失（实际 fund_flow_120d.csv 已存在 435K 行）| 标记纠正 |
| `import_from_westock_baostock_akshare.py` 中 westock_batch 参数顺序错误 (kline 期望 `<code> --period`, 旧实现拼成 `--period <code>`) | 重写 westock_batch 用 `subcommand` 参数, 顺序固定 `westock <subcommand> <codes> <flags>` |

---

## 6. vnpy 借鉴后的数据架构

```
market_data/                              # Git LFS 源
├── raw/
│   ├── kline_daily/                      (4 CSV, 283 MB)  → daily_price
│   ├── technical_indicators/             (5 CSV, 343 MB)  → technical_indicators [skip part1/2]
│   └── reference/                        (11 CSV, 12.5 MB) → stock_basic + block_trade + dividend + announcements
├── stock_profile.csv                     (2.0 MB, 5205 行, westock profile 批量)  ← 新
├── benchmark_data.csv                    (0.7 MB, 9000 行, westock kline 6 指数)   ← 新
├── finance_summary.csv                   (采集中, baostock profit_data)            ← 新
├── fund_flow_120d.csv                    (40 MB, 435K 行, 已有)
├── holder_num.csv                        (0.3 MB, 5.5K 行, 已有)
└── parquet/                              # Gitignore (vnpy 风格派生层)
    └── daily/                            (10232 parquet, 223 MB)

database/
└── quant.db                              # Gitignore (本地 SQLite, ~1.2 GB)
    ├── daily_price         (行情)
    ├── stock_basic         (合约)
    ├── technical_indicators (技术指标)
    ├── block_trade         (大宗交易)
    ├── dividend            (分红)
    ├── announcements       (公告)
    ├── backtest_result     (回测结果 - 事务层)
    └── strategy_config     (策略配置 - 事务层)
```

---

## 7. 后续工作（按 ROI 更新）

| 优先级 | 任务 | 价值 | 状态 |
|---|---|---|---|
| 🟢 P0 (已完成) | 补 stock_profile + benchmark + finance_summary CSV | 补齐 8 维评分 + alpha 因子 | ✅ profile+benchmark 完成, finance 后台跑 |
| 🟡 P1 | **V6 OOS 重跑**（用真实数据通路）| 验证 V6 是否仍 OOS 失败 | 待 finance 完成后启动 |
| 🟡 P1 | **V龙头 OOS 跑**（看 XGBoost v4 在真实数据上）| 验证 ML 策略可行性 | 待 finance 完成后启动 |
| 🟢 P2 | 把 technical_indicators 也转 parquet（vnpy 风格）| - | - |
| 🟢 P2 | finance_summary 多年多季度 loop（2007-2026 × 5000 只 × 4 季）| 完备财务时序 | 待 stock_profile join 后启动 |
| 🟢 P3 | V6 / V龙头 继承 EquityStrategy | vnpy 风格 alpha strategy | - |
| 🟢 P3 | RiskEngine + PositionTracker + Recorder（vnpy 借鉴 P0）| - | - |

---

## 8. AKShare 网络问题 — 后续建议

- 当前 AKShare RemoteDisconnected（2026-06-24 实测）
- 备选：等 AKShare 网络恢复；或等 westock 出指数日线更长时间序列（当前 limit=1500 ≈ 6 年）
- 如果 AKShare 长期不通，且 westock 不扩展指数，可考虑:
  - 自己写 fetch 沪深300 指数 (Tushare pro token 申请)
  - 或使用 parquets 已有 daily 数据计算等权基准

---

*最后更新: 2026-06-24 09:43*