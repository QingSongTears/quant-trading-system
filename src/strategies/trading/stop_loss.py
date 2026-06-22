"""
止盈止损引擎
============
支持的退出逻辑:
1. fixed_stop:     固定比例止损
2. atr_stop:       ATR 动态止损
3. trailing_stop:  移动止盈 (吊灯止损)
4. time_stop:      时间止损
"""

from __future__ import annotations
from dataclasses import dataclass
from .config import TradingConfig


@dataclass
class StopLossResult:
    """止损检查结果"""
    triggered: bool = False
    reason: str = ""
    exit_price: float = 0.0


def check_stop_loss(
    current_price: float,
    entry_price: float,
    atr: float | None,
    highest_price: float,
    config: TradingConfig,
) -> StopLossResult:
    """检查所有止损条件，返回最先触发的

    - method='fixed': 仅固定比例止损
    - method='atr':   仅 ATR 动态止损
    - method='both':  取两者较大值（推荐A股实战）
    """
    if config.stop_loss_method == "fixed":
        return _check_fixed_stop(current_price, entry_price, config)

    if config.stop_loss_method == "atr":
        return _check_atr_stop(current_price, entry_price, atr, config)

    # method='both': 取两者较大值（更宽松的止损价）
    fixed_result = _check_fixed_stop(current_price, entry_price, config)
    atr_result = _check_atr_stop(current_price, entry_price, atr, config)

    if fixed_result.triggered and atr_result.triggered:
        return fixed_result if fixed_result.exit_price >= atr_result.exit_price else atr_result
    if fixed_result.triggered:
        return fixed_result
    if atr_result.triggered:
        return atr_result

    return StopLossResult()


def check_take_profit(
    current_price: float,
    entry_price: float,
    atr: float | None,
    highest_price: float,
    config: TradingConfig,
) -> StopLossResult:
    """检查止盈条件"""
    # 移动止盈 (吊灯止损)
    if config.take_profit_method == "trailing_atr":
        return _check_trailing_stop(current_price, highest_price, atr, entry_price, config)

    # 固定止盈
    if config.take_profit_method == "fixed":
        profit_pct = (current_price - entry_price) / entry_price
        if profit_pct >= config.fixed_tp_pct:
            return StopLossResult(triggered=True, reason="take_profit_fixed", exit_price=current_price)

    return StopLossResult()


def check_time_stop(
    holding_days: int,
    current_price: float,
    entry_price: float,
    config: TradingConfig,
) -> StopLossResult:
    """时间止损检查"""
    if not config.time_stop_enabled:
        return StopLossResult()

    if holding_days >= config.max_holding_days:
        return StopLossResult(triggered=True, reason="time_stop_max_days", exit_price=current_price)

    if holding_days >= config.no_profit_days:
        profit_pct = (current_price - entry_price) / entry_price
        if profit_pct < 0.03:  # <3% 视为不涨
            return StopLossResult(triggered=True, reason="time_stop_no_profit", exit_price=current_price)

    return StopLossResult()


def _check_fixed_stop(
    current_price: float, entry_price: float, config: TradingConfig,
) -> StopLossResult:
    """固定比例止损: current_price <= entry_price × (1 - fixed_stop_pct)"""
    stop_price = entry_price * (1 - config.fixed_stop_pct)
    if current_price <= stop_price:
        return StopLossResult(triggered=True, reason="stop_loss_fixed", exit_price=current_price)
    return StopLossResult()


def _check_atr_stop(
    current_price: float, entry_price: float, atr: float | None, config: TradingConfig,
) -> StopLossResult:
    """ATR 动态止损: current_price <= entry_price - ATR × mult"""
    if atr is None or atr <= 0:
        return StopLossResult()

    stop_price = entry_price - (atr * config.atr_stop_mult)
    if current_price <= stop_price:
        return StopLossResult(triggered=True, reason="stop_loss_atr", exit_price=current_price)
    return StopLossResult()


def _check_trailing_stop(
    current_price: float,
    highest_price: float,
    atr: float | None,
    entry_price: float,
    config: TradingConfig,
) -> StopLossResult:
    """移动止盈 (吊灯止损): 最高价回撤 2×ATR 触发"""
    if atr is None or atr <= 0:
        return StopLossResult()

    # 至少要有一定盈利才启用移动止盈
    profit_pct = (highest_price - entry_price) / entry_price
    if profit_pct < 0.05:  # 盈利<5% 不启用
        return StopLossResult()

    trail_stop = highest_price - (atr * config.trailing_atr_mult)
    if current_price <= trail_stop:
        return StopLossResult(triggered=True, reason="take_profit_trailing", exit_price=current_price)

    return StopLossResult()
