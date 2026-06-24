# DATA_SCHEMA.md — quant.db 完整字段文档

> 目的：所有表的字段定义、数据来源、索引、外键、示例查询
>
> 最后更新：2026-06-24 11:30
>
> DB 路径：`database/quant.db`（gitignore, 本地生成）
>
> 唯一入口：`python scripts/build_db.py [--incremental] [--table X]`

---

## 全局约定

| 规范 | 说明 |
|---|---|
| `code` | 6 位字符串，左补 0（如 `000001`/`600000`/`920001`）。**沪深北三市统一 6 位**。指数代码单独处理（见 `benchmark_data`） |
| `market` | `SH` / `SZ` / `BJ`（全大写）。`stock_basic.market` 是 NOT NULL |
| 日期 | `YYYY-MM-DD` 字符串（SQLite DATE type） |
| 浮点空值 | 导入时 `'-'` / `''` / `'nan'` 统一转 `NULL`（`build_db.py` 宽容 `_to_float`） |
| 增量模式 | `--incremental` 利用 UNIQUE 约束防重复；UPDATE 路径用 `COALESCE` 不覆盖已有数据 |

## 股票池（统一）

- **5,569 只**：沪深 5,247（SH 2,328 + SZ 2,919）+ 北交所 322 + 历史遗漏 38
- 数据源：`tencent_quotes.csv`（沪深 5,209）+ westock profile 批量（北交 322 + 漏股 38）
- `stock_basic.code` 是所有表的隐式外键（**除 benchmark_data 用指数代码**）

---

## 业务表（13 张）

### 1. stock_basic（股票基础）

| 字段 | 类型 | 说明 | 来源 |
|---|---|---|---|
| code | VARCHAR(10) PK NOT NULL | 6 位数字 | tencent_quotes + westock |
| name | VARCHAR(50) NOT NULL | 股票名称 | tencent_quotes + westock |
| market | VARCHAR(2) NOT NULL | SH/SZ/BJ | 推断（前缀）|
| list_date | DATE | 上市日期 | tencent_quotes |
| delist_date | DATE | 退市日期 | 暂未填（已知退市股：add_bj_stocks 占位） |
| industry | VARCHAR(50) | 所属行业（申万）| westock profile (兜底) |
| sector | TEXT | 所属板块 | westock profile |
| listed_date_alt | TEXT | 上市日期（westock）| westock profile |
| issue_price | REAL | 发行价 | westock profile |
| reg_capital | REAL | 注册资本（万元）| westock profile |
| establish_date | TEXT | 公司成立日期 | westock profile |
| chairman | TEXT | 董事长 | westock profile |
| website | TEXT | 公司网站 | westock profile |
| business | TEXT | 主营业务 | westock profile |
| reg_address | TEXT | 注册地址 | westock profile |
| office_address | TEXT | 办公地址 | westock profile |
| tel | TEXT | 电话 | westock profile |
| email | TEXT | 邮箱 | westock profile |

**索引**：PK(code)
**外键**：所有 `*_data.code` 都隐式外键到此表

### 2. stock_profile（股票简介）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | 自增 |
| code | TEXT NOT NULL | 6 位（同 stock_basic）|
| name | TEXT | 名称 |
| listed_date | TEXT | 上市日期 |
| industry | TEXT | 行业（westock 口径）|
| sector | TEXT | 板块 |
| issue_price | REAL | 发行价 |
| reg_capital | REAL | 注册资本 |
| chairman | TEXT | 董事长 |
| establish_date | TEXT | 成立日期 |
| website | TEXT | 网址 |
| business | TEXT | 主营业务 |
| reg_address | TEXT | 注册地址 |
| **circulating_shares** | REAL | **流通股本（股）** — fund_flow_scorer 用，**待 westock 批量补** |
| source | TEXT | 'westock' |
| updated_at | TIMESTAMP | 更新时间 |

**索引**：`idx_sp_code` / `idx_sp_industry` / `idx_sp_sector`
**关系**：与 `stock_basic.industry` 冗余，**主路径**优先查此表，**兜底**用 stock_basic（v_leader_features.py）

### 3. daily_price（行情）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| code | VARCHAR(10) NOT NULL | 6 位 |
| trade_date | DATE NOT NULL | |
| open/high/low/close | REAL NOT NULL | OHLC |
| volume | BIGINT NOT NULL | 成交量 |
| amount | FLOAT | 成交额 |
| pct_change | FLOAT | 涨跌幅 |
| turnover | FLOAT | 换手率（部分早期数据为空）|

**索引**：`idx_dp_code_date` / `idx_dp_date` / `idx_dp_date_code`
**来源**：`market_data/raw/kline_daily/kline_daily_*.csv`（4 个分年 CSV）
**行数**：4,181,854（2023-01-03 ~ 2026-06-18）

### 4. technical_indicators（技术指标）

| 字段 | 类型 |
|---|---|
| id | INTEGER PK |
| code | VARCHAR(10) NOT NULL |
| trade_date | DATE NOT NULL |
| macd_dif/dea/hist | REAL |
| rsi14 | REAL（**注意：2024-01-19 之前为 NULL，因 RSI 需 14 天 lookback**）|
| kdj_k/d/j | REAL |
| boll_mid/upper/lower | REAL |

**索引**：`idx_ti_code_date`
**来源**：`market_data/raw/technical_indicators/tech_indicators_{2024,2025,2026}.csv`（**自动跳过 part1/part2 冗余备份**）
**行数**：3,016,976（2024-01-02 ~ 2026-06-16）

### 5. fund_flow_data（资金流向）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| code | VARCHAR(10) NOT NULL | 6 位 |
| market | VARCHAR(10) | sh/sz/bj（CSV 拆分） |
| name | VARCHAR(50) | 名称 |
| trade_date | DATE | |
| main_net | FLOAT | 主力净流入（万元）|
| super_large_net | FLOAT | 超大单净流入 |
| large_net | FLOAT | 大单净流入 |
| medium_net | FLOAT | 中单净流入 |
| small_net | FLOAT | 小单净流入 |

**索引**：`uq_ff_code_date` UNIQUE / `idx_ff_code` / `idx_ff_date` / `idx_ff_code_date`
**来源**：`market_data/fund_flow_120d.csv`（435,092 行，120 天滚动）

### 6. block_trade（大宗交易）

| 字段 | 类型 |
|---|---|
| id | INTEGER PK |
| code | VARCHAR(10) NOT NULL |
| trade_date | DATE NOT NULL |
| name | VARCHAR(50) |
| deal_price | FLOAT 成交价 |
| close_price | FLOAT 收盘价 |
| premium_pct | FLOAT 溢价率 |
| volume | BIGINT |
| amount | FLOAT |
| buyer | VARCHAR(100) 买方 |
| seller | VARCHAR(100) 卖方 |

**来源**：`market_data/raw/reference/block_trade.csv`
**行数**：54,700（2000-08-29 ~ 2026-06-18）

### 7. dividend（分红）

| 字段 | 类型 |
|---|---|
| id | INTEGER PK |
| code | VARCHAR(10) NOT NULL |
| ex_div_date | DATE 除权日 |
| pre_tax_bonus | FLOAT 每股税前分红 |
| transfer_ratio | FLOAT 转股比例 |
| bonus_ratio | FLOAT 送股比例 |
| record_date | DATE 股权登记日 |

**来源**：`market_data/raw/reference/dividend.csv`
**行数**：40,377（1991-06-28 ~ 2026-07-09）

### 8. announcements（公告）

| 字段 | 类型 |
|---|---|
| id | INTEGER PK |
| code | VARCHAR(10) NOT NULL |
| trade_date | DATE |
| type | VARCHAR(100) 公告类型 |
| title | VARCHAR(500) |
| url | VARCHAR(500) |

**来源**：`market_data/raw/reference/announcements.csv`
**行数**：4,616（2026-03-21 ~ 2026-06-16）

### 9. holder_num（股东户数）

| 字段 | 类型 |
|---|---|
| id | INTEGER PK |
| code | TEXT NOT NULL |
| end_date | DATE NOT NULL |
| holder_num | INTEGER |
| pre_holder_num | INTEGER |
| holder_change_pct | REAL |
| avg_holding | REAL 户均持股 |
| source | TEXT DEFAULT 'tencent' |

**索引**：`uq(code, end_date)` / `idx_hn_code` / `idx_hn_code_date`
**来源**：`market_data/holder_num.csv`
**行数**：5,521（2013-06-30 ~ 2026-06-18）

### 10. benchmark_data（指数 K 线）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| index_code | VARCHAR(10) NOT NULL | **指数代码**（如 `sh000300`，**与股票 code 不同 domain**） |
| code | TEXT | 冗余（westock 格式） |
| name | TEXT | 指数名（沪深300/中证500 等） |
| trade_date | DATE NOT NULL | |
| close | REAL NOT NULL | 收盘价 |
| open/high/low | REAL | OHLC（westock 扩展） |
| volume/amount | REAL | |
| exchange_factor | REAL | 换手率 |
| pct_change | REAL | 涨跌幅（CSV 无，扩展列） |
| source | TEXT | 'westock' |

**索引**：`UNIQUE(index_code, trade_date)`
**来源**：`market_data/benchmark_data.csv`（westock 6 指数 × 1500 天 = 9000 行）
**指数列表**：沪深300 / 中证500 / 上证50 / 中证1000 / 科创50 / 创业板指
**覆盖**：2020-04-15 ~ 2026-06-24

### 11. finance_summary（财务摘要）

**东方财富 schema 35 列 + baostock 扩展 10 列 = 45 列**（兼容代码 `fundamental_scorer.py`/`pipeline.py`/`v6_pipeline_hybrid.py` 的 `ROE/EPSTTM/NetProfitRatio` 等命名）

| 原 schema 字段（东方财富命名） | 类型 | 来源 |
|---|---|---|
| code | VARCHAR(10) NOT NULL | baostock 6 位 |
| _date | DATE | 季度末（如 2024-12-31）|
| EndDate | DATE | 同上 |
| ROE / ROETTM / ROEWeighted | FLOAT | ← baostock roeAvg |
| EPS / EPSTTM / BasicEPS / DilutedEPS | FLOAT | ← baostock epsTTM |
| NAPS | FLOAT | 每股净资产（baostock 无，留空）|
| NetProfitRatio / NetProfitRatioTTM | FLOAT | ← baostock npMargin |
| DebtAssetsRatio / DebtEquityRatio | FLOAT | 资产负债率（baostock 无，留空）|
| OperatingRevenue / OperatingRevenueTTM / OperatingRevenueGrowRate | FLOAT | ← baostock MBRevenue |
| OperatingProfit / OperatingProfitTTM | FLOAT | baostock 无 |
| TotalOperatingRevenue | FLOAT | ← baostock MBRevenue |
| NPParentCompanyOwners / NPParentCompanyOwnersTTM / NPParentCompanyYOY | FLOAT | ← baostock netProfit |
| NetOperateCashFlow / NetOperateCashFlowTTM / TotalAssets / TotalShareholderEquity / TotalLiability / NetAssetGrowRate / TotalAssetGrowRate / CashFlowPS / OperCashFlowPS / MainIncomePS | FLOAT | 东方财富专属字段（baostock 无）|

| baostock 扩展字段 | 类型 | 来源 |
|---|---|---|
| baostock_pub_date | DATE | 公告日期 |
| roe_avg / np_margin / gp_margin | FLOAT | baostock 原始字段 |
| net_profit | FLOAT | |
| eps_ttm | FLOAT | |
| main_revenue | FLOAT | |
| total_share | FLOAT | 总股本 |
| liqa_share | FLOAT | 流通股本 |
| source | TEXT | 'baostock' |

**索引**：`UNIQUE(code, _date)` / `idx_fs_code` / `idx_fs_code_date`
**来源**：`market_data/finance_summary.csv`（baostock profit_data 11 字段，按映射写入 27 列）
**当前行数**：5,172（2024 Q4 单季，**等 finance BG 完成填多年数据**）

### 12. research_report（研报）

| 字段 | 类型 |
|---|---|
| id | INTEGER PK |
| code | TEXT NOT NULL 6 位 |
| date | DATE |
| rating | TEXT 评级 |
| rating_change | TEXT 评级变化 |
| title | TEXT |
| author | TEXT |
| institution | TEXT |
| url | TEXT |

**来源**：`market_data/raw/reference/research_report.csv`
**行数**：1,919（2040 CSV，去重）
**引用**：`sentiment_scorer.py` / `news_event_scorer.py`

### 13. em_global_news（财经新闻）

| 字段 | 类型 |
|---|---|
| id | INTEGER PK |
| date | DATE |
| title | TEXT |
| url | TEXT |
| summary | TEXT |
| source | TEXT |

**来源**：`market_data/raw/reference/em_global_news.csv`
**行数**：101
**引用**：`v6_pipeline_hybrid.py`（sentiment 维度）

### 14. ths_hot_reason（同花顺热股）

| 字段 | 类型 |
|---|---|
| id | INTEGER PK |
| date | DATE |
| rank | INTEGER |
| code | TEXT |
| name | TEXT |
| hot_value | REAL |
| concept | TEXT |
| reason | TEXT |
| change_pct | REAL |

**来源**：`market_data/raw/reference/ths_hot_reason.csv`
**行数**：138

---

## 事务表（不 import，由代码运行时写入）

- **backtest_result** (0 行): 回测结果记录
- **strategy_config** (0 行): 策略配置
- **data_source_meta** (0 行): 数据源元信息

---

## build_db.py 14 个 importer

| 命令 | 表 | 数据源 |
|---|---|---|
| `--table stock_basic` | stock_basic | `raw/reference/tencent_quotes.csv` |
| `--table daily_price` | daily_price | `raw/kline_daily/*.csv` |
| `--table fund_flow` | fund_flow_data | `fund_flow_120d.csv` |
| `--table technical_indicators` | technical_indicators | `raw/technical_indicators/*.csv`（跳过 part1/part2）|
| `--table block_trade` | block_trade | `raw/reference/block_trade.csv` |
| `--table dividend` | dividend | `raw/reference/dividend.csv` |
| `--table announcements` | announcements | `raw/reference/announcements.csv` |
| `--table holder_num` | holder_num | `holder_num.csv` |
| `--table benchmark` | benchmark_data | `benchmark_data.csv` |
| `--table finance` | finance_summary | `finance_summary.csv` |
| `--table stock_profile` | stock_basic + stock_profile | `stock_profile.csv` |
| `--table research_report` | research_report | `raw/reference/research_report.csv` |
| `--table em_global_news` | em_global_news | `raw/reference/em_global_news.csv` |
| `--table ths_hot_reason` | ths_hot_reason | `raw/reference/ths_hot_reason.csv` |

**调用**：
```bash
# 全量重建（清空 DB 重灌）
python scripts/build_db.py --full

# 增量（仅追加新行，UNIQUE 防重复）
python scripts/build_db.py --incremental

# 单表
python scripts/build_db.py --incremental --table finance
```

---

## 跨表数据通路

```
tencent_quotes.csv ──────────────┐
                                 ├─► stock_basic (5569 股)
westock profile (批量 53 批) ──┘
                                 │
                                 ├─► stock_profile (5565 条, 含 circulating_shares 待补)
                                 │
                                 ▼
                          股票池 (所有表隐式外键)

kline_daily_*.csv (4 年) ──────► daily_price (4.18M)
tech_indicators_*.csv (3 年) ──► technical_indicators (3.02M)
fund_flow_120d.csv ────────────► fund_flow_data (435K, 120天滚动)
holder_num.csv ────────────────► holder_num (5521)
block_trade.csv / dividend.csv / announcements.csv ──► 各自 (54K/40K/4.6K)

westock profile ───────────────► stock_profile + stock_basic.industry
fund_flow_120d.csv ────────────► fund_flow_data (CSV 源)
westock kline ─────────────────► benchmark_data (9000, 6 指数)
baostock profit_data loop ─────► finance_summary (5K, 多年进行中)

research_report.csv / em_global_news.csv / ths_hot_reason.csv ──► 各自
```

---

## 外键关系图

```
stock_basic (5569) ───┬─ daily_price (4.18M, code)
                      ├─ technical_indicators (3.02M)
                      ├─ fund_flow_data (435K)
                      ├─ block_trade (54K)
                      ├─ dividend (40K)
                      ├─ announcements (4.6K)
                      ├─ holder_num (5.5K)
                      ├─ finance_summary (5K)
                      ├─ research_report (1.9K)
                      └─ ths_hot_reason (138)

stock_profile (5565) ←──── 主路径 (v_leader_features.py)
                      ↓ 兜底
                    stock_basic.industry (5205/5569, 99.9% 覆盖)

benchmark_data (9000, 6 指数) ─── 独立 domain, 不与股票 join
backtest_result / strategy_config / data_source_meta ─── 事务层, 0 行
```

---

## 已知 GAP

1. **finance_summary** 当前只有 2024 Q4（5172 行）— finance 多年 BG 完成后填到 2015-2024 × 4Q ≈ 200K 行
2. **stock_profile.circulating_shares** 全部 NULL — fund_flow_scorer.py 期望字段，待 westock 单独拉
3. **stock_basic.delist_date** 全部 NULL — 已知 38 只历史退市股可加标记
4. **AKShare 远程访问断开**（2026-06-24）— 资金流/指数走 westock 替代

---

*最后更新: 2026-06-24 11:30*