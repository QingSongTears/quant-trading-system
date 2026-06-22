# ⚠️ LEGACY — Stock Screener 子系统

> **状态**: 归档保留，不再积极维护
> **新策略请写到 `src/strategies/` 主系统**（使用 `BaseSelectionStrategy` 基类）
> **新数据加载请用 `src/models/repository.py::DataRepository`**

## 为什么归档

`stock_screener/` 是项目早期的独立子系统（v2 之前），有自己的：

- `core/data_loader.py` — 独立 CSV 读取
- `backtest/engine.py` — 600+ 行独立回测引擎
- `core/strategy.py` — 独立策略基类
- `strategies/v2-v7.py` — V2-V7 实验策略

主系统已经全部实现相同功能：

| 功能 | 主系统位置 |
|------|------------|
| 数据访问 | `src/models/repository.py::DataRepository` |
| 选股回测 | `src/backtest/portfolio_engine.py::PortfolioBacktestEngine` |
| 信号回测 | `src/backtest/engine.py::BacktestEngine` |
| 选股策略基类 | `src/backtest/base_selection_strategy.py::BaseSelectionStrategy` |
| 信号策略基类 | `src/backtest/base_strategy.py::BaseStrategy` |
| 多维评分 | `src/scoring/` (8 个 Scorer) |
| 操盘层 | `src/strategies/trading/` (Issue #73) |

## 当前主推策略（主系统）

- **V6超卖反转** (`v6_reversal_selection.py`) — 替代 v3, v6_improved
- **V6多维融合** (`v6_pipeline_hybrid.py`) — 替代 v7_bull_wave
- **V龙头主升** (`v_leader_main_surge.py` 骨架) — 替代 bull_8d_*

详见 [`LIVE_TRADING_ROADMAP.md`](../../LIVE_TRADING_ROADMAP.md) 第四节「策略命名对照表」。

## 何时删除

满足以下**全部**条件后可考虑删除整个目录：
1. 所有 stock_screener 内部脚本（`research/*.py`, `optimization_round*.py`）确认无外部引用
2. tests 中所有 `from src.strategies.stock_screener` import 改用主系统
3. scripts/param_server.py 中 stock_screener 引用迁移到主系统
4. 至少 30 天无新代码合并到 stock_screener/

---

**最后更新**: 2026-06-22 — 标记为 LEGACY（清理任务 E4）
