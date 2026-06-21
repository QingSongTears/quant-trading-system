"""
src.constants — 全局常量单一来源
================================

本包提供全项目共享的常量值,所有重复定义的位置必须从这里 import 而非重新硬编码。

约定:
- 百分比统一用**小数**(decimal)表示,如 0.08 表示 8%
- 时间统一用**自然日**或**交易日**(在常量名中显式标注)
- 金额统一用**元**(人民币)

模块:
- risk:     风控阈值(止损、止盈、回撤、持仓比例)
- signal:   信号阈值(RSI、BB、量比、涨幅)
- market:   市场参数(年化天数、无风险利率、市值下限)
- fees:     交易成本(手续费、印花税、滑点)
"""
from .risk import (
    STOP_LOSS_DEFAULT,
    STOP_LOSS_AGGRESSIVE,
    STOP_LOSS_CONSERVATIVE,
    TRAILING_STOP_PCT,
    TRAILING_DD_PCT,
    TIME_STOP_DAYS_DEFAULT,
    TIME_STOP_RETURN_DEFAULT,
    MAX_DRAWDOWN_EXIT_PCT,
    MAX_SINGLE_POSITION_PCT,
    MAX_PORTFOLIO_STOP_PCT,
)
from .signal import (
    MAX_RSI_14,
    MAX_RSI_6,
    MAX_BB_POS,
    MAX_DD_60D,
    MIN_VOL_RATIO,
    MIN_PRICE_CHG,
)
from .market import (
    RISK_FREE_RATE,
    ANNUAL_TRADING_DAYS,
    MIN_MARKET_CAP_REVERSAL,
    MIN_MARKET_CAP_BULL,
)
from .fees import (
    COMMISSION_RATE,
    STAMP_DUTY_RATE,
    SLIPPAGE_RATE,
    MIN_COMMISSION,
)

__all__ = [
    # risk
    "STOP_LOSS_DEFAULT",
    "STOP_LOSS_AGGRESSIVE",
    "STOP_LOSS_CONSERVATIVE",
    "TRAILING_STOP_PCT",
    "TRAILING_DD_PCT",
    "TIME_STOP_DAYS_DEFAULT",
    "TIME_STOP_RETURN_DEFAULT",
    "MAX_DRAWDOWN_EXIT_PCT",
    "MAX_SINGLE_POSITION_PCT",
    "MAX_PORTFOLIO_STOP_PCT",
    # signal
    "MAX_RSI_14", "MAX_RSI_6", "MAX_BB_POS", "MAX_DD_60D",
    "MIN_VOL_RATIO", "MIN_PRICE_CHG",
    # market
    "RISK_FREE_RATE", "ANNUAL_TRADING_DAYS",
    "MIN_MARKET_CAP_REVERSAL", "MIN_MARKET_CAP_BULL",
    # fees
    "COMMISSION_RATE", "STAMP_DUTY_RATE", "SLIPPAGE_RATE", "MIN_COMMISSION",
]