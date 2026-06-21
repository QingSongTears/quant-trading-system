"""
金融指标单一实现 — 全项目统一从这里 import
================================================

约定 (PR2.2 起生效):
- max_drawdown 永远返回负数或0
- sharpe_ratio 默认 risk_free=0.02, ann_factor=250

⚠️ 历史口径混乱 (审计发现,PR2.2 修复):
- sharpe 公式: 8+ 处副本,risk_free 在 0.02 / 0.025 / 未扣减 之间混乱
- 年化因子: sqrt(250) (A 股惯例) vs sqrt(252) (美股惯例) 混用
- max_drawdown 符号: 实现层 * -1 转正 vs 模型层保留负数 不一致
"""
from __future__ import annotations

import math
from typing import Union

import numpy as np
import pandas as pd

from ..constants.market import ANNUAL_TRADING_DAYS, RISK_FREE_RATE


ArrayLike = Union[np.ndarray, pd.Series, list, float]


def _to_numpy(x: ArrayLike) -> np.ndarray:
    """统一转 numpy 数组,空输入返回空数组"""
    if isinstance(x, pd.Series):
        return x.to_numpy()
    if isinstance(x, (list, tuple)):
        return np.asarray(x, dtype=float)
    if isinstance(x, np.ndarray):
        return x
    return np.asarray([x], dtype=float)


# ============================================================
# Sharpe Ratio
# ============================================================
def sharpe_ratio(
    returns: ArrayLike,
    risk_free: float = RISK_FREE_RATE,
    ann_factor: int = ANNUAL_TRADING_DAYS,
) -> float:
    """
    计算夏普比率

    公式: (mean(returns) * ann_factor - risk_free) / (std(returns) * sqrt(ann_factor))

    Args:
        returns: 日收益率序列(decimal, e.g. 0.01 表示 1%)
        risk_free: 年化无风险利率(decimal, 默认 0.02 = 2%)
        ann_factor: 年化因子(A 股惯例 250)

    Returns:
        float,无单位。数据不足或波动率为0时返回 0
    """
    arr = _to_numpy(returns)
    if len(arr) < 2:
        return 0.0
    # 去掉 NaN
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2:
        return 0.0
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))  # 样本标准差
    if std < 1e-9:
        return 0.0
    daily_rf = risk_free / ann_factor
    return float((mean - daily_rf) * ann_factor / (std * math.sqrt(ann_factor)))


# ============================================================
# Max Drawdown
# ============================================================
def max_drawdown(equity: ArrayLike) -> float:
    """
    计算最大回撤

    约定: 返回**负数或0**,e.g. -0.25 表示从峰值回撤 25%

    公式: min((equity - cummax) / cummax)

    Args:
        equity: 净值序列(累计收益,如初始 1.0)

    Returns:
        float,≤ 0。无回撤或数据不足时返回 0
    """
    arr = _to_numpy(equity)
    if len(arr) < 2:
        return 0.0
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2:
        return 0.0
    peak = np.maximum.accumulate(arr)
    dd = (arr - peak) / np.where(peak == 0, 1, peak)  # 防 peak=0
    return float(dd.min())


# ============================================================
# Annual Return
# ============================================================
def annual_return(
    total_return: float,
    days: int,
    ann_factor: int = ANNUAL_TRADING_DAYS,
) -> float:
    """
    计算年化收益率(百分比,如 15.0 表示 15%)

    公式: ((1 + total_return) ** (ann_factor/days) - 1) * 100

    Args:
        total_return: 总收益率(decimal,如 0.30 表示 30%)
        days: 持仓天数(自然日或交易日,与 ann_factor 单位一致)
        ann_factor: 年化因子(默认 250,与 days 同单位)

    Returns:
        float,百分比。days ≤ 0 或 total_return ≤ -1 时返回 0
    """
    if days <= 0 or total_return <= -1:
        return 0.0
    try:
        ratio = (1 + total_return) ** (ann_factor / days) - 1
        return float(ratio * 100)
    except (OverflowError, ValueError):
        return 0.0


# ============================================================
# Volatility
# ============================================================
def volatility(
    returns: ArrayLike,
    ann_factor: int = ANNUAL_TRADING_DAYS,
) -> float:
    """
    计算年化波动率(百分比,如 20.0 表示 20%)

    公式: std(returns, ddof=1) * sqrt(ann_factor) * 100

    Args:
        returns: 日收益率序列(decimal)
        ann_factor: 年化因子(默认 250)

    Returns:
        float,百分比。数据不足时返回 0
    """
    arr = _to_numpy(returns)
    if len(arr) < 2:
        return 0.0
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2:
        return 0.0
    return float(np.std(arr, ddof=1) * math.sqrt(ann_factor) * 100)


# ============================================================
# Calmar Ratio (年化收益 / |最大回撤|)
# ============================================================
def calmar_ratio(annual_ret_pct: float, max_dd: float) -> float:
    """
    计算卡玛比率 = 年化收益(%) / |最大回撤|

    Args:
        annual_ret_pct: 年化收益(%,如 15.0)
        max_dd: 最大回撤(负数,如 -0.25;本函数取绝对值)

    Returns:
        float,无单位。max_dd = 0 时返回 0
    """
    if max_dd == 0:
        return 0.0
    return float(annual_ret_pct / abs(max_dd))


# ============================================================
# Profit Factor (总盈利 / |总亏损|)
# ============================================================
def profit_factor(returns: ArrayLike) -> float:
    """
    计算盈亏比 = sum(正收益) / |sum(负收益)|

    Args:
        returns: 日收益率序列(decimal)

    Returns:
        float,无单位。无亏损时返回 0
    """
    arr = _to_numpy(returns)
    gains = arr[arr > 0].sum()
    losses = arr[arr < 0].sum()
    if losses == 0:
        return 0.0
    return float(gains / abs(losses))


# ============================================================
# Win Rate (胜率 %)
# ============================================================
def win_rate(returns: ArrayLike) -> float:
    """
    计算胜率(%,如 55.0 表示 55%)

    Args:
        returns: 日收益率序列(decimal)

    Returns:
        float,百分比。空数据返回 0
    """
    arr = _to_numpy(returns)
    if len(arr) == 0:
        return 0.0
    return float((arr > 0).sum() / len(arr) * 100)