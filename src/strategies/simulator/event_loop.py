"""
simulator.event_loop — 每日模拟循环 (ADR-0011 #82)

从 simulator.py:_simulate_one 拆分:
  - 单只股票的每日循环
  - 持仓状态更新
  - 止盈止损检查
  - 买入逻辑（含 RiskEngine.check_order 前置, ADR-0011 D4-A）
  - 平仓逻辑（含 RiskEngine.check_order 前置, ADR-0011 D4-A）
  - 每日净值记录
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ...backtest.astock_strategy import (
    AStockRules,
    get_stock_atr,
)
from ..trading.position_sizer import calc_position_size
from ..trading.stop_loss import (
    check_stop_loss,
    check_take_profit,
)
from .portfolio import close_position
from .portfolio_types import Position

if TYPE_CHECKING:
    from .engine import SimulatorEngine

logger = logging.getLogger(__name__)


def _vt_symbol(code: str) -> str:
    """本地 code (000001) → vnpy vt_symbol (000001.SZ)"""
    if "." in code:
        return code
    # 简化推断: 6 开头 → SH, 其他 → SZ
    market = "SH" if code.startswith("6") else "SZ"
    return f"{code}.{market}"


def _check_risk(engine: "SimulatorEngine", req: dict) -> bool:
    """调 RiskEngine.check_order, 拒绝时记录日志返回 False

    不传 risk_engine 时 (向后兼容) → 直接通过
    """
    if engine.risk_engine is None:
        return True
    ok, reason = engine.risk_engine.check_order(req)
    if not ok:
        logger.warning(
            f"风控拒绝 {req.get('vt_symbol', '')} "
            f"vol={req.get('volume', 0)} price={req.get('price', 0):.2f}: "
            f"{reason}"
        )
    return ok


def simulate_one(
    engine: "SimulatorEngine",
    code: str,
    start: str,
    end: str,
    strategy_id: int,
) -> None:
    """模拟单只股票 (写入 engine.positions / engine.equity_curve / engine.trades)

    拆分前位于 simulator.py:_simulate_one (222-310).
    """
    from src.data import data_mgr
    rows = data_mgr.query(
        """SELECT trade_date, open, high, low, close, pct_change
           FROM daily_price
           WHERE code=:code AND trade_date>=:start AND trade_date<=:end
           ORDER BY trade_date""",
        {"code": code, "start": start, "end": end},
    )

    if len(rows) < 20:
        return

    pos = Position(code=code)
    astock = AStockRules()

    for row in rows:
        trade_date = row["trade_date"]
        open_p = row.get("open") or 0
        high = row.get("high") or 0
        low = row.get("low") or 0
        close = row.get("close") or 0
        pct_chg = row.get("pct_change") or 0

        prev_close = close / (1 + pct_chg / 100) if pct_chg != 0 and pct_chg > -100 else close
        atr = get_stock_atr(code, trade_date) or (close * 0.03)

        # === 持仓状态更新 ===
        if pos.size > 0:
            pos.current_price = close
            pos.market_value = pos.size * close
            pos.unrealized_pnl = pos.market_value - pos.size * pos.cost_basis
            pos.profit_pct = (close / pos.cost_basis - 1) * 100 if pos.cost_basis else 0.0
            if close > pos.highest_price:
                pos.highest_price = close

            # 止盈止损检查
            stop_result = check_stop_loss(
                close, pos.entry_price, atr, pos.highest_price, engine.config
            )
            tp_result = check_take_profit(
                close, pos.entry_price, atr, pos.highest_price, engine.config
            )
            exit_reason = ""
            if stop_result.triggered:
                exit_reason = stop_result.reason
            elif tp_result.triggered:
                exit_reason = tp_result.reason

            if exit_reason:
                can_sell, _ = astock.can_sell(close, prev_close)
                if can_sell:
                    # ── ADR-0011 D4-A: 平仓前置 RiskEngine.check_order ──
                    close_req = {
                        "vt_symbol": _vt_symbol(code),
                        "volume": pos.size,
                        "price": close,
                    }
                    if not _check_risk(engine, close_req):
                        # 风控拒绝: 持仓保持, 下一日重新评估 (不 raise)
                        pass
                    else:
                        trade = close_position(
                            pos, close, trade_date, exit_reason, atr,
                            slippage_bps=engine.config.slippage_bps,
                            run_id=engine.run_id,
                        )
                        engine.trades.append(trade)
                        engine.capital += pos.size * trade.exit_price - trade.commission - trade.stamp_tax
                        pos = Position(code=code)

        # === 买入逻辑（仅在无持仓时）===
        if pos.size == 0:
            can_buy, _ = astock.can_buy(close, prev_close)
            if pct_chg > 0 and can_buy:
                shares = calc_position_size(
                    engine.capital, close, atr=atr, config=engine.config
                )
                if shares >= 100 and shares * close <= engine.capital:
                    cost = shares * close
                    comm = max(cost * 0.00025, 5)
                    slip = astock.calc_slippage(close, 10, True)
                    actual_cost = shares * slip + comm
                    if actual_cost <= engine.capital:
                        # ── ADR-0011 D4-A: 买入前置 RiskEngine.check_order ──
                        buy_req = {
                            "vt_symbol": _vt_symbol(code),
                            "volume": shares,
                            "price": slip,
                        }
                        if _check_risk(engine, buy_req):
                            engine.capital -= actual_cost
                            pos = Position(
                                code=code, size=shares, cost_basis=slip,
                                entry_date=trade_date, entry_price=slip,
                                highest_price=close, current_price=close,
                                market_value=shares * close,
                            )

        # === 记录每日净值 ===
        total_value = engine.capital + pos.market_value if pos.size > 0 else engine.capital
        engine.equity_curve.append({
            "date": trade_date, "code": code,
            "capital": round(engine.capital, 2),
            "position_value": round(pos.market_value, 2) if pos.size > 0 else 0,
            "total": round(total_value, 2),
        })


__all__ = ["simulate_one"]