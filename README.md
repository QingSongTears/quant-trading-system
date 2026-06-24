# A股量化交易模型系统

基于学术文献 + 多维评分的 A 股双层策略回测系统，支持信号策略、选股策略、复合模型三层架构，叠加操盘层和模拟交易引擎。

> **状态**: v2.0-beta | **测试**: 持续集成 (Playwright E2E + pytest) | **Python**: 3.10+
> **当前分支**: `develop`（包含 Issue #73 操盘体系 + XGBoost v4 + PR3 工程重构）
> **实盘路径**: 详见 [`LIVE_TRADING_ROADMAP.md`](./LIVE_TRADING_ROADMAP.md)
> **框架差距调研**: 详见 [`docs/vnpy_vs_ours_deep_diff.md`](./docs/vnpy_vs_ours_deep_diff.md)（2026-06-23，15 项缺口 + 10 个代码修复 + 10 个测试缺口）

---

## 快速开始

```bash
git clone https://github.com/QingSongTears/quant-trading-system.git
cd quant-trading-system
git checkout develop
pip install -r requirements.txt
python run.py              # 启动 Web 服务（默认端口 5050）
python run.py --download   # 仅下载数据
```

访问 `http://localhost:5050` 进入 Web 界面。
访问 `http://localhost:5050/simulate` 进入模拟交易。

---

## 双层策略架构

```
┌─────────────────────────────────────────────────────────┐
│  信号层 (Strategies)                                      │
│  ├─ V6超卖反转  →  短期超跌反弹 (RSI/BB/回撤)            │
│  └─ V龙头主升  →  中期主升浪 (8维评分 + ML)             │
├─────────────────────────────────────────────────────────┤
│  评分层 (Scoring) — 8 维独立评分器                         │
│  technical / fundamental / fund_flow / institutional /   │
│  chip / sentiment / news_event / lhb_institutional       │
├─────────────────────────────────────────────────────────┤
│  操盘层 (Trading) — Issue #73                              │
│  ├─ PositionSizer  : fixed / kelly / atr / turtle       │
│  ├─ StopLoss       : fixed / atr / both                 │
│  ├─ TakeProfit     : fixed / trailing_atr               │
│  └─ TimeStop       : max_holding_days / no_profit_days  │
├─────────────────────────────────────────────────────────┤
│  规则层 (AStock Rules)                                     │
│  T+1 / 涨跌停 / 100 股整倍 / 佣金万 2.5 / 印花税千 0.5   │
├─────────────────────────────────────────────────────────┤
│  模拟层 (Simulator) — src/strategies/simulator.py         │
│  信号→仓位→止损→T+1→执行→DB 持久化                       │
├─────────────────────────────────────────────────────────┤
│  执行层 (Broker) —  待接入 (xtp / ptrade / qmt)            │
└─────────────────────────────────────────────────────────┘
```

---

## 策略体系

### 主推层（实盘候选）🟢

| 业务名 | 类路径 | 信号逻辑 | 学术依据 |
|--------|--------|----------|----------|
| **V6超卖反转** | `v6_reversal_selection.V6ReversalSelectionStrategy` | RSI14≤38 + BB≤0.10 + 60日DD≤-8% + 趋势确认 + 反转放量 | Wilder(1978) + Bollinger(2001) |
| **V6多维融合** | `v6_pipeline_hybrid.V6PipelineHybridStrategy` | V6 信号 × 多维评分融合（tech 60% + fund_flow + chip 40%） | 综合评分框架 |
| **V龙头主升** | `v_leader_main_surge.VLeaderMainSurgeStrategy` | 8 维评分 + 技术指标 + 滞后特征 → XGBoost v4 → 月度高胜率候选池 | Fama-French(1993) + ML |

### 后备层（观察中）🟡

| 业务名 | 描述 | 备注 |
|--------|------|------|
| 三因子均衡 (M1) | 小市值 + 反转 + 低波等权 | Fama & French(1993) + Jegadeesh(1990) + Baker(2011) |
| 极致小市值 (M2) | 卫星仓位策略（200 选 50） | Fama & French SMB 增强 |
| 盾+矛全天候 (M3) | 温度计动态调节（盾30/矛70 ↔ 盾70/矛30） | 市场温度计 + 三因子 |
| 技术指标投票 (M4) | 5 策略投票委员会（±2 阈值） | 综合投票模型 |

### 归档层（仅参考）📦

v2_trend / v3_reversal / v4_multifactor / v5_hybrid / v6_improved / v7_bull_wave / bull_8d_*
详见 [`LIVE_TRADING_ROADMAP.md`](./LIVE_TRADING_ROADMAP.md) 第四节。

---

## 代码结构（v2.0）

```
src/
├── backtest/                       ← 回测引擎
│   ├── engine.py                     单股信号回测 (基于 backtesting.py)
│   ├── portfolio_engine.py           组合选股回测 (纯 Pandas/NumPy)
│   ├── astock_strategy.py            A股规则引擎 (T+1/涨跌停/手续费)
│   ├── base_strategy.py              信号策略基类
│   └── base_selection_strategy.py    选股策略基类
│
├── data/                           ← 数据层 (2026-06-24 统一门面)
│   ├── manager.py                    DataManager 统一入口 (5 类数据源 lazy)
│   ├── downloader.py                 DataDownloader (akshare/baostock 历史下载)
│   ├── westock.py                    实时行情 (npx westock-data-clawhub)
│   ├── xgb_loader.py / xgb_scaler.py XGBoost 模型加载 / JsonScaler
│   └── datafeed/                     vnpy 风格 Datafeed 抽象
│       ├── base.py                   BaseDatafeed (ABC)
│       ├── local.py                  LocalDatafeed (SQLite, 主用)
│       └── parquet.py                ParquetDatafeed (全市场 scan 快)
│
├── indicator/                      ← 行情指标 (2026-06-24 借鉴 vnpy)
│   ├── bar_generator.py              BarGenerator (低→高 K 线合成)
│   └── array_manager.py              ArrayManager (17 指标 MA/EMA/MACD/...)
│
├── engine/                         ← 引擎层 (借鉴 vnpy 4.4, 2026-06-24)
│   ├── base.py                       BaseEngine (ABC, 状态机)
│   └── oms.py                        OmsEngine (订单管理 + 6 类事件订阅)
│
├── research/                       ← 研究层 (2026-06-24 借鉴 vnpy.alpha, ⚠️ 占位)
│   ├── dataset.py                    BaseDataset + AStockDataset
│   ├── alpha_model.py                BaseAlphaModel + AStockAlphaModel
│   └── lab.py                        AlphaLab (train/predict 编排)
│
├── strategies/                     ← 策略层
│   ├── v6_reversal_selection.py      V6 超卖反转
│   ├── v6_pipeline_hybrid.py         V6 多维融合
│   ├── simulator.py                  模拟交易引擎
│   ├── trading/                      操盘层 (#73)
│   │   ├── config.py                 TradingConfig
│   │   ├── position_sizer.py         仓位算法 (fixed/kelly/atr/turtle)
│   │   └── stop_loss.py              止损/止盈 (5 种)
│   ├── stock_screener/               归档策略 (v2-v7)
│   └── [legacy 信号策略]
│
├── scoring/                        ← 评分层
│   ├── base.py                       Scorer 基类 (Engine 单例)
│   ├── combiner.py                   综合评分收口 (5 种公式统一)
│   ├── technical_scorer.py           技术面 (极端反转 v3)
│   ├── fundamental_scorer.py         基本面
│   ├── fund_flow_scorer.py           资金面
│   ├── institutional_scorer.py       机构面
│   ├── lhb_institutional_scorer.py   龙虎榜
│   ├── chip_scorer.py                筹码面
│   ├── sentiment_scorer.py           情绪面
│   └── news_event_scorer.py          消息面
│
├── models/                         ← 复合模型
│   ├── three_factor.py               M1 三因子
│   ├── technical_voting.py           M4 技术投票
│   ├── shield_spear.py               M3 盾矛
│   ├── extreme_small_cap.py          M2 极致小市值
│   └── database.py / repository.py   ORM + 数据访问
│
├── constants/                      ← 集中常量 (新增)
│   ├── fees.py / market.py / risk.py / signal.py
│
├── metrics/                        ← 绩效指标 (新增)
├── utils/                          ← 工具 (keys/market)
├── data/                           ← 数据下载 (AKShare / WeStock Data)
├── selection/                      ← 选股管线 (SelectionPipeline)
└── web/                            ← FastAPI 应用
    ├── app.py                        应用工厂
    ├── routes/main.py + api.py       页面 + API 路由
    └── templates/                    10 个 Jinja2 页面
```

---

## API 端点

### 回测 API

| 方法 | 端点 | 说明 |
|------|------|------|
| `POST` | `/api/backtest/run` | 单股信号策略回测 |
| `POST` | `/api/backtest/portfolio/run` | 选股策略回测（V6 / V龙头） |
| `POST` | `/api/backtest/voting/run` | 投票模型回测 |
| `POST` | `/api/backtest/batch/run` | 批量回测（多策略 × 多股票）|
| `POST` | `/api/backtest/paramsearch/run` | 参数搜索（grid / random / bayesian）|
| `GET` | `/api/backtest/results` | 回测结果列表 |

### 模拟交易 API（Issue #73）

| 方法 | 端点 | 说明 |
|------|------|------|
| `POST` | `/api/simulate/run` | 运行模拟交易 |
| `GET` | `/api/simulate/positions` | 当前持仓 |
| `GET` | `/api/simulate/trades` | 交易明细 |
| `GET` | `/api/simulate/performance` | 绩效指标 |
| `GET` | `/api/simulate/equity` | 净值曲线 |
| `GET` | `/api/simulate/list` | 历史模拟记录 |

### 数据 / 研究 API

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/api/data/coverage` | 数据覆盖概览 |
| `GET` | `/api/data/search?q=` | 股票搜索 |
| `POST` | `/api/data/download` | 触发数据下载（full / incremental）|
| `GET` | `/api/strategies` | 策略列表 |
| `GET` | `/api/models/summary` | 多模型对比 |
| `GET` | `/api/stockpool/list` | 股票池筛选 |
| `GET` | `/api/research/aggregate` | AI 投研聚合（基本面/技术/新闻/板块）|
| `POST` | `/api/research/ai-report` | AI 投研报告（需配置 AI API）|

> 所有 `/api/*` 端点要求 Bearer Token 鉴权。

---

## 配置

### `config/config.yaml` — 全局配置

```yaml
database:
  engine: "sqlite"
  path: "database/quant.db"

data:
  primary_source: "akshare"
  secondary_source: "westock"
  fallback_source: "baostock"
  download:
    start_date: "2022-06-01"
    request_interval: 0.8

backtest:
  costs:
    commission_rate: 0.00025    # 万 2.5
    stamp_duty_rate: 0.0005     # 千 0.5
    slippage: 5                 # 5bps
  benchmark: "sh000300"         # 沪深 300
  risk_free_rate: 0.02

web:
  host: "0.0.0.0"
  port: 5050
```

### `config/strategies.yaml` — 策略注册表

业务名为 key（中文），`class_path` 走白名单加载。每条策略包含 `status` 字段：🟢主推 / 🟡后备 / 📦归档。

---

## 数据源

| 数据 | 来源 | 方式 |
|------|------|------|
| 日线行情 (OHLCV) | AKShare | `python run.py --download` |
| 实时行情 | WeStock Data (npx CLI) | 自动后备 |
| 基准指数 (沪深300) | AKShare | 自动下载 |
| 技术指标 (MACD/RSI/KDJ/布林) | 预计算 CSV → `technical_indicators` 表 | `scripts/import_technical_indicators.py` |
| 流通股本 | WeStock Data (#64) | `scripts/update_shares.py` |
| 财务摘要 | 东方财富 → `finance_summary` 表 | `scripts/import_finance_summary.py` |
| 资金流向 | 东方财富 → `fund_flow_data` 表 | `scripts/import_fund_flow.py` |
| 龙虎榜 / 融资融券 / 股东数 | 东方财富 → 多张表 | `scripts/import_institutional_data.py` |

---

## 开发

```bash
# 测试
pytest tests/ -v                    # 单元 + 集成
pytest tests/e2e/ -v                # Playwright E2E

# 代码检查
flake8 src/ tests/

# 数据下载
python run.py --download            # 全量
python run.py --download-incr       # 增量

# 模型训练
python scripts/train_xgb_v4.py      # V龙头 XGBoost v4
```

---

## 技术栈

| 层 | 技术 |
|------|------|
| Web 框架 | FastAPI + Jinja2 |
| 回测引擎 | backtesting.py / 自研 PortfolioEngine / Simulator |
| 评分框架 | 自研 8 维 Scorer + Engine 单例 |
| 机器学习 | XGBoost v4 + StandardScaler + scikit-learn |
| 数据库 | SQLite + SQLAlchemy 2.0 ORM |
| 数据处理 | Pandas + NumPy |
| 数据源 | AKShare + WeStock Data + Baostock |
| 可视化 | ECharts + Chart.js (CDN) |
| 测试 | pytest + Playwright E2E |
| CI/CD | GitHub Actions (Playwright + lint + pytest) |

---

## 路线图

- [x] **Phase 1**: 数据层 (AKShare + SQLite + WeStock Data)
- [x] **Phase 2**: 策略引擎 (5 信号 + 4 选股 + 4 复合模型)
- [x] **Phase 3**: Web 可视化 (FastAPI + ECharts)
- [x] **Phase 4**: 高级功能 (策略注册/批量回测/参数搜索)
- [x] **Phase 5**: 测试上线 (Playwright E2E + CI/CD)
- [x] **Phase 6**: 操盘体系 (#73) — 已完成, 待 OOS 验证
- [x] **Phase 7**: 多维评分框架 (8 维 Scorer + 综合评分收口)
- [x] **Phase 8**: V龙头 XGBoost v4 (AUC 0.7912)
- [ ] **Phase 9**: OOS 验证 (walk-forward 框架)
- [ ] **Phase 10**: 模拟盘 + 实盘链路 (Broker 接入)
- [ ] **Phase 11**: 监控告警 + 灾备
- [ ] **Phase 12**: 小资金实盘（5-10w 启动）

> 实盘化详细路径见 [`LIVE_TRADING_ROADMAP.md`](./LIVE_TRADING_ROADMAP.md)

---

## 📚 关键文档索引

| 主题 | 文档 |
|------|------|
| 实盘路径 | [`LIVE_TRADING_ROADMAP.md`](./LIVE_TRADING_ROADMAP.md) |
| vnpy 差距分析（更新版） | [`docs/vnpy_vs_ours_deep_diff.md`](./docs/vnpy_vs_ours_deep_diff.md) ← 2026-06-23 重做 |
| vnpy 差距分析（旧版） | [`docs/VNPY_GAP_ANALYSIS.md`](./docs/VNPY_GAP_ANALYSIS.md) |
| vnpy 借鉴落地计划 | [`docs/Vnpy_Optimization_Notes.md`](./docs/Vnpy_Optimization_Notes.md) |
| **DataManager 使用** | [`docs/data_manager_usage.md`](./docs/data_manager_usage.md) ← 2026-06-24 |
| **BarGenerator 使用** | [`docs/bar_generator_usage.md`](./docs/bar_generator_usage.md) ← 2026-06-24 |
| **ArrayManager 使用** | [`docs/array_manager_usage.md`](./docs/array_manager_usage.md) ← 2026-06-25 |
| **Datafeed 使用** | [`docs/datafeed_usage.md`](./docs/datafeed_usage.md) ← 2026-06-25 |
| **Research 使用** | [`docs/research_usage.md`](./docs/research_usage.md) ← 2026-06-25 |
| 剩余任务清单 | [`TODO.md`](./TODO.md) |

---

> 🔵 **AI 辅助生成** · 策略参数基于学术文献和社区实证 · 所有模型需样本外验证后方可用于实盘