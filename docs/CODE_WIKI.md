# 量化交易系统 - Code Wiki 文档

> 版本: v2.1 | 更新日期: 2026-06-27

---

## 目录

- [1. 项目概述](#1-项目概述)
- [2. 系统架构](#2-系统架构)
- [3. 核心模块详解](#3-核心模块详解)
  - [3.1 回测引擎](#31-回测引擎)
  - [3.2 数据层](#32-数据层)
  - [3.3 数据库](#33-数据库)
  - [3.4 事件系统](#34-事件系统)
  - [3.5 网关层](#35-网关层)
  - [3.6 指标计算](#36-指标计算)
  - [3.7 模型层](#37-模型层)
  - [3.8 研究模块](#38-研究模块)
  - [3.9 评分系统](#39-评分系统)
  - [3.10 选股管线](#310-选股管线)
  - [3.11 策略库](#311-策略库)
  - [3.12 操盘层](#312-操盘层)
  - [3.13 Web 应用](#313-web-应用)
  - [3.14 引擎基类](#314-引擎基类)
  - [3.15 风控子系统](#315-风控子系统)
  - [3.16 常量与工具](#316-常量与工具)
- [4. 数据文件详解](#4-数据文件详解)
  - [4.1 market_data/ 原始数据](#41-market_data-原始数据)
  - [4.2 data/ 应用数据](#42-data-应用数据)
- [5. 关键类与函数速查](#5-关键类与函数速查)
- [6. 模块依赖关系图](#6-模块依赖关系图)
- [7. 项目运行方式](#7-项目运行方式)
- [8. 配置说明](#8-配置说明)
- [9. 测试体系](#9-测试体系)
- [10. 脚本工具集](#10-脚本工具集)
- [11. 术语表](#11-术语表)

---

## 1. 项目概述

本系统是一个面向 **A 股市场** 的 Python 量化交易平台，采用学术文献驱动的多维评分体系，实现从数据获取 → 多维度评分 → 策略回测 → 模拟交易 → 实盘对接的完整链路。

### 核心能力

| 能力 | 说明 |
|------|------|
| **多维选股评分** | 8 个独立评分维度（技术面、基本面、资金面、机构面、筹码面、情绪面、消息面、龙虎榜） |
| **双层策略架构** | 信号层（V6 超卖反转、V 龙头主升浪）+ 操盘层（仓位管理、止损止盈） |
| **回测引擎** | 支持单股信号回测和组合选股回测，含 A 股特殊规则（T+1、涨跌停、手续费） |
| **Web 仪表盘** | Flask + ECharts 实现选股、回测、组合监控、信号诊断、 walk-forward 分析等 25+ 页面 |
| **数据管道** | 支持 AKShare、Baostock、WeStock Data 三种数据源，覆盖日线、资金流、财务、融资融券等 |
| **研究实验室** | Alpha 因子研究、特征工程、XGBoost 模型训练 |

### 技术栈

| 层级 | 技术选型 |
|------|---------|
| 编程语言 | Python 3.10+ |
| Web 框架 | Flask + Jinja2 模板 |
| 数据库 | SQLite（WAL 模式） |
| 数据源 | AKShare、Baostock、WeStock Data |
| 机器学习 | XGBoost、scikit-learn |
| 可视化 | ECharts（前端 CDN） |
| 任务调度 | GitHub Actions（CI / 每日数据更新） |

---

## 2. 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                       Web 层 (Flask)                             │
│   routes/api.py  routes/main.py  routes/research.py              │
├─────────────────────────────────────────────────────────────────┤
│                   策略层 / 选股层                                 │
│   strategies/  selection/pipeline.py  scoring/  strategy/         │
├─────────────────────────────────────────────────────────────────┤
│                      回测引擎层                                   │
│   backtest/engine.py  backtest/portfolio_engine.py               │
├─────────────────────────────────────────────────────────────────┤
│            事件 / 网关 / OMS / 风控 / 指标层                      │
│   event/  gateway/  engine/  risk/  indicator/                   │
│   (MainEngine 在 gateway/, OmsEngine 在 engine/,                 │
│    RiskEngine 在 risk/ — 均继承 engine/base.py:BaseEngine)        │
├─────────────────────────────────────────────────────────────────┤
│                      数据层                                       │
│   data/manager.py  data/datafeed/  data/downloader.py            │
├─────────────────────────────────────────────────────────────────┤
│                      数据库 (SQLite)                              │
│   db/engine.py  db/repository.py  db/tables.py                   │
└─────────────────────────────────────────────────────────────────┘
```

### 请求处理流程（Web → 数据 → 评分 → 前端）

1. 用户访问页面（如 `/screener`）→ `web/routes/main.py` 渲染 `screener.html`
2. 前端调用 `/api/screen` → `web/routes/api.py` 调用 `selection/pipeline.py`
3. 管线通过 `data/manager.py` 获取数据 → `data/datafeed/parquet.py` 或 `data/datafeed/local.py`
4. `scoring/` 下各评分器计算子评分 → `scoring/combiner.py` 加权合并
5. 排名结果以 JSON 返回 → 前端渲染表格 + ECharts 图表

### 回测流程

1. `backtest/engine.py` 接收策略 + 参数集
2. 遍历历史 K 线，调用 `strategy.on_bar()` 生成信号
3. `engine/oms.py` 模拟订单执行（含滑点和手续费）
4. `backtest/portfolio_engine.py` 跟踪持仓、净值、回撤
5. `backtest/report.py` 生成指标报告（夏普比率、收益率、胜率等）+ HTML 报告

---

## 3. 核心模块详解

### 3.1 回测引擎

**目录:** `src/backtest/`

| 文件 | 职责 |
|------|------|
| `engine.py` | 核心事件驱动回测循环；管理策略生命周期、K 线遍历、结果收集 |
| `portfolio_engine.py` | 组合构建、仓位分配、风险预算、净值跟踪 |
| `base_strategy.py` | 所有策略的抽象基类；定义 `on_bar()`、`on_start()`、`on_end()` 钩子 |
| `base_selection_strategy.py` | 选股策略基类（基于排名而非信号） |
| `astock_strategy.py` | A 股专用策略基类（处理 T+1、印花税、涨跌停） |
| `report.py` | 生成回测绩效报告（夏普比率、最大回撤、胜率、HTML 输出） |

**设计模式:** 模板方法模式 — `BaseStrategy` 定义骨架，子类覆盖具体信号逻辑。

---

### 3.2 数据层

**目录:** `src/data/`

| 文件 | 职责 |
|------|------|
| `manager.py` | 统一数据访问入口；协调缓存、校验、数据源选择 |
| `datafeed/base.py` | 抽象数据源接口（`get_bars()`、`get_fund_flow()` 等） |
| `datafeed/parquet.py` | 基于 Parquet 的高性能列式读取 |
| `datafeed/local.py` | 基于 CSV 的本地数据读取（离线开发用） |
| `downloader.py` | 从 AKShare/Baostock API 下载数据 |
| `westock.py` | WeStock Data 实时行情适配器 |
| `westock_downloader.py` | WeStock 专用数据下载器 |
| `validator.py` | 数据质量校验（缺失值、异常值检测） |
| `simulation_repo.py` | 模拟回测场景下的内存数据仓库 |
| `xgb_loader.py` | XGBoost 模型加载 |
| `xgb_scaler.py` | ML 模型特征缩放 |

**设计模式:** 策略模式 — `datafeed/base.py` 定义接口；`parquet.py` 和 `local.py` 提供可互换实现。

---

### 3.3 数据库

**目录:** `src/db/`

| 文件 | 职责 |
|------|------|
| `engine.py` | SQLite 连接管理、WAL 模式、连接池 |
| `repository.py` | 通用仓储模式，封装 CRUD 操作 |
| `tables.py` | SQL 表 DDL 定义（日线、资金流、财务等 13 张业务表） |
| `sql_utils.py` | SQL 查询构建器和辅助函数 |

**数据库表清单（13 张业务表 + 3 张事务表）:**

| 表名 | 行数 | 说明 |
|------|------|------|
| `stock_basic` | 5,569 | 股票基础信息（代码、名称、市场、行业、上市日期） |
| `stock_profile` | 5,565 | 股票简介（流通股本、董事长、主营业务等） |
| `daily_price` | 4,181,854 | 日线行情（OHLCV、涨跌幅、换手率，2023-2026） |
| `technical_indicators` | 3,016,976 | 技术指标（MACD、RSI、KDJ、布林带，2024-2026） |
| `fund_flow_data` | 435,092 | 资金流向（主力/超大单/大单/中单/小单净流入） |
| `block_trade` | 54,700 | 大宗交易（成交价、溢价率、买卖方） |
| `dividend` | 40,377 | 分红送股（除权日、每股分红、转股/送股比例） |
| `announcements` | 4,616 | 公告信息（类型、标题、URL） |
| `holder_num` | 5,521 | 股东户数（户均持股、变化率） |
| `benchmark_data` | 9,000 | 指数 K 线（沪深300/中证500/上证50/中证1000/科创50/创业板指） |
| `finance_summary` | 5,172 | 财务摘要（ROE、EPS、资产负债率等 45 列） |
| `research_report` | 1,919 | 券商研报（评级、作者、机构） |
| `em_global_news` | 101 | 财经新闻（标题、摘要、来源） |
| `ths_hot_reason` | 138 | 同花顺热股（排名、概念、热度值） |
| `backtest_result` | 0 | 回测结果（事务表，运行时写入） |
| `strategy_config` | 0 | 策略配置（事务表） |

---

### 3.4 事件系统

**目录:** `src/event/`

| 文件 | 职责 |
|------|------|
| `engine.py` | 发布-订阅事件总线；支持异步处理器和优先级排序 |

**事件类型:** 字符串常量定义在 `src/event/__init__.py`，分组如下：

| 事件常量 | 含义 |
|---------|------|
| `EVENT_TICK` (`eTick`) | 行情推送（实时） |
| `EVENT_BAR` (`eBar`) | K 线推送（实时/历史） |
| `EVENT_QUOTE` (`eQuote`) | 五档行情 |
| `EVENT_SIGNAL` (`eSignal`) | 选股信号（V6 / V 龙头） |
| `EVENT_TARGET` (`eTarget`) | 目标持仓（`set_target` 触发） |
| `EVENT_ORDER` (`eOrder`) | 委托回报 |
| `EVENT_TRADE` (`eTrade`) | 成交回报 |
| `EVENT_CANCEL` (`eCancel`) | 撤单回报 |
| `EVENT_POSITION` (`ePosition`) | 持仓变化 |
| `EVENT_ACCOUNT` (`eAccount`) | 资金变化（被 RiskEngine 关联） |
| `EVENT_CONTRACT` (`eContract`) | 合约信息 |
| `EVENT_LOG` (`eLog`) / `EVENT_ERROR` (`eError`) / `EVENT_TIMER` (`eTimer`) | 系统事件 |

---

### 3.5 网关层

**目录:** `src/gateway/`

| 文件 | 职责 |
|------|------|
| `base_gateway.py` | 抽象订单网关接口（`send_order`/`cancel_order`/`connect`） |
| `main_engine.py` | 主网关编排器（`MainEngine`）；持有 `EventEngine` 单例 + 管理 `gateways/engines/strategies` 三个注册表 |
| `object.py` | 订单/成交数据对象（`OrderRequest`/`OrderData`/`TradeData`/`PositionData`/`AccountData`/`ContractData` 等） |

**设计目的:** 解耦策略逻辑与交易执行，使同一策略可在模拟盘和实盘间无缝切换。
**注:** `MainEngine` 虽然住在 `gateway/` 目录，但同时充当功能引擎注册中心（`add_engine(OmsEngine)` / `add_engine(RiskEngine)`），它和 `src/engine/base.py:BaseEngine` 是 v2.1 借鉴 vnpy 4.4 引入的双层引擎架构的一部分。

---

### 3.6 指标计算

**目录:** `src/indicator/`

| 文件 | 职责 |
|------|------|
| `atomic.py` | 原子指标原语（SMA、EMA、RSI、MACD、布林带、ATR 等） |
| `bar_generator.py` | K 线时间周期聚合（如 5 分钟 → 日线、Tick → 分钟线） |
| `operators.py` | 指标组合算子（交叉、背离、阈值判断） |
| `protocol.py` | 指标协议/接口定义 |

**设计思路:** 指标可组合 — 原子指标通过算子组合成复杂信号。

---

### 3.7 模型层

**目录:** `src/models/`

| 文件 | 职责 |
|------|------|
| `database.py` | 数据库配置模型 |
| `extreme_small_cap.py` | 微盘股识别与评分模型 |
| `market_thermometer.py` | 市场状态检测（牛市/熊市/震荡） |
| `repository.py` | 模型持久化仓储 |
| `shield_spear.py` | 风险管理模型（盾=防御/矛=进攻，动态调节） |
| `technical_voting.py` | 多指标投票系统（5 个策略投票委员会） |
| `three_factor.py` | 三因子模型（小市值 + 反转 + 低波动等权组合） |
| `_list_date.py` | 上市日期工具函数 |

---

### 3.8 研究模块

**目录:** `src/research/`

| 文件 | 职责 |
|------|------|
| `alpha_model.py` | Alpha 因子研究与评估 |
| `dataset.py` | 特征矩阵构建（用于 ML 训练, ADR-0008 默认接 LeaderFeatureBuilder） |
| `lab.py` | 研究实验室；编排实验、walk-forward 和样本外测试 |
| `features/leader_features.py` | 龙头股特征工程（量价领先指标, ADR-0008 默认 5 维） |

**工作流:** `lab.py` → `dataset.py`（构建特征）→ `alpha_model.py`（评估 Alpha）→ 训练 XGBoost → 导出模型

#### AStockDataset 默认行为（ADR-0008，2026-06-27）

`AStockDataset` 通过 `feature_builder` 参数控制特征工程行为，三档明确：

| 写法 | 维度 | 用途 |
|---|---|---|
| **默认** `AStockDataset(lookback=20, horizon=5)` | **9 维**（4 OHLCV + 5 技术指标） | **生产训练**（开箱即用，训练/推理同源） |
| **显式 None** `AStockDataset(..., feature_builder=None)` | **5 维** OHLCV 降级 | 单测 / mock（生产禁用） |
| **显式 v_leader** `feature_builder=v_leader_features.FeatureBuilder(engine)` | **74 维** | 生产 v_leader 策略（继承链） |

**5 维技术指标列名**（与 `v_leader_features.TECH_COLS` 严格一致）：

```
macd_hist  rsi14  kdj_k  kdj_j  boll_pos
```

**关键不变量**（ADR-0008 D4：训练/推理同源实时算）：
- `LeaderFeatureBuilder.build()` 实时算 → 不依赖 `technical_indicators` 表存在
- 训练 `fit()` 与推理 `predict()` 走同一 `feature_builder` 实例 → 逐 bit 一致
- `LeaderFeatureBuilder.build_batch()` 强制 `reset_kdj=True` 跨股票重置 KDJ 状态

**典型用法**：

```python
from src.research import AStockDataset
from src.research.features import LeaderFeatureBuilder

# 1. 默认 (9 维, 开箱即用)
ds = AStockDataset(lookback=20, horizon=5)
X, y = ds.fit("2020-01-01", "2023-12-31")

# 2. 显式 OHLCV 降级 (5 维, 仅测试)
ds = AStockDataset(lookback=20, horizon=5, feature_builder=None)

# 3. 74 维 v_leader (生产策略)
from src.strategies.v_leader_features import FeatureBuilder
fb = FeatureBuilder(engine, indicator_version="v1")
ds = AStockDataset(lookback=20, horizon=5, feature_builder=fb)
```

---

### 3.9 评分系统

**目录:** `src/scoring/`

| 文件 | 职责 |
|------|------|
| `base.py` | 抽象评分器基类 |
| `combiner.py` | 加权多维评分组合器（合并为最终排名） |
| `chip_scorer.py` | 筹码面评分（盈利集中度、换手率） |
| `fund_flow_scorer.py` | 资金面评分（个人/主力/机构净流入） |
| `fundamental_scorer.py` | 基本面评分（ROE、营收增长、估值） |
| `institutional_scorer.py` | 机构面评分（基金持仓、券商上调） |
| `news_event_scorer.py` | 新闻事件情绪评分 |
| `sentiment_scorer.py` | 市场情绪评分 |
| `technical_scorer.py` | 技术面评分（趋势、动量、均值回归） |

**管线流程:** 每个评分器返回 0–100 分 → `combiner.py` 按权重合并 → 综合评分 → 股票排名。

---

### 3.10 选股管线

**目录:** `src/selection/`

| 文件 | 职责 |
|------|------|
| `pipeline.py` | 端到端选股管线：数据加载 → 评分 → 过滤 → 排名 |

**入口:** `Pipeline.run(config)` — 接收配置字典，返回排名后的股票列表。

---

### 3.11 策略库

**目录:** `src/strategies/`

| 文件 | 职责 |
|------|------|
| `bollinger_breakout.py` | 布林带突破策略 |
| `low_turnover.py` | 低换手率 / 高确定性持有策略 |
| `low_volatility.py` | 低波动率异象策略 |
| `ma_cross.py` | 移动平均线交叉策略 |
| `macd_signal.py` | MACD 信号线交叉策略 |
| `reversal.py` | 均值回归 / 反转策略 |
| `rsi_reversal.py` | RSI 反转策略 |
| `simulator.py` | 模拟交易引擎封装 |
| `small_cap.py` | 小市值溢价策略 |
| `turtle_trading.py` | 海龟交易法则（Richard Dennis 趋势跟踪） |
| `v6_pipeline_hybrid.py` | V6 混合策略（多因子 + 多信号融合） |
| `v6_reversal_selection.py` | V6 超卖反转选股策略 |
| `v6_with_fallback.py` | V6 带兜底逻辑的策略 |
| `v_leader_features.py` | V 龙头特征策略 |
| `v_leader_main_surge.py` | V 龙头主升浪检测（XGBoost 驱动） |
| `trading/config.py` | 操盘策略配置 |
| `trading/position_sizer.py` | 仓位算法（固定/凯利/ATR/海龟） |
| `trading/stop_loss.py` | 止损/止盈逻辑（5 种模式） |

**策略分层:**
- **主推层（🟢）**: V6 超卖反转、V6 多维融合、V 龙头主升浪
- **后备层（🟡）**: 三因子均衡、极致小市值、盾+矛全天候、技术投票
- **归档层（📦）**: v2~v7 系列、bull_8d 系列

> **ADR-0006 策略基类收敛 4→2**（2026-06-27 落地）
>
> 历史共有 4 个并行基类：
>
> | 基类 | 位置 | 状态 |
> |---|---|---|
> | `BaseSelectionStrategy` | `src/backtest/base_selection_strategy.py` | ✅ 永久保留（9 个生产策略的真正基类） |
> | `EquityStrategy` | `src/strategy/equity_strategy.py` | ✅ 永久保留（vnpy 模板 + A 股选股，操盘层入口） |
> | `AlphaStrategy` | `src/strategy/alpha_strategy.py` | ⚠️ `DeprecationWarning`，v3.0 删除（当前 0 业务继承） |
> | `BaseStrategy` | `src/backtest/base_strategy.py` | 🗑️ 已降级重命名为 `BacktestingPyAdapter`（仅 5 个单股回测策略使用） |
>
> 新策略应继承 `BaseSelectionStrategy`（选股）或 `EquityStrategy`（vnpy 操盘）。详见 `docs/adr/0006-strategy-base-classes.md`。

---

### 3.12 操盘层

**目录:** `src/strategies/trading/`

| 文件 | 职责 |
|------|------|
| `config.py` | 操盘配置（TradingConfig 数据类） |
| `position_sizer.py` | 仓位计算算法：固定比例、凯利公式、ATR 自适应、海龟仓位 |
| `stop_loss.py` | 止损/止盈：固定比例、ATR 移动止损、双止损、时间止损、无利润止损 |

---

### 3.13 Web 应用

**目录:** `src/web/`

| 文件 | 职责 |
|------|------|
| `app.py` | Flask 应用工厂；注册蓝图、错误处理器、上下文处理器 |
| `auth.py` | 身份验证（Session）和 RBAC 权限控制 |
| `routes/api.py` | REST API 端点（选股、回测、组合、信号、模拟交易） |
| `routes/main.py` | 页面路由（GET 请求渲染 Jinja2 模板） |
| `routes/research.py` | 研究实验室页面路由 |

**模板页面（25+）:** 仪表盘、选股器、回测列表/详情、组合、预测、诊断、行业、信号、Walk-forward、工作台、数据监控、资金流报告、研究报告等。

---

### 3.14 引擎基类

**目录:** `src/engine/`

| 文件 | 职责 |
|------|------|
| `base.py` | `BaseEngine` 抽象根类；`start/stop/close` 状态机（NEW → ACTIVE → STOPPED），统一 `is_active` 标志 + `src.log` 集成 |
| `oms.py` | `OmsEngine`（**lazy import**）：全局缓存 ticks/orders/trades/positions/accounts/contracts；订阅 6 类事件；A 股 T+1 字段维护（`td_volume = volume - yd_volume`） |

**继承关系（v2.1 借鉴 vnpy 4.4 BaseEngine）：**

```
BaseEngine (src/engine/base.py)
  ├── EventEngine   (src/event/engine.py)
  ├── OmsEngine     (src/engine/oms.py)
  └── [RiskEngine 当前未继承 BaseEngine, 见 §3.15 — 计划 v2.2 改造]
```

> **注：**`src.engine` 包对 `OmsEngine` 采用 `__getattr__` lazy import，避免 `src.engine ↔ src.event` 循环依赖。用户 `from src.engine import OmsEngine` 写法仍可用。

---

### 3.15 风控子系统

**目录:** `src/risk/`（v2.1 新增，借鉴 vnpy.trader.engine.RiskManager；v2.2 经 [ADR-0007](../adr/0007-risk-engine.md) 完善）

| 文件 | 职责 |
|------|------|
| `engine.py` | `RiskEngine` 主类 + `RiskConfig` 数据类；下单前风控拦截（含 6 步检查 + 双通道告警） |
| `event_data.py` | `RiskAlert` dataclass（`EVENT_RISK_ALERT` 的载荷）+ `RiskAlertLevel` 字面量类型 |
| `sector_map.py` | 行业字典（`get_sector(vt_symbol)` → 行业字符串；默认覆盖 TOP20 持仓股，未知 = "未知"） |
| `__init__.py` | 导出 `RiskEngine`、`RiskConfig` |

#### 3.15.1 类与配置

| 项 | 名称 | 说明 |
|---|---|---|
| 类名 | `RiskEngine` | 风控引擎（zh_name：风控引擎 / en_name：RiskEngine / description：下单前单笔/单日/集中度风控检查） |
| 配置 | `RiskConfig`（`@dataclass`） | 全部阈值都是"上限"，触发即拒绝下单 |
| 事件载荷 | `RiskAlert` | `EVENT_RISK_ALERT` 的 data，字段：`reason` / `level` / `vt_symbol` / `timestamp` |

**`RiskConfig` 9 字段**（`src/risk/engine.py:40-78`，ADR-0007 修复 2/3 落地后）：

| 分类 | 字段 | 默认 | 说明 |
|------|------|------|------|
| **单笔控制** | `max_order_pct` | `0.20` | 单股最大仓位比例（**小数**，相对账户总资产；ADR-0007 修复 2 起真正生效） |
| | `max_order_volume` | `100_000_000` | 单笔最大股数（A 股单笔上限） |
| | `max_order_amount` | `5_000_000` | 单笔最大金额（500 万，小账户够用） |
| **单日控制** | `max_daily_trades` | `50` | 日内最大交易次数（双向；仅 ALLTRADED 计入，ADR-0007 修复 4） |
| | `max_daily_drawdown` | `0.05` | 日内净值回撤熔断（**小数**，0.05 = 5%；PR2.2 起改小数化） |
| | `max_daily_loss` | `100_000` | 日内最大亏损金额（绝对值，元） |
| **全局控制** | `max_positions` | `10` | 同时最大持仓数（新开仓受限，已持仓加仓放行） |
| **集中度** | `sector_concentration_pct` | `0.40` | 单行业最大占比（**小数**；风控阶段比 selection 阶段 0.30 宽松） |
| | `single_symbol_concentration_pct` | `0.22` | 单标的占比（**小数**；与 `MAX_SINGLE_POSITION_PCT` 对齐） |
| **账户兜底** | `initial_balance` | `0.0` | 启动时账户余额（`EVENT_ACCOUNT` 未到账前的兜底） |
| **可注入** | `sector_map` | `None` | `vt_symbol → 行业` 查表函数（默认 `src.risk.sector_map.get_sector`） |

#### 3.15.2 关键方法签名

| 方法 | 签名 | 说明 |
|------|------|------|
| `__init__` | `(event_engine: EventEngine, config: RiskConfig \| None = None)` | 自动注册 `EVENT_ORDER` / `EVENT_TRADE` / `EVENT_ACCOUNT` 订阅 |
| `on_order(event)` | `None` | 订单回报回调 → **仅 ALLTRADED** 时 `_daily_trades += 1`（ADR-0007 修复 4） |
| `on_trade(event)` | `None` | 成交回报回调 → 更新持仓 + 累计 PnL + 日净值峰值 + `_last_prices` |
| `on_account(event)` | `None` | 账户回报回调 → 首次有效余额锁定后**注销订阅**（省 CPU） |
| `check_order(order_req)` | `tuple[bool, str]` | **下单前**单笔检查（6 步；兼容 `dict`/`OrderRequest`/duck-typed） |
| `check_daily_limit()` | `tuple[bool, str]` | 日内熔断检查（交易次数 / 亏损 / 回撤） |
| `_sector_held_amount(sector)` | `float` | 计算某行业当前持仓金额（基于 `_last_prices` × `volume`） |
| `_reject_order / _reject_daily` | `tuple[bool, str]` | 拒绝 + 同步 `put EVENT_RISK_ALERT`（warn / error 级别） |
| `_ensure_daily_reset()` | `None` | 跨日期自动复位日内统计 |
| `get_stats()` | `dict` | 调试用统计快照（含 account_balance / position_count 等） |

#### 3.15.3 `check_order` 检查链路（**6 步**，按顺序）

| # | 检查 | 触发条件 | 拒绝时 level |
|---|------|----------|--------------|
| 1 | 单笔股数 | `vol > max_order_volume` | `warn` |
| 2 | 单笔金额 | `vol * price > max_order_amount` | `warn` |
| 3 | 最大持仓数 | 新开仓（`held <= 0`）且当前持仓数 ≥ `max_positions` | `warn` |
| 4 | 单股仓位比例 | `account_balance > 0` 且 `amount/balance > max_order_pct` | `warn` |
| 5 | 单标的集中度 | `account_balance > 0` 且 `amount/balance > single_symbol_concentration_pct` | `warn` |
| 6 | 单行业集中度 | `account_balance > 0` 且 `(已持行业金额 + amount)/balance > sector_concentration_pct` | `warn` |

**步骤 4-6 的前置条件**：`account_balance > 0`。`EVENT_ACCOUNT` 未到账时使用 `initial_balance` 兜底，缺省 0 → 步骤 4-6 静默跳过（**不报错**，避免回测场景被误拒）。

`check_daily_limit` 链路（3 步，触发熔断返回 `False`）：

1. 日内交易次数 ≥ `max_daily_trades` → 拒（`error`）
2. 日内亏损 ≤ `-max_daily_loss`（绝对值） → 拒（`error`）
3. 日内净值回撤（基于 `daily_peak`） ≥ `max_daily_drawdown` → 拒（`error`）

#### 3.15.4 事件流（双通道：hard reject + EVENT_RISK_ALERT）

```
                    ┌──────────────────────────────────┐
   策略层下单 ──────►│ risk.check_order(order_req)      │
                    │   ├─ 步骤 1-6 顺序校验            │
                    │   └─ 任一失败 → 步骤 _reject_order│
                    │       ├─ return (False, reason)   │  ← hard reject
                    │       └─ put EVENT_RISK_ALERT     │  ← soft event
                    │            (RiskAlert, level=warn)│     (UI / 监控消费)
                    └──────────────────────────────────┘
                                       │
                                       ▼
                    ┌──────────────────────────────────┐
   OMS 推送 ────────►│ EventEngine.register(EVENT_*)  │
   (Order/Trade/     │   ├─ on_order: ALLTRADED → 计数  │
    Account)         │   ├─ on_trade: 更新持仓+峰值+价  │
                    │   └─ on_account: 锁定余额+注销   │
                    └──────────────────────────────────┘
```

**双通道语义**（ADR-0007 D3 + 方案 C）：
- **hard reject（默认）**：所有 check_* 返回 `(False, msg)` 时**阻断下单**。
- **EVENT_RISK_ALERT（软告警）**：同步 `put` 一条 `RiskAlert`，载荷字段 `reason` / `level` / `vt_symbol` / `timestamp`，让 UI / 监控 / 推送系统消费。
- **容错**：`_emit_alert` 用 `try/except` 包住, 推送失败仅 `logger.warning`, 不影响主流程拦截。

#### 3.15.5 集成关系

```
MainEngine (src/gateway/main_engine.py)
  └── add_engine(RiskEngine)              # 通过 BaseEngine 注册表 (v2.3 计划)
        ├── self.event_engine             # 持有 EventEngine 引用
        ├── self._positions               # vt_symbol → 净持仓 (从 EVENT_TRADE 累计)
        ├── self._last_prices             # vt_symbol → 最近成交价 (集中度计算用)
        ├── self._account_balance         # 优先 EVENT_ACCOUNT 注入, 兜底 initial_balance
        └── self._daily_{trades,pnl,peak} # 每日 00:00 _ensure_daily_reset 自动复位

调用顺序 (策略层 → 风控 → 网关):
  EquityStrategy.set_target(vt_symbol, target)        # ADR-0006 v2.2 操盘入口
    └── 触发 EVENT_TARGET
          └── EquityStrategy 处理
                ├── risk.check_order(order_req)        # 单笔拦截 (6 步)
                │     └─ 拒 → put EVENT_RISK_ALERT
                ├── risk.check_daily_limit()          # 日内熔断
                │     └─ 熔断 → put EVENT_RISK_ALERT (error)
                └── main_engine.send_order(...)       # 通过则委托给第一个 Gateway
                          │
                          ▼
                  OMS 推送 EVENT_ORDER / EVENT_TRADE / EVENT_ACCOUNT
                          │
                          ▼
                  RiskEngine.on_order / on_trade / on_account
                          │
                          ▼
                  维护 _positions / _last_prices / _daily_* / _account_balance
```

**已知限制（v2.2 现状）：**
- `RiskEngine` **不继承** `BaseEngine`，因此无法通过 `main_engine.get_engine("risk")` 检索（仅在 `self._event_engine` 持有强引用）。计划 v2.3 改造为 `BaseEngine` 子类，统一 `add_engine()` 接口。
- 不区分多空方向（用 `volume` 直接累加），A 股 T+1 单边做多语义下等同于净持仓。v3.0 接融券时需加 `direction` 字段。
- `on_account` 锁定余额后**注销订阅**，账户变动（入金/出金）目前**不感知**——v3.0 实盘时需加 `EVENT_ACCOUNT_REFRESH` 事件或定时重订阅。
- `sector_map` 硬编码 22 只 TOP20 持仓股，全市场覆盖率 0.4%；v3.0 接申万行业分类（`src/selection/sector_constraint.load_industry_map`）。详细见 [dev-notes/risk-engine-decisions.md §3.1](../dev-notes/risk-engine-decisions.md)。
- `_last_prices` 选 `EVENT_TRADE` 更新而非 `EVENT_TICK`：1% 误差 < 22% 阈值，CPU/内存 5k×10tick/s → 5k×1trade/day。详见 [dev-notes §1.2](../dev-notes/risk-engine-decisions.md)。

#### 3.15.6 关联 ADR / 实施入口

- [ADR-0007 RiskEngine 完善](../adr/0007-risk-engine.md) — 本节设计依据（5 个不确定项 + 4 个核心修复 + 备选方案 C 双通道）
- [ADR-0006 策略基类收敛](../adr/0006-strategy-base-classes.md) — 上游调用方 `EquityStrategy`（v2.2 操盘入口）
- [ADR-0005 vnpy 命名前缀](../adr/0005-data-object-vnpy-naming.md) — 沿用 `vt_symbol` / `vt_orderid` 字段命名
- 实施入口：
  - `src/risk/engine.py` — `RiskEngine` + `RiskConfig`（437 行）
  - `src/risk/event_data.py` — `RiskAlert` dataclass（39 行）
  - `src/risk/sector_map.py` — `get_sector` 行业字典（86 行）
- 守门豁免：`dev_tools/hooks/check_naming.py` `RiskEngine: vnpy-shorthand`（命名规范允许 `Engine` 后缀类去掉 vnpy 的 `Manager` 后缀）
- 实施期边角讨论：[docs/dev-notes/risk-engine-decisions.md](../dev-notes/risk-engine-decisions.md)（草稿层）

**相关常量（非 RiskEngine 字段，来自 `src/constants/risk.py`）：**
止损/止盈百分比（策略级使用，与 RiskEngine 的下单前拦截**正交**）：

| 常量 | 值 | 说明 |
|------|---|------|
| `STOP_LOSS_DEFAULT` | `0.05` | 默认 5% 止损 |
| `STOP_LOSS_AGGRESSIVE` | `0.07` | 激进型 7% |
| `STOP_LOSS_CONSERVATIVE` | `0.03` | 保守型 3% |
| `TRAILING_STOP_PCT` | `0.12` | 移动止盈触发 12% |
| `TRAILING_DD_PCT` | `0.03` | 触发后回撤 3% 卖出 |
| `TIME_STOP_DAYS_DEFAULT` | `20` | 默认持有 20 天 |
| `MAX_DRAWDOWN_EXIT_PCT` | `0.25` | 组合回撤 25% 全清仓 |
| `MAX_SINGLE_POSITION_PCT` | `0.22` | 单只持仓 ≤ 22% |

> **单位约定（PR1.2 起）：**百分比统一用**小数**（`0.05` = 5%），避免与历史整数百分比版本混淆 100 倍。

---

### 3.16 常量与工具

**目录:** `src/constants/` 和 `src/utils/`

| 文件 | 职责 |
|------|------|
| `constants/fees.py` | 交易费用常量（佣金费率、印花税、最低佣金） |
| `constants/market.py` | 市场常量（涨跌停限制、交易时间） |
| `constants/risk.py` | 风险参数常量（最大回撤阈值、VaR 置信度） |
| `constants/signal.py` | 信号类型常量 |
| `utils/keys.py` | 键名常量 |
| `utils/market.py` | 市场工具函数 |
| `utils/pricing.py` | 定价工具函数 |

---

## 4. 数据文件详解

### 4.1 market_data/ 原始数据

本目录存储所有从外部数据源获取的原始数据文件，是数据库的唯一导入源。

#### 4.1.1 `market_data/raw/kline_daily/` — 日线行情

| 文件 | 大小 | 行数 | 说明 |
|------|------|------|------|
| `kline_daily_2023.csv` | 68.5 MB | ~750,000 | 2023 年全市场 A 股日线数据（开盘价、最高价、最低价、收盘价、成交量、成交额、涨跌幅、换手率） |
| `kline_daily_2024.csv` | 86.3 MB | ~940,000 | 2024 年全市场 A 股日线数据 |
| `kline_daily_2025.csv` | 88.9 MB | ~960,000 | 2025 年全市场 A 股日线数据 |
| `kline_daily_2026.csv` | 40.9 MB | ~430,000 | 2026 年全市场 A 股日线数据（截至当前交易日） |
| `kline_daily_2026_normalized.csv` | 41.8 MB | ~430,000 | 2026 年日线数据的归一化版本（用于机器学习特征工程） |

**数据来源:** AKShare（东方财富接口）  
**字段:** `code`（股票代码）、`trade_date`（交易日期）、`open`、`high`、`low`、`close`、`volume`、`amount`、`pct_change`、`turnover`

---

#### 4.1.2 `market_data/raw/technical_indicators/` — 技术指标

| 文件 | 大小 | 行数 | 说明 |
|------|------|------|------|
| `tech_indicators_2024.csv` | 96.2 MB | ~1,000,000 | 2024 年全市场技术指标（MACD DIF/DEA/柱、RSI14、KDJ K/D/J、布林带中轨/上轨/下轨） |
| `tech_indicators_2025.csv` | 100.9 MB | ~1,050,000 | 2025 年全市场技术指标 |
| `tech_indicators_2025_part1.csv` | 49.8 MB | ~520,000 | 2025 年上半年技术指标（冗余备份，`build_db.py` 自动跳过） |
| `tech_indicators_2025_part2.csv` | 50.7 MB | ~530,000 | 2025 年下半年技术指标（冗余备份，`build_db.py` 自动跳过） |
| `tech_indicators_2026.csv` | 45.1 MB | ~470,000 | 2026 年全市场技术指标 |

**数据来源:** 由 `scripts/technical_indicators.py` 基于日线数据预计算  
**注意:** `part1` 和 `part2` 是 2025 年的冗余拆分备份，`build_db.py` 导入时自动跳过这两个文件。

---

#### 4.1.3 `market_data/raw/reference/` — 参考数据

| 文件 | 大小 | 行数 | 说明 |
|------|------|------|------|
| `tencent_quotes.csv` | 284 KB | ~5,209 | 腾讯行情导出的股票列表（代码、名称、市场、上市日期、行业等）。用于构建 `stock_basic` 表 |
| `block_trade.csv` | 9.2 MB | ~54,700 | 大宗交易数据。字段：代码、交易日期、成交价、收盘价、溢价率、成交量、成交额、买方营业部、卖方营业部。数据跨度 2000-08 至 2026-06 |
| `dividend.csv` | 1.6 MB | ~40,377 | 分红送股数据。字段：代码、除权日、每股税前分红、转股比例、送股比例、股权登记日。数据跨度 1991 至 2026 |
| `announcements.csv` | 764 KB | ~4,616 | 上市公司公告。字段：代码、日期、公告类型、标题、URL。2026 年 3-6 月数据 |
| `research_report.csv` | 266 KB | ~1,919 | 券商研报数据。字段：代码、日期、评级、评级变化、标题、作者、机构、URL |
| `em_global_news.csv` | 37 KB | ~101 | 东方财富全球财经新闻。字段：日期、标题、URL、摘要、来源 |
| `investment_calendar.csv` | 7.8 KB | ~投资日历 | 投资日历事件（新股IPO、股东大会等） |
| `lockup_expiry.csv` | 21 KB | ~限售解禁 | 限售股解禁日期和数量 |
| `sector_board.csv` | 966 B | ~行业板块 | 行业板块分类映射 |
| `ths_hot_reason.csv` | 11.7 KB | ~138 | 同花顺热股数据。字段：日期、排名、代码、名称、热度值、所属概念、入选原因、涨跌幅 |

---

#### 4.1.4 `market_data/raw/technical/` — 技术面补充

| 文件 | 大小 | 说明 |
|------|------|------|
| `tech_indicators_2026.csv` | 1.4 MB | 2026 年技术指标的补充/修正版本 |

---

#### 4.1.5 汇总数据文件（market_data/ 根目录）

| 文件 | 大小 | 行数 | 说明 |
|------|------|------|------|
| `benchmark_data.csv` | ~9,000 行 | 6 个基准指数（沪深300、中证500、上证50、中证1000、科创50、创业板指）× 约 1500 天的收盘价数据。来源：WeStock Data |
| `stock_profile.csv` | ~5,565 行 | 股票详细简介（流通股本、董事长、主营业务、注册资本、注册地址等）。来源：WeStock Data 批量 API |
| `finance_summary.csv` | ~5,172 行 | 财务摘要数据 45 列（ROE、EPS、每股净资产、资产负债率、营业收入增长率等）。来源：Baostock 利润表 |
| `fund_flow_120d.csv` | ~435,092 行 | 120 天滚动资金流向数据。字段：代码、市场、名称、交易日期、主力净流入、超大单净流入、大单净流入、中单净流入、小单净流入（单位：万元） |
| `holder_num.csv` | ~5,521 行 | 股东户数数据。字段：代码、截止日期、股东户数、上期户数、变化率、户均持股数 |
| `margin_trading.csv` | ~融资融券 | 融资融券数据（融资余额、融券余额、融资买入额等） |
| `share_structure.csv` | ~股本结构 | 股本结构变动（总股本、流通股本、限售股本变化） |

---

#### 4.1.6 `market_data/parquet/` — Parquet 列式存储

| 目录 | 文件数 | 说明 |
|------|--------|------|
| `parquet/daily/` | ~10,200 | 每只股票一个 Parquet 文件（如 `sz000001.SZ.parquet`），存储日线行情数据。用于高性能批量扫描和全市场选股 |

**优势:** Parquet 列式存储比 CSV 读取快 10-50 倍，特别适合需要扫描全市场所有股票的场景。

---

### 4.2 data/ 应用数据

本目录存储由代码生成的中间数据和模型文件。

| 文件 | 大小 | 说明 |
|------|------|------|
| `xgb_model.json` | 3.0 MB | XGBoost v4 模型文件（用于 V 龙头主升浪策略）。8 维评分 + 技术指标 + 滞后/动量特征 → 月度预测。AUC = 0.7912 |
| `xgb_scaler.json` | 5.5 KB | 对应 XGBoost 模型的特征缩放参数（StandardScaler 均值和标准差） |
| `bull_sample_pool.json` | 44.6 KB | 牛市 8 维评分候选池数据 |
| `bull_8d_weights.json` | 326 B | 牛市 8 维评分权重配置 |
| `bull_8d_monthly_result.json` | 2.4 KB | 牛市 8 维月度评分结果 |
| `daily_tech_weights.json` | 322 B | 日线技术面评分权重配置 |
| `weekly_sample_summary.json` | 271 B | 周度样本摘要 |
| `picks_by_date_cache.json` | 5.1 MB | 按日期缓存的选股结果（用于快速展示历史选股记录） |
| `v3_final_summary.json` | 2.1 KB | v3 模型最终汇总指标 |
| `fund_flow_v1_报告.md` | 3.4 KB | 资金流 v1 分析报告 |
| `v3_技术面评分器分析报告.md` | 3.5 KB | 技术面 v3 评分器分析报告 |
| `六维评分_框架_搭建报告.md` | 2.6 KB | 六维评分框架搭建过程记录 |
| `六维评分_v2优化报告.md` | 5.6 KB | 六维评分 v2 优化记录 |
| `六维评分_全面完成报告.md` | 6.0 KB | 六维评分完成总结 |

---

## 5. 关键类与函数速查

### 回测引擎

| 类 | 位置 | 用途 |
|----|------|------|
| `BaseStrategy` | `src/backtest/base_strategy.py` | 所有策略的抽象基类 |
| `BaseSelectionStrategy` | `src/backtest/base_selection_strategy.py` | 选股排名策略基类 |
| `AstockStrategy` | `src/backtest/astock_strategy.py` | A 股专用策略基类 |
| `BacktestEngine` | `src/backtest/engine.py` | 事件驱动回测循环 |
| `PortfolioEngine` | `src/backtest/portfolio_engine.py` | 组合跟踪与风险管理 |
| `ReportGenerator` | `src/backtest/report.py` | 回测绩效报告生成 |

### 数据层

| 类 | 位置 | 用途 |
|----|------|------|
| `DataManager` | `src/data/manager.py` | 统一数据访问入口 |
| `BaseDataFeed` | `src/data/datafeed/base.py` | 抽象数据源接口 |
| `ParquetDataFeed` | `src/data/datafeed/parquet.py` | Parquet 高性能读取 |
| `LocalDataFeed` | `src/data/datafeed/local.py` | CSV 本地读取 |
| `DataDownloader` | `src/data/downloader.py` | 外部数据下载器 |

### 评分系统

| 类 | 位置 | 用途 |
|----|------|------|
| `BaseScorer` | `src/scoring/base.py` | 抽象评分器接口 |
| `Combiner` | `src/scoring/combiner.py` | 多维评分加权组合器 |
| `TechnicalScorer` | `src/scoring/technical_scorer.py` | 技术面评分 |
| `FundFlowScorer` | `src/scoring/fund_flow_scorer.py` | 资金面评分 |
| `FundamentalScorer` | `src/scoring/fundamental_scorer.py` | 基本面评分 |
| `ChipScorer` | `src/scoring/chip_scorer.py` | 筹码面评分 |
| `InstitutionalScorer` | `src/scoring/institutional_scorer.py` | 机构面评分 |
| `SentimentScorer` | `src/scoring/sentiment_scorer.py` | 情绪面评分 |
| `NewsEventScorer` | `src/scoring/news_event_scorer.py` | 消息面评分 |

### 指标计算

| 类 | 位置 | 用途 |
|----|------|------|
| `AtomicIndicator` | `src/indicator/atomic.py` | 原子指标计算 |
| `BarGenerator` | `src/indicator/bar_generator.py` | K 线周期聚合 |
| `IndicatorOperator` | `src/indicator/operators.py` | 指标组合算子 |

### 研究模块

| 类 | 位置 | 用途 |
|----|------|------|
| `AlphaModel` | `src/research/alpha_model.py` | Alpha 因子评估 |
| `DatasetBuilder` | `src/research/dataset.py` | 特征矩阵构建 |
| `ResearchLab` | `src/research/lab.py` | 实验编排与 OOS 测试 |

### 网关/事件

| 类 | 位置 | 用途 |
|----|------|------|
| `BaseGateway` | `src/gateway/base_gateway.py` | 订单网关抽象 |
| `MainEngine` | `src/gateway/main_engine.py` | 网关编排器 + 引擎注册中心 |
| `EventEngine` | `src/event/engine.py` | 发布-订阅事件总线（同步派发） |
| `Event` | `src/event/engine.py` | 事件对象（`type`/`data`/`timestamp`） |

### 引擎基类 / OMS / 风控（v2.1）

| 类 | 位置 | zh_name / en_name | 用途 |
|----|------|--------------------|------|
| `BaseEngine` | `src/engine/base.py` | 引擎基类 / BaseEngine | 所有功能引擎的抽象根；状态机 NEW→ACTIVE→STOPPED |
| `OmsEngine` | `src/engine/oms.py` | 订单管理引擎 / OmsEngine | 全局缓存 + 6 类事件订阅 + A 股 T+1 维护 |
| `RiskEngine` | `src/risk/engine.py` | 风控引擎 / RiskEngine | 下单前单笔/单日/集中度风控拦截（不继承 BaseEngine，v2.3 计划改造） |
| `RiskConfig` | `src/risk/engine.py` | 风控配置 / RiskConfig | `@dataclass`，9 个阈值字段（详见 §3.15.1，含 2 个集中度字段） |

### 策略基类（ADR-0006 收敛 4→2）

| 类 | 位置 | 状态 | 用途 |
|----|------|------|------|
| `BaseSelectionStrategy` | `src/backtest/base_selection_strategy.py` | ✅ 永久 | 9 个生产选股策略的基类（纯 pandas） |
| `EquityStrategy` | `src/strategy/equity_strategy.py` | ✅ 永久 | vnpy 模板 + A 股选股，操盘层入口（V6 已走通） |
| `AlphaStrategy` | `src/strategy/alpha_strategy.py` | ⚠️ Deprecated | v2.1.1 起 `DeprecationWarning`；v3.0 删除。仍持有 `set_target/buy/sell/cover` API |
| `BacktestingPyAdapter` | `src/backtest/base_strategy.py`（原 `BaseStrategy`） | 🗑️ 降级 | 5 个单股回测策略的 backtesting.py 薄包装 |

> **`set_target(vt_symbol, target)`** 在 `src/strategy/alpha_strategy.py:192`，由 `AlphaStrategy`（及子类 `EquityStrategy`）持有，被 `RiskEngine.check_order` 间接拦截——风险点是 v2.2 需要把拦截点正式接到 `set_target` 路径。

---

## 6. 模块依赖关系图

```
web/routes/api.py
  └── selection/pipeline.py
        └── scoring/combiner.py
              ├── scoring/technical_scorer.py ──→ indicator/atomic.py
              ├── scoring/fund_flow_scorer.py ──→ data/manager.py
              ├── scoring/fundamental_scorer.py ──→ data/manager.py
              ├── scoring/institutional_scorer.py ──→ data/manager.py
              ├── scoring/chip_scorer.py ──→ data/manager.py
              ├── scoring/sentiment_scorer.py ──→ data/manager.py
              └── scoring/news_event_scorer.py ──→ data/manager.py

backtest/engine.py
  └── backtest/base_strategy.py
        └── strategies/* (具体策略实现)

data/manager.py
  ├── data/datafeed/parquet.py ──→ db/repository.py
  ├── data/datafeed/local.py
  └── data/downloader.py (AKShare/Baostock)

research/lab.py
  ├── research/dataset.py ──→ data/manager.py
  └── research/alpha_model.py ──→ research/features/leader_features.py

scoring/combiner.py
  └── data/xgb_loader.py ──→ data/xgb_model.json
                           └── data/xgb_scaler.json
```

### 外部依赖

| 包名 | 用途 |
|------|------|
| `flask` | Web 框架 |
| `pandas` | 数据处理 |
| `numpy` | 数值计算 |
| `xgboost` | 梯度提升模型 |
| `scikit-learn` | ML 工具（缩放、指标） |
| `akshare` | A 股数据 API |
| `baostock` | 历史数据 API |
| `pyarrow` | Parquet 文件 I/O |
| `jinja2` | 模板渲染 |

---

## 7. 项目运行方式

### 环境搭建

```bash
# Python 3.10+
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

### 初始化数据库

```bash
python scripts/build_db.py              # 全量重建（清空重灌）
python scripts/build_db.py --incremental # 增量追加（UNIQUE 防重复）
python scripts/build_db.py --incremental --table finance  # 单表增量
```

### 启动 Web 服务

```bash
python run.py
# → Flask 开发服务器 http://127.0.0.1:5050
```

### 数据下载

```bash
python run.py --download               # 全量下载
python run.py --download-incr           # 增量下载
python scripts/update_all_data.py       # 全量更新脚本
python scripts/incremental_update.py    # 每日增量更新
```

### 运行回测

```bash
python scripts/batch_backtest_v3.py    # 批量回测
python scripts/bull_backtest_v3.py      # 牛市模型回测
```

### 训练 ML 模型

```bash
python scripts/train_xgb_v4.py
# → 输出模型到 data/xgb_model.json + data/xgb_scaler.json
```

### 运行测试

```bash
pytest tests/ -v                       # 全部测试
pytest tests/test_backtest.py -v        # 回测模块测试
pytest tests/e2e/ -v                   # E2E 端到端测试
flake8 src/ tests/                     # 代码风格检查
```

---

## 8. 配置说明

### `config/config.yaml` — 全局配置

```yaml
system:
  name: "QuantTradingSystem"
  version: "0.1.0"
  port: 5050               # Web 服务端口
  debug: false

database:
  engine: "sqlite"
  path: "database/quant.db"

data:
  primary_source: "akshare"      # 主数据源
  secondary_source: "westock"     # 补充数据源（实时行情/技术指标/财报）
  fallback_source: "baostock"     # 备选数据源
  download:
    start_date: "2022-06-01"      # 历史数据起始日期
    request_interval: 0.8         # 请求间隔（秒，避免反爬）
    max_retries: 3                # 最大重试次数
    timeout: 30                   # 请求超时
    batch_size: 50                # 批量下载股票数

backtest:
  costs:
    commission_rate: 0.0003       # 佣金费率 0.03%
    stamp_duty_rate: 0.0005       # 印花税 0.05%（仅卖出）
    slippage: 0.0001              # 滑点 0.01%
    min_commission: 5.0           # 最低佣金（元）
  benchmark: "sh000300"           # 基准指数（沪深300）
  risk_free_rate: 0.02            # 无风险利率 2%

web:
  title: "QuantTrading - A股量化回测系统"
```

### `config/strategies.yaml` — 策略注册表

每条策略配置包含：
- `name`: 中文名称（业务名）
- `class_path`: Python 类路径
- `params`: 策略参数
- `description`: 策略描述
- `source`: 学术来源
- `strategy_type`: 策略类型（signal/portfolio/voting/stock_screener）
- `status`: 状态标记（🟢主推 / 🟡后备 / 📦归档）

### 环境变量

| 变量 | 用途 |
|------|------|
| `DATABASE_PATH` | 覆盖 SQLite 数据库路径 |
| `DATA_DIR` | 覆盖市场数据目录 |
| `FLASK_SECRET_KEY` | Flask 会话密钥 |

---

## 9. 测试体系

**目录:** `tests/`

| 测试文件 | 覆盖范围 |
|----------|---------|
| `test_backtest.py` | 回测引擎核心逻辑 |
| `test_bar_generator.py` | K 线周期聚合 |
| `test_base_engine.py` | 基础引擎 |
| `test_base_gateway.py` | 网关基类 |
| `test_data_manager.py` | 数据管理器 |
| `test_database.py` | 数据库操作 |
| `test_datafeed_base.py` | 数据源基类 |
| `test_datafeed_local.py` | CSV 数据源 |
| `test_datafeed_parquet.py` | Parquet 数据源 |
| `test_downloader.py` | 数据下载器 |
| `test_engine_integration.py` | 引擎集成 |
| `test_equity_strategy.py` | 股票策略 |
| `test_event_engine.py` | 事件系统 |
| `test_gateway_object.py` | 网关对象 |
| `test_indicator_registry.py` | 指标注册 |
| `test_leader_features.py` | 龙头特征工程 |
| `test_main_engine.py` | 主引擎 |
| `test_oms_engine.py` | 订单管理系统 |
| `test_portfolio_engine.py` | 组合引擎 |
| `test_pricing.py` | 定价工具 |
| `test_rbac.py` | 角色权限控制 |
| `test_regression_real_data.py` | 真实数据回归测试 |
| `test_research.py` | 研究模块 |
| `test_research_apis.py` | 研究 API 端点 |
| `test_scorer_registry.py` | 评分器注册 |
| `test_sql_injection.py` | SQL 注入防护 |
| `test_strategies.py` | 策略实现 |
| `test_url_redirects.py` | URL 重定向 |
| `test_v_leader_main_surge.py` | V 龙头主升浪策略 |
| `test_web.py` | Web 端点 |
| `test_westock_security.py` | WeStock 安全 |
| `test_xgb_scaler.py` | XGBoost 特征缩放 |
| `e2e/test_workbench.py` | 端到端工作台流程 |

**测试配置:** `tests/conftest.py` 提供共享 Fixture（模拟数据、测试客户端、临时数据库）。

**运行命令:**
```bash
pytest tests/ -v                # 全部测试
pytest tests/test_backtest.py -v # 单模块测试
pytest tests/e2e/ -v            # E2E 测试
```

---

## 10. 脚本工具集

**目录:** `scripts/`

### 数据管道脚本

| 脚本 | 用途 |
|------|------|
| `build_db.py` | 从 CSV 导入到 SQLite（14 个导入器，支持全量/增量/单表） |
| `consolidate_db.py` | 数据库整合（合并冗余表） |
| `optimize_db.py` | 数据库性能优化（索引、VACUUM） |
| `update_all_data.py` | 全量数据刷新 |
| `incremental_update.py` | 每日增量更新 |
| `update_daily_data.py` | 日线数据更新 |
| `update_quotes_v3.py` | 行情更新 v3 |
| `import_akshare.py` / `import_baostock_akshare.py` | 从 AKShare 导入 |
| `import_fund_flow.py` / `download_fund_flow_full.py` | 资金流导入/下载 |
| `import_finance_summary.py` | 财务数据导入 |
| `import_technical_indicators.py` | 技术指标导入 |
| `import_institutional_data.py` | 机构数据（龙虎榜、融资融券、股东数） |
| `import_stock_profile.py` | 股票简介导入 |
| `import_holder_num_only.py` | 股东户数导入 |
| `import_margin_akshare.py` | 融资融券导入 |
| `import_lhb_akshare.py` / `import_lhb_institutional.py` | 龙虎榜导入 |
| `fill_kline_missing.py` / `fill_recent_klines.py` | 填充缺失 K 线 |
| `fill_circulating_shares.py` / `backfill_shares.py` | 补全流通股本 |
| `fill_eastmoney.py` / `fill_recent_generic.py` | 东方财富数据补充 |
| `refresh_stock_profile.py` | 刷新股票简介 |
| `refresh_tencent_quotes.py` | 刷新腾讯行情 |
| `rebuild_quotes.py` | 重建行情数据 |
| `rebuild_technical_indicators.py` | 重建技术指标 |
| `mark_delisted_stocks.py` | 标记退市股票 |
| `gen_scaler.py` | 生成 ML 特征缩放器 |
| `build_parquet.py` | CSV → Parquet 转换 |
| `check_fill_rate.py` | 检查数据填充率 |

### 回测脚本

| 脚本 | 用途 |
|------|------|
| `batch_backtest_v3.py` | 批量回测（最新版本） |
| `batch_backtest_run.py` | 批量回测执行器 |
| `full_bull_backtest.py` | 完整牛市回测 |
| `bull_backtest_v3.py` | 牛市回测 v3 |
| `six_dim_backtest.py` | 六维评分回测 |
| `multi_dim_backtest.py` | 多维回测 |
| `multi_dim_portfolio_backtest.py` | 多维组合回测 |
| `mini_backtest_compare.py` | 小型回测对比 |
| `full_all_strategies_v3.py` | 全策略回测 v3 |

### ML / 研究脚本

| 脚本 | 用途 |
|------|------|
| `train_xgb_v4.py` | 训练 XGBoost v4 模型 |
| `daily_pred_train_v3.py` | 日线预测训练 v3 |
| `walk_forward.py` / `seed_walk_forward.py` | Walk-forward 优化 |
| `param_grid_search.py` | 超参数网格搜索 |
| `run_v6.py` | 运行 V6 管线 |
| `analyze_score_attribution.py` | 评分归因分析 |
| `export_7d_scores.py` | 导出 7 日评分 |
| `regenerate_combined_scores.py` | 重新生成综合评分 |
| `bull_8d_analysis.py` / `bull_8d_monthly_fixed.py` | 牛市 8 维分析 |

### 审计 / 调试脚本

| 脚本 | 用途 |
|------|------|
| `audit_*.py` | 审计 HTML 路由、净值页面、控制台 |
| `debug_*.py` | 调试市场数据、策略、工作台 |
| `verify_*.py` | 验证脚本（表头、牛市模型、E2E） |
| `trace_404.py` / `trace_404_v2.py` | 追踪 404 错误 |
| `show_db_stats.py` | 显示数据库统计 |
| `static_html_scan.py` | 扫描静态 HTML |
| `check_fill_rate.py` | 检查数据填充率 |

### 报告生成脚本

| 脚本 | 用途 |
|------|------|
| `gen_fund_flow_report_data.py` | 生成资金流报告数据 |
| `graphic_audit.py` | 图形化审计 |
| `full_html_audit.py` | 完整 HTML 审计 |
| `audit_console.py` / `audit_home_tabs.py` / `audit_nav_tabs.py` | 控制台/导航审计 |
| `audit_all_nav_pages.py` / `audit_all_routes.py` | 全路由审计 |

---

## 11. 术语表

| 术语 | 英文 | 说明 |
|------|------|------|
| **AKShare** | AKShare | 开源 A 股数据 API（爱可分享），封装东方财富公开接口 |
| **Baostock** | Baostock | 专业证券数据平台，提供财务三表等数据 |
| **WeStock Data** | WeStock Data | 腾讯自选股 npm CLI 工具，用于获取实时行情和技术指标 |
| **K 线** | K-line / Candlestick | 蜡烛图价格数据（开盘价 O、最高价 H、最低价 L、收盘价 C、成交量 V） |
| **资金流向** | Fund Flow | 按投资者类型（个人/主力/机构）分类的净流入/流出 |
| **评分器** | Scorer | 对股票在特定维度（技术面、资金面等）打 0–100 分的组件 |
| **选股管线** | Pipeline | 端到端选股工作流（数据加载 → 评分 → 过滤 → 排名） |
| **回测** | Backtest | 在历史数据上模拟策略交易 |
| **OMS** | Order Management System | 订单管理系统 |
| **NAV** | Net Asset Value | 净值（组合价值随时间变化） |
| **夏普比率** | Sharpe Ratio | 风险调整收益指标 |
| **最大回撤** | Max Drawdown | 峰值到谷底的最大亏损幅度 |
| **Walk-Forward** | Walk-Forward | 滚动窗口的样本外测试方法 |
| **OOS** | Out-of-Sample | 样本外测试（在未见过的数据上验证） |
| **V 龙头** | V-Leader | 量价龙头模型（成交量与价格同步领先的股票） |
| **XGBoost** | Extreme Gradient Boosting | 极端梯度提升算法（本项目用于月度股票预测） |
| **Parquet** | Apache Parquet | 列式存储格式，用于高性能数据分析 |
| **WAL** | Write-Ahead Logging | 预写式日志（SQLite 的journal 模式） |
| **T+1** | T+1 Settlement | A 股当日买入次日才能卖出的交易制度 |
| **布林带** | Bollinger Bands | 约翰·布林格发明的价格波动区间指标 |
| **RSI** | Relative Strength Index | 相对强弱指标（Welles Wilder 发明） |
| **MACD** | Moving Average Convergence Divergence | 指数平滑异同移动平均线（Gerald Appel 发明） |
| **KDJ** | KDJ Indicator | 随机指标（George Lane 发明） |
| **ATR** | Average True Range | 平均真实波幅（Welles Wilder 发明） |

---

## 附录：目录结构总览

```
quant-trading-system/
├── src/                        # 主应用源码
│   ├── backtest/              # 回测引擎 + 策略基类（BaseSelectionStrategy / BacktestingPyAdapter）
│   ├── constants/             # 常量定义（费用、市场、风险、信号）
│   ├── data/                  # 数据管理与数据源
│   ├── db/                    # 数据库层
│   ├── engine/                # 引擎基类（BaseEngine）+ OmsEngine（lazy import）
│   ├── event/                 # 事件系统（EventEngine + 14 个 EVENT_* 常量）
│   ├── gateway/               # 订单网关 + MainEngine（含 gateways/engines/strategies 注册表）
│   ├── indicator/             # 技术指标计算
│   ├── metrics/               # 绩效指标
│   ├── models/                # 量化模型
│   ├── research/              # 研究与 Alpha
│   ├── risk/                  # 风控子系统（v2.1 新增：RiskEngine + RiskConfig）
│   ├── scoring/               # 多维评分系统（7 个 scorer + ScorerRegistry）
│   ├── selection/             # 选股管线
│   ├── strategies/            # 策略实现 + trading 子包（操盘层）
│   ├── strategy/              # 策略基类（ADR-0006: EquityStrategy 永久 / AlphaStrategy 废弃）
│   ├── utils/                 # 工具函数
│   └── web/                   # Web 应用（Flask）
├── tests/                     # 测试套件
├── scripts/                   # 工具与批处理脚本（active/_deprecated/_deprecated/tests 三桶分类）
├── config/                    # 配置文件
├── data/                      # 应用数据（模型、缓存、报告）
├── market_data/               # 原始市场数据（CSV + Parquet）
│   ├── raw/
│   │   ├── kline_daily/       # 日线行情（2023-2026，4 个年度 CSV）
│   │   ├── reference/         # 参考数据（股票列表、大宗交易、分红、公告等）
│   │   ├── technical/         # 技术指标补充
│   │   └── technical_indicators/ # 技术指标（2024-2026，含冗余 part1/2）
│   ├── parquet/daily/         # Parquet 列式存储（~10,200 个股票文件）
│   └── 汇总 CSV               # benchmark, finance, fund_flow, holder_num 等
├── output/                    # 生成的 HTML 报告和审计结果
├── docs/                      # 文档（adr/ + CODE_WIKI.md + ...）
├── run.py                     # 应用入口
├── pyproject.toml             # 项目配置
└── requirements.txt           # Python 依赖
```

---

*本文档基于代码仓库自动生成，最后更新: 2026-06-27*
