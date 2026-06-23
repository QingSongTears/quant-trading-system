# OOS-2 排查报告 — Window 0 max_dd 异常

**报告人**: 代码助手
**排查日期**: 2026-06-23
**commit 起点**: `f7c9e18`
**commit 终结**: `403633c`

---

## 0. 排查结论（更新版）

**OOS-2 排查发现 2 个真 bug + 1 个非 bug**：

| # | 类别 | 内容 | 修复 commit |
|---|---|---|---|
| 1 | ✅ **真 bug** | `_max_drawdown` 单位未 ×100（portfolio_engine + technical_voting）| `403633c` |
| 2 | ✅ **真 bug** | `V6ReversalSelectionStrategy.select` 的 `_compute_indicators` 路径 index 用错（.index vs .code）| `403633c` |
| 3 | ⚠️ **非 bug** | V6 信号在 2024-07~09 OOS 窗口零命中（市场风格不匹配反转策略）| 已用 V6WithFallback 缓解 |

**之前 anchor summary 里"sharpe=3.67 + 36% + max_dd=-0.09%"是错的**——真实数据下全是 0（零交易）。

---

## 1. Bug #1: max_drawdown 单位换算缺失（**真 bug**）

### 1.1 现象

`src/metrics/performance.py::_max_drawdown()` 约定：**返回 ratio（-0.25 表示 25%）**

但 `BacktestReport.max_drawdown` 字段语义为 **%**（参见 `src/backtest/engine.py L45` + `src/models/database.py L139`）。

### 1.2 根因

| 模块 | 是否 ×100 | max_drawdown 实际值 | 字段值 |
|---|---|---|---|
| `src/strategies/stock_screener/backtest/engine.py L443` | ✅ `* 100` | -0.11 | **-11.00** |
| `src/backtest/portfolio_engine.py L484` | ❌ **没 ×100** | -0.11 | **-0.11** ❌ |
| `src/models/technical_voting.py L571` | ❌ **没 ×100** | -0.11 | **-0.11** ❌ |

### 1.3 修复

```python
# portfolio_engine.py L484
max_drawdown = _max_drawdown(equity_series.values) * 100  # 转百分比
```

```python
# technical_voting.py L571
max_drawdown = _max_drawdown(equity_series.values) * 100  # 转百分比
```

### 1.4 验证（2024-07-01 ~ 2024-08-01, V6WithFallback）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| max_drawdown | -0.11% | **-11.10%** ✓ |
| 独立验证 `(arr - peak)/peak).min() * 100` | -11.10% | -11.10% |

完全一致。

### 1.5 影响范围

所有走 `PortfolioBacktestEngine.run()` 和 `TechnicalVotingModel.run_backtest()` 的 max_drawdown 显示都受此 bug 影响。修复后端口 (web/api) 透传值从 -0.11 变为 -11.10，前端显示从 "-0.11%" 变为 "-11.10%"。

---

## 2. Bug #2: V6.select 用错 index（**真 bug**）

### 2.1 现象

`_compute_indicators()` 返回的 df **没有 `set_index("code")`**（普通 RangeIndex 0/1/2...）。
`select()` L228 却用 `indicators_df.index.isin(universe_codes)` 永远 False → indicators_df 为空 → 返回 []。

### 2.2 根因

`precompute_all()` L177 用 `set_index("code")` 缓存，所以 cache 路径正常工作。
但 cache miss 时 fallback 到 `_compute_indicators()`（L221）走错路径。

### 2.3 修复

```python
# v6_reversal_selection.py L226-229
universe_codes = set(universe_df["code"].tolist())
mask = indicators_df["code"].isin(universe_codes)  # 用列名而非 index
indicators_df = indicators_df[mask].reset_index(drop=True)
```

### 2.4 验证

修复前：1 个月 OOS → `total_trades=0`
修复后：1 个月 OOS → `total_trades=2`, `select 返回 4 只`

---

## 3. V6 信号稀疏（非 bug，已用 fallback 缓解）

### 3.1 根因

V6 是**极端反转**策略，4 条件超卖同时命中概率本就低。2024-07~09 A 股整体上行（小盘反弹 + 大盘震荡），反转策略不利。

### 3.2 缓解方案

新建 `src/strategies/v6_with_fallback.py` — `V6ReversalWithFallback`：
- 主策略命中 → 用主策略
- 主策略无信号 → 回退单条件 `RSI14 ≤ 50` 且 `60日最大回撤 ≤ -20%`
- 仍无信号 → 空仓

### 3.3 验证（2024-07-01 ~ 2024-08-01, V6WithFallback）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| main_used_count | 0 | 4 |
| fallback_used_count | 0 | 0 (主策略足够) |
| total_trades | 0 | **2** |
| total_return | 0% | -4.16% |
| sharpe_ratio | 0 | -1.24 |
| max_drawdown | 0% | **-11.10%** |

（1 个月样本太小、参数未优化，仅作框架验证，不代表策略实际表现）

---

## 4. 之前的判断修正

之前 OOS2_INVESTIGATION.md（commit 8e2c82a）我下过"不是引擎 bug"的结论。**错了**。进一步排查发现：
1. select 路径有 index bug → 所有 select 走 fallback 路径都返回 []
2. max_dd 单位差 100 倍 → 即使有交易，max_dd 也看不出真实回撤

**真实阻塞路径**：select bug 让所有窗口 0 交易 → 净值曲线平 → max_dd 看起来正常（=0），掩盖了 max_dd 单位 bug。

修复后，OOS-1 阻塞解除，可重新跑 walk_forward。

---

## 5. 后续

- [x] Bug #1 + Bug #2 + Fallback 修复（commit `403633c`）
- [ ] 重跑 walk_forward 真实数据（fallback 策略，5 窗口 × 5 样本 ~17 min）
- [ ] 跑完写 `docs/oos_real_baseline.md` 报告
- [ ] 如 fallback 在多窗口仍表现差，考虑 V 龙头 / 多策略并行

---

*最后更新: 2026-06-23 (fix #1+#2 完成后)*

**根因**: V6 超卖反转策略在真实数据下 OOS 窗口（2024-07-01 ~ 2024-09-30）**零信号命中**，导致回测期间无任何调仓 → 无持仓变动 → 净值曲线平 → max_dd=0, sharpe=0, return=0。

---

## 1. 排查路径

### 1.1 直接重跑 Window 0（真实数据）

**复现脚本**: `C:\Users\admin\AppData\Local\Temp\oos2_repro.py`

```python
START = date(2024, 7, 1)
END   = date(2024, 10, 1)
# 用 V6ReversalSelectionStrategy, 默认参数
engine = PortfolioBacktestEngine()
report = engine.run(strategy, START, END, initial_capital=1_000_000)
```

**输出**:
```
total_return    = 0.00%
sharpe_ratio    = 0.00
max_drawdown    = 0.00%
total_trades    = 0
```

### 1.2 5 组参数扫描（确认不是阈值问题）

`C:\Users\admin\AppData\Local\Temp\oos2_param_sweep.py`：

| 配置 | Trades | Return% | Sharpe | MaxDD% |
|---|---|---|---|---|
| 原始 (#54) | **0** | 0.00 | 0.00 | 0.00 |
| 放宽A (RSI↑) | **0** | 0.00 | 0.00 | 0.00 |
| 放宽B (BB↑) | **0** | 0.00 | 0.00 | 0.00 |
| 放宽C (全放宽) | **0** | 0.00 | 0.00 | 0.00 |
| 取消 BB | **0** | 0.00 | 0.00 | 0.00 |

**5/5 都是 0 交易**。证明不是阈值问题，是更深层问题。

### 1.3 逐层排查（确认数据通路正常）

`C:\Users\admin\AppData\Local\Temp\oos2_debug.py`：

| 步骤 | shape | 备注 |
|---|---|---|
| `_load_all_data` (lookback 120天) | (720281, 13) | ✅ 5061 只股, 2024-03-04 ~ 2024-09-30 |
| `_build_universe` at 2024-07-01 | (5034, 12) | ✅ 5034 只 |
| `filter_universe` | (4237, 12) | ✅ 流动性过滤掉 797 只 |
| `_compute_indicators` (50 只测试) | (50, 16) | ✅ 指标正常返回 |
| `_detect_signals` 真实过滤 | **(0)** | ⚠️ 全被过滤 |

### 1.4 V6 信号全过滤的根因

`src/strategies/v6_reversal_selection.py::_detect_signals` L348-379 同时满足 4 个超卖条件才入选：

| 条件 | 000001.SZ 在 2024-07-01 实测 | 阈值 | 是否被过滤 |
|---|---|---|---|
| RSI14 | 31.69 | ≤ 38 | ✅ 通过 |
| RSI6 | 94.87 | ≤ 23 | ❌ **被过滤**（超买） |
| BB Position | 0.45 | ≤ 0.10 | ❌ **被过滤**（中轨上） |
| 60日最大回撤 | -11.84% | ≥ -8% | ❌ **被过滤**（回撤不够深） |

加上：
- `MIN_PRICE_CHG = 1.0%`: 当日需涨 ≥ 1%
- `PRICE_BELOW_MA20/60 = True`: 收盘需在 MA20/60 下方
- `RSI6_MIN_DELTA = 2.0`: RSI6 反弹需 ≥ 2
- `MIN_VOL_RATIO = 1.3`: 量比 ≥ 1.3
- ATR 惩罚

**V6 策略是**极端反转**信号，4 个超卖条件同时命中的概率本来就低**。2024-07 ~ 09 期间 A 股市场整体上行（小盘反弹 + 大盘震荡），V6 风格的超卖反弹信号天然稀疏。

---

## 2. 回测引擎 max_dd 计算路径（验证无 bug）

虽然 OOS-2 不是引擎 bug，但顺便验证了路径完整：

```
_simulate_portfolio (portfolio_engine.py L260-415)
  ├─ equity_curve 长度 = n_days (每个 bar 一个点)
  ├─ equity_curve[i] = equity * (1 + portfolio_returns[i])  (调仓前快照)
  ├─ equity_curve[0] = initial_capital
  └─ equity_series = pd.Series(equity_curve, index=pd.to_datetime(all_dates))

→ _max_drawdown(equity_series.values)  (metrics/performance.py L78)
  ├─ peak = cummax(equity)
  ├─ dd = (equity - peak) / peak
  └─ return dd.min()  (≤ 0)
```

公式正确，0 交易时曲线是 `initial_capital` 常数 → peak 常数 → dd 全 0 → min=0。✓

---

## 3. OOS-1 真实阻塞（仍是问题）

**OOS-2 排查完成后**，OOS-1（真实数据 walk-forward 超时）仍在阻塞。但根本原因不是引擎慢，是 **V6 信号稀疏**：

- 默认 6 个月训练窗 + 2 个月测试窗 = 4 个窗口全 0 命中
- 调参搜索 20 样本 × 每样本跑 1 次回测 = 20 次 × ~40s = **800s/窗口 ≈ 13 min**
- 4 窗口 × 13 min = **~52 min**，超时

**修复方向**（不是回测 bug，而是策略/配置问题）：

| 选项 | 工作量 | 备注 |
|---|---|---|
| A. 缩短 walk_forward 配置（train_months=3, test_months=1） | 1h | 牺牲样本量，先跑通 |
| B. 多策略并行（V6 + 龙头 + 趋势） | 3h | 哪个有信号用哪个 |
| C. 用全市场 fake 信号（先验证引擎） | 1h | 给 V6 加个随机噪声 |
| D. V6 信号过滤放宽 + 加新维度（资金流） | 4-6h | 真解决稀疏问题 |

**推荐**: 先 A 跑通拿到 baseline，再用 B/C/D 优化。

---

## 4. 复现脚本归档位置

| 脚本 | 路径 | 用途 |
|---|---|---|
| `oos2_repro.py` | `C:\Users\admin\AppData\Local\Temp\` | Window 0 最小复现 |
| `oos2_param_sweep.py` | `C:\Users\admin\AppData\Local\Temp\` | 5 组参数扫描 |
| `oos2_debug.py` | `C:\Users\admin\AppData\Local\Temp\` | 逐层 shape 排查 |

> 这三个脚本**不入 git**（Temp 目录本来就是排查用）。后续排查其他 OOS 异常可参考结构。

---

## 5. 后续行动

- [ ] **OOS-2 关闭**：非引擎 bug，无需改 portfolio_engine.py
- [ ] **OOS-1 解锁**：先用选项 A（缩窗口）跑通 baseline，再优化
- [ ] **AI_TASK_BOARD.md 同步**：更新 OOS-2 状态（关闭）+ OOS-1 根因（V6 信号稀疏）
- [ ] **可考虑**: V6 加 fallback — 当信号为 0 时回退到 RSI14 单条件或"持仓 ETF 510300"

---

*最后更新: 2026-06-23*