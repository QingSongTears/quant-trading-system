# 框架与 vnpy 4.4 差距分析（更新版）

**更新日期**: 2026-06-23
**上一版**: docs/Vnpy_Optimization_Notes.md (2026-06-23，已部分落地)

---

## 0. TL;DR

V1~V5（EventEngine / Gateway / 数据模型 / MainEngine / AlphaStrategy）**大部分已完成**，剩余 7 项差距集中在"实盘风控"和"ML 流水线统一"。

---

## 1. 已落地清单（commit `2e981ea` + `b2c0a05` + `f7c9e18` + `403633c`）

| V# | 模块 | vnpy 参考 | 本项目 | 状态 |
|---|---|---|---|---|
| V1 | EventEngine | `vnpy/event/engine.py` | `src/event/engine.py` | ✅ 已做（同步派发版）|
| V2 | BaseGateway | `vnpy/trader/gateway.py` | `src/gateway/base_gateway.py` | ✅ 已做（8 个 ABC）|
| V3 | 统一数据模型 | `vnpy/trader/object.py` | `src/gateway/object.py` | ✅ 已做（8 个 dataclass + 4 个 enum）|
| V4 | MainEngine | `vnpy/trader/engine.py` | `src/gateway/main_engine.py` | ✅ 已做 |
| V5a | AlphaStrategy 基类 | `vnpy/alpha/strategy/template.py` | `src/strategy/alpha_strategy.py` | ✅ 已做（abstract + Protocol + 4 报单 + execute_trading）|
| V5b | EquityStrategy 选股模板 | `vnpy/alpha/strategy/strategies/equity_demo.py` | `src/strategy/equity_strategy.py` | ✅ 已做（Top-K + 月度再平衡）|
| V? | BaseDatafeed | `vnpy/alpha/lab.py` (AlphaLab) + `vnpy/trader/datafeed.py` | `src/datafeed/base.py` + `src/datafeed/local.py` | ✅ 已做（4.4x 批量加速）|
| V? | StrategyEngine Protocol | vnpy strategy_engine 强绑定 | `src/strategy/alpha_strategy.py::StrategyEngine` | ✅ 已做（兼容回测/实盘）|

---

## 2. 剩余差距（按实盘化重要性排序）

### 🔴 P0 — 实盘必备（模拟盘之前必须）

#### GAP-1: RiskEngine（独立风控模块）

**vnpy**: `vnpy/trader/engine.py::RiskManager`
- 单笔/单日/单合约风控
- 强平/拒单规则
- 资金冻结管理

**本项目**: 散落在 `src/backtest/astock_strategy.py`（涨跌停/T+1）和策略类内（无统一拦截）

**需要建**: `src/risk/engine.py`
```python
class RiskEngine:
    """下单前风控检查"""
    def check_order(self, req: OrderRequest) -> tuple[bool, str]: ...
    def check_daily_limit(self) -> bool: ...
    def freeze_margin(self, req: OrderRequest) -> float: ...
```

**优先级**: ⭐⭐⭐（实盘必做；模拟盘也建议）

---

#### GAP-2: Recorder（行情录制/回放）

**vnpy**: `vnpy/trader/engine.py::RecorderEngine` / `vnpy/chart/`
- 行情 Tick/K 线落盘
- 实盘录制 → 回放 = 仿真回测

**本项目**: 无

**需要建**: `src/datafeed/recorder.py`
- 录: 把 LocalDatafeed / 实盘 gateway 的 bar 写 parquet
- 放: 从 parquet 还原成 BarData 喂策略

**优先级**: ⭐⭐⭐（仿真盘/实盘必备）

---

#### GAP-3: PositionTracker（独立持仓/资金跟踪）

**vnpy**: `vnpy/trader/engine.py::OmsEngine`
- 维护 PositionData/AccountData
- 计算可用资金/冻结/盈亏
- 处理今日持仓 vs 历史持仓（A 股 T+1）

**本项目**: 持仓在策略 `pos_data` 里（AlphaStrategy），资金在 `get_cash_available()`。**没有独立 OMS**。

**需要建**: `src/oms/engine.py`
- 跟 vnpy OmsEngine 对齐
- 维护 AccountData / PositionData 单例
- 提供 update_trade / update_order 回调

**优先级**: ⭐⭐⭐（实盘必做）

---

### 🟡 P1 — ML 流水线统一

#### GAP-4: AlphaLab（数据/特征/模型/信号统一管理）

**vnpy**: `vnpy/alpha/lab.py`
- AlphaLab: 统一管理 daily/minute/component/dataset/model/signal 6 类数据
- AlphaDataset: 特征工程 + 时序对齐
- AlphaModel: ML 模型抽象（继承后 train/predict/save/load）

**本项目**: V龙头训练流程分散在 `scripts/train_xgb_v4.py` / `scripts/build_bull_sample_pool.py` / `scripts/param_server.py`

**需要建**: `src/alpha/lab.py` + `src/alpha/dataset.py` + `src/alpha/model.py`
- 借鉴 vnpy 把 V龙头骨架 (`src/strategies/v_leader_main_surge.py`) 接入

**优先级**: ⭐⭐（ML 策略演进必做）

---

#### GAP-5: OptimizeStrategy（统一参数优化）

**vnpy**: `vnpy/alpha/strategy/optimize.py`
- 网格/随机/贝叶斯优化
- 输出最优参数 + IS 指标

**本项目**: `scripts/walk_forward.py` 有简单随机搜索；`src/backtest/engine.py::optimize` 有网格搜索

**需要建**: 抽到 `src/strategy/optimizer.py`
- 给 V6 / V龙头 都能用
- 输出 best_params + 收敛曲线

**优先级**: ⭐⭐（参数扫描统一）

---

### 🟢 P2 — 长期演进

#### GAP-6: Chart 模块（可视化）

**vnpy**: `vnpy/chart/`（基于 plotly）
- K 线 + 指标叠加
- 净值曲线 + 回撤曲线

**本项目**: web dashboard (`src/web/`) 有基础 K 线 + 回测结果，但**没有 plotly 联动 + 策略实时图表**

**需要建**: `src/chart/widget.py` + web 集成

**优先级**: ⭐（nice-to-have）

---

#### GAP-7: Broker 真实接入（实盘阶段）

**vnpy**: `vnpy/gateway/ctp/` / `xtp/` / `rohon/`（30+ gateway）

**本项目**: BaseGateway 已建（`src/gateway/base_gateway.py`），但**没有具体券商实现**

**需要建**（实盘阶段）:
- `src/gateway/xtp_gateway.py` — 中泰 XTP（推荐，A 股个人首选）
- `src/gateway/qmt_gateway.py` — 迅投 QMT
- `src/gateway/ptrade_gateway.py` — 恒生 PTrade

**优先级**: ⭐（实盘才需要；阶段 4）

---

## 3. vnpy 有但**我们不需要**借鉴的

| 模块 | 原因 |
|---|---|
| `vnpy/trader/converter.py`（CTA↔Alpha）| 我们只用 Alpha 体系 |
| `vnpy/trader/database.py`（SQLite ORM）| 我们已有 `src/db/engine.py` + `src/models/repository.py` |
| `vnpy/trader/setting.py`（统一配置）| 我们有 `src/config.py` + `config/*.yaml` |
| `vnpy/trader/wechat.py`（微信通知）| 非必需 |
| `vnpy/trader/rpc/grpc`（远程通信）| 单机够用 |

---

## 4. 我们有但 vnpy 没有的（保持优势）

| 模块 | 说明 |
|---|---|
| `PortfolioBacktestEngine` | 支持组合选股（Top-K + 调仓），vnpy 的 BacktestingEngine 只做单标的 |
| `ScorerRegistry` + 8 维评分 | 模块化评分器，vnpy alpha 没有等价体系 |
| `walk_forward.py` OOS 验证 | vnpy alpha 没有专门的 OOS 框架 |
| `metrics/performance.py` | 8+ 处 sharpe/max_dd 副本统一委托 |
| `build_db.py` CSV→DB 工具 | vnpy 不需要（用 parquet） |
| V6 / V龙头 业务策略 | vnpy alpha 没有具体策略示例 |

---

## 5. 优先行动建议（按 ROI）

| 顺序 | 任务 | 估时 | ROI |
|---|---|---|---|
| 1 | **GAP-1 RiskEngine** | 3-4h | ⭐⭐⭐ 实盘必备 |
| 2 | **GAP-3 PositionTracker (OMS)** | 4-6h | ⭐⭐⭐ 实盘必备 |
| 3 | **GAP-2 Recorder** | 2-3h | ⭐⭐⭐ 仿真盘必备 |
| 4 | **GAP-4 AlphaLab** | 6-8h | ⭐⭐ ML 演进 |
| 5 | **GAP-5 Optimizer** | 2h | ⭐⭐ 复用 |

GAP-1 + GAP-2 + GAP-3 = 阶段 4（Broker 接入）前置依赖。

---

*最后更新: 2026-06-23 — 框架差距更新版*