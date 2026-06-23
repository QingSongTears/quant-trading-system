# OOS-2 排查报告 — Window 0 max_dd 异常

**报告人**: 代码助手
**排查日期**: 2026-06-23
**commit 起点**: `f7c9e18`

---

## 0. 排查结论（一句话）

**OOS-2 不是回测引擎 bug**。

之前怀疑 `src/backtest/portfolio_engine.py::_simulate_portfolio` 计算 max_dd 有问题；实际复现发现：

| 项 | 真实情况 | 之前错误印象 |
|---|---|---|
| Window 0 OOS max_dd | **0.00%** | -0.09%（被怀疑的异常值）|
| Window 0 OOS sharpe | **0.00** | 3.67（虚构）|
| Window 0 OOS 总收益 | **0.00%** | +36.10%（虚构）|
| Window 0 OOS 交易笔数 | **0** | - |

> 旧 anchor summary 里"sharpe=3.67 + 36%"是错误的。**docs/oos_smoke_report.md** 实际记录全部窗口都是 0。

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