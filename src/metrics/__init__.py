"""
src.metrics — 金融指标单一实现
================================

本包提供全项目共享的金融指标计算函数,所有重复定义的位置必须从这里 import 而非重新实现。

约定 (PR2.2 起生效):
- max_drawdown 永远返回**负数或0**(e.g. -0.25 表示回撤 25%)
  ⚠️ 历史口径: src/backtest/engine.py:217 把 backtesting.py 的负数 * -1 转正数 → 现统一为负数
- sharpe_ratio 默认扣减无风险利率 0.02,年化因子 250 (A 股惯例)
  ⚠️ 历史口径混乱: sqrt(250) vs sqrt(252)、risk_free 0.02 vs 0.025 vs 未扣减 → 现统一
- annual_return / volatility 统一年化因子 250

模块:
- performance: sharpe_ratio, max_drawdown, annual_return, volatility, calmar_ratio
"""
from __future__ import annotations
from .performance import (
    sharpe_ratio,
    max_drawdown,
    annual_return,
    volatility,
    calmar_ratio,
    profit_factor,
    win_rate,
)

__all__ = [
    "sharpe_ratio",
    "max_drawdown",
    "annual_return",
    "volatility",
    "calmar_ratio",
    "profit_factor",
    "win_rate",
]