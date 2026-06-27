# ADR-0006: 策略基类收敛 4 → 2

| 字段 | 值 |
|---|---|
| **状态** | Accepted |
| **日期** | 2026-06-27 |
| **决策人** | @QingSongTears |
| **影响范围** | src/strategy/, src/backtest/ |
| **目标阶段** | v2.1 架构治理 |

## 1. 上下文

历史积累导致 4 个并行策略基类，互不兼容：

| 基类 | 位置 | 风格 | 实际继承者 |
|---|---|---|---|
| `BaseStrategy` | `src/backtest/base_strategy.py:19` | backtesting.py | 5 个单股策略 |
| `BaseSelectionStrategy` | `src/backtest/base_selection_strategy.py:21` | 纯 pandas | **9 个生产策略**（V6/V龙头/三因子/盾矛/极小盘/低波/低换手/布林/海龟）|
| `AlphaStrategy` | `src/strategy/alpha_strategy.py:48` | vnpy | **0 个业务类** |
| `EquityStrategy` | `src/strategy/equity_strategy.py:20` | vnpy + 选股 | 1 个（v6_reversal_selection）|

后果：
- 9 个生产策略分散继承，风格不统一
- 新人不知道该继承谁
- 测试资源错配（vnpy 借鉴层 1000+ 行测试，业务策略测试反而最薄）
- 4 种抽象契约互不兼容

## 2. 决策

**收敛 4 → 2**：

### ✅ 保留（永久）

1. **`BaseSelectionStrategy`**（`src/backtest/base_selection_strategy.py`）
   - 9 个生产策略的真正基类
   - 纯 pandas 风格，与现有策略 100% 兼容
   - 不动现有继承关系

2. **`EquityStrategy`**（`src/strategy/equity_strategy.py`）
   - vnpy 模板 + A 股选股，**操盘层入口**
   - V6 已走通（commit b2e6455）
   - v2.2 目标：所有生产策略迁移到此基类

### ⚠️ 废弃（DeprecationWarning，v3.0 删除）

3. **`AlphaStrategy`**（`src/strategy/alpha_strategy.py`）
   - 0 业务继承
   - 加 `DeprecationWarning`（v2.1.1）
   - 保留为 utility/helper，下个大版本删除

### 🗑️ 降级（重命名 → internal）

4. **`BaseStrategy`** → `BacktestingPyAdapter`（`src/backtest/`）
   - 5 个单股回测策略用
   - 不是"通用基类"，是 backtesting.py 库的薄包装
   - 改名更准确
   - 现有 5 个继承者改 import 即可

## 3. 备选方案

### 方案 A：保留全部 4 个
- 优点：零风险
- 缺点：现状不变，技术债复利
- 否决：违背 v2.1 治理目标

### 方案 B：收敛到 1 个（只留 BaseSelectionStrategy）
- 优点：最简洁
- 缺点：vnpy 借鉴层（事件/订单/风控）全废
- 否决：v2.2 还要走通操盘层，需要 EquityStrategy 作为入口

### 方案 C：收敛 4 → 2（已选）
- 优点：保留两条清晰路径（生产策略 / 操盘模板）
- 缺点：需要 2 个版本过渡期
- 否决理由：不适用

### 方案 D：全迁 EquityStrategy
- 优点：vnpy 一统天下
- 缺点：现有 9 个 BaseSelectionStrategy 策略全部重写
- 否决：v2.1 时间窗口不允许

## 4. 后果

### 正面
- 新人只需选 1 个基类（BaseSelectionStrategy 选股，EquityStrategy 操盘）
- 测试资源对齐（base 测试 vs 业务测试比例合理）
- v2.2 操盘层走通路径清晰

### 负面
- 既有 5 个 BaseStrategy 策略要改 import（v2.1.1 一次性）
- AlphaStrategy 标记废弃，文档要更新

### 风险
- 业务策略迁移到 EquityStrategy 可能发现 API 不全 → v2.2 期间补
- **触发回滚**：如果 AlphaStrategy 在 v2.2 期间有第 2 个业务继承者 → 重新评估

## 5. 实施

| 时间 | 行动 |
|---|---|
| **v2.1.1** | AlphaStrategy 加 DeprecationWarning；BaseStrategy → BacktestingPyAdapter rename |
| **v2.2** | 选 2-3 个业务策略试迁 EquityStrategy |
| **v3.0** | 删 AlphaStrategy；BaseSelectionStrategy 标记 internal，所有新策略继承 EquityStrategy |

## 6. 关联

- 反对 / 推翻：无
- 关联 issue：#1（v2.1 治理入口）
- 关联 PR：#76（V6 走通 EquityStrategy 模板验证）
- 实施入口：src/backtest/base_selection_strategy.py + src/strategy/equity_strategy.py
- 守门：dev_tools/hooks/check_naming.py（确保新类有合法后缀）

## 7. 备注

**为什么不"一步到位全部迁到 EquityStrategy"？**

- BaseSelectionStrategy 9 个继承者，迁移需要重写
- EquityStrategy 还在演进（V6 才走通，缺风控/仓位 API）
- v2.1 时间窗口紧，v2.2 才是操盘层主战场
- **渐进式优于大爆炸**：保留两条路径，逐个迁移，符合"形似神不似"诊断的根治方向
