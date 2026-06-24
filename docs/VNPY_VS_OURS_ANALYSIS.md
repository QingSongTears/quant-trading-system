# vnpyS vs quant-trading-system 深度对比 + 数据库架构方案

> 分析日期：2026-06-23
> 分析对象：
> - **vnpyS** — 开源 VeighNa 4.4 量化交易框架 (D:\gitHub\vnpyS)
> - **quant-trading-system** — 我们的 A 股双层量化回测系统 (D:\gitHub\qunat\quant-trading-system)

---

## 一、一句话结论

**vnpyS 是"通用交易平台框架"，我们项目是"A 股策略研发系统"。两者根本不在一个赛道。**

- vnpyS 想做"基础设施"——多市场、多券商、多周期、可插拔；用 alpha 模块补 AI 能力。
- 我们项目想做"特定场景下的策略产出机器"——8 维评分、XGBoost、操盘层、Web 交互；目标 50–100w 个人账户实盘。

直接拿 vnpyS 当目标来"补齐"是错的。我们应该学它的**架构思想**，但要按自己的定位选择性吸收。

---

## 二、模块对比

| 维度 | vnpyS | 我们项目 | 评价 |
|---|---|---|---|
| **整体定位** | 通用量化交易平台框架 | A 股策略研发 + 选股 + 回测 + 模拟 | 路线不同 |
| **核心引擎** | trader.event_engine + MainEngine + 多 App | gateway.main_engine + EventEngine + Strategy | 借鉴了 |
| **交易网关** | 30+ 网关 (CTP/恒生/IB/tdx/xtp 等) | BaseGateway + Simulator，未接实盘网关 | **显著差距** |
| **数据库抽象** | BaseDatabase 抽象 + 多驱动 (sqlite/mysql/postgres/clickhouse/mongodb/duckdb) | SQLAlchemy 单引擎 + SQLite (单文件 980MB, 4.18M 行) | **显著差距** |
| **数据源 (datafeed)** | BaseDatafeed 抽象 + 多家 (rqdata/wind/tushare/xt...) | BaseDatafeed + LocalDatafeed (SQLite) + ParquetDatafeed (空) | 框架对齐，实现一半 |
| **行情周期** | MINUTE/HOUR/DAY/WEEK/MONTH/TICK | 仅 DAY_1 | **显著差距** |
| **AI/ML** | vnpy.alpha: dataset (Alpha158/101, factor 引擎) + model (Lasso/LightGBM/MLP 标准化) + strategy + lab | XGBoost v4 + 8 维评分，无 factor 表达引擎 | **显著差距** |
| **回测引擎** | cta_backtesting / portfolio_backtesting / spread_backtesting / 优化器 | backtest.engine + portfolio_engine，自研 | 够用，但参数优化偏弱 |
| **图表 UI** | vnpy.chart (Qt 图表组件) | Web 端 ECharts | 路线不同，Web 更易分享 |
| **RPC** | vnpy.rpc (跨进程通信) | 无 | 暂不需要 |
| **A 股规则** | 通用，无 A 股专属 | T+1、涨跌停、100 股整倍、印花税、佣金、滑点内置 | **我们的强项** |
| **评分模型** | 无 (靠 alpha 因子库) | 8 维独立评分器 + combiner + 归因分析 | **我们的强项** |
| **Web 端** | 无 (Qt GUI) | FastAPI + htmx + ECharts + Bootstrap | **我们的强项** |
| **测试** | 单元测试 + alpha101 测试 | pytest + Playwright E2E + 真实数据回归 | 我们更全面 |
| **数据规模** | 取决于驱动 | SQLite 980MB / 4.18M 日线 / 5200+ 股票 | 中等规模 |

---

## 三、我们最显著的不足（按优先级）

### P0 - 战略级缺失

#### 1. 没有真正的实盘网关

vnpyS 有 30+ 网关适配器（CTP/恒生/IB/tdx/xtp），覆盖国内外主流券商。我们只有一个 `base_gateway.py` + simulator，连 `xtp/ptrade/qmt` 都还 TBD。

**影响**：从模拟盘走到实盘的最后 1 公里被卡住。
**建议**：直接接 xtp（华泰、中信通用）或 ptrade（个人账户也能开通）。

#### 2. 行情周期只有 1d

vnpyS 支持 MINUTE/HOUR/DAY/WEEK/MONTH + TICK。我们 `LocalDatafeed` 里 1m/5m/30m 直接抛 `NotImplementedError`。

**影响**：日内策略、T+0 套利、分钟级特征全部做不了。
**建议**：先支持 1m/5m/30m，A 股用 5m/15m 已经够大多数策略。

#### 3. 数据库驱动单一

vnpyS 用 `BaseDatabase` 抽象，支持 sqlite/mysql/postgres/clickhouse/mongodb/duckdb。我们 `get_db_url()` 直接写死 `sqlite:///{path}`。

**影响**：单 SQLite 文件 980MB 已经开始扛不住（4.18M 行 daily_price + technical_indicators + finance_summary + fund_flow），单表查询变慢、并发写不进（SQLite 写锁）。
**建议**：立刻抽象 DB 层，按"用途"分库（详见第五节）。

### P1 - 重要但不致命

#### 4. AI 因子体系缺失

vnpyS `vnpy.alpha.dataset` 有完整的 factor 表达引擎（cs/ts/ta/math_function 四大类，alpha_101/158 内置因子库），能用表达式（如 `TsMean($close, 20) / $close`）一键生成训练数据。我们只有写死在 `technical_scorer.py` 里的 7 个子指标，要加新指标得改代码。

**影响**：策略研发速度慢、复用度低、无法跟 alpha 社区对标。
**建议**：参考 qlib 的算子体系做个轻量版 factor 引擎。

#### 5. 回测优化器弱

vnpyS `cta_backtesting.OptimizationSetting` 支持参数网格搜索、并行优化、Walk-Forward。我们 `scripts/param_grid_search.py` 有个简单实现，walk-forward 没工程化。
**建议**：把 walk-forward 做成标准流程。

#### 6. ParquetDatafeed 是空壳

`src/datafeed/parquet.py` 已经写好完整实现（polars lazy），但 `market_data/parquet/daily/` 目录不存在。这其实就是 vnpy alpha 的标准做法，**vnpyS `AlphaLab.save_bar_data` 就是一个文件一只股票**。
**建议**：跑一遍 `scripts/build_parquet.py`，把这块真正落地（详见第六节）。

### P2 - 加分项

#### 7. 没有 RPC

vnpyS `vnpy.rpc` 支持跨进程通信。短期用不上，策略变多 + 单进程卡 IO 时会需要。

#### 8. 图表组件弱

vnpyS `vnpy.chart` 是 Qt 原生 K 线组件，支持画线、画指标、保存模板。我们用 ECharts 折线图，没法做交互式 K 线分析。短期可先用 `kline-charts` 这种开源 Web 组件顶上。

---

## 四、我们的强项（不要因为 vnpyS 而自卑）

| 强项 | 说明 |
|---|---|
| **A 股规则内置** | T+1、涨跌停、100 股整倍、印花税千 0.5、佣金万 2.5、滑点 5bps 全部内置到 `astock_strategy.py`，vnpyS 没这层 |
| **8 维评分系统** | technical / fundamental / fund_flow / institutional / chip / sentiment / news_event / lhb_institutional，独立评分器 + combiner + 归因分析 (3.1 万样本实证)，vnpyS 没有的"研报驱动"路线 |
| **XGBoost v4 ML 模型** | 8 维评分 + 技术指标 + 滞后/动量特征 → 月度 ret_60d > 10% 预测，真在用 |
| **操盘层 (Issue #73)** | PositionSizer / StopLoss / TakeProfit / TimeStop，仓位+止盈止损完整 |
| **Web 端 + E2E** | FastAPI + htmx + ECharts + Playwright，部署成本低，分享方便 |
| **数据流工程化** | CSV (源，提交) → DB (派生，本地生成) → Query，增量下载+全量兜底 |
| **测试覆盖** | pytest + Playwright E2E + 真实数据回归 (`test_regression_real_data.py`) |

**结论：vnpyS 的 alpha 模块偏学术（qlib 启发），我们的策略模块偏实战（操盘层 + 评分归因）。**---

## 五、数据库架构设计（推荐方案）

### 核心理念：**三层分离，OLAP/OLTP/Reference 分家**

```
┌────────────────────────────────────────────────────────────────────┐
│  Layer 1: Reference Layer (低频静态数据)                              │
│  ─────────────────────────────────────                              │
│  stock_basic / stock_profile / contract.json                        │
│  存储：SQLite (量化系统) 或 JSON                                    │
│  规模：~5000 行，永久驻留                                          │
│  更新：每天一次或更低频                                            │
├────────────────────────────────────────────────────────────────────┤
│  Layer 2: OLTP Layer (高频事务数据)                                  │
│  ─────────────────────────────────                                  │
│  order / trade / position / account / strategy_config /             │
│  backtest_result / simulator_log                                   │
│  存储：SQLite (短期) → PostgreSQL (中长期)                          │
│  规模：100w 账户每天 ~1000 订单 → 100w 行/天                        │
│  更新：实时写入                                                    │
│  特点：强事务、行级锁、ACID                                        │
├────────────────────────────────────────────────────────────────────┤
│  Layer 3: OLAP Layer (分析查询数据)                                   │
│  ─────────────────────────────────                                  │
│  daily_price / technical_indicators / fund_flow_data /             │
│  finance_summary / 衍生因子 / ML 训练集                              │
│  存储：Parquet (按 vt_symbol 切片) + DuckDB/ClickHouse (汇总)        │
│  规模：5200 股票 × 240 交易日 × N 年 = 当前 ~4.2M 行                │
│  更新：批处理 (T+1)                                               │
│  特点：列式压缩、predicate pushdown、polars 友好                     │
└────────────────────────────────────────────────────────────────────┘
```

### 5.1 参考层 (Reference Layer)

**存什么**：股票基本信息、合约信息、交易日历、行业分类

**怎么存**：
- 小 (≤10w 行)：继续用 SQLite 单表 `stock_basic`、`stock_profile`，现状就行
- 大 (≥10w 行)：换 DuckDB（嵌入式 OLAP，比 SQLite 查询快 10x）或 PostgreSQL

**理由**：参考数据几乎不写，纯读。SQLite 单表 100w 行以下毫无压力。

### 5.2 事务层 (OLTP Layer)

**存什么**：订单、成交、持仓、账户、策略配置、回测结果

**怎么存**：
- 现在：SQLite WAL 模式（已经开了）
- 未来：
  - 单账户：SQLite 够用，撑到 100w 订单没问题
  - 多账户/生产：PostgreSQL（行级锁、MVCC、并发写）
  - 时序数据 (tick/分钟)：QuestDB / TimescaleDB（专门做时序，压缩比 10x，查询快 100x）

**理由**：订单/成交是高频小事务，需要 ACID + 行级锁。SQLite WAL 已经能扛单进程多线程写，但 SQLite **不支持网络访问**，多机部署必须换 PG。

### 5.3 分析层 (OLAP Layer) ← **重点改造**

**存什么**：日线/分钟线行情、技术指标、资金流、财务、衍生因子

**怎么存**：

#### 方案 A：**Parquet 一股一文件 + 单文件全历史**（推荐，跟 vnpy alpha 对齐）

```
market_data/parquet/
├── daily/
│   ├── 000001.SZ.parquet    # 22KB，全历史 1000+ 行
│   ├── 000002.SZ.parquet
│   └── ... (5200 个文件)
├── minute_5m/
│   ├── 000001.SZ.parquet    # ~150KB，含近 1 年 5min K 线
│   └── ...
├── technical_indicators/
│   ├── 000001.SZ.parquet
│   └── ...
└── fund_flow/
    └── ...
```

**优势**：
- **OLAP 友好**：列式存储 + 谓词下推 (predicate pushdown)，全市场某日查询飞快
- **polars 原生**：vnpy alpha 就是这么做的，`pl.scan_parquet("dir/*.parquet").filter(...)` 一次 lazy，自动并行
- **增量更新友好**：单股票新数据 → `pl.concat([old, new]).unique().sort()`，不用锁整个库
- **压缩比高**：parquet snappy 压缩，5200 股票 × 1000 行 ≈ 100-200 MB（vs SQLite 980MB）
- **跨语言**：Pandas/polars/DuckDB/Spark 都能直接读

**劣势**：
- 小 universe 精确查询（50 只股票某一天）比 SQLite 慢（要打开 50 个文件）
- 没有原子事务保证（多文件更新要么全成功要么全失败）

**实测数据（你 parquet.py 里已经写过）**：

```
get_stock_list:        Parquet 1.1s vs SQLite 35s   ✅ 30x
全市场某日 5040 只:    Parquet 1.1s vs SQLite 6s    ✅ 5x
50 只某日:             Parquet 1.2s vs SQLite 2ms  ❌ 0.002x
单只股票 K 线:         Parquet 10ms vs SQLite 36ms ✅ 4x
```

**结论**：**分析查询用 Parquet，hot path 单点查询用 SQLite，两者并存。**

#### 方案 B：单库大宽表（不推荐）

```
daily_price_wide (
  trade_date DATE,
  code_000001 DOUBLE, code_000002 DOUBLE, ...
)
```

**劣势**：
- 列数随股票增长（5200 列），大部分 DB 引擎列数上限 1000-2000
- 任何股票变动要 ALTER TABLE
- 写放大严重（每天 5200 列更新）

**结论**：列数膨胀后必死，千万别走这条路。

#### 方案 C：ClickHouse / DuckDB（中期推荐）

当数据量到 1 亿+ 行、要做跨市场跨年分析时：
- **DuckDB** (嵌入式)：单文件、polars 兼容、单机分析之王
- **ClickHouse** (分布式)：列式分布式 OLAP，PB 级数据秒级响应

短期用 Parquet 就够，中期加 DuckDB 作为汇总查询层。

---

## 六、关于"每只股票单独成一个文件"的回答

### 直接答：**是，分析场景下这是最优解，但要注意分层**

**为什么对**：
1. **vnpy alpha 的官方做法**：`AlphaLab.save_bar_data` 就是 `daily/{vt_symbol}.parquet`，4.4 版本直接抄了 Qlib。
2. **polars / DuckDB 的设计哲学**：列式存储 + 单文件小数据集 + 跨文件 lazy scan，这是现代数据栈的标配。
3. **你的实测**：parquet 在 30x 全市场 scan 上碾压 SQLite。
4. **维护成本低**：增量更新只动一个文件，不用锁库；备份/同步按文件粒度；Git LFS 按文件管理。

**为什么不绝对**：
1. **事务数据不要拆**：订单/成交/持仓这种高频小事务，强依赖 ACID，Parquet 没事务。
2. **不要把所有表都拆成"一文件"**：
   - `stock_basic` 只有 5000 行，SQLite 单表完胜 parquet 50 个文件
   - `fund_flow_data` 是 (code, date) 二维关系，Parquet 按日期分文件反而合理（按 date 分区）
3. **跨表关联 (join) 是痛点**：parquet 不擅长 join，要靠 polars/DuckDB 内存关联

### 推荐的具体目录结构

```
quant-trading-system/
├── database/
│   ├── quant.db              # SQLite: 事务层 (orders, positions, configs, simulator_log)
│   └── quant_olap.duckdb     # DuckDB: 参考层 + 汇总查询
│
├── market_data/
│   ├── raw/                  # CSV 源数据 (Git LFS)
│   │   ├── kline_daily/      # 按年分 CSV
│   │   ├── reference/        # 公告/分红/新闻等
│   │   └── technical_indicators/
│   │
│   └── parquet/              # Parquet OLAP 层 (gitignore)
│       ├── daily/
│       │   ├── 000001.SZ.parquet    # 一股一文件，按 vt_symbol 命名
│       │   ├── 000002.SZ.parquet
│       │   └── ...
│       ├── minute_5m/
│       ├── technical_indicators/
│       ├── fund_flow/
│       └── finance_summary/
│
├── datafeed/
│   ├── base.py               # 抽象基类
│   ├── local.py              # SQLite (OLTP)  - hot path 单点查询
│   └── parquet.py            # Parquet (OLAP) - 全市场 scan、ML 训练
```

### 数据流

```
CSV 源 → SQLite (事务层，OLTP) ↘
                                 Parquet (分析层，OLAP) → polars lazy → ML/选股
                  ↑
             实盘 gateway 推送
```

### 改造路线（按 ROI 排序）

1. **【立刻】跑 `scripts/build_parquet.py`**
   - 把现在的 4.18M daily_price 从 SQLite 导出到 parquet
   - `market_data/parquet/daily/000001.SZ.parquet` 等 5200 个文件
   - 预计耗时 5-10 分钟，磁盘 200MB

2. **【1 周内】改造 `datafeed/parquet.py` 为生产可用**
   - 当前文件存在但目录为空 → 跑通 → 在 scoring/selection 里替换部分查询为 parquet

3. **【2 周内】加 DuckDB 作为查询加速层**
   - 现状是 SQLite 单表 980MB + 4 个表
   - DuckDB 直接 attach SQLite + Parquet，跨源查询不用 ETL

4. **【1 月内】抽象 DB 层（拆 OLTP/OLAP）**
   - `src/db/engine.py` 改为返回两个 engine：
     - `get_oltp_engine()` → SQLite (orders/positions/configs)
     - `get_olap_engine()` → DuckDB (聚合查询)
   - 配置文件 `database.oltp` + `database.olap` 两段

5. **【2 月内】分钟线 + Tick**
   - 加 `market_data/parquet/minute_5m/`
   - QuestDB 存 tick（时序优化）

---

## 七、不要盲目"对标 vnpyS"

vnpyS 是通用框架，覆盖 8 个市场（中/美/欧/港/期货/期权/外汇/数字货币）、30+ 券商、6 种 DB、4 种语言。

我们是一个**聚焦 A 股个人账户**的策略研发系统。**完全对标 vnpyS 会让我们变成平庸的"另一个 vnpy"，永远赶不上**。

应该**选择性吸收**：
- 吸收：alpha factor 表达引擎、数据库抽象、多周期支持、CTA 回测优化器
- 不吸收：RPC、Qt 图表、多市场网关、IB 接口、30+ 券商适配

**把"8 维评分 + XGBoost + 操盘层"做到极致，已经是 A 股领域的天花板。**

---

## 八、下一步建议（可立即执行）

| 优先级 | 事项 | 预计工时 | 收益 |
|---|---|---|---|
| P0 | 跑 `scripts/build_parquet.py`，落地 ParquetDatafeed | 1h | 全市场 scan 30x 提速 |
| P0 | 抽象 DB 层为 OLTP/OLAP 双引擎 | 4h | 未来换 PG/QuestDB 无痛 |
| P1 | 加 1m/5m 分钟 K 线支持 | 2 天 | 打开日内策略大门 |
| P1 | 接 xtp 或 ptrade 网关 | 1 周 | 从模拟盘走到实盘 |
| P2 | 移植 vnpy alpha factor 表达引擎 | 2 周 | 因子研发效率 10x |
| P2 | 集成 DuckDB 做跨源查询 | 3 天 | SQL 直接查 Parquet + SQLite |

---

## 附录：vnpyS 关键文件对照

| vnpyS 文件 | 我们对应实现 | 借鉴价值 |
|---|---|---|
| `vnpy/trader/database.py` | `src/db/engine.py` + `src/db/tables.py` | 数据库抽象接口设计 |
| `vnpy/trader/engine.py` | `src/gateway/main_engine.py` | MainEngine 架构 |
| `vnpy/trader/event.py` | `src/event/` | 事件类型常量 |
| `vnpy/alpha/lab.py` | 无 | **强烈建议抄**：factor 文件存储 + lab 流程 |
| `vnpy/alpha/dataset/template.py` | 无 | **强烈建议抄**：factor 表达引擎 |
| `vnpy/alpha/model/template.py` | `scripts/train_xgb_v4.py` | 模型训练接口标准化 |
| `vnpy/alpha/strategy/template.py` | `src/strategies/base_strategy.py` | 策略接口标准化 |
| `vnpy/chart/widget.py` | `src/web/` (ECharts) | 路线不同，Web 更友好 |
| `vnpy/rpc/` | 无 | 暂不需要 |