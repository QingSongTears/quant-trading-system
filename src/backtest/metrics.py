"""
backtest 指标薄封装 — ADR-0009
================================

单点封装 `src/metrics/performance.py` 的金融指标,
提供 `compute_metrics()` 一站式入口供 BacktestEngine / PortfolioBacktestEngine 共用。

设计动机:
- engine.py 与 portfolio_engine.py 原本都 `from ..metrics import sharpe_ratio, ...` (重复 import)
- 现在抽到 `backtest/metrics.py`, engine / portfolio_engine 都从这里调用
- 不重复实现, 指标签名变化只需改 src/metrics/performance.py
"""
from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

# 复用全项目统一指标实现 (PR2.2 收敛)
from ..metrics import (
    sharpe_ratio as _sharpe_ratio,
    max_drawdown as _max_drawdown,
    volatility as _volatility,
    annual_return as _annual_return,
    calmar_ratio as _calmar_ratio,
    profit_factor as _profit_factor,
    win_rate as _win_rate,
)

# 与 src/metrics 同名, 但本模块专注于"回测语义"的包装 (单位转换、字段名映射)
__all__ = [
    "compute_metrics",
    "calc_sharpe_from_equity",
    "calc_volatility_from_equity",
    "calc_max_drawdown_pct",
    "calc_annual_return_pct",
    "calc_calmar_ratio",
    "calc_win_rate",
    "calc_profit_factor",
    "calc_benchmark_return",
]


def calc_sharpe_from_equity(
    equity_series: pd.Series,
    risk_free: float = 0.02,
) -> float:
    """从权益曲线计算夏普比率(基于日收益率)

    Args:
        equity_series: 权益曲线 (pd.Series)
        risk_free: 年化无风险利率 (默认 0.02)

    Returns:
        float, 无单位。数据不足时返回 0
    """
    if equity_series is None or len(equity_series) < 2:
        return 0.0
    daily_returns = equity_series.pct_change().dropna()
    if len(daily_returns) == 0:
        return 0.0
    return float(_sharpe_ratio(daily_returns.values, risk_free=risk_free))


def calc_volatility_from_equity(equity_series: pd.Series) -> float:
    """从权益曲线计算年化波动率(%)"""
    if equity_series is None or len(equity_series) < 2:
        return 0.0
    daily_returns = equity_series.pct_change().dropna()
    if len(daily_returns) == 0:
        return 0.0
    return float(_volatility(daily_returns.values))


def calc_max_drawdown_pct(equity: np.ndarray | pd.Series) -> float:
    """最大回撤(%)

    src/metrics.max_drawdown 返回 ratio (负数, 如 -0.25)
    本函数 ×100 转百分比, 与 BacktestReport.max_drawdown 字段语义一致

    Returns:
        float, %。如 -25.0 表示回撤 25%
    """
    if equity is None or len(equity) < 2:
        return 0.0
    raw = float(_max_drawdown(equity))
    return raw * 100.0


def calc_annual_return_pct(total_return_pct: float, trading_days: int) -> float:
    """年化收益率(%) — 由总收益率与交易日数推算

    Args:
        total_return_pct: 总收益率(%, 如 20.0 表示 20%)
        trading_days: 交易日数
    """
    if trading_days <= 0:
        return 0.0
    return float(_annual_return(total_return_pct / 100.0, trading_days))


def calc_calmar_ratio(annual_ret_pct: float, max_dd_pct: float) -> float:
    """卡玛比率 = 年化收益(%) / |最大回撤|

    Args:
        annual_ret_pct: 年化收益率(%, 如 15.0)
        max_dd_pct: 最大回撤(%, 负数, 如 -25.0;本函数取绝对值)
    """
    if max_dd_pct == 0:
        return 0.0
    return float(_calmar_ratio(annual_ret_pct, max_dd_pct))


def calc_win_rate(daily_returns: np.ndarray | pd.Series) -> float:
    """胜率(%)"""
    return float(_win_rate(np.asarray(daily_returns)))


def calc_profit_factor(daily_returns: np.ndarray | pd.Series) -> float:
    """盈亏比"""
    val = float(_profit_factor(np.asarray(daily_returns)))
    return val if val else 0.0


def calc_benchmark_return(
    bench_close: pd.Series | None,
    start: date,
    end: date,
) -> float:
    """计算基准指数同期收益率(%)

    Args:
        bench_close: 基准收盘价序列(按交易日升序)
        start, end: 回测起止日期

    Returns:
        float, %。如 5.0 表示基准涨 5%。空数据返回 0
    """
    if bench_close is None or len(bench_close) < 2:
        return 0.0
    try:
        return float((bench_close.iloc[-1] / bench_close.iloc[0] - 1) * 100)
    except Exception:
        return 0.0


def compute_metrics(
    equity_series: pd.Series,
    initial_capital: float,
    trading_days: int,
    risk_free: float = 0.02,
) -> dict[str, float]:
    """一站式计算回测核心指标 — 供 BacktestReport 构造时使用

    Args:
        equity_series: 权益曲线 (pd.Series, 索引为日期)
        initial_capital: 初始资金
        trading_days: 交易日数
        risk_free: 年化无风险利率

    Returns:
        dict 含 total_return / annual_return / sharpe_ratio / max_drawdown /
              annual_volatility / calmar_ratio / win_rate / profit_factor /
              final_equity / total_trades(=0, 由调用方补)
    """
    if equity_series is None or len(equity_series) < 1:
        return _empty_metrics()

    final_equity = float(equity_series.iloc[-1])
    total_return = (final_equity / initial_capital - 1) * 100 if initial_capital > 0 else 0.0
    annual_return = calc_annual_return_pct(total_return, trading_days)

    sharpe = calc_sharpe_from_equity(equity_series, risk_free=risk_free)
    max_dd = calc_max_drawdown_pct(equity_series.values)
    annual_vol = calc_volatility_from_equity(equity_series)

    daily_returns = equity_series.pct_change().dropna()
    win = calc_win_rate(daily_returns.values) if len(daily_returns) > 0 else 0.0
    pf = calc_profit_factor(daily_returns.values) if len(daily_returns) > 0 else 0.0

    calmar = calc_calmar_ratio(annual_return, max_dd)

    return {
        "final_equity": round(final_equity, 2),
        "total_return": round(total_return, 2),
        "annual_return": round(annual_return, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown": round(max_dd, 2),
        "annual_volatility": round(annual_vol, 2),
        "calmar_ratio": round(calmar, 2),
        "win_rate": round(win, 2),
        "profit_factor": round(pf, 2),
    }


def _empty_metrics() -> dict[str, float]:
    return {
        "final_equity": 0.0,
        "total_return": 0.0,
        "annual_return": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown": 0.0,
        "annual_volatility": 0.0,
        "calmar_ratio": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
    }