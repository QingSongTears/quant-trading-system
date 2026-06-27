"""
simulator.portfolio_types — Position / TradeRecord dataclass (ADR-0011 #82)

公开 dataclass: 从 simulator.py 顶层迁出, 保持导入路径
`from src.strategies.simulator import Position, TradeRecord` 仍可用 (facade re-export).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Position:
    """当前持仓状态"""
    code: str = ""
    size: int = 0
    cost_basis: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    entry_date: str = ""
    entry_price: float = 0.0
    highest_price: float = 0.0
    current_price: float = 0.0
    profit_pct: float = 0.0


@dataclass
class TradeRecord:
    """单笔交易记录"""
    trade_id: str = ""
    run_id: str = ""
    code: str = ""
    direction: str = ""        # BUY / SELL
    signal_source: str = ""
    entry_date: str = ""
    entry_price: float = 0.0
    entry_size: int = 0        # 股数
    exit_date: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""      # stop_loss / take_profit / signal / time_stop
    pnl: float = 0.0           # 盈亏(含费用)
    pnl_pct: float = 0.0       # 盈亏百分比
    commission: float = 0.0
    stamp_tax: float = 0.0
    net_pnl: float = 0.0
    holding_days: int = 0
    slippage: float = 0.0


__all__ = ["Position", "TradeRecord"]