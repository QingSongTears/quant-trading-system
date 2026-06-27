# ADR-0007: RiskEngine 完善（下单前风控骨架收敛）

| 字段 | 值 |
|---|---|
| **状态** | Proposed |
| **日期** | 2026-06-27 |
| **决策人** | @QingSongTears |
| **影响范围** | src/risk/, src/constants/risk.py, src/event/, src/gateway/object.py |
| **目标阶段** | v2.2 vnpy 走通 |

## 1. 上下文（Context）

`src/risk/engine.py:1`（VNPY-3，2026-06-27 落地）已实现 `RiskEngine` 骨架，借鉴 vnpy.trader.engine.RiskManager，对外暴露：

- `RiskConfig`（单笔/单日/全局三组阈值）
- `RiskEngine.check_order(req)` — 下单前拦截
- `RiskEngine.check_daily_limit()` — 日内熔断
- 事件订阅：`EVENT_ORDER` / `EVENT_TRADE`

issue #77 评审时发现 5 个落地不确定项 + 4 个核心缺陷：

**A. 5 个不确定项（落地前必须决策）**

1. `max_order_pct` 的单位
2. `account_balance` 的来源（决定 `max_order_pct` 是否可实际生效）
3. `STATUS_ALLTRADED` 常量位置
4. 风控失败的语义（hard reject vs soft warn）
5. dev-notes 临时记录是否单独存档

**B. 4 个核心缺陷（落地后必须修复）**

| # | 问题 | 文件 / 行 |
|---|---|---|
| 1 | 命名规范：`RiskEngine` 不符合本项目"借鉴 vnpy 时去掉 Manager 后缀"惯例（ADR-0006 守门已通过，但 `RiskManager→RiskEngine` 是缩写而非规则，建议补 ADR 留底） | `src/risk/engine.py:56` |
| 2 | `max_order_pct = 0.20` 写死但**从未生效**——`check_order()` 只校验股数 / 金额 / 持仓数，无比例检查；且与同文件 `max_daily_drawdown_pct = 5.0`（百分数）混用，单位不统一 | `src/risk/engine.py:43,49` |
| 3 | "最大持仓数"是**数量维度**，缺"集中度维度"——A 股组合风控的关键是"单一行业 / 单一标的占比"，仅靠 `max_positions=10` 容易出现 10 只全仓小盘股 | `src/risk/engine.py:53,172-179` |
| 4 | `on_order` 任意状态 +1，重复计入（一次 PARTTRADED + 一次 ALLTRADED 算 2 次），与 vnpy RiskManager 默认行为不一致 | `src/risk/engine.py:103-110` |

**C. 关联上下文**

- ADR-0006 已收敛策略基类 4→2，`EquityStrategy` 是 v2.2 操盘层入口，`RiskEngine` 是其下单前的"最后一道闸"
- ADR-0005 已确立 vnpy 命名前缀（`vt_symbol` / `vt_orderid`），RiskEngine 应继续沿用
- `src/constants/risk.py:5-12` 明确"百分比统一用小数"，`STOP_LOSS_DEFAULT = 0.05` 是先例

## 2. 决策（Decision）

### D1. 不确定项的决策（5 项）

| 不确定项 | 决策 | 依据 |
|---|---|---|
| ① `max_order_pct` 单位 | **0.20 = 20%（小数）** | 与 `src/constants/risk.py:5-12` 约定一致；与 `STOP_LOSS_DEFAULT = 0.05` 同语义 |
| ② `account_balance` 来源 | **优先 `EVENT_ACCOUNT` 订阅**，启动时未到账则用 `RiskConfig.initial_balance` 兜底注入 | 仿 vnpy RiskManager `account` 字段；订阅是非侵入式，注入是兼容路径 |
| ③ `STATUS_ALLTRADED` 常量 | **`src/gateway/object.py:52` 已存在**，直接 `from src.gateway.object import OrderStatus` | 复用既有 enum，无需新建 |
| ④ 失败语义 | **hard reject（默认） + EVENT_RISK_ALERT 软告警** 双通道 | 风控默认 hard，但 UI / 监控需要消费事件做可视化（vnpy 风格） |
| ⑤ dev-notes | **写 `docs/dev-notes/risk-engine-decisions.md`**，记录本 ADR 之外的临时讨论 | 与 `OOS2_INVESTIGATION.md` / `DATA_IMPROVEMENT_TASKS.md` 同级 |

### D2. 4 个核心修复

#### 修复 1：命名规范守门
- 当前 `RiskEngine` 命名：**保留**
- 理由：vnpy 原类名 `RiskManager`，去掉 `Manager` 后缀改为 `RiskEngine`，符合本项目"借鉴 vnpy 去掉 Manager/App/Engine 后缀"的简写惯例
- 动作：在 ADR-0006 守门 `dev_tools/hooks/check_naming.py` 增加豁免条目 `RiskEngine: vnpy-shorthand`
- 影响面：零（仅文档化）

#### 修复 2：max_order_pct 单位统一 + 真正生效
- 字段重命名：`max_order_pct` 保持小数（0.20），文档化明确"小数"
- 字段重命名：`max_daily_drawdown_pct = 5.0` 改为 `max_daily_drawdown = 0.05`（小数化）
- `check_order()` 增加第 4 步：单股仓位比例校验
  ```python
  if account_balance > 0:
      pct = amount / account_balance
      if pct > self.config.max_order_pct:
          return False, f"超单股仓位比例: {pct:.2%} > {self.config.max_order_pct:.2%}"
  ```
- 影响面：`RiskConfig` 字段类型不变，仅默认值改 + 新增校验逻辑

#### 修复 3：集中度维度
- 新增 `RiskConfig.sector_concentration_pct = 0.40`（单行业最大占比 40%）
- 新增 `RiskConfig.single_symbol_concentration_pct = 0.22`（单标的占比，与 `MAX_SINGLE_POSITION_PCT` 对齐）
- `check_order()` 新增步骤 5：调用 `_check_concentration(vt_symbol, amount)` 计算"该标的 + 同行业已持仓金额"占账户比
- 数据源：硬编码 sector 字典（v2.2 简化版），v3.0 接申万行业分类
- 影响面：`RiskConfig` 新增 2 字段，`check_order` 新增 1 步骤

#### 修复 4：on_order 语义
- 只在 `OrderStatus.ALLTRADED` 时 `_daily_trades += 1`
- 部分成交通过 `EVENT_TRADE` 累加（已实现）
- 撤单 / 拒绝不计交易次数（更贴近"实际成交"语义）
- 关键改动：
  ```python
  def on_order(self, event: Event) -> None:
      order = event.data
      if order is None or getattr(order, "status", None) != OrderStatus.ALLTRADED:
          return
      self._ensure_daily_reset()
      self._daily_trades += 1
  ```

### D3. 新增事件 EVENT_RISK_ALERT

- 在 `src/event/__init__.py` 新增 `EVENT_RISK_ALERT = "eRiskAlert"`，载荷为 `RiskAlert(reason: str, level: str, vt_symbol: str)`
- `check_order()` / `check_daily_limit()` 返回 `(False, msg)` 时，**同步** `self._event_engine.put(EVENT_RISK_ALERT, RiskAlert(...))`
- 影响面：`src/event/__init__.py` 新增 1 行 + `src/risk/event_data.py` 新增 `RiskAlert` dataclass

## 3. 备选方案（Alternatives Considered）

### 方案 A：hard reject（已选）
- 优点：vnpy 默认语义，符合"风控就是拦截"的直觉；前端无需消费事件即可获得正确性
- 缺点：无法做"预警 / 半拦截 / 提示"等柔性场景
- 否决理由：不适用（作为默认路径已选）

### 方案 B：soft warn（仅日志 + 事件，不阻止下单）
- 优点：策略层可自定义响应（降仓 / 重试 / 强平）
- 缺点：默认不安全，新人易忽略
- 否决：作为 `RiskConfig.strict: bool = True` 的 fallback 保留，硬拦截时 strict=True

### 方案 C：双通道（hard + EVENT_RISK_ALERT，已选）
- 优点：默认安全 + 可观测
- 缺点：双通道维护成本（msg 文本要同时写日志和事件）
- 否决理由：不适用

---

### 方案 A（account_balance 来源）：订阅 EVENT_ACCOUNT（已选）
- 优点：实时更新；多账户切换自然支持
- 缺点：启动时可能尚未触发第一笔 EVENT_ACCOUNT
- 否决理由：不适用

### 方案 B：构造注入 initial_balance
- 优点：启动即可用
- 缺点：账户变动后失效，需手动 set_balance
- 否决：作为 fallback（`RiskConfig.initial_balance` 字段，缺省 0）

### 方案 C：每次 check_order 时从 gateway query
- 优点：永远新鲜
- 缺点：阻塞 IO，破坏"纯函数式 check"的契约
- 否决：违反 v2.2 操盘层"非阻塞"原则

---

### 方案 A（集中度）：硬编码 sector 字典（v2.2，已选）
- 优点：零依赖，落地快
- 缺点：覆盖不全
- 否决理由：不适用（v2.2 简化版可接受）

### 方案 B：外部申万行业表（JSON 文件）
- 优点：覆盖全
- 缺点：v2.2 时间紧，外部数据未稳定
- 否决：v3.0 再做

### 方案 C：不做集中度，只看持仓数
- 优点：最简
- 缺点：与小盘股满仓风险完全无关
- 否决：违反 v2.1 PRD §6.3 风控要求

## 4. 后果（Consequences）

### 正面
- 单位统一（小数化）消除 `STOP_LOSS_PCT` 类历史 bug（src/constants/risk.py:7-8 已记录）
- 集中度检查避免"10 只全仓小盘股"的黑天鹅
- `on_order` 语义修正后，`max_daily_trades=50` 真正能拦住日内过度交易
- `EVENT_RISK_ALERT` 让前端 / 监控可消费（推送 / 看板）
- 与 ADR-0006 的 `EquityStrategy` 形成"操盘入口 + 风控闸口"配对，v2.2 操盘层走通路径完整

### 负面
- `RiskConfig` 字段默认值改 1 个（`max_daily_drawdown_pct: 5.0` → `max_daily_drawdown: 0.05`），破坏向后兼容
  - 缓解：在 docstring 标注 "PR2.2 起改小数"，提供迁移提示
- 新增 2 个字段（`sector_concentration_pct` / `single_symbol_concentration_pct`），已有测试可能需要 fixture 更新
- sector 字典硬编码 → 业务变化时要手动改文件

### 风险
- `EVENT_ACCOUNT` 启动延迟 → 首次 `check_order` 时 `account_balance=0`，仓位比例校验被跳过
  - 缓解：`RiskConfig.initial_balance` 兜底 + 启动时日志 warn
- 集中度计算中的 sector 字典错配 → 误判 / 漏判
  - 缓解：v2.2 只对 TOP20 持仓股做精确映射，其余 fallback 到"未知行业 = 单独一类"
- `on_order` 改 ALLTRADED 后，测试中的 mock OrderData 需要补 `status=OrderStatus.ALLTRADED` 字段
  - 缓解：补 test fixture（issue #77 关联任务）
- **触发回滚**：如果 v2.2 实盘运行时发现 sector 字典覆盖率 <60% → 退回方案 C（只持仓数）

## 5. 实施（Implementation）

| 阶段 | 行动 | 关联 issue |
|---|---|---|
| **Step 1** | 在 `docs/dev-notes/risk-engine-decisions.md` 落临时讨论记录（D1⑤） | #77 |
| **Step 2** | `src/event/__init__.py` 新增 `EVENT_RISK_ALERT` 常量 + `src/risk/event_data.py` 新增 `RiskAlert` dataclass（D3） | #77 |
| **Step 3** | `src/risk/engine.py` 修复 on_order 语义（修复 4）+ 单位统一小数化（修复 2 字段重命名 + 新校验） | #77 |
| **Step 4** | `src/risk/engine.py` 新增集中度检查（修复 3）+ sector 字典硬编码 `src/risk/sector_map.py` | #77 |
| **Step 5** | `src/risk/engine.py` 增加 `on_account` 事件回调 + `initial_balance` 兜底注入（D1②） | #77 |
| **Step 6** | `tests/test_risk_engine.py` 补充 fixture（OrderData status 字段）+ 集中度测试 | #77 |
| **Step 7** | `dev_tools/hooks/check_naming.py` 增加 `RiskEngine: vnpy-shorthand` 豁免（修复 1） | #77 |
| **Step 8** | `docs/CODE_WIKI.md` §风控子系统 增加 RiskEngine 章节 | #77 |
| **Step 9** | ADR 状态改 `Accepted`，删除 dev-notes（如果已固化为 ADR） | #77 |

## 6. 关联

- 反对 / 推翻：无
- 关联 issue：#77（VNPY-3 RiskEngine 实施入口）
- 关联 ADR：
  - ADR-0005（vnpy 命名前缀 → RiskEngine 继续沿用 `vt_symbol` / `vt_orderid`）
  - ADR-0006（策略基类收敛 → `EquityStrategy` 是 RiskEngine 上游调用方）
- 实施入口：`src/risk/engine.py` + `src/constants/risk.py`
- 守门：`dev_tools/hooks/check_naming.py`（豁免 `RiskEngine`）

## 7. 备注

**为什么 `max_order_pct` 默认 0.20（20%）而不是更小？**

- 与 `MAX_SINGLE_POSITION_PCT = 0.22`（src/constants/risk.py:44）几乎对齐，保留 2% 余量给单笔微调
- A 股 10 只持仓 = 平均 10%/只，留 20% 是为"重点持仓"留口（双低 / 低波等策略需要 2-3 只重仓）

**为什么 dev-notes 单独存档？**

- 本 ADR 是"骨架决策"，但 v2.2 实施期间会有大量"边角讨论"（sector 字典维护、event 载荷设计）
- ADR 一旦 Accepted 不易改，dev-notes 是"草稿层"
- 与 `OOS2_INVESTIGATION.md` / `DATA_IMPROVEMENT_TASKS.md` 风格一致

**为什么不直接复用 vnpy RiskManager？**

- vnpy 的 RiskManager 是 `app/risk_manager` 子模块，与 MainEngine 强耦合
- 本项目 v2.1 治理已确定"借鉴风格而非依赖 vnpy"，ADR-0006 已立此规则
- 自己实现更轻量（200 行 vs vnpy 1000+ 行），易测试
