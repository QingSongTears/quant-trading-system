"""
simulator.portfolio — 仓位 + 绩效 (ADR-0011 #82)

从 simulator.py 拆分:
  - 平仓逻辑 (_close_position)
  - 绩效计算 (_calc_performance)
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np

from ..trading.stop_loss import check_stop_loss, check_take_profit
from .portfolio_types import Position, TradeRecord

if TYPE_CHECKING:
    from .engine import SimulatorEngine
    from ..trading.config import TradingConfig


def close_position(
    pos: Position,
    exit_price: float,
    exit_date: str,
    reason: str,
    atr: float,
    slippage_bps: float,
    run_id: str,
) -> TradeRecord:
    """平仓 → 计算 P&L / 手续费 / 印花税 → 追加 TradeRecord

    拆分前位于 simulator.py:_close_position (313-352).
    """
    # 滑点: 卖出按 exit_price 下浮
    slippage = exit_price * (1 - slippage_bps / 10_000)
    sell_amount = pos.size * slippage
    buy_amount = pos.size * pos.cost_basis

    # 手续费: 买卖各一次, 单边最低 5 元
    comm = max(buy_amount * 0.00025, 5) + max(sell_amount * 0.00025, 5)
    # 印花税: 仅卖出
    tax = sell_amount * 0.0005
    total_cost = comm + tax

    net_pnl = sell_amount - buy_amount - total_cost
    pnl_pct = (slippage / pos.cost_basis - 1) * 100 if pos.cost_basis else 0.0

    # 持仓天数
    try:
        entry_dt = datetime.strptime(pos.entry_date, "%Y-%m-%d")
        exit_dt = datetime.strptime(exit_date, "%Y-%m-%d")
        holding_days = (exit_dt - entry_dt).days
    except (ValueError, TypeError):
        holding_days = 0

    import uuid
    return TradeRecord(
        trade_id=uuid.uuid4().hex[:8],
        run_id=run_id,
        code=pos.code,
        direction="SELL",
        signal_source="",
        entry_date=pos.entry_date,
        entry_price=pos.cost_basis,
        entry_size=pos.size,
        exit_date=exit_date,
        exit_price=slippage,
        exit_reason=reason,
        pnl=round(net_pnl, 2),
        pnl_pct=round(pnl_pct, 2),
        commission=round(comm, 2),
        stamp_tax=round(tax, 2),
        net_pnl=round(net_pnl, 2),
        holding_days=holding_days,
        slippage=round(slippage - exit_price, 2),
    )


def calc_performance(
    result,
    initial_capital: float,
    final_capital: float,
    equity_curve: list[dict],
    trades: list[TradeRecord],
) -> None:
    """计算绩效指标 → 原地写入 SimulationResult

    拆分前位于 simulator.py:_calc_performance (354-409).
    """
    if not equity_curve:
        result.final_capital = final_capital
        return

    start_val = initial_capital
    end_val = equity_curve[-1]["total"]
    result.final_capital = end_val

    total_return = (end_val / start_val - 1) * 100 if start_val > 0 else 0.0
    result.total_return = round(total_return, 2)

    # 年化收益 (按 252 交易日)
    days = len(equity_curve)
    if days > 0 and start_val > 0:
        annual = ((1 + total_return / 100) ** (252 / max(days, 1)) - 1) * 100
        result.annual_return = round(annual, 2)

    # Sharpe (假设无风险利率 0)
    returns: list[float] = []
    prev_total = start_val
    for pt in equity_curve:
        total = pt["total"]
        ret = (total / prev_total - 1) if prev_total > 0 else 0.0
        returns.append(ret)
        prev_total = total

    if len(returns) > 1:
        avg_ret = float(np.mean(returns))
        std_ret = float(np.std(returns, ddof=0))
        if std_ret > 0:
            result.sharpe_ratio = round(
                (avg_ret / std_ret) * (252 ** 0.5), 2
            )

    # 最大回撤
    peak = start_val
    max_dd = 0.0
    for pt in equity_curve:
        total = pt["total"]
        if total > peak:
            peak = total
        dd = (peak - total) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    result.max_drawdown = round(max_dd, 2)

    # 胜率 / 盈亏比
    closed_trades = [t for t in trades if t.direction == "SELL"]
    result.total_trades = len(closed_trades)
    if closed_trades:
        wins = sum(1 for t in closed_trades if t.net_pnl > 0)
        result.win_rate = round(wins / len(closed_trades) * 100, 1)

        total_win = sum(t.net_pnl for t in closed_trades if t.net_pnl > 0)
        total_loss = sum(abs(t.net_pnl) for t in closed_trades if t.net_pnl <= 0)
        if total_loss > 0:
            result.profit_factor = round(total_win / total_loss, 2)