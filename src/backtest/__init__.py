"""
回测模块 — ADR-0009 精简版
==========================

公开 API 转发(向后兼容):
- BacktestEngine         (from .engine)
- PortfolioBacktestEngine (from .portfolio_engine)
- BacktestReport         (from .report)

模块结构 (ADR-0009 D1-B 水平拆分):
- data_loader.py     DataRepository 包装 + K 线 + 因子 + LRU 缓存
- metrics.py         sharpe / max_drawdown / win_rate 等指标薄封装
- runner.py          单股回测循环 (backtesting.py 第三方边界隔离)
- portfolio_runner.py 组合回测循环 (纯 Pandas/NumPy)
- optimizer.py       网格 / 随机 / 贝叶斯参数搜索
- engine.py          BacktestEngine 薄封装 (~150 行)
- portfolio_engine.py PortfolioBacktestEngine 薄封装 (~150 行)
- report.py          BacktestReport dataclass + ECharts 转换
"""
from __future__ import annotations
from .engine import BacktestEngine, BacktestReport  # noqa: F401
from .portfolio_engine import PortfolioBacktestEngine  # noqa: F401

__all__ = [
    "BacktestEngine",
    "PortfolioBacktestEngine",
    "BacktestReport",
]