# A股量化交易模型系统

基于学术文献的 A 股多因子量化回测系统，支持信号策略、选股策略和复合模型三层架构。

> **状态**: v2.0-alpha | **测试**: 133/133 ✅ | **Python**: 3.10+

---

## 快速开始

```bash
git clone https://github.com/QingSongTears/quant-trading-system.git
cd quant-trading-system
pip install -r requirements.txt
python run.py
```

访问 `http://localhost:8000` 进入 Web 界面。

---

## 架构

```
src/
├── backtest/           ← 回测引擎层
│   ├── engine.py          单股信号回测引擎 (基于 backtesting.py)
│   ├── portfolio_engine.py 组合选股回测引擎 (纯 Pandas/NumPy)
│   ├── base_strategy.py   信号策略基类
│   └── base_selection_strategy.py 选股策略基类
│
├── strategies/         ← 策略层
│   ├── ma_cross.py         S1 双均线交叉
│   ├── macd_signal.py      S2 MACD金叉死叉
│   ├── rsi_reversal.py     S3 RSI超买超卖
│   ├── bollinger_breakout.py S4 布林带突破
│   ├── turtle_trading.py   S5 海龟交易法则
│   ├── small_cap.py        S01 小市值选股
│   ├── reversal.py         S02 短期反转选股
│   ├── low_volatility.py   S03 低波动选股
│   └── low_turnover.py     S04 低换手选股
│
├── models/             ← 复合模型层
│   ├── three_factor.py     M1 三因子均衡模型
│   └── technical_voting.py M4 技术指标投票模型
│
├── web/                ← Web 层 (FastAPI + Jinja2)
│   ├── app.py             应用工厂
│   ├── routes/api.py      4 个回测 API 端点
│   └── templates/         6 个页面模板
│
├── data/               ← 数据层
│   ├── downloader.py      AKShare 数据下载
│   └── westock.py         腾讯自选股行情
│
└── models/             ← 数据模型
    ├── database.py        SQLAlchemy ORM 表定义
    └── repository.py      数据访问层
```

---

## 策略体系

### 信号策略 (Signal) — 单股择时

| 策略 | 来源 | 引擎 |
|------|------|------|
| 双均线交叉 (5/20) | 经典趋势跟踪 | BacktestEngine |
| MACD金叉死叉 (12/26/9) | Appel (1979) | BacktestEngine |
| RSI超买超卖 (14) | Wilder (1978) | BacktestEngine |
| 布林带突破 (20/2σ) | Bollinger (2001) | BacktestEngine |
| 海龟交易法则 (20/10) | Faith (2007) | BacktestEngine |

### 选股策略 (Portfolio) — 全市场选股+等权组合

| 策略 | 因子 | 来源 |
|------|------|------|
| 小市值选股 | SMB | Fama & French (1993) |
| 短期反转选股 | Reversal | Jegadeesh (1990) |
| 低波动选股 | Low Vol | Baker et al. (2011) |
| 低换手选股 | Liquidity | Datar et al. (1998) |

### 复合模型 (Model)

| 模型 | 类型 | 说明 |
|------|------|------|
| M1 三因子均衡 | 因子合成 | 小市值+反转+低波等权 (300只) |
| M4 技术投票 | 投票委员会 | 5策略投票≥+2买入/≤-2卖出 |

---

## API 端点

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/` | 首页仪表盘 |
| `GET` | `/backtest` | 回测执行页 |
| `GET` | `/backtest/:id` | 回测详情 |
| `GET` | `/compare?ids=1,2,3` | 多模型对比 |
| `GET` | `/strategies` | 策略管理 |
| `GET` | `/data` | 数据管理 |
| `POST` | `/api/backtest/run` | 信号策略回测 |
| `POST` | `/api/backtest/portfolio/run` | 选股策略回测 |
| `POST` | `/api/backtest/voting/run` | 投票模型回测 |
| `GET` | `/api/strategies` | 策略列表 |
| `GET` | `/api/data/search?q=` | 股票搜索 |
| `GET` | `/api/backtest/results` | 回测结果列表 |

---

## 配置

编辑 `config/config.yaml`:

```yaml
database:
  path: database/quant.db    # SQLite 路径

backtest:
  benchmark: "000300"        # 沪深300
  risk_free_rate: 0.02       # 无风险利率
  costs:
    commission_rate: 0.0003  # 万三佣金
    stamp_duty_rate: 0.0005  # 千五印花税

web:
  host: "0.0.0.0"
  port: 8000
```

策略注册编辑 `config/strategies.yaml`（支持热加载）。

---

## 数据源

| 数据 | 来源 | 方式 |
|------|------|------|
| 日线行情 (OHLCV) | AKShare | `python run.py --download` |
| 实时行情 | 腾讯自选股 | 自动后备 |
| 基准指数 (沪深300) | AKShare | 自动下载 |

---

## 开发

```bash
# 运行测试
pytest tests/ -v

# 代码检查
flake8 src/ tests/

# 数据下载
python run.py --download
```

---

## 技术栈

| 层 | 技术 |
|------|------|
| Web 框架 | FastAPI + Jinja2 |
| 回测引擎 | backtesting.py / 自研 PortfolioEngine |
| 数据库 | SQLite + SQLAlchemy ORM |
| 数据处理 | Pandas + NumPy |
| 可视化 | ECharts (前端) |
| CI/CD | GitHub Actions |

---

## 路线图

- [x] Phase 1: 数据层 (AKShare + SQLite)
- [x] Phase 2: 策略引擎 (5 信号 + 4 选股)
- [x] Phase 3: Web 可视化 (FastAPI + ECharts)
- [x] Phase 4: 高级功能 (策略注册/批量回测)
- [x] Phase 5: 测试上线 (133 E2E + CI/CD)
- [ ] Phase 6: M5/M2/M3 复合模型 + 样本外验证

---

> 🔵 **AI 辅助生成** · 策略参数基于学术文献和社区实证 · 所有模型需样本外验证后方可用于实盘
