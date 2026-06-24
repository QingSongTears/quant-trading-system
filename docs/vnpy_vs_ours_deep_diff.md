# vnpy vs 本项目 — 深度差距调研报告（2026-06-23）

**调研背景**: 数据库结构优化暂停期间，对 vnpy 项目层面的真实差距做一次地毯式调研。
**对比对象**: `E:\work\work\vnpy\`（vnpy 4.4 源码） vs `E:\work\work\quant-trading-system\`（本项目）。
**调研方法**: 5 路并行 Explore agent，分别覆盖 Event/Gateway/Object、Alpha/研究模块、其它 vnpy 模块、本地代码质量审计、策略实际集成情况。
**输入文档**: `docs/VNPY_GAP_ANALYSIS.md`（7 项剩余差距） + `docs/Vnpy_Optimization_Notes.md`（V1~V7 落地计划）。

---

## 0. TL;DR

上一版差距分析（`VNPY_GAP_ANALYSIS.md`）识别了 7 项缺口，但**聚焦在"vnpy 有什么→我们没有什么"**，忽略了：
- 我们已经写了但**根本没被使用**的代码（AlphaStrategy/EquityStrategy 零消费者）
- 仓库内**同时存在 3 套数据对象**（gateway / 回测 / simulator）
- 借鉴模块**单测覆盖率 0**
- **研究层抽象薄弱**（AlphaLab/Dataset/Model 三大件都没有）

本次深度调研共识别 **15 项关键缺口**，按"该不该改 + 改了 ROI 多高"重新排序。

---

## 1. 整体盘点

### 1.1 借鉴模块与 vnpy 对照总览

| 抽象层 | vnpy | 本项目 | 落地度 |
|--------|------|--------|--------|
| Event Engine | `vnpy/event/engine.py` 异步队列 | `src/event/engine.py` 同步派发 | 🟢 80%（优于 vnpy：异常隔离 + 统计） |
| Data Object | `vnpy/trader/object.py` 全套 | `src/gateway/object.py` 全套 | 🟡 70%（缺 Exchange/Product/yd_volume/5档盘口） |
| MainEngine | `vnpy/trader/engine.py` | `src/gateway/main_engine.py` | 🔴 30%（缺 OmsEngine/内建 Engine/顶层代理） |
| BaseGateway | `vnpy/trader/gateway.py` | `src/gateway/base_gateway.py` | 🟡 60%（缺双事件分发/on_quote/on_log） |
| AlphaStrategy | `vnpy/alpha/strategy/template.py` | `src/strategy/alpha_strategy.py` | 🟡 **0 个消费者** |
| EquityStrategy | `vnpy/alpha/strategy/strategies/equity_demo.py` | `src/strategy/equity_strategy.py` | 🟡 **0 个消费者** |
| Datafeed | `vnpy/alpha/lab.py + trader/datafeed.py` | `src/datafeed/base/local/parquet.py` | 🟢 90%（双源实现优于 vnpy） |
| AlphaLab | `vnpy/alpha/lab.py` | ❌ 无 | 🔴 缺失 |
| AlphaDataset | `vnpy/alpha/dataset/*` | ❌ 无 | 🔴 缺失 |
| AlphaModel | `vnpy/alpha/model/*` | ❌ 无（XGBoost 散落脚本） | 🔴 缺失 |
| Optimize | `vnpy/trader/optimize.py` | `src/backtest/engine.py::run_bayesian_search` | 🟡 60%（缺 DEAP/NSGA-II） |
| Recorder | `vnpy/trader/engine.py::RecorderEngine` | ❌ 无 | 🔴 缺失 |
| RiskEngine | `vnpy/trader/engine.py::RiskManager` | 散落在 `astock_strategy.py` | 🔴 缺失 |
| OMS | `vnpy/trader/engine.py::OmsEngine` | ❌ 无（gateway 内缓存） | 🔴 缺失 |
| utility | `vnpy/trader/utility.py` (BarGenerator/ArrayManager/...) | `src/utils/` 仅 keys/market | 🔴 缺失最大 |
| logger | `vnpy/trader/logger.py` loguru + daily-rotate | stdlib `logging` | 🟡 建议升级 |

---

## 2. 关键缺口（按 ROI 排序）

### 🔴 P0-1：研究层"三件套"全缺（AlphaLab / AlphaDataset / AlphaModel）

**vnpy 提供**:
- `AlphaLab` 统一管理 dataset/model/signal 6 类数据的生命周期（save/load/list/remove）
- `AlphaDataset` 提供 `add_feature(name, expr)` + `cs_rank/ts_delay` 等 25+ 算子的表达式 DSL，特征工程可复用
- `AlphaModel` 抽象基类 `fit/predict/detail`，内建 LGB/MLP/Lasso 三个 model zoo
- `processor.py` 9 个标准预处理算子（cs_norm / robust_zscore / cs_rank_norm / ts_norm / drop_na / fill_na ...）
- `Segment` 枚举强制 train/valid/test 三段切分

**本项目现状**:
- 训练产物散落在 `scripts/train_xgb_v4.py:303-307`（XGBoost model）+ `data/xgb_scaler.json` + `data/all_7d_scores.json`
- 特征工程硬编码在 `train_xgb_v4.py:163-208`（30+ 特征不可复用、不可对比）
- 截面归一化临时用 `pct_rank` 函数（train_xgb_v4.py:110），时序标准化 `StandardScaler` 一把梭
- 训练/验证切分靠 `months_sorted[:-5]` 硬切（train_xgb_v4.py:145-149）
- 只支持 XGBoost，无 model zoo

**影响**: 任何新策略的特征/模型都要"从零写"，研究效率低、无法横向对比。

**修复方案**:
- 新建 `src/research/lab.py`（仿 `vnpy.alpha.lab`）
- 新建 `src/research/dataset.py`（仿 `vnpy.alpha.dataset.template`）+ `src/research/processor.py`
- 新建 `src/research/model/base.py` + 把 `train_xgb_v4.py` 包成 `XgbModel(AlphaModel)`

**ROI**: ⭐⭐⭐（ML 演进必做）

---

### 🔴 P0-2：仓库内"3 套数据对象"并存

**现状**:
| 来源 | K 线 | 委托 | 成交 | 持仓 | 资金 |
|------|------|------|------|------|------|
| `src/gateway/object.py` | `BarData` (dataclass) | `OrderRequest` | `TradeData` | `PositionData` | `AccountData` |
| `src/backtest/*` | `pandas.DataFrame` (OHLCV) | `backtesting.py::Trade` | 同 | `Strategy.position` (浮点) | `cash=` 参数 |
| `src/strategies/simulator.py` | — | — | `TradeRecord` 自定义 dataclass | `Position` 自定义 | `SimulationResult` |

**vnpy 是单一数据对象**贯穿 BacktestingEngine ↔ MainEngine ↔ Gateway，策略代码不感知差异。

**后果**:
- `grep "from src.gateway" src/backtest` 零命中，`grep "from src.gateway" src/strategies` 零命中
- 回测产出 DataFrame，实盘期望 BarData dataclass，**没有 adapter**
- Simulator 自定义 `TradeRecord.direction: str` vs `TradeData.direction: Direction`（enum），字段不兼容

**修复方案**:
- 短期：新增 `src/adapters/` 把 DataFrame↔BarData 互转；Simulator 的 `TradeRecord`/`Position` 改为继承/包装 gateway 版
- 中期：回测引擎输出统一改为 BarData 列表

**ROI**: ⭐⭐⭐（实盘化前置）

---

### 🔴 P0-3：借鉴模块**单测覆盖率为 0**

| 模块 | 文件数 | 单元测试数 |
|------|--------|-----------|
| `src/event/*` | 2 | **0** |
| `src/gateway/*` | 4 | **0** |
| `src/strategy/*` | 3 | **0** |
| `src/datafeed/*` | 4 | **0** |
| **合计** | **13** | **0** |

`tests/` 下没有任何文件 import 上述模块，全是 database/downloader/backtest/portfolio/scoring/web 范畴。

**后果**:
- EventEngine 线程逻辑（put/未启动/handler race）无人测，藏着 RuntimeError 风险
- BaseGateway 缓存字典读写无锁，无人测并发场景
- EquityStrategy.on_bars 状态机（rebalance_days / sort_values KeyError）无人测
- ParquetDatafeed `_load_full_dataset` OOM 风险无人测

**修复方案**: 详见 §3 高优先级修复清单 + §4 单测缺口清单。

**ROI**: ⭐⭐⭐（上线前必修）

---

### 🔴 P0-4：`AlphaStrategy` / `EquityStrategy` 基类**0 个消费者**

**现状**:
- `src/strategy/alpha_strategy.py`（158 行） + `src/strategy/equity_strategy.py`（143 行）完整复刻 vnpy `AlphaStrategy`，包括：
  - `on_init / on_bars / on_trade` 抽象
  - `pos_data / target_data / active_orderids` 状态
  - `buy/sell/cover/short/send_order/cancel_order/cancel_all` 包装
  - `set_target → execute_trading → strategy_engine.send_order` 调仓链
- 但 `grep "AlphaStrategy\|EquityStrategy" src/strategies/ src/models/` 零命中
- 所有主推策略（V6 / V6多维融合 / V龙头 / 三因子 / 盾矛）走老路 `BaseSelectionStrategy.select() → list[str]`

**这是"形似神不似"的核心**：
- vnpy 的 `AlphaStrategy` 在 alpha 模块里被 `EquityDemoStrategy` / 用户继承，是真正在用的基类
- 我们写了基类，但**没有任何策略继承**它，等于"为未来准备的空骨架"

**修复方案**（按 ROI）:
1. 选 1 个主推策略（建议 V6）改造为继承 `EquityStrategy`，让 `select → set_target → execute_trading` 走通
2. `MainEngine` 实现 `StrategyEngine` Protocol（`send_order / cancel_order / write_log / get_cash_available / get_holding_value`）
3. 回测侧构造 `Dict[str, BarData]` 喂给 `on_bars`（或改 `execute_trading` 接受 OHLCV DataFrame）

**ROI**: ⭐⭐⭐（不做就无法"回测→实盘"）

---

### 🟡 P1-5：`MainEngine` 缺 OmsEngine / 缺内建 Engine / 缺顶层代理

**vnpy MainEngine 提供**:
- `add_gateway/engine/app` 三套注册 + `add_app` 内部自动 `add_engine`
- 内建 `LogEngine / OmsEngine / EmailEngine / WechatEngine` 4 个 engine
- 顶层 `connect/subscribe/send_order/cancel_order` API，自动写日志
- `close()` 先关 EventEngine → 再关 engines → 最后关 gateways

**本项目**:
- 只有 `add_gateway/add_strategy` 两个 API（L86/L128 main_engine.py）
- 无 OmsEngine（OMS 缓存散在 BaseGateway L72-77，多 gateway 数据无全局视图）
- 顶层 connect/send_order 代理缺失，调用方需自取 gateway
- `close()` 顺序与 vnpy 相反（L75-80）— 先关 gateway 再关 EventEngine，gateway close 时若还需推 on_log 会失败

**修复方案**: 新建 `src/oms/engine.py`，参照 vnpy `OmsEngine`，集中管理 ticks/orders/trades/positions/accounts/contracts。

**ROI**: ⭐⭐（实盘必做，模拟盘可选）

---

### 🟡 P1-6：`BaseGateway` 缺双事件分发 / on_quote / on_log

**vnpy BaseGateway**:
- 全局事件 + 特定 vt_symbol 事件双分发（如 `eTick.000001.SZ`）
- 回调 `on_tick/on_trade/on_order/on_position/on_account/on_contract/on_quote/on_log` 8 个

**本项目**:
- 仅全局事件分发（`base_gateway.py:148`）
- 缺 `on_quote/on_log`（base_gateway.py:145-174）
- 写日志走 `logger.info` 而非 Event（base_gateway.py:195-198）

**影响**: 策略订阅特定 symbol 的 tick 必须注册全局 `eTick` 然后过滤，性能/可读性差；做市/期权策略无法上线。

**ROI**: ⭐⭐（实盘阶段补）

---

### 🟡 P1-7：数据对象缺 Exchange / Product 枚举

**vnpy**:
- `Exchange`: 40+ 个枚举（CFFEX/SHFE/SSE/SZSE/BSE/...）
- `Product`: 11 种（STOCK/FUTURE/OPTION/ETF/FUTURE_OPTION/...）
- `Status/OrderStatus` 区分 vnpy 风格

**本项目**: 全用 `str`（`object.py`），类型安全 / IDE 提示 / MainEngine.add_gateway 校验全丢。

**修复方案**: 移植 vnpy `constant.py` 到 `src/gateway/constant.py`，所有引用从 str 改为枚举。

**ROI**: ⭐⭐（类型安全，影响所有数据流）

---

### 🟡 P1-8：`PositionData.yd_volume` 缺失（T+1 关键字段）

**vnpy** `PositionData` 有 `yd_volume: float`（昨仓）。
**本项目** `object.py:217-230` 缺此字段。

**影响**: A 股 T+1 必须区分今/昨仓才能算"可卖数量"，目前散落在 simulator.py。

**ROI**: ⭐⭐（实盘必做）

---

### 🟡 P1-9：`utils/` 极度单薄 — 缺 BarGenerator / ArrayManager / 价格规整

**vnpy `trader/utility.py` 提供**:
- `BarGenerator`：tick → 1min → 5min/15min/小时/日，自动合成
- `ArrayManager`：numpy 滚动数组 + TA-Lib 30+ 指标（MA/MACD/RSI/Bollinger/ATR/...）
- `round_to / floor_to / ceil_to`：价格按 tick_size 规整（Decimal 高精度）
- `get_digits`：小数位数
- `load_json / save_json`：用户配置
- `get_file_path / get_folder_path`：路径工具
- `virtual` 装饰器

**本项目**: `src/utils/` 仅 `keys.py`(命名规范化) + `market.py`(代码推断)。

**影响面最大**:
- `src/models/technical_voting.py`（24KB）：5 个技术指标自写
- `src/models/extreme_small_cap.py` / `shield_spear.py` / `three_factor.py` 都类似
- 回测里价格规整靠 `round(x, 2)` 凑合

**修复方案**:
- 新建 `src/utils/bar_generator.py` + `src/utils/array_manager.py`（注意：vnpy 依赖 talib，确认本项目是 pandas-ta 还是自实现）
- 新建 `src/utils/pricing.py`（round_to/floor_to/get_digits 共 30 行）

**ROI**: ⭐⭐⭐（影响面最大，重构 24KB 技术指标代码）

---

### 🟡 P1-10：日志体系未升级（loguru + daily-rotate）

**vnpy** 用 loguru，配置双 sink（stdout + `vt_YYYYMMDD.log` 按日切分），支持 `extra={"gateway_name": "..."}` 来源 tag。
**本项目** 全项目 `logging.getLogger(__name__)`，无按日切分、无彩色终端、无来源 tag。

**影响**: 多策略并行 + 长跑回测时日志不可追溯。

**修复方案**: 新建 `src/log.py` 包装 loguru，迁移各模块 import（0.5 人天）。

**ROI**: ⭐⭐（长跑服务必备）

---

### 🟡 P1-11：参数优化无统一入口（无 DEAP / 无多目标）

**vnpy `trader/optimize.py`**:
- `OptimizationSetting.add_parameter` + 网格穷举
- `run_ga_optimization` 完整 DEAP NSGA-II 多目标

**本项目**:
- `BacktestEngine.run_grid_search` 网格（功能等价）
- `run_bayesian_search` 自创弱贝叶斯（仅 1 维有效，engine.py:527-633）

**修复方案**: 引入 `deap` 库，新增 `src/optimize/ga.py` 支持多目标（夏普 + 回撤 + 换手）。

**ROI**: ⭐⭐（重策略调参时用）

---

### 🟢 P2-12：BaseApp 插件机制（Web 路由硬编码）

**vnpy `BaseApp`** 抽象类（`app_name / app_module / engine_class / widget_name`），配合 `MainEngine.add_app()` 做插件注册。

**本项目**: `src/web/app.py` 把路由直接 hardcode：
```python
from .routes import main, api
app.include_router(main.router)
app.include_router(api.router, prefix="/api")
```

**影响**: 5 个端点足够，规模到 20+ 时会变痛。

**ROI**: ⭐（锦上添花）

---

### 🟢 P2-13：EventEngine 单 worker + 同步派发

**vnpy**: 异步 Queue + worker 线程；本项目：同步派发。

**风险**: A 股小流量场景够用；若日后引入期货/期权（高频 tick）会成为瓶颈。

**ROI**: ⭐（按需升级）

---

### 🟢 P2-14：`OrderRequest.type` 命名不一致

**vnpy**: `type: OrderType`
**本项目**: `order_type: OrderType`（object.py:144）

**影响**: 与 vnpy 文档/迁移代码风格不一致，混用易混。

**ROI**: ⭐（一致性）

---

### 🟢 P2-15：`BaseSelectionStrategy` 命名歧义 + 放置位置不当

**现状**:
- `src/backtest/base_selection_strategy.py` 定义纯选股协议（只 `select()`，无交易接口）
- 但所有主推策略都从它继承（v6/v_leader 等），文件命名暗示"回测专用"，注册到 strategies.yaml 后又被当"策略"

**影响**: 命名混淆了 "signal generator" 与 "trading strategy"。

**修复**: 改名 `BaseSignalGenerator`，放在 `src/signal/` 下，与 strategy 分层。

**ROI**: ⭐（概念清晰）

---

## 3. 代码质量 Top 10 修复清单

每条：`文件:行号` + 问题 + 建议 + 估时。

| # | 文件:行号 | 问题 | 建议修复 | 估时 |
|---|-----------|------|----------|------|
| 1 | `src/event/engine.py:110-143` | `put()` 未启动时仅 warning 丢弃事件，回测链路上"丢 tick 还不报错"是灾难 bug | 改为 `raise RuntimeError("EventEngine 未启动")` | 15 min |
| 2 | `src/event/engine.py:110-143` | `put` 同步派发但 handler 修改 `_handlers` 时无锁 | 加 `threading.RLock` 保护 `_handlers/_general_handlers` | 30 min |
| 3 | `src/event/engine.py:170-177` | 定时器线程用 bool flag，stop 后最多 sleep 2 秒 | 改用 `threading.Event` + `event.wait(interval)` | 20 min |
| 4 | `src/gateway/base_gateway.py:72-78, 145-174` | 缓存字典（ticks/orders/positions）读写无锁 | 加 `threading.RLock` | 45 min |
| 5 | `src/gateway/main_engine.py:47-56, 128-140` | `Type` 未参数化（应为 `Type[AlphaStrategy]`），策略无生命周期管理 | 引入 `start_strategy/stop_strategy` + 强类型 | 1.5 h |
| 6 | `src/strategy/alpha_strategy.py`（全文） | 几乎所有方法零注解，Protocol 字段裸 `...` | `from __future__ import annotations` + 补齐 | 1 h |
| 7 | `src/strategy/equity_strategy.py:96-101` | `signals.sort_values("signal")` 无 KeyError 防护，子类返缺列的 DataFrame 直接崩 | `required_cols = {"vt_symbol", "signal"}` 显式校验 | 20 min |
| 8 | `src/datafeed/base.py:162-180` | `get_trading_calendar` 默认实现直接 import `src.data.repository`，反向耦合 | 改抽象方法，让 LocalDatafeed/ParquetDatafeed 实现 | 30 min |
| 9 | `src/datafeed/parquet.py:81-82, 285-308` | `_dataset_cache` 无内存上限（5K parquet 一次性 ~223 MB），`get_stock_list` 5K 文件逐个 read_file | `functools.lru_cache(maxsize=512)` + `pyarrow.dataset.to_table(columns=...).distinct()` | 1.5 h |
| 10 | `src/datafeed/parquet.py:225-253` | `_dataset_cache` 多线程并发加载无锁 | 双检查锁 + `threading.Lock` | 20 min |

---

## 4. 单测缺口清单（应补 10 个测试文件）

> **现状**: `src/event + src/gateway + src/strategy + src/datafeed` 共 13 个文件、**0 个直接测试**。

| 新测试文件 | 估时 | 核心用例 |
|------------|------|----------|
| `tests/test_event_engine.py` | 1 h | register/unregister 防重复；put 同步派发；handler 抛异常不影响后续；start/stop 生命周期；并发 put+unregister 无 RuntimeError |
| `tests/test_gateway_object.py` | 30 min | 所有 dataclass 默认值；`vt_symbol` 拼接；`is_active` 状态；`extra` dict 初始化 |
| `tests/test_base_gateway.py` | 1 h | ABC 不能实例化；MockGateway 触发 on_tick/on_trade → Event；缓存读写；write_log level |
| `tests/test_main_engine.py` | 30 min | 默认 EventEngine 单例 vs 外部传入；同名覆盖；add_strategy/get_strategy；stop 关闭 |
| `tests/test_alpha_strategy.py` | 1 h | `setting=None/{}` 都能 init；buy/sell/cover 调用 `strategy_engine.send_order` 参数正确；update_trade 增减 pos_data；update_order 终态移出 active；cancel_all 仅取消 active；execute_trading 触发对应 buy/sell |
| `tests/test_equity_strategy.py` | 1.5 h | `generate_signals` 返 None/空 → 跳过；缺 `signal` 列 → ValueError；on_bars 未到 rebalance_days 早返回；universe < top_k 跳过；filter_universe 逻辑；top_k=0 除零保护；整百股 min_volume 取整；模拟一次完整再平衡校验 send_order 次数 |
| `tests/test_datafeed_base.py` | 20 min | `Interval.SUPPORTED` 包含所有常量；`code_to_market` 6/0/3/4/8/未知 分支；vt_symbol 拆分；ABC 不能实例化 |
| `tests/test_local_datafeed.py` | 1 h | get_bars 单日/区间/count 三种入参；非 1d 周期 NotImplementedError；exchange 与 code 前缀不匹配 warning；get_stock_list 过滤 market |
| `tests/test_parquet_datafeed.py` | 1.5 h | 目录不存在 FileNotFoundError；_read_file 缓存命中不二次 IO；get_bars 文件缺失返 []；get_bars_by_date universe 路径；_df_to_bars 字段映射 |
| `tests/test_engine_integration.py` | 1 h | 端到端：MainEngine + MockGateway + EventEngine + EquityStrategy；gateway.on_tick → EventEngine → strategy.on_bars → strategy_engine.send_order → 回调 gateway.on_trade → strategy.update_trade → pos_data 累加 |

**总估时**: 约 9.5 小时补齐 10 个测试文件，可分 2-3 个 PR 完成。

---

## 5. 形似神不似 — 5 大真相

| # | 真相 | 根因 | 修复 |
|---|------|------|------|
| 1 | `AlphaStrategy/EquityStrategy` 写了但**零消费者** | 基类定义于 6/23，文档暗示"未来用"，但当前所有主推策略都走 `BaseSelectionStrategy.select() → list[str]` 老路 | 选 V6 改造继承 + MainEngine 实现 Protocol |
| 2 | 仓库内**3 套数据对象并存**（gateway/回测/simulator） | gateway 是新写的（vnpy 风格），回测继承 backtesting.py 老路，simulator 自定义 | 新增 `src/adapters/` 做 DataFrame↔BarData 互转，simulator 包装 gateway dataclass |
| 3 | `BaseSelectionStrategy` 命名歧义（"策略" vs "选股"） | 放在 `src/backtest/` 下，选股时又当策略用 | 改名 `BaseSignalGenerator`，移 `src/signal/` |
| 4 | `v_leader_main_surge.py` 看着像 vnpy 风格但永远不下单 | 14KB 文件，XGBoost AUC 0.7912 学术气势，`select()` 只返 `List[str]` 候选代码，没有任何 buy/sell | 在 `EquityStrategy` 走通后改造 |
| 5 | `StrategyEngine` Protocol 6 个方法**零实现** | `MainEngine` 没有 send_order/cancel_order/write_log/get_cash_available/get_holding_value/get_signal 任何一个方法；新建 LiveTradingEngine 需从头写 | 在 MainEngine 内实现 Protocol 委托给 gateway，否则落库模拟 |

---

## 6. 优先行动建议（更新版）

按 ROI 排序，覆盖原 `VNPY_GAP_ANALYSIS.md` 的 7 项 + 新增 8 项 = **15 项**。

| 顺序 | 任务 | 估时 | ROI | 来源 |
|------|------|------|-----|------|
| 1 | **P0-3 单测 0 覆盖**：补 10 个测试文件（分 2-3 PR） | 9.5 h | ⭐⭐⭐ 上线前必修 | 审计 |
| 2 | **P0-4 让基类真有人用**：V6 改造继承 `EquityStrategy` + MainEngine 实现 `StrategyEngine` Protocol | 3-4 h | ⭐⭐⭐ 回测→实盘桥梁 | 集成调研 |
| 3 | **P0-2 3 套数据对象收口**：新增 `src/adapters/` + simulator 包装 | 4-5 h | ⭐⭐⭐ 实盘化前置 | 集成调研 |
| 4 | **P1-9 utils 工具集**：BarGenerator/ArrayManager/价格规整 | 1-2 d | ⭐⭐⭐ 影响 24KB 代码 | 模块盘点 |
| 5 | **代码质量 Top10**（事件引擎线程 + 缓存锁 + Parquet OOM） | 6 h | ⭐⭐⭐ 隐藏 bug | 审计 |
| 6 | **P0-1 研究层三件套**（AlphaLab/Dataset/Model） | 6-8 h | ⭐⭐ ML 演进 | Alpha 对比 |
| 7 | **P1-5 OmsEngine**（vnpy OmsEngine 等价物） | 4-6 h | ⭐⭐ 实盘必做 | 架构对比 |
| 8 | **P1-7 Exchange/Product 枚举** | 2 h | ⭐⭐ 类型安全 | 架构对比 |
| 9 | **P1-8 PositionData.yd_volume** | 30 min | ⭐⭐ T+1 关键 | 架构对比 |
| 10 | **P1-10 loguru 升级** | 0.5 d | ⭐⭐ 长跑必备 | 模块盘点 |
| 11 | **P1-11 DEAP 多目标优化** | 1 d | ⭐⭐ 重策略调参 | Alpha 对比 |
| 12 | **P1-6 双事件分发 + on_quote/on_log** | 1 h | ⭐⭐ 实盘阶段 | 架构对比 |
| 13 | **GAP-1 RiskEngine**（VNPY 原版） | 3-4 h | ⭐⭐⭐ 实盘必备 | 原分析 |
| 14 | **GAP-2 Recorder**（VNPY 原版） | 2-3 h | ⭐⭐⭐ 仿真盘必备 | 原分析 |
| 15 | **P2-12 BaseApp + P2-15 改名** | 1 d | ⭐ 锦上添花 | 模块盘点 |

---

## 7. 不应移植的 vnpy 模块（明确结论）

| 模块 | 原因 |
|------|------|
| `vnpy/rpc/` | 单进程够用，多 worker 再考虑 |
| `vnpy/chart/` | 项目走 Web 路线，应选 Plotly/ECharts/TradingView Lightweight Chart |
| `vnpy/trader/ui/` | 桌面 GUI 与项目架构方向相反 |
| `vnpy/trader/wechat.py` | 企业微信 iLink 比直接走飞书/钉钉 webhook 复杂 10 倍 |
| `vnpy/trader/converter.py` | 项目无 live trading，无 OMS 概念 |

---

## 8. 我们保持的优势（勿改）

| 模块 | 优势 |
|------|------|
| `ScorerRegistry + 8 维评分` | `__init_subclass__` 自动注册，比 vnpy 手动模型注册更现代 |
| `combine()` 收口（scoring/combiner.py） | 消除 5+ 种 combined_score 公式并存 |
| `WalkForwardValidator`（walk_forward.py） | 滚动 OOS + 门禁 + 报告，vnpy 完全没有 |
| A 股规则独立模块（astock_strategy.py） | T+1/涨跌停/印花税/100股整倍 vnpy 没有 |
| `portfolio_engine` 向量化重算 | pivot 一次性构造 [date x code] 收益矩阵 |
| 多源 Datafeed（SQLite + Parquet 双实现） | vnpy 只走 parquet |
| 概率分组评估（train_xgb_v4.py） | 业务导向 precision/recall |
| `EventEngine` handler 异常隔离 | **优于 vnpy**（vnpy handler 抛异常会中断 worker） |
| `EventEngine` 统计（event_count/error_count） | **优于 vnpy** |
| `OrderData.left` 字段（剩余未成交） | **优于 vnpy** |
| `AccountData.commission/margin` | **优于 vnpy** |

---

## 9. 与原 VNPY_GAP_ANALYSIS.md 的关系

| 原版 GAP | 本次调研结论 |
|-----------|--------------|
| GAP-1 RiskEngine | 仍 P0，估时 3-4h |
| GAP-2 Recorder | 仍 P0，估时 2-3h |
| GAP-3 PositionTracker (OMS) | **升级为 P1-5 OmsEngine**（更完整，含缓存+双事件分发） |
| GAP-4 AlphaLab | **升级为 P0-1 研究层三件套**（含 Dataset+Model+processor，比原版更广） |
| GAP-5 Optimize | 演化为 **P1-11 DEAP 多目标** |
| GAP-6 Chart | 仍 P2，不应移植（Web 路线） |
| GAP-7 Broker 真实接入 | 仍 P2，实盘阶段 |

**新增（本次调研特有）**:
- P0-2 3 套数据对象并存
- P0-3 借鉴模块单测 0 覆盖
- P0-4 基类零消费者（**形似神不似**）
- P1-6 双事件分发 / on_quote / on_log
- P1-7 Exchange/Product 枚举
- P1-8 yd_volume
- P1-9 utils 极度单薄（最大影响面）
- P1-10 loguru 升级
- P2-14 OrderRequest 命名不一致
- P2-15 BaseSelectionStrategy 命名歧义
- 代码质量 Top 10 修复

---

## 10. 参考路径汇总

**vnpy 源码**:
- `E:\work\work\vnpy\vnpy\event\engine.py`
- `E:\work\work\vnpy\vnpy\trader\{engine,event,gateway,object,constant,app,optimize,utility,logger,database,setting,converter,wechat}.py`
- `E:\work\work\vnpy\vnpy\alpha\{lab.py,dataset/*,model/*,strategy/*}`
- `E:\work\work\vnpy\vnpy\rpc\` / `E:\work\work\vnpy\vnpy\chart\`

**本项目**:
- 借鉴模块：`e:\work\work\quant-trading-system\src\event\` + `src\gateway\` + `src\strategy\` + `src\datafeed\`
- 主推策略：`src\strategies\v6_reversal_selection.py` + `v6_pipeline_hybrid.py` + `v_leader_main_surge.py`
- 旧基类：`src\backtest\base_strategy.py` + `src\backtest\base_selection_strategy.py`
- 回测引擎：`src\backtest\engine.py` + `src\backtest\portfolio_engine.py`
- 模拟撮合（孤儿）：`src\strategies\simulator.py`
- ML 脚本：`scripts\train_xgb_v4.py` + `walk_forward.py` + `param_server.py` + `build_bull_sample_pool.py`
- 配置：`config\strategies.yaml`（17 条策略注册）
- 入口：`run.py`

**相关文档**:
- `docs/VNPY_GAP_ANALYSIS.md` — 上版差距分析（7 项）
- `docs/Vnpy_Optimization_Notes.md` — V1~V7 落地计划
- `docs/LIVE_TRADING_ROADMAP.md` — 实盘路径

---

*调研日期: 2026-06-23 — 5 路并行 Explore agent × 深度代码审计*
*下次更新: 建议在 P0-3（单测补齐）落地后回顾*