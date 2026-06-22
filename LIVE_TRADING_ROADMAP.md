# 实盘化路线图 — LIVE_TRADING_ROADMAP

> **目标**：50–100w 个人账户，从开发验证走到稳定实盘。
> **当前分支**：`develop`（已包含 #73 操盘体系、PR3 重构、XGBoost v4）
> **最近更新**：2026-06-22

---

## 一、项目定位

A 股多因子量化系统，分两层策略并行：

- **V6 超卖反转**（短期信号）：RSI/BB/回撤四条件 → 抓超跌反弹
- **V龙头 主升浪**（中期信号）：8 维评分 + 技术指标 + ML → 抓主升浪启动

信号层之上是 **操盘层**（仓位 + 止盈止损 + T+1 + 涨跌停），之下是 **模拟交易引擎**，最终目标接 **券商接口** 进实盘。

---

## 二、资金 / 上线里程碑

| 阶段 | 资金规模 | 状态 |
|------|----------|------|
| 模拟盘验证 | 0（虚拟 100w） | 待启动 |
| 小资金实盘 | 5–10w | 阶段 5 启动线 |
| 中等实盘 | 30w | 阶段 5 +1 月评估 |
| 目标规模 | 50–100w | 阶段 5 +3 月评估放量 |

**硬纪律**：
- 阶段 1（OOS 验证）不过不上阶段 3（模拟盘）
- 阶段 3 不过不上阶段 5（实盘）
- 任何 in-sample 优化必须带 OOS 报告

---

## 三、策略架构

```
┌─────────────────────────────────────────────────────────┐
│  信号层 (Strategies)                                      │
│  ├─ V6超卖反转 (v6_reversal_selection)                  │
│  └─ V龙头主升  (XGBoost v4 + 8 维评分)                  │
├─────────────────────────────────────────────────────────┤
│  操盘层 (Trading) — Issue #73                            │
│  ├─ PositionSizer  : fixed / kelly / atr / turtle       │
│  ├─ StopLoss       : fixed / atr / both                 │
│  ├─ TakeProfit     : fixed / trailing_atr               │
│  └─ TimeStop       : max_holding_days / no_profit_days  │
├─────────────────────────────────────────────────────────┤
│  规则层 (AStock Rules) — astock_strategy.py               │
│  ├─ T+1  / 涨跌停  / 100 股整倍                          │
│  └─ 佣金万 2.5 / 印花税千 0.5 / 滑点 5bps                │
├─────────────────────────────────────────────────────────┤
│  模拟层 (Simulator) — simulator.py                        │
│  └─ 信号→仓位→止损→T+1→执行→DB 持久化                    │
├─────────────────────────────────────────────────────────┤
│  执行层 (Broker)  —  待接入                                │
│  └─ xtp / ptrade / qmt  (TBD)                            │
└─────────────────────────────────────────────────────────┘
```

### 数据流（CSV 优先）

```
┌──────────────────────────────────────────────────────────┐
│  数据源 (Web UI 一键更新)                                │
│  ├─ HTML 按钮 "更新数据" → POST /api/data/download       │
│  └─ downloader.py → 增量下载 (有则增, 无则全量)            │
└────────────────────┬─────────────────────────────────────┘
                     ▼
┌──────────────────────────────────────────────────────────┐
│  CSV 存储 (源数据, Git 提交)                              │
│  market_data/                                            │
│  ├─ raw/kline_daily/kline_daily_2023.csv (~72MB, LFS)    │
│  ├─ raw/kline_daily/kline_daily_2024.csv (~90MB, LFS)    │
│  ├─ raw/kline_daily/kline_daily_2025.csv (~93MB, LFS)    │
│  ├─ raw/kline_daily/kline_daily_2026.csv (~42MB, LFS)    │
│  ├─ raw/technical_indicators/tech_indicators_*.csv (LFS)│
│  ├─ fund_flow_120d.csv                                   │
│  ├─ holder_num.csv                                       │
│  └─ reference/announcements.csv, news.csv, ...           │
└────────────────────┬─────────────────────────────────────┘
                     ▼
┌──────────────────────────────────────────────────────────┐
│  SQLite DB (派生, 本地生成, 不提交)                        │
│  database/quant.db                                       │
│  └─ scripts/build_db.py 从 CSV 重建 (或 run.py 启动检查) │
└────────────────────┬─────────────────────────────────────┘
                     ▼
┌──────────────────────────────────────────────────────────┐
│  查询 (portfolio_engine / scoring / web api)             │
│  └─ daily_price (4.18M 行, 5206 股票, 含 pct_change)     │
└──────────────────────────────────────────────────────────┘
```

**关键原则**：
- **CSV 是源**，可提交 (Git LFS 处理大文件)
- **DB 是派生**，本地生成，已 gitignore (`database/*.db*`)
- **增量更新**：先查 DB 最大日期，只下载新数据
- **全量兜底**：DB 为空 / CSV 缺失时全量下载

---

## 四、策略命名对照表（重点）

| 现代码标识 | 业务名 | 描述 | 状态 |
|------------|--------|------|------|
| `v6_reversal_selection` | **V6超卖反转** | RSI14≤38 + BB≤0.10 + 60日DD≤-8% + 趋势确认 + 反转放量 | 🟢 主推 |
| `v6_pipeline_hybrid` | **V6多维融合** | V6 信号 + fund_flow + chip 多维评分融合 | 🟢 主推 |
| XGBoost v4 (`train_xgb_v4.py`) | **V龙头主升** | 8 维评分 + 技术指标 + 滞后/动量特征，ML 月度预测（ret_60d > 10%） | 🟢 主推 |
| `bull_8d_monthly_fixed` | V龙头-月预测-LR | 8 维评分 + 逻辑回归月预测（V龙头 v1 原型） | 📦 归档（已并入 XGBoost v4） |
| `v7_bull_wave` (stock_screener) | V7牛股波段 | 七维共振识别主升浪（V龙头 v0 实验） | 📦 归档（已并入 V龙头） |
| `v5_hybrid` | V5超卖+趋势 | V3 超卖 + V2 趋势双重验证 | 📦 归档 |
| `v6_improved` (stock_screener) | V6改进-老版 | 连续阳线 + 量价过滤（V6 新架构前身） | 📦 归档 |
| `v3_reversal` | V3超卖-原版 | 早期超卖反转（V5/V6 前身） | 📦 归档 |
| `v2_trend` | V2趋势回调 | 趋势确认 + 浅回调上车 | 📦 归档 |
| `v4_multifactor` | V4多因子 | 板块 + 多因子加权 | 📦 归档 |
| `three_factor` | M1三因子均衡 | 小市值 + 反转 + 低波等权 | 🟡 后备 |
| `extreme_small_cap` | M2极致小市值 | 卫星仓位（200 选 50） | 🟡 后备 |
| `shield_spear` | M3盾矛全天候 | 温度计动态调节（盾30/矛70 ↔ 盾70/矛30） | 🟡 后备 |
| `technical_voting` | M4技术投票 | 5 策略投票委员会（±2 阈值） | 🟡 后备 |

**约定**：
- 🟢 主推：实盘主策略，长期维护
- 🟡 后备：观察中，参数冻结，不主动优化
- 📦 归档：仅历史参考，冻结代码

---

## 五、当前状态盘点（基于 develop 分支）

### ✅ 已完成（无需再做）

| 工作 | 提交 | 备注 |
|------|------|------|
| Issue #73 操盘体系 | `50fe7a0` | trading/ + simulator.py + astock_strategy.py + 6 个 API + /simulate 页面 |
| PR3 工程重构 | `9326885` | Engine 单例 + Scorer 注入 + API session 简化 |
| XGBoost v4 模型 | `c48fd8a` | AUC 0.7912，特征 78→74 重训练 |
| 流通股本 WeStock Data | `2f95331` | #64 |
| Playwright E2E | `a9426eb` | #72 CI 流水线 |
| 综合评分收口 | `8ebaa10` | combiner.py 统一 5 种公式 |
| 集中常量 | `02db5cb` | constants/ 模块（fees/market/risk/signal） |
| API 鉴权 | main→develop 沿途 | 所有 /api/* 端点 Bearer Token |

### 🟡 进行中（需要推进）

| 工作 | 状态 | 阻塞 |
|------|------|------|
| V龙头 主升策略实现 | XGBoost v4 已有原型，缺完整选股管线（样本构建 → 训练 → 预测 → 输出候选池） | OOS 验证 |
| V6 在新架构下的 OOS 验证 | TODO 写了"已关闭"但未看到 walk-forward 实现 | walk-forward 框架 |

### ❌ 待办（实盘前的硬骨头）

| 工作 | 说明 | 优先级 |
|------|------|--------|
| Walk-Forward OOS 验证框架 | TODO T5.2 实际上没实现 | 🔴 P0 |
| scripts/ 60+ 脚本清理 | 大量重复（bull_*/debug_*/test_*），建议分桶归档 | 🟡 P1 |
| stock_screener/ 整合 | v2-v7 都是历史原型，决定保留/合并/删除 | 🟡 P1 |
| README 同步到 v2.0 | README 还停留在 v1.0 描述 | 🟡 P1 |
| Broker 接入 | xtp / ptrade / qmt（TBD） | 🔴 P0 |
| 监控告警 | 净值偏离 / 信号-订单对账 / 异常告警（钉钉/微信） | 🔴 P0 |
| 灾备 | 双 ISP + 备用电源 + 手机端监控 | 🔴 P0 |
| 模拟盘 1-2 个月 | 阶段 3 必备 | 🔴 P0 |
| 行业暴露控制 | 单行业 ≤ 30% 约束（TODO T3.2） | 🟢 P2 |

---

## 六、实盘化 5 阶段路径

### 阶段 0 — 项目整合  ⬅️ 当前

**目标**：把所有工作收口到一条主线，命名规范化，状态清晰。

**任务**：
- [x] 写本 ROADMAP（替代 TODO.md 作为权威任务清单）
- [ ] 命名规范化代码改动（见第七节）
- [ ] README 同步到 v2.0
- [ ] scripts/ 分桶：保留 / 归档 / 删除（产出 `_deprecated/` 目录）

**门禁**：本文档 commit + 命名 alias 跑通 + 测试通过

### 阶段 1 — OOS 验证（实盘门票）

**目标**：证明 V6 / V龙头 在样本外依然有效。

**任务**：
- [ ] 实现 walk-forward 框架（`scripts/walk_forward.py`）
  - 36 个月训练 → 12 个月测试 → 滚动
- [ ] V6 在 2024-01 ~ 2026-06 上做 OOS 回测
- [ ] V龙头（XGBoost v4）在 2024-01 ~ 2026-06 上做 OOS 验证
- [ ] 参数敏感性扫描（±20% 微调）
- [ ] 输出报告：`docs/oos_validation_report.md`

**门禁**：
- OOS 夏普稳定（mean > 0.5，std < 0.3）
- 参数微调 ±20% 后 OOS 夏普退化 < 30%
- 最大回撤 < 25%（在 95% 滚动窗口上）

### 阶段 2 — 操盘层验证

**目标**：证明 4 种仓位算法 + 5 种止损在 V6/V龙头 信号下都按预期工作。

**任务**：
- [ ] 单元测试覆盖 trading/config.py / position_sizer.py / stop_loss.py
- [ ] 集成测试：信号 → 仓位 → 止损 → T+1 全链路
- [ ] A股规则引擎边界测试（涨跌停/T+1/100 股整倍）

**门禁**：测试覆盖率 > 90%，所有边界场景通过

### 阶段 3 — 模拟盘

**目标**：用 simulator.py 在历史区间跑 1-2 个月（滚动），与回测结果对比。

**任务**：
- [ ] 接 V6 + V龙头 信号到 simulator.py（已有 SignalAdapter）
- [ ] 跑 2024-01 ~ 2026-06 全区间模拟
- [ ] 对比模拟盘 vs portfolio_engine 回测结果（差异应 < 10%）
- [ ] 评估滑点模型是否合理（5bps 是否够）
- [ ] 输出：`docs/simulate_vs_backtest_diff.md`

**门禁**：
- 模拟盘夏普 ≥ 回测夏普 × 0.8（差异容忍度）
- 最大回撤差异 < 5%

### 阶段 4 — Broker 接入 + 监控

**目标**：从模拟盘到真实下单 + 实时监控。

**任务**：
- [ ] 选定券商接口（xtp / ptrade / qmt，根据券商支持）
- [ ] 实现 broker 适配器（继承统一接口）
- [ ] 实时信号 → 下单链路
- [ ] 监控告警（净值偏离 / 异常 / 断网重连）
- [ ] 灾备：双 ISP + 备用电源 + 手机端监控

**门禁**：
- 模拟环境下单到成交 < 500ms
- 监控告警 < 30s 响应
- 双链路故障切换 < 5min

### 阶段 5 — 小资金实盘

**目标**：5–10w 启动，1-2 个月观察期，逐步放量到 50-100w。

**里程碑**：
| 时间 | 资金 | 触发条件 |
|------|------|----------|
| Day 1 | 5w | 启动 |
| Week 4 | 5w → 8w | 月度净值 +X% |
| Week 8 | 8w → 20w | 累计收益 > 0 且回撤 < 15% |
| Week 12 | 20w → 50w | 同上 + 模拟盘与实盘差异 < 5% |
| Week 16 | 50w → 100w | 同上 + 持续 4 周 |

**止损线**（任一触发立即撤出至模拟盘）：
- 单周回撤 > 10%
- 单月回撤 > 15%
- 任何交易日滑点异常 > 30bps

---

## 七、命名规范化代码改动

为方便后续维护和团队理解，建议以下 alias / 重命名：

### 7.1 业务名 → 类 alias

**`src/strategies/v6_reversal_selection.py`**：
```python
# 当前: class V6ReversalSelectionStrategy
# 建议: 增加中文业务名 + 旧名兼容
class V6ReversalStrategy:  # 业务名（V6 超卖反转）
    """V6超卖反转 — RSI14≤38 + BB≤0.10 + 60日DD≤-8% + 趋势确认 + 反转放量"""
    name = "V6超卖反转"
    # alias 兼容
    V6ReversalSelectionStrategy = V6ReversalStrategy  # backward compat
```

**`src/strategies/v6_pipeline_hybrid.py`**：
```python
class V6MultiDimStrategy:  # 业务名（V6 多维融合）
    """V6超卖信号 × fund_flow × chip 多维评分融合精选"""
    name = "V6多维融合"
    V6PipelineHybridStrategy = V6MultiDimStrategy  # backward compat
```

### 7.2 V龙头 — 新建模块

新文件：`src/strategies/v_leader_main_surge.py`

```python
"""
V龙头 主升浪 — XGBoost v4 月度预测
================================
基于近 2 年强势票（涨幅/连板/题材）样本，每日日 K 提取多维特征，
训练 ML 模型预测 60 日大涨概率，输出月度高胜率候选池。
"""
from ..scoring import ScorerRegistry
from ..scoring.combiner import combine


class VLeaderMainSurgeStrategy(BaseSelectionStrategy):
    """V龙头主升 — 月度高胜率候选池"""
    name = "V龙头主升"
    n_stocks = 30
    rebalance_days = 20  # 月度调仓
    lookback_days = 60

    # ML 模型 + 评分器（8 维）+ 技术指标 = 多维因子
    ML_MODEL_PATH = "models/xgb_v4_2026_06.pkl"
    FEATURE_COLS = ["tech_score", "fund_score", "flow_score", ...]
    ...
```

### 7.3 config/strategies.yaml 更新

```yaml
strategies:
  # 主推层
  - name: "V6超卖反转"
    class_path: "src.strategies.v6_reversal_selection.V6ReversalStrategy"
    ...
  - name: "V6多维融合"
    class_path: "src.strategies.v6_pipeline_hybrid.V6MultiDimStrategy"
    ...
  - name: "V龙头主升"
    class_path: "src.strategies.v_leader_main_surge.VLeaderMainSurgeStrategy"
    ...
```

### 7.4 scripts/ 清理方案

```
scripts/
├── active/                   # 当前使用
│   ├── batch_backtest.py
│   ├── param_grid_search.py
│   ├── train_xgb_v4.py
│   ├── import_*.py
│   └── ...
├── archive/                  # 归档（仅参考，不在主流程）
│   ├── bull_*.py            # V龙头 v0/v1 原型
│   ├── debug_*.py           # 一次性 debug
│   └── test_*.py            # 一次性手动测试
└── _deprecated/             # 废弃（30 天后删除）
    └── ...
```

---

## 八、关键文档清单

| 文档 | 位置 | 状态 |
|------|------|------|
| 本 ROADMAP | `LIVE_TRADING_ROADMAP.md` | 🆕 本文件 |
| #73 设计文档 | `docs/issue-73-simulate-design.md` | ✅ 已有 |
| v2.0 Roadmap | `docs/v2.0_Roadmap.md` | ✅ 已有 |
| 项目总览 | `docs/项目总览.md` | ✅ 已有 |
| OOS 验证报告 | `docs/oos_validation_report.md` | ❌ 阶段 1 产出 |
| 模拟盘差异报告 | `docs/simulate_vs_backtest_diff.md` | ❌ 阶段 3 产出 |
| 实盘运行手册 | `docs/live_trading_runbook.md` | ❌ 阶段 4 产出 |

---

## 九、风险清单

| 风险 | 等级 | 缓解 |
|------|------|------|
| in-sample 过拟合 | 🔴 高 | OOS 验证（阶段 1 强制门禁）|
| 滑点低估 | 🟡 中 | 模拟盘对比（阶段 3），实盘 5w 启动观察 |
| 数据延迟/缺失 | 🟡 中 | 监控告警，备用数据源（WeStock 兜底） |
| 系统断电/断网 | 🔴 高 | 双 ISP + UPS + 手机端监控 |
| 心理情绪（实盘撤出太晚）| 🟡 中 | 严格执行阶段 5 止损线，写入 runbook |
| 策略同质化（V6 + V龙头相关性高）| 🟢 低 | V龙头还没合进来时先观察，未来若相关性 > 0.7 考虑替换一条 |

---

## 十、立即行动清单（本周内）

| # | 任务 | 估时 | 阻塞 |
|---|------|------|------|
| 1 | 提交本 ROADMAP 到 develop | 5min | - |
| 2 | 命名 alias 改动（V6 两个类 + V6 业务名） | 1h | - |
| 3 | 新建 V龙头 选股策略骨架 `v_leader_main_surge.py` | 2h | XGBoost v4 模型路径确认 |
| 4 | 更新 `config/strategies.yaml` 业务名 | 30min | #2 |
| 5 | scripts/ 分桶（保留/归档/废弃） | 2h | - |
| 6 | 同步 README 到 v2.0（双层架构）| 1h | #2 |
| 7 | 实现 walk-forward 骨架 `scripts/walk_forward.py` | 3h | 阶段 1 启动 |

预计本周末完成 #1-#6，进入阶段 1。

---

*最后更新：2026-06-22 — 切到 develop 分支后基于真实代码状态盘点*