"""
仓位管理器 — 支持的算法
=======================
1. fixed:    固定比例仓位
2. kelly:    凯利公式 (Half Kelly)
3. atr:      ATR 动态仓位 (推荐)
4. turtle:  海龟法则仓位
"""

from __future__ import annotations
import math
from .config import TradingConfig


def calc_position_size(
    capital: float,
    price: float,
    atr: float | None = None,
    win_rate: float | None = None,
    avg_win: float | None = None,
    avg_loss: float | None = None,
    config: TradingConfig | None = None,
) -> int:
    """计算买入股数（A股：向下取整到100的倍数）

    Args:
        capital: 当前可用资金
        price: 当前股价
        atr: 当前 ATR 值（atr/turtle 模式需要）
        win_rate: 胜率（kelly 模式需要）
        avg_win: 平均盈利（kelly 模式需要）
        avg_loss: 平均亏损（kelly 模式需要）
        config: 操盘策略配置

    Returns:
        买入股数（100 的整数倍）
    """
    if config is None:
        config = TradingConfig()

    if config.position_method == "fixed":
        shares = _fixed_sizing(capital, price, config)
    elif config.position_method == "kelly":
        shares = _kelly_sizing(capital, price, win_rate, avg_win, avg_loss, config)
    elif config.position_method == "atr":
        shares = _atr_sizing(capital, price, atr, config)
    elif config.position_method == "turtle":
        shares = _turtle_sizing(capital, price, atr, config)
    else:
        shares = _fixed_sizing(capital, price, config)

    # 限制单股最大仓位
    max_by_pct = capital * config.max_position_pct / price
    shares = min(shares, int(max_by_pct))

    return _round_lot(shares)


def _fixed_sizing(capital: float, price: float, config: TradingConfig) -> int:
    """固定比例仓位: capital × max_position_pct / price"""
    return int(capital * config.max_position_pct / price)


def _kelly_sizing(
    capital: float,
    price: float,
    win_rate: float | None,
    avg_win: float | None,
    avg_loss: float | None,
    config: TradingConfig,
) -> int:
    """凯利公式: f* = (p × b - q) / b, 使用 Half Kelly
    
    p = 胜率, q = 1-p, b = 赔率 (avg_win / avg_loss)
    Half Kelly = f* × 0.5
    """
    if not all(v is not None and v > 0 for v in [win_rate, avg_win, avg_loss]):
        return _fixed_sizing(capital, price, config)

    p = win_rate
    q = 1.0 - p
    b = avg_win / avg_loss

    if b <= 0:
        return _fixed_sizing(capital, price, config)

    kelly_f = (p * b - q) / b
    kelly_f = max(0, min(kelly_f, config.max_position_pct))

    # Half Kelly
    half_kelly = kelly_f * 0.5
    return int(capital * half_kelly / price)


def _atr_sizing(capital: float, price: float, atr: float | None, config: TradingConfig) -> int:
    """ATR 动态仓位: (capital × risk%) / (ATR × point_value)
    
    让每笔交易的风险金额相等，高波动少买，低波动多买。
    """
    if atr is None or atr <= 0:
        return _fixed_sizing(capital, price, config)

    risk_amount = capital * config.risk_per_trade
    shares = risk_amount / (atr * 1.0)  # point_value = 1 (A股每股)
    return int(shares)


def _turtle_sizing(capital: float, price: float, atr: float | None, config: TradingConfig) -> int:
    """海龟法则: Unit = 1% × capital / (N × point_value)
    
    N = ATR, 最多持有4个Unit
    """
    if atr is None or atr <= 0:
        return _fixed_sizing(capital, price, config)

    unit = (capital * 0.01) / (atr * 1.0)
    return int(unit)  # 1 Unit


def calc_add_position(
    current_shares: int,
    capital: float,
    price: float,
    entry_price: float,
    atr: float | None,
    config: TradingConfig,
) -> int:
    """金字塔加仓: 每涨 add_on_atr_mult × ATR 加仓一次"""
    if not config.pyramiding_enabled or atr is None or atr <= 0:
        return 0

    profit_pct = (price - entry_price) / entry_price
    atr_pct = atr / entry_price

    if atr_pct <= 0:
        return 0

    atr_units = profit_pct / atr_pct
    adds_possible = int(atr_units / config.add_on_atr_mult)

    if adds_possible <= 0:
        return 0

    adds_done = current_shares  # 简化: 用当前股数推算已加仓次数
    # 实际 should track adds count externally
    return _round_lot(int(capital * config.max_position_pct / price * 0.5))


def _round_lot(shares: int) -> int:
    """向下取整到 100 的整数倍（A股规则）"""
    return (shares // 100) * 100
