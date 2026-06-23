# vnpy 设计借鉴 — 优化清单

> **目的**: 把 vnpy 4.4 的成熟架构设计移植到本项目，重点强化 **阶段 4（Broker 接入）** 和 **阶段 5（实盘）** 的基础设施。
> **参照**: `E:\work\work\vnpy\`

---

## 一、vnpy 的核心架构

```
┌──────────────────────────────────────────────────────────┐
│  EventEngine (单例, 全项目共享)                            │
│  - Queue + Worker Thread                                 │
│  - put(Event) → 派发给 handler[type]                      │
└────────────────────┬─────────────────────────────────────┘
                     │ events
     ┌───────────────┼───────────────┐
     ▼               ▼               ▼
┌─────────┐   ┌─────────┐    ┌──────────────┐
│ Gateway │   │ Engine  │    │ App (UI/数据) │
│ (CTP等) │   │(风控/OMS)│    └──────────────┘
└─────────┘   └─────────┘
     │ callbacks (on_tick/on_trade/on_order)
     ▼
┌──────────────────────────────────────────────────────────┐
│  MainEngine (持有 EventEngine + 所有 Gateways/Engines/Apps)│
│  add_gateway / add_engine / add_app                       │
└──────────────────────────────────────────────────────────┘
                     ▲
                     │ strategy signals
                ┌─────────┐
                │Strategy │
                │ on_bars │
                └─────────┘
```

### vnpy 的 4 个核心抽象

| 抽象 | 文件 | 我们项目对应 | 借鉴价值 |
|------|------|-------------|----------|
| **EventEngine** | `vnpy/event/engine.py` | 无 | ⭐⭐⭐ 必学 |
| **MainEngine** | `vnpy/trader/engine.py` | `src/web/app.py` create_app | ⭐⭐ 借鉴 |
| **BaseGateway** | `vnpy/trader/gateway.py` | 无（Broker 待开发）| ⭐⭐⭐ 必学 |
| **Data objects** | `vnpy/trader/object.py` | `simulator.py` 的 TradeRecord/Position | ⭐⭐ 统一 |

---

## 二、本项目现状 vs vnpy 差距

| 方面 | 本项目 | vnpy | 差距 |
|------|--------|------|------|
| **事件系统** | 无（直接调用）| EventEngine + Queue | 缺失 |
| **Broker 接口** | 无（待开发）| BaseGateway + 30+ gateway 实现 | 缺失 |
| **数据模型** | simulator.py 内 dataclass | 全局统一 TickData/OrderData | 局部 |
| **策略调用** | `set_target(vt_symbol, target)` | 同样 | 已有，但未统一 |
| **回测引擎** | 自研（多策略组合）| backtesting.py | 反而我们更优 |
| **数据存储** | CSV + SQLite | Parquet + MongoDB | 短期保留 CSV |
| **ML 框架** | XGBoost v4 散落 | vnpy.alpha (AlphaLab) | 落后 |

---

## 三、优先级落地清单（按 LIVE_TRADING_ROADMAP 阶段）

### 🔴 P0 — 阶段 4 Broker 接入的骨架

#### V1. EventEngine（事件引擎）— **新建** `src/event/engine.py`

借鉴 vnpy 简化版（不引入 Queue 多线程，本项目事件量小）：

```python
class EventEngine:
    """事件引擎 — 简化版 vnpy EventEngine

    功能:
      - register(type, handler): 注册事件处理函数
      - put(event): 发布事件（同步派发）
      - timer(interval): 定时事件
      - start/stop: 启动/停止（定时器线程）
    """
```

事件类型（参考 vnpy）：
- `EVENT_TICK = "eTick"` — 行情推送
- `EVENT_SIGNAL = "eSignal"` — 策略信号
- `EVENT_ORDER = "eOrder"` — 委托回报
- `EVENT_TRADE = "eTrade"` — 成交回报
- `EVENT_POSITION = "ePosition"` — 持仓变化
- `EVENT_LOG = "eLog"` — 日志事件
- `EVENT_TIMER = "eTimer"` — 定时器

#### V2. BaseGateway（A 股券商网关基类）— **新建** `src/gateway/base_gateway.py`

借鉴 vnpy BaseGateway，适配 A 股：

```python
class BaseGateway(ABC):
    """A 股券商网关基类

    必须实现的抽象方法:
      - connect(setting): 连接券商柜台
      - close(): 断开连接
      - query_account(): 查询资金
      - query_position(): 查询持仓
      - query_orders(): 查询委托
      - send_order(req): 报单
      - cancel_order(req): 撤单
      - subscribe(symbols): 订阅行情

    回调 (publish_event):
      - on_tick(tick): 行情推送
      - on_trade(trade): 成交回报
      - on_order(order): 委托回报
      - on_position(pos): 持仓回报
      - on_account(acct): 资金回报
    """
```

具体券商实现（参考 vnpy.gateway.ctp）：
- `src/gateway/xtp_gateway.py` — 中泰证券 XTP
- `src/gateway/ptrade_gateway.py` — 恒生 PTrade
- `src/gateway/qmt_gateway.py` — 迅投 QMT

#### V3. 统一数据模型 — 新建 `src/gateway/object.py`

借鉴 vnpy.object.py：

```python
@dataclass
class TickData: ...        # 行情
@dataclass
class BarData: ...         # K 线
@dataclass
class OrderRequest: ...    # 报单请求
@dataclass
class OrderData: ...       # 委托回报
@dataclass
class TradeData: ...       # 成交回报
@dataclass
class PositionData: ...    # 持仓
@dataclass
class AccountData: ...     # 资金
```

让 simulator.py 里的 `TradeRecord`/`Position` 改为继承这些统一模型。

### 🟡 P1 — 阶段 4 / 5 衔接

#### V4. MainEngine — 新建 `src/gateway/main_engine.py`

```python
class MainEngine:
    """主引擎 — 持有 EventEngine + 所有 Gateways/Strategies"""
    def __init__(self, event_engine=None): ...
    def add_gateway(cls, name): ...
    def get_gateway(name): ...
    def add_strategy(cls, name): ...
```

整合到 `src/web/app.py` 的 `create_app()` 里作为单例。

#### V5. AlphaStrategy 化我们的 V6/V龙头

借鉴 vnpy.alpha.strategy，V6/V龙头 重构为：
```python
class V6ReversalStrategy(AlphaStrategy):
    def on_init(self): ...           # 预计算指标
    def on_bars(self, bars): ...       # 选股逻辑
    def get_signal(self) -> DataFrame: # 输出信号
    def set_target(symbol, vol): ...  # 下单目标
    def execute_trading(): ...        # 触发执行
```

### 🟢 P2 — 长期演进

#### V6. Parquet 替代 CSV (可选)

vnpy 用 parquet 存行情（列存储、压缩率高、polars 读快）。
本项目保留 CSV (git 友好), 但可以**双格式**：
- CSV 提交 (源)
- Parquet 本地生成 (派生，gitignore)

#### V7. AlphaLab 化 V龙头 工作流

借鉴 vnpy.alpha.lab，重构 V龙头 训练流程：
```
数据 → Dataset (特征工程) → Model (XGBoost v4) → Signal → Strategy
```

当前散落在 `scripts/train_xgb_v4.py` 里。

---

## 四、执行顺序（建议）

| 步骤 | 内容 | 估时 | 价值 |
|------|------|------|------|
| 1 | V3 统一数据模型 (`src/gateway/object.py`) | 30min | 基础 |
| 2 | V1 EventEngine (`src/event/engine.py`) | 30min | 解耦 |
| 3 | V2 BaseGateway (`src/gateway/base_gateway.py`) | 1h | 阶段 4 核心 |
| 4 | V4 MainEngine | 30min | 编排 |
| 5 | V5 V6/V龙头 适配 AlphaStrategy | 2-3h | 长期 |
| 6 | 跑测试 + commit | 30min | 验证 |

---

## 五、不借鉴的部分（保持现有优势）

1. **数据存储**: 我们 CSV + SQLite 适合个人项目，vnpy 的 MongoDB 太重
2. **回测引擎**: 我们 PortfolioBacktestEngine 支持组合选股，vnpy 主要做单标的 CTA
3. **评分体系**: 8 维 Scorer 比 vnpy.alpha 灵活（支持自定义 scorer 注册）

---

*最后更新: 2026-06-23 — vnpy 4.4 借鉴分析*
