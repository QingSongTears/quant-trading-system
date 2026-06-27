# ADR-0012: 监控 / 报警 / 可观测性（v3.0 前置：实盘化前最后准备）

| 字段 | 值 |
|---|---|
| **状态** | Proposed |
| **日期** | 2026-06-27 |
| **决策人** | @QingSongTears |
| **影响范围** | src/monitoring/（新建包）、src/event/__init__.py（新增 2 个 EVENT 常量）、src/strategies/simulator/、src/web/routes/monitoring.py（新增路由）、tests/test_monitoring_*.py（新增 4-5 个测试文件） |
| **目标阶段** | v3.0 实盘化前置 — 监控/报警/可观测性 |

---

## 1. 上下文（Context）

### 1.1 v3.0 实盘化目标（AGENTS.md §0）

| 阶段 | 状态 | 目标 | 判据 |
|---|---|---|---|
| v2.1 | ✅ | 架构治理 + 模拟盘稳定 | pre-commit 守门 + 模拟盘 0 故障 |
| v2.2 | ✅ | vnpy 借鉴层走通业务路径 | EquityStrategy 接入 Simulator |
| v2.3 | ✅ | Datafeed 统一 | simulator 不绕过 datafeed |
| **v3.0** | **进行中前置** | **实盘化** | **接 xtp/ptrade/qmt + 风控 + 监控** |

> **v3.0 实盘化的硬前置**：实盘后 50-100w 真金白银，每秒都可能"出错"——数据延迟、API 失败、订单超时、风控熔断、异常亏损——任何一种异常 **必须可观测、可报警、可追溯**。
> 没有监控的实盘 = 盲飞；50-100w 资金 = 赌大小。

### 1.2 现况：监控盲区（5 个核心痛点）

| # | 痛点 | 现状 | 实盘后果 |
|---|---|---|---|
| 1 | **PnL 不可观测** | simulator 跑完只在 DB 写一行 `simulation_result.total_return`，运行中无任何 PnL 推送 | 实盘亏/赚不知道什么时候、哪一天发生的 |
| 2 | **持仓不可观测** | simulator 的 `engine.positions` 是进程内 dict，外部拿不到 | 实盘时不知道"现在持仓是什么"，紧急平仓找不到入口 |
| 3 | **风控告警丢失** | RiskEngine.check_order() 拒绝时**只记录 logger.warning**（simulator 现状），EVENT_RISK_ALERT 通道虽然存在但 simulator 没用 | 实盘时单笔拦截无 UI 提示，事后查日志 |
| 4 | **无异常检测** | 数据延迟 / API 失败 / 订单超时**没有任何机制检测** | 实盘数据卡 30 分钟才发现，订单卡单不知情 |
| 5 | **无可观测性通道** | 日志是唯一的可观测性通道（`logger.warning` / `logger.info`），无 metric sink、无 metrics endpoint | 实盘时 CPU / 内存 / 延迟 / 错误率全部黑盒 |

**关键讽刺**：
- `EVENT_RISK_ALERT`（ADR-0007 D3）**已经实现**——RiskEngine.check_order 拒绝时 put 一条 RiskAlert，**但 simulator 的 `_check_risk` 路径没有消费它**（#82 Step 5 只是 wrapper，没透传到 EventEngine）
- vnpy 风格的 EventEngine 已经能承载监控事件流（ADR-0002 同步派发，#77 已落地 EVENT_RISK_ALERT）—— **基础设施已就位，只缺 producer/consumer 串联**

### 1.3 历史与 ADR 上下文

- **2026-06-24 ADR-0002**（EventEngine 同步派发）：监控事件流的基础设施已就位；`register(type, handler)` + `put(Event)` 是监控的标准模式
- **2026-06-25 ADR-0007**（RiskEngine 完善）：`EVENT_RISK_ALERT` + `RiskAlert` dataclass 已实现（`src/risk/event_data.py:25`）；**本次监控可直接复用**
- **2026-06-27 ADR-0009**（BacktestEngine 重构）：建立了"按职责拆分"模式（event_loop / portfolio / metrics 拆分），本 ADR 沿用
- **2026-06-27 ADR-0010**（Datafeed 统一）：所有 SQL 查询走 datafeed，**监控要消费的"持仓 / PnL / 告警"全是事件流派生（不是 SQL）**，与 datafeed 边界正交
- **2026-06-27 ADR-0011**（Simulator 重构）：simulator 已经集成 RiskEngine.check_order（#82 D4-A），simulator 是本 ADR 的**主要 producer 之一**
- **2026-06-27 #82 收尾**：948 测试全绿（除环境依赖 pre-existing failures），simulator 30 commit / 34 新测试

### 1.4 关联上下文（实盘化准备清单）

v3.0 实盘化前必做的"前 3 项"：

1. **Simulator 重构 + 测试覆盖**（#82 ✅）
2. **监控 / 报警 / 可观测性**（本 ADR #83）
3. **风控规则压测 + 实盘化回归测试**（后续 P4.x）

> **本 ADR 是 v3.0 实盘化的"左半边"**——没有监控，风控规则再严也只是"事后查日志"，50-100w 实盘等于盲飞。

### 1.5 关键约束

- **复用 EVENT_RISK_ALERT**：RiskEngine 已经 put `RiskAlert`，monitoring 直接 subscribe；不重复造轮子
- **不接外部通道**：微信 / 邮件 / Slack / 钉钉**留到 v3.0 实盘阶段**（依赖第三方 SDK + 鉴权 + 限流，本 ADR 只做"日志 + 事件 + Web UI"三通道）
- **不引入时序库**（Prometheus / InfluxDB / TimescaleDB）：v3.0 实盘阶段再评估；本 ADR 用 SQLite + 内存 ring buffer
- **不改 src/risk/**（#77 治理 PR）；不改 simulator/（#82）；不改 datafeed（#81）
- **文件 < 500 行**：监控包按职责拆 5-6 文件，每个 ≤ 200 行
- **948 测试仍全绿**：monitoring 测试独立，新增到 1000+ 测试不破坏基线

---

## 2. 决策（Decision）

### D1. 监控范围：方案 C（基础 + 风控 + 异常检测）

**决策**：**方案 C（全量覆盖）**——监控 3 类事件：PnL/持仓（基础）+ 风控告警（已有 #77）+ 异常检测（数据延迟/API 失败/订单超时）

| 类别 | 监控项 | 数据源 | 触发频率 |
|---|---|---|---|
| **基础 PnL** | 每日 PnL（已实现盈亏 + 持仓盈亏 + 总权益） | simulator 每日 `_simulate_one` 末尾 | 每日 1 次 |
| **基础持仓** | 当前持仓（code / size / cost_basis / market_value / unrealized_pnl） | simulator `engine.positions` | 每次变化时 |
| **风控告警** | 单笔拒绝（warn）/ 日熔断（error）/ 集中度超限 | RiskEngine（已有 EVENT_RISK_ALERT） | 触发时 |
| **异常：数据延迟** | 距最近一根 K 线时间差 > 阈值（如 30 分钟） | BarGenerator / datafeed 定时器 | 每分钟 1 次 |
| **异常：API 失败** | datafeed / simulator / order 连续失败次数 > 阈值 | 各模块 error 计数 | 触发时 |
| **异常：订单超时** | 订单状态长时间未变化（如 5 分钟未 ALLTRADED） | OmsEngine 订单回报 | 每分钟 1 次 |

**理由**：

- **基础 PnL + 持仓是实盘的"仪表盘"**——不可能不上
- **风控告警 EVENT_RISK_ALERT 复用**（#77 已实现）——零成本接入
- **异常检测是实盘高风险场景**：xtp 断线 / Tushare 限流 / 订单卡单——**任何一种都能让账户爆亏**

**为什么不选方案 A（仅基础 PnL + 持仓）**：

- 风控告警是 v3.0 实盘的核心需求——实盘时单笔拦截必须 UI 可见
- `EVENT_RISK_ALERT` 已经实现 + simulator 集成 check_order——只缺一个 consumer（监控包）
- **方案 A = 把 #77 的成果扔掉 50%**

**为什么不选方案 B（基础 + 风控，不含异常检测）**：

- 实盘场景下异常检测比 PnL 更紧急——"卡单 30 分钟不报"比"今天亏 5%"致命
- 数据延迟 / API 失败 / 订单超时是 xtp / ptrade / qmt 的常态坑——必须监控
- **方案 B = 实盘后第一次故障 = 报警通道空，事后追悔**

---

### D2. 报警通道：方案 B（日志 + 事件 + Web UI，不接外部通道）

**决策**：**方案 B（日志 + 事件 + Web UI）**——3 通道；外部通道（微信/邮件/Slack）留 v3.0 实盘阶段

| 通道 | 实现 | 用途 |
|---|---|---|
| **日志** | `logger.warning` / `logger.error` | 服务端日志（落 `output/logs/*.log`），用于事后追溯 |
| **事件** | `engine.put(Event(EVENT_ALERT, ...))` | 内部事件流——其他模块可订阅（如订单引擎收到熔断事件后停止新开仓） |
| **Web UI** | `GET /api/monitoring/alerts` + `GET /api/monitoring/health` | 浏览器实时查看（v3.0 实盘时再加自动轮询） |
| ~~外部通道~~ | ~~微信 / 邮件 / Slack / 钉钉~~ | **留 v3.0 实盘阶段**——本 ADR 不接 |

**新增事件常量**（`src/event/__init__.py`）：
- `EVENT_PNL_UPDATE = "ePnlUpdate"` — PnL 推送
- `EVENT_POSITION_UPDATE = "ePositionUpdate"` — 持仓推送
- `EVENT_ANOMALY = "eAnomaly"` — 异常事件
- `EVENT_ALERT = "eAlert"` — 报警事件（AlertRule 触发后 put）
- **EVENT_RISK_ALERT 复用**（已存在）

**理由**：

- **3 通道覆盖实盘核心需求**：日志（追溯）+ 事件（内部联动）+ Web UI（人眼看）
- **不接外部通道**：微信 / 邮件 SDK 依赖第三方服务 + 鉴权 + 限流 + 退避策略——本 ADR 范围太大；**v3.0 实盘阶段再单独 ADR 决策**
- **与 #82 simulator 风格一致**：simulator 也是"日志 + DB 持久化"双通道，没接外部通道

**为什么不选方案 A（日志 + 事件，无 Web UI）**：

- 实盘时人眼必须在浏览器看——Web UI 是 v3.0 的"标配"
- simulator 持久化到 SQLite 但**没人看**——监控的价值在于"实时可见"
- Web UI 增量成本低（FastAPI + Jinja2 模板 + 简单 polling 即可）

**为什么不选方案 C（含外部通道）**：

- 外部通道**不是"监控范围"的子集，是"通知方式"的扩展**——本 ADR 锁定 4 个监控事件，外部通道只是"另一个 sink"
- 微信 SDK（itchat / wxpy）已不维护，邮件需 SMTP 鉴权，Slack/钉钉需 webhook——**至少 3 个第三方集成 + 1 个 ADR 才能决策**
- **v3.0 实盘阶段再开 ADR-0013 决策外部通道**

---

### D3. 持久化：方案 B（内存 ring buffer + SQLite 可选）

**决策**：**方案 B（内存 ring buffer + SQLite 可选）**——内存默认，SQLite 可选配置

| 存储 | 实现 | 默认 | 用途 |
|---|---|---|---|
| **内存 ring buffer** | `collections.deque(maxlen=1000)` × 3 类（PnL / Position / Alert） | ✅ 默认 | 实时展示（Web UI 拉取最近 100 条） |
| **SQLite 持久化** | `monitoring.db`（与主 DB 分离） | ❌ 可选配置 | 重启后追溯 / 历史查询（v3.0 实盘开启） |
| ~~时序库~~ | ~~Prometheus / InfluxDB / TimescaleDB~~ | — | **留 v3.0 实盘评估** |

**理由**：

- **内存 ring buffer 足够覆盖 v2.x 阶段**：simulator 跑完就关进程，不需要跨重启持久化
- **SQLite 可选配置**：监控包接受可选 `sqlite_path` 参数；不传则只走内存（与 ADR-0010 D3 "业务宽表 → DataRepository 保留"风格一致——提供扩展点，强制最小化）
- **不引入时序库**：Prometheus 等需要服务发现 + scrape + 长期存储——v2.x 阶段投入产出比不足
- **ring buffer 的 O(1) append + 自动淘汰**：1000 条 × 3 类 = 3000 条 ≈ 几百 KB，零内存压力

**为什么不选方案 A（仅内存）**：

- v3.0 实盘必须能"昨天发生过什么"——纯内存重启即丢失
- SQLite 增量成本低（一张表 + 3 个 insert），"可选"不影响 v2.x 阶段

**为什么不选方案 C（独立时序库）**：

- 时序库是"长期监控 + 高频写入 + 告警系统"——本 ADR 是"轻量 + 内嵌 + 可选"
- Prometheus / InfluxDB 需要独立部署 + 端口 + 鉴权——v2.x 阶段维护成本过高
- **v3.0 实盘化时再单独 ADR 评估时序库**

---

### D4. 与 EventEngine 集成：方案 A（新建 EVENT_PNL_UPDATE / EVENT_POSITION_UPDATE / EVENT_ANOMALY，复用 EVENT_RISK_ALERT）

**决策**：**方案 A（新建 4 个 EVENT 常量 + 复用 EVENT_RISK_ALERT）**——按监控事件类型新增 4 个常量，复用现有 RiskAlert

**新增事件**（`src/event/__init__.py` 追加）：

```python
# ── 监控 (ADR-0012) ──────────────────────────
EVENT_PNL_UPDATE = "ePnlUpdate"          # PnL 推送 (simulator 每日推送)
EVENT_POSITION_UPDATE = "ePositionUpdate" # 持仓推送 (simulator 持仓变化时)
EVENT_ANOMALY = "eAnomaly"               # 异常事件 (数据延迟/API 失败/订单超时)
EVENT_ALERT = "eAlert"                   # 报警事件 (AlertRule 触发后)
# EVENT_RISK_ALERT 复用 (ADR-0007 D3 已存在)
```

**新增 dataclass**（`src/monitoring/event_data.py`）：
- `PnlSnapshot` — PnL 快照（date / total_value / daily_pnl / unrealized_pnl / position_count）
- `PositionSnapshot` — 持仓快照（vt_symbol / size / cost_basis / market_value / unrealized_pnl / timestamp）
- `AnomalyEvent` — 异常事件（kind: Literal["data_delay", "api_failure", "order_timeout"] / severity / detail / timestamp）

**集成点**：

| producer | 事件 | 触发点 | payload |
|---|---|---|---|
| `simulator.event_loop` | `EVENT_PNL_UPDATE` | 每日 `_simulate_one` 末尾 | `PnlSnapshot` |
| `simulator.event_loop` | `EVENT_POSITION_UPDATE` | 持仓变化时（开仓/平仓/价格更新） | `PositionSnapshot` |
| `simulator.event_loop` | `EVENT_RISK_ALERT`（间接） | RiskEngine.check_order 拒绝时 | `RiskAlert`（已存在） |
| `monitoring.anomaly_detector` | `EVENT_ANOMALY` | 数据延迟 / API 失败 / 订单超时 | `AnomalyEvent` |
| `monitoring.alert_dispatcher` | `EVENT_ALERT` | AlertRule 触发后 | `dict[str, Any]` |

**理由**：

- **新建事件类型 vs 复用现有 + sink** —— 实盘场景下监控事件类型清晰区分（PnL / Position / Anomaly / Alert），订阅者按类型 register，**调试栈浅 + 类型安全**
- **复用 EVENT_RISK_ALERT**：RiskEngine 已 put `RiskAlert`，监控包直接 subscribe，零额外代码
- **与 vnpy 风格一致**：vnpy 的 EventEngine 也按类型分发——eTick / eOrder / eTrade / eAccount，每个事件类型独立

**为什么不选方案 B（复用现有事件 + 加 metric sink）**：

- **现有 EVENT_ORDER / EVENT_TRADE / EVENT_ACCOUNT 不适合做监控事件**——它们是"业务事件流"，监控要的是"派生指标"（PnL / 持仓 / 异常）
- **"加 metric sink" 等于把所有事件 producer 都加 hook**——侵入性高，simulator / RiskEngine / OmsEngine 都要改
- **方案 A 是"显式事件流"**：producer 知道自己在发什么事件，consumer 按类型订阅——调试 / 单测都简单
- **方案 B 是"隐式 metric sink"**：所有事件都过 sink——日志爆炸 + 调试栈深 + 类型不安全

---

### 最终选择

| 决策点 | 选哪个 | 实际落地 |
|---|---|---|
| 监控范围 | **D1-C（全量）** | PnL + 持仓 + 风控告警 + 异常检测（数据延迟/API 失败/订单超时）|
| 报警通道 | **D2-B（日志+事件+Web UI）** | 3 通道；外部通道（微信/邮件）留 v3.0 实盘 |
| 持久化 | **D3-B（内存+SQLite 可选）** | 内存 ring buffer（默认）+ SQLite 可选；时序库留 v3.0 评估 |
| EventEngine 集成 | **D4-A（新建 4 事件 + 复用 RISK_ALERT）** | EVENT_PNL_UPDATE / EVENT_POSITION_UPDATE / EVENT_ANOMALY / EVENT_ALERT；EVENT_RISK_ALERT 复用 |

---

## 3. 备选方案（Alternatives Considered）

### D1 备选：监控范围

#### 方案 A：仅基础 PnL + 持仓
- 优点：实现最简，1-2 个事件
- 缺点：风控告警丢失（#77 成果浪费）；无异常检测（实盘后第一次故障 = 报警通道空）
- 否决理由：v3.0 实盘硬需求（PnL/持仓/告警/异常）缺一不可

#### 方案 B：基础 + 风控，不含异常检测
- 优点：复用 #77 EVENT_RISK_ALERT；新增工作量少
- 缺点：实盘场景下异常检测是最高优先级（xtp 断线 / 数据卡单）
- 否决理由：实盘后第一次"API 失败" = 监控盲区

#### 方案 C：全量（基础 + 风控 + 异常检测，已选）
- 优点：覆盖 v3.0 实盘硬前置；基础设施完备
- 缺点：异常检测增量代码 + 测试较多
- 否决理由：不适用（已选）

---

### D2 备选：报警通道

#### 方案 A：仅日志 + 事件
- 优点：实现最简
- 缺点：实盘时人眼必须在浏览器看——Web UI 是标配
- 否决理由：方案 A 等于"事后查日志"——与 v3.0 实盘目标冲突

#### 方案 B：日志 + 事件 + Web UI（已选）
- 优点：3 通道覆盖 v2.x 实盘核心需求
- 缺点：Web UI 增量代码（FastAPI 路由 + 模板）
- 否决理由：不适用（已选）

#### 方案 C：含外部通道（微信 / 邮件 / Slack）
- 优点：通知更及时
- 缺点：第三方 SDK 依赖 + 鉴权 + 限流 + 退避策略；至少 3 个集成 + 1 个 ADR
- 否决理由：v3.0 实盘阶段单独 ADR 决策（ADR-0013 候选）

---

### D3 备选：持久化

#### 方案 A：仅内存
- 优点：0 持久化代码
- 缺点：v3.0 实盘时"昨天发生过什么"查不到
- 否决理由：重启即丢失

#### 方案 B：内存 ring buffer + SQLite 可选（已选）
- 优点：内存默认（v2.x 足够）+ SQLite 扩展点（v3.0 实盘可选启用）
- 缺点：SQLite 可选配置（多 1 个参数）
- 否决理由：不适用（已选）

#### 方案 C：独立时序库
- 优点：长期监控 + 高频写入 + 告警系统
- 缺点：Prometheus / InfluxDB 部署成本高；v2.x 阶段维护过重
- 否决理由：v3.0 实盘阶段单独 ADR 评估

---

### D4 备选：与 EventEngine 集成

#### 方案 A：新建 4 个 EVENT 常量 + 复用 RISK_ALERT（已选）
- 优点：类型清晰 + 调试栈浅 + vnpy 风格一致
- 缺点：新增 4 个事件类型 + 3 个 dataclass
- 否决理由：不适用（已选）

#### 方案 B：复用现有事件 + 加 metric sink
- 优点：0 新增事件类型
- 缺点：侵入性高（所有 producer 加 hook）；调试栈深；类型不安全
- 否决理由：现有 EVENT_ORDER / EVENT_TRADE 是业务事件，不是监控指标

#### 方案 C：完全独立（monitoring 包自建事件总线）
- 优点：与 EventEngine 解耦
- 缺点：双事件总线（EventEngine + monitoring bus）难维护；违反"统一事件流"原则
- 否决理由：违反 ADR-0002 已落定的 EventEngine 同步派发模式

---

## 4. 后果（Consequences）

### 正面

- **v3.0 实盘化基础设施完备**：PnL / 持仓 / 风控告警 / 异常检测 / 报警通道全栈覆盖
- **复用 EVENT_RISK_ALERT**（#77 成果）——零成本接入风控告警
- **Web UI 实时可见**——`GET /api/monitoring/{pnl,positions,alerts,health}` 4 端点
- **新增监控测试 ≥ 30 个**：独立测试包 + 集成测试
- **测试基线 948 → 978+**：新增 monitoring 测试**只增不减**
- **明确边界**：monitoring 包是 EventEngine consumer + sink——不修改 simulator/risk/datafeed/web 现有行为

### 负面

- **新增 src/monitoring/ 包（5-6 文件）**：新人认知成本（但每个文件 ≤ 200 行 + 职责单一）
- **EventEngine 启动后挂 4 个监控 handler**：CPU 增加 1-2%（handler 都是简单 dict 操作）
- **Web 路由新增 /api/monitoring/\* 4 端点**：API 表面增加（但全部 GET，符合 RESTful）

### 风险

- **simulator 推送 EVENT_PNL_UPDATE 增加运行时开销**：每只股票 × 每天推送 1 次 × 模拟期间天数 = 几百次 put
  - 缓解：EventEngine put 是同步派发但 handler 简单（仅写 ring buffer + 可选 SQLite insert），单次 < 1ms
- **Ring buffer 容量配置（默认 1000）**：高频场景可能溢出
  - 缓解：deque maxlen 自动淘汰；1000 × 3 类 ≈ 几百 KB；v3.0 实盘可调 10_000
- **SQLite 可选路径冲突**：monitoring.db 与主 DB 同目录
  - 缓解：monitoring 包接受 `sqlite_path=None`（默认内存）；开启时显式指定路径
- **AnomalyDetector 误报**：阈值过严 → 噪声报警；阈值过松 → 漏报
  - 缓解：默认阈值保守（数据延迟 30min / API 失败 5 次 / 订单超时 5min），可配置
- **回滚触发**：若 Step 2（monitoring 包）导致 ≥3 处测试失败 → 退回 D1-A（仅基础 PnL+持仓）

---

## 5. 实施（Implementation）

| 阶段 | 行动 | 关联 issue |
|---|---|---|
| **Step 1** | **写 ADR-0012**（本文件）+ **新建 `src/monitoring/` 包骨架**：建 `src/monitoring/__init__.py`（facade re-export）+ `event_data.py`（PnlSnapshot / PositionSnapshot / AnomalyEvent dataclass）+ `event_types.py`（导出 4 个 EVENT 常量）；`src/event/__init__.py` 追加 `EVENT_PNL_UPDATE / EVENT_POSITION_UPDATE / EVENT_ANOMALY / EVENT_ALERT` | #83 |
| **Step 2** | **Step 1 红绿测试**：`tests/test_monitoring_event_data.py`（dataclass 字段默认值 + 序列化 ~8 tests）+ `tests/test_monitoring_event_types.py`（事件常量定义 + 4 tests） | #83 |
| **Step 3** | **Step 2 指标采集器**：建 `src/monitoring/collector.py`（PnlCollector / PositionCollector）+ `anomaly_detector.py`（数据延迟 / API 失败 / 订单超时 3 类异常检测）+ `tests/test_monitoring_collector.py`（~10 tests）+ `tests/test_monitoring_anomaly.py`（~6 tests） | #83 |
| **Step 4** | **Step 3 持久化**：建 `src/monitoring/store.py`（InMemoryBuffer ring buffer + SqliteStore 可选 + MetricStore 抽象）+ `tests/test_monitoring_store.py`（~10 tests） | #83 |
| **Step 5** | **Step 4 报警规则 + 异常检测**：建 `src/monitoring/alert.py`（AlertRule 阈值 + AlertDispatcher）+ `dispatcher.py`（日志 + 事件 + Web UI 3 通道分发）+ `tests/test_monitoring_alert.py`（~8 tests）+ `tests/test_monitoring_dispatcher.py`（~6 tests） | #83 |
| **Step 6** | **Step 5 simulator + RiskEngine 集成**：simulator.event_loop 在每日末尾 put `EVENT_PNL_UPDATE`；持仓变化时 put `EVENT_POSITION_UPDATE`；RiskEngine 的 `_check_risk` wrapper 透传到 EventEngine.put（**注意**：simulator 当前 logger.warning 已记录，本 ADR 改为 EventEngine.put 触发监控订阅器；不破坏 ADR-0011 D4-A 行为） | #83 |
| **Step 7** | **Step 6 Web 路由 + 守门 + ADR Accepted**：建 `src/web/routes/monitoring.py`（`GET /api/monitoring/pnl` / `/positions` / `/alerts` / `/health`）+ `tests/test_monitoring_routes.py`（~6 tests）+ `tests/test_monitoring_integration.py`（~4 tests）端到端 + `scripts/dev/baseline_tests.sh`（守门脚本）；948 → 978+ 测试全绿 + ADR-0012 Accepted | #83 |

**约束**：

- Step 1 必须先于 Step 2（ADR 是事实单源）
- Step 2 必须先于 Step 3-5（红绿循环：测试先看红、改完看绿）
- Step 3-5 可并行（collector / store / alert 各管一文件）
- Step 6 必须先于 Step 7（simulator 集成是 Web 路由的数据源）
- 整个实施过程不引入 `_legacy/` 备份（与 ADR-0010/0011 风格一致）
- **不改 src/risk/**（#77 治理 PR）
- **不改 simulator/ 包内 dataclass / facade 接口**（#82 已锁定）—— 只能"在 event_loop / engine 注入 EventEngine.put 调用"
- **不改 src/data/datafeed/**（#81 治理 PR）
- **不改 src/strategies/{v6_reversal_selection, v6_pipeline_hybrid}.py**（#81 已改过）

**ADR 流程（本 ADR 后续状态变更）**：

- **现在**：Proposed（待评审）
- **Step 7 完成后**：Proposed → Accepted

---

## 6. 关联

- 反对 / 推翻：无
- 关联 issue：**#83**（v3.0 实盘化前置 — 监控/报警/可观测性，P2.3 #82 之下一号）
- 关联 ADR：
  - **ADR-0002**（EventEngine 同步派发 → 监控事件流的基础设施）
  - **ADR-0007**（RiskEngine 完善 → EVENT_RISK_ALERT + RiskAlert 已实现，本 ADR 直接复用）
  - **ADR-0009**（BacktestEngine 重构 → 建立了"按职责拆分"模式，本 ADR 沿用）
  - **ADR-0010**（Datafeed 统一 → 与本 ADR 正交；监控消费的是事件流，不是 SQL）
  - **ADR-0011**（Simulator 重构 → simulator 是本 ADR 的主要 producer；RiskEngine.check_order 已集成）
- 实施入口：`src/monitoring/`（新建包） + `src/event/__init__.py`（追加 4 个 EVENT 常量） + `src/strategies/simulator/event_loop.py`（注入 put 调用） + `src/web/routes/monitoring.py`（新增 4 端点）
- 守门：978+ 测试 + `scripts/dev/baseline_tests.sh` + 7 个 dev_tools/hooks/ 守门 + check_file_size.py ≤ 500 行
- 关联文件（不动）：`src/risk/`（#77 治理 PR）、`src/strategies/{v6_reversal_selection,v6_pipeline_hybrid}.py`（#81 已改过）、`src/strategies/simulator/{portfolio_types,result_types,signal_adapter,persistence,__init__}.py`（#82 已锁定）、`src/data/datafeed/`（#81 治理 PR）、`AGENTS.md`

---

## 7. 备注

**为什么选 D1-C（全量）不选 D1-A（仅基础）？**

- v3.0 实盘硬需求：PnL/持仓/告警/异常缺一不可
- EVENT_RISK_ALERT 已实现（#77）——复用零成本
- 异常检测（数据延迟/API 失败/订单超时）是 xtp / ptrade / qmt 的常态坑

**为什么不接外部通道（D2-C）？**

- 外部通道（微信/邮件/Slack）不是"监控范围"的子集，是"通知方式"的扩展
- 第三方 SDK 依赖 + 鉴权 + 限流 + 退避策略——至少 1 个独立 ADR 决策
- **v3.0 实盘阶段再开 ADR-0013 决策外部通道**

**为什么 D3-B（内存 + SQLite 可选）不选 D3-A（仅内存）？**

- v3.0 实盘必须能"昨天发生过什么"——纯内存重启即丢失
- SQLite 增量成本低（1 张表 + 3 个 insert），"可选"不影响 v2.x 阶段
- 与 ADR-0010 D3 "DataManager.business 暴露 DataRepository" 风格一致——提供扩展点，强制最小化

**为什么 D4-A（新建事件）不选 D4-B（metric sink）？**

- 现有 EVENT_ORDER / EVENT_TRADE / EVENT_ACCOUNT 是"业务事件"，不是"监控指标"
- 监控要的是"派生指标"（PnL / 持仓 / 异常）——不是原始业务事件
- 新建事件 vs sink：调试栈浅 + 类型安全 + vnpy 风格一致

**为什么不引入时序库（D3-C）？**

- 时序库是"长期监控 + 高频写入 + 告警系统"——本 ADR 是"轻量 + 内嵌 + 可选"
- Prometheus / InfluxDB 需要独立部署 + 端口 + 鉴权——v2.x 阶段维护成本过高
- **v3.0 实盘化时再单独 ADR 评估**

**为什么 simulator.event_loop 末尾 put EVENT_PNL_UPDATE 而不是 risk engine put？**

- simulator 是 PnL 的"天然 producer"——每只股票每日跑完就有 equity_curve
- RiskEngine 只关心"风控拦截"，不关心"PnL 走势"
- 关注点分离：simulator = PnL producer；RiskEngine = 风控拦截 producer；monitoring = consumer

**为什么 monitoring 包与 simulator / risk 解耦？**

- monitoring 是"纯消费者 + sink"——只 register handler，不修改 producer 行为
- producer 只 put 事件，consumer 按需订阅——与 vnpy 风格一致
- 单测可独立：monitoring 测试 mock EventEngine；simulator 测试不依赖 monitoring