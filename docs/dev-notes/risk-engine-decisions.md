# RiskEngine 实施期边角讨论 — dev-notes

**作者**: 代码助手
**日期**: 2026-06-27
**commit 范围**: `ae15a03` (修复 1/2/3/4) + `343ccd1` (集中度 + EVENT_RISK_ALERT)
**对应 ADR**: [ADR-0007 RiskEngine 完善](../adr/0007-risk-engine.md)
**层级**: 草稿层 (草稿 → ADR 的中间形态, 写完后由 owner 决策是否升格为 ADR)

---

## 0. 概述

ADR-0007 立项时是"骨架决策"（5 个不确定项 + 4 个核心修复 + 备选方案）。
但 v2.2 真正落地时, 会冒出大量"边角问题"（sector 字典维护、event 载荷字段、
测试拆分治理、_last_prices 边界）—— 这些**不适合直接塞进 ADR**（会污染决策
清晰度）但又**不能丢**（下次有人维护时要重新踩坑）。

dev-notes 就是这层"草稿", 与 `OOS2_INVESTIGATION.md` /
`DATA_IMPROVEMENT_TASKS.md` 同级。规则:
- ✅ 写实施中遇到的具体问题 + 决策原因
- ✅ 列未决项（sector 字典覆盖率、threshold 动态化等）
- ❌ 不写"应该由 ADR 决定的东西"（即 D1/D2 那一层）
- ❌ 不写"完整 ADR"（那是草稿层的越界）

---

## 1. 决策表

### 1.1 集中度阈值：风控阶段 0.40, selection 阶段 0.30

**背景**: ADR-0007 修复 3 规定 `sector_concentration_pct = 0.40`, 但 src/selection/sector_constraint.py 实际用 0.30。

**最终决策**:

| 阶段 | 阈值 | 原因 |
|------|------|------|
| **selection（选股）** | `0.30` | 选股时已经决定要"重仓几只", 再过严会过滤掉"重点持仓"（双低、低波需要 2-3 只重仓 30%+） |
| **risk（风控）** | `0.40` | 风控是"最后一道闸", 给策略层留 10% 加仓余量; 同时 0.40 已是"压死线"（超过即触发 ADR §4 风险"触发回滚"） |

**为什么不是统一 0.30**:
- 选股用 0.30, 风控用 0.40 → 选股出 3 只 30% 重仓, 风控层可以"通过 1 只 + 加仓 1 只到 40%"
- 若统一 0.30, 选股出 3 只 30% 后, 想加仓第 1 只到 35% 会被风控拦截 → 矛盾
- 若统一 0.40, 选股阶段会放出"4 只各 25%"这种"伪分散"（25% × 4 = 100% 全行业占满）, 选不到真分散的 3 只

**实际代码位置**: `src/risk/engine.py:69` (RiskConfig 默认值) + `src/selection/sector_constraint.py` (待查, 实施期确认)

---

### 1.2 _last_prices 选择 EVENT_TRADE 不选 EVENT_TICK

**背景**: 集中度计算需要"该标的的当前价格", 候选:
- `EVENT_TICK`（逐笔行情, 1-3 秒一次）
- `EVENT_TRADE`（成交回报, 几秒到几分钟一次）
- `EVENT_BAR_1MIN` / `EVENT_BAR_DAILY`（K 线推送）

**最终决策**: 用 `EVENT_TRADE` 更新 `_last_prices`, `EVENT_TICK` 暂不订阅。

**原因**:

| 维度 | EVENT_TRADE | EVENT_TICK | 选哪个 |
|------|------------|------------|--------|
| 实时性 | 中（成交时才有） | 高（1-3 秒） | TICK |
| 噪音 | 低（实际成交价） | 高（瞬时波动 0.5%+） | TRADE |
| 事件频率 | 低（每只每天 100-1000 笔） | 极高（每只每秒 1-5 笔） | TRADE |
| 实现复杂度 | 已订阅（on_trade） | 需新增订阅 + 类型处理 | TRADE |
| 集中度计算误差 | < 1%（保守: 用最近成交价代替当前价） | 反而 < 1%（但有噪音） | TRADE |
| CPU / 内存 | 几乎零 | 字典高频更新（5000 只 × 10 tick/s = 50k/s） | TRADE |

**关键论据**: 风控是"决策时点检查", 不需要"持续最新价"; 1% 误差在 22% 阈值下 = 0.22%, 远低于 1% 误差上限, **不构成业务影响**。

**何时升级到 EVENT_TICK**:
- 集中度阈值收到 5% 以下
- 接入实盘, 要求"接近实时"风控
- v3.0 接 vnpy CTP gateway 时, 行情推送本身就在 event loop, 增量成本为 0

**实际代码位置**: `src/risk/engine.py:127` (`_last_prices: dict[str, float] = {}`) + `src/risk/engine.py:194-196` (on_trade 写入)

---

### 1.3 测试拆分治理（> 500 行 → 拆 conftest 风格 + 模块测试）

**背景**: 第一波 ae15a03 后, `tests/test_risk_engine.py` 涨到 405 行, 加上第二波 343ccd1 集中度测试后**预估会突破 500 行**, 触发 AGENTS.md §4 黑名单"单文件 > 500 行"守门。

**最终决策**: 拆成 3 个文件 + 1 个共享 fixture:

| 文件 | 行数 | 职责 |
|------|------|------|
| `tests/_risk_fixtures.py` | 67 | 共享 DummyOrder / DummyOrderData / DummyTrade / DummyAccount / freeze_today |
| `tests/test_risk_engine.py` | 405 | RiskConfig / __init__ / check_order 单笔控制 / check_daily_limit / on_order / on_trade / on_account |
| `tests/test_risk_engine_concentration.py` | 361 | check_order 集中度步骤 5-6 + EVENT_RISK_ALERT 双通道 |
| `tests/test_event_data.py` | 47 | RiskAlert dataclass + timestamp 字段 |

**为什么不用 conftest.py**: conftest.py 是 pytest 默认 fixture 加载点, 但本项目"不区分 conftest 与 fixtures"是历史包袱（tests/conftest.py 已有但功能不同）。**新建 `_risk_fixtures.py` 命名**避免和现有 conftest 冲突, 也让"风控 fixtures" 概念显式化。

**为什么不用 class grouping**: pytest 的 class grouping 是测试组织方式, 但**不减少文件行数**（同一个文件多 class）。本次拆分的诉求是"按职责拆文件", 不是"按测试组织"。

**实际代码位置**:
- 拆分决策: `ae15a03` commit message 里有"测试拆分"说明
- 集中度测试独立: `343ccd1` commit

---

### 1.4 EVENT_ACCOUNT 启动延迟处理

**背景**: 实盘启动时, 账户余额推送 (`EVENT_ACCOUNT`) 可能在 1-30 秒后才到（取决于 CTP/XTP 网关实现）。首次 `check_order` 时 `account_balance=0` → 仓位比例校验被跳过。

**最终决策**: 双层兜底:
1. `RiskConfig.initial_balance` 静态兜底（启动时已知账户规模, 如 100w → 100_000_000）
2. `on_account` 首次拿到有效 `balance > 0` 后**注销回调**（省 CPU）

**为什么不用 `if account_balance == 0: return False`**:
- 那会让 `initial_balance=0` 的回测场景被误判
- 也不符合"风控默认放行, 显式配置才严格"的设计哲学

**为什么注销回调不保留**:
- vnpy CTP/XTP 推送频率不高（每秒 1-2 次账户快照）, 但 5000 只股跑 1 小时 = 3600 × 2 = 7200 次空跑
- 注销失败不影响主流程（try/except 容错, `src/risk/engine.py:230-231`）
- v3.0 实盘时如果发现"账户变动后需重新计算" → 恢复订阅, 加"超时重新订阅"逻辑

**未决项**: 注销后, 账户变动（如入金 / 出金）如何感知？目前**没有实现**, 等 v3.0 实盘时加 `EVENT_ACCOUNT_REFRESH` 事件或定时重订阅。

**实际代码位置**: `src/risk/engine.py:204-231` (on_account) + `src/risk/engine.py:131` (`_account_balance` 兜底)

---

### 1.5 集中度计算中"未知行业"的处理

**背景**: sector 字典只覆盖 TOP20 持仓股, 未命中的 → "未知"。集中度校验时, "未知"行业怎么处理？

**最终决策**: 未知行业 = 单独一类, 不与已有行业混算。

**原因**:
- 如果把"未知"和"已知"混算, 100 只未知行业的票会被算成"未知行业 100%" → 100% 集中 → 必拒
- 单独成类后, 100 只"未知" 各占 1%, 不会触发集中度
- 与 `src/selection/sector_constraint.py:UNKNOWN` 对齐（已确认）

**已知局限**: 若策略层在某只"未知行业"票上加仓到 50%, 集中度不会拦截。**这是有意的**——sector 字典不全, 不应该让"未知"标签挡掉正常交易。v3.0 接申万行业分类后, 这个问题自动消失。

**实际代码位置**: `src/risk/sector_map.py:17` (`UNKNOWN = "未知"`) + `src/risk/sector_map.py:74` (默认返回 UNKNOWN)

---

## 2. 实施期小决策（一次性的, 不会出现在 ADR）

| 决策 | 选择 | 原因 |
|------|------|------|
| 事件载荷类名 | `RiskAlert`（不是 `RiskEvent`） | vnpy 风格, 与 `OrderData` / `TradeData` / `AccountData` 区别开（Data 是数据, Alert 是事件） |
| `level` 字段类型 | `Literal["info", "warn", "error"]` | 类型安全; 字符串拼接错误 → mypy 拒绝; 当前只用 warn / error, info 留作"v3.0 半拦截"扩展 |
| `timestamp` 默认值 | `field(default_factory=datetime.now)` | dataclass 必填字段, 但调用方不应关心时间 |
| `_sector_held_amount` fallback | 未知价持仓按 0 计算（不报错） | 风控热路径, 异常 → 保守忽略, 不阻断 |
| 持仓从成交事件流累计 | `volume` 直接累加, 不区分多空 | A 股 T+1 单边, 等于净持仓; v3.0 接融券时再加 `direction` 字段 |
| RiskEngine 不继承 BaseEngine | 是 | v2.1 PRD §6.3 计划 v2.2 改造, 但 v2.2 操盘层接 Simulator 优先, BaseEngine 子类化推 v2.3 |
| `check_order` 入参 duck-typing | 兼容 dict / 对象 / 4 个属性（volume / price / vt_symbol / ?） | 测试场景多用 dict, 实盘用 OrderRequest 对象, duck-typing 覆盖最广 |
| `_reject_order` / `_reject_daily` 拆 2 函数 | 是 | level 不同（warn vs error）, 1 函数分支也行但读起来啰嗦 |
| 日初自动复位 | 用 `date.today()` 简单比, 不用 cron | "开盘前复位"用 `simulator` 触发更准, `date.min` 哨兵保证"首次必复位" |

---

## 3. 边角问题（已发现, 未深入）

### 3.1 sector 字典覆盖率

- 当前: 22 只股（2026-06-27）
- TOP20 持仓股实际覆盖: 22 / 20 = 110%（含部分次重仓）
- A 股全市场: ~5400 只
- 覆盖率: 0.4%
- **未决项**: 是否需要把 sector 字典扩到 TOP100? 扩到 TOP500? 还是直接 v3.0 接申万?

**当前选择**: 不扩, v2.2 跑通优先, v3.0 接申万（ADR §5 实施 Step 4 已说明）。

### 3.2 _last_prices 字典内存增长

- 当前: 5k 只股 × 1 个 float = 5k × 16 bytes = 80 KB
- 1 天成交 1000 只: 1000 × 16 = 16 KB
- **未决项**: 是否需要 LRU 限制? 持仓 0 的票是否清理?
- **当前选择**: 不限制, 内存 80 KB 可忽略, 持仓 0 但成交过的票仍需保留价格（用于"加仓时计算"）。

### 3.3 风控失败的"重试"语义

- 现状: `_emit_alert` 失败时 `logger.warning`, 不影响主流程
- **未决项**: 是否需要重试? 重试上限?
- **当前选择**: 不重试, 风控事件推送失败 = 用户少看到一次告警, 不影响下单拦截（hard reject 仍生效）。

### 3.4 on_order ALLTRADED 校验的边界

- 现状: 只在 `OrderStatus.ALLTRADED` 时 `_daily_trades += 1`
- 边界: 若 OMS 误推送 `OrderStatus = None` 的 order? → `getattr(order, "status", None) != OrderStatus.ALLTRADED` → return（不算交易, 保守）
- 边界: 若 OMS 重复推送 ALLTRADED 2 次? → 算 2 次（与 vnpy 一致）
- **未决项**: 是否需要去重? 当前**不去重**, 理由是 OMS 端应保证"一次订单最多 1 次 ALLTRADED"。

### 3.5 sector_concentration_pct 与 single_symbol_concentration_pct 的优先级

- 现状: check_order 顺序是 `单股仓位比例` → `单标的集中度` → `单行业集中度`
- 边界: 若单笔占账户 25%, 单标阈值 22% → 步骤 5 拒, 步骤 6 不跑
- 边界: 若单笔占 20%, 单标 22% 通过, 但行业 40% 超 → 步骤 6 拒
- **未决项**: 优先级合理吗? 行业是"更高维度", 应先校验?
- **当前选择**: 保留当前顺序（先"个股层"再"行业层"）, 理由: 步骤 5 早拒, 减少"已知拒"时的 sector_map 查询; 步骤 6 是兜底。

---

## 4. 与其他 dev-notes 的关联

| dev-notes | 关联点 |
|-----------|--------|
| `OOS2_INVESTIGATION.md` | 排查 V6 max_dd / index bug; 与本 dev-notes 同级, 都是"实施期发现"层 |
| `DATA_IMPROVEMENT_TASKS.md` | 数据层任务清单; 与本 dev-notes 同级, 但关注数据, 不是风控 |

dev-notes 是**草稿层**, 不会被任何 ADR 引用, 也不会被 CODE_WIKI 引用。
它的存在是为了"下次有人维护时不踩同一个坑"。

---

## 5. 升级为 ADR 的判据

以下情况出现, 建议把本 dev-notes 升格为 ADR-0007.x:

| 情况 | 升格原因 |
|------|----------|
| sector 字典覆盖策略变化（如 v3.0 决定接申万） | 影响范围跨 3+ 文件, 需 ADR 记录 |
| EVENT_RISK_ALERT 升级为"半拦截"模式 | 行为变化, 需 ADR |
| 集中度阈值动态化（如波动率调整） | 行为变化, 需 ADR |
| _last_prices 改用 EVENT_TICK | 性能 / 资源影响, 需 ADR |

否则 dev-notes 保持草稿层, 后续 v2.2 实施期问题继续追加到本文件。

---

*最后更新: 2026-06-27 (ae15a03 + 343ccd1 完成后)*
