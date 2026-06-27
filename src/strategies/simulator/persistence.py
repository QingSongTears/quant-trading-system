"""
simulator.persistence — DB 持久化 (ADR-0011 #82)

从 simulator.py:_save_to_db 拆分:
  - 3 张表 DDL (simulation / simulation_trades / simulation_equity)
  - 主表 / 交易表 / 净值曲线 写入
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .portfolio_types import TradeRecord


_DDL = [
    """
    CREATE TABLE IF NOT EXISTS simulation (
        run_id TEXT PRIMARY KEY,
        model TEXT, config TEXT, status TEXT,
        start_date TEXT, end_date TEXT,
        initial_capital REAL, final_capital REAL,
        total_return REAL, annual_return REAL,
        sharpe_ratio REAL, max_drawdown REAL,
        win_rate REAL, total_trades INTEGER,
        profit_factor REAL, error TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS simulation_trades (
        trade_id TEXT PRIMARY KEY,
        run_id TEXT, code TEXT, direction TEXT,
        signal_source TEXT, entry_date TEXT,
        entry_price REAL, entry_size INTEGER,
        exit_date TEXT, exit_price REAL,
        exit_reason TEXT, pnl REAL, pnl_pct REAL,
        commission REAL, stamp_tax REAL,
        net_pnl REAL, holding_days INTEGER,
        slippage REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS simulation_equity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT, date TEXT, code TEXT,
        capital REAL, position_value REAL,
        total REAL
    )
    """,
]


def _ensure_tables(engine) -> None:
    """建表 (幂等)"""
    from sqlalchemy import text as _t
    with engine.connect() as conn:
        for ddl in _DDL:
            conn.execute(_t(ddl))
        conn.commit()


def _insert_main_row(engine, result) -> None:
    """写入 simulation 主表"""
    from sqlalchemy import text as _t
    with engine.connect() as conn:
        conn.execute(
            _t(
                """INSERT OR REPLACE INTO simulation
                   (run_id, model, config, status, start_date, end_date,
                    initial_capital, final_capital, total_return, annual_return,
                    sharpe_ratio, max_drawdown, win_rate, total_trades,
                    profit_factor, error)
                   VALUES (:run_id, :model, :config, :status, :start_date,
                           :end_date, :initial_capital, :final_capital,
                           :total_return, :annual_return, :sharpe_ratio,
                           :max_drawdown, :win_rate, :total_trades,
                           :profit_factor, :error)"""
            ),
            {
                "run_id": result.run_id,
                "model": result.model,
                "config": json.dumps(result.config),
                "status": result.status,
                "start_date": result.start_date,
                "end_date": result.end_date,
                "initial_capital": result.initial_capital,
                "final_capital": result.final_capital,
                "total_return": result.total_return,
                "annual_return": result.annual_return,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown": result.max_drawdown,
                "win_rate": result.win_rate,
                "total_trades": result.total_trades,
                "profit_factor": result.profit_factor,
                "error": result.error,
            },
        )
        conn.commit()


def _insert_trades(engine, run_id: str, trades: list["TradeRecord"]) -> None:
    """写入交易记录 (每条一行)"""
    from sqlalchemy import text as _t
    with engine.connect() as conn:
        for t in trades:
            conn.execute(
                _t(
                    """INSERT OR REPLACE INTO simulation_trades
                       (trade_id, run_id, code, direction, signal_source,
                        entry_date, entry_price, entry_size,
                        exit_date, exit_price, exit_reason,
                        pnl, pnl_pct, commission, stamp_tax,
                        net_pnl, holding_days, slippage)
                       VALUES (:trade_id, :run_id, :code, :direction,
                               :signal_source, :entry_date, :entry_price,
                               :entry_size, :exit_date, :exit_price,
                               :exit_reason, :pnl, :pnl_pct, :commission,
                               :stamp_tax, :net_pnl, :holding_days,
                               :slippage)"""
                ),
                {
                    "trade_id": t.trade_id,
                    "run_id": t.run_id,
                    "code": t.code,
                    "direction": t.direction,
                    "signal_source": t.signal_source,
                    "entry_date": t.entry_date,
                    "entry_price": t.entry_price,
                    "entry_size": t.entry_size,
                    "exit_date": t.exit_date,
                    "exit_price": t.exit_price,
                    "exit_reason": t.exit_reason,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                    "commission": t.commission,
                    "stamp_tax": t.stamp_tax,
                    "net_pnl": t.net_pnl,
                    "holding_days": t.holding_days,
                    "slippage": t.slippage,
                },
            )
        conn.commit()


def _insert_equity(engine, run_id: str, equity_curve: list[dict]) -> None:
    """写入净值曲线 (抽样: 每 5 条存 1 条, 控制表大小)"""
    from sqlalchemy import text as _t
    with engine.connect() as conn:
        for i, pt in enumerate(equity_curve):
            if i % 5 != 0:
                continue
            conn.execute(
                _t(
                    "INSERT INTO simulation_equity "
                    "(run_id, date, code, capital, position_value, total) "
                    "VALUES (:run_id, :date, :code, :capital, "
                    ":position_value, :total)"
                ),
                {
                    "run_id": run_id,
                    "date": pt["date"],
                    "code": pt["code"],
                    "capital": pt["capital"],
                    "position_value": pt["position_value"],
                    "total": pt["total"],
                },
            )
        conn.commit()


def save_to_db(result, equity_curve: list[dict], trades: list["TradeRecord"]) -> None:
    """保存 SimulationResult + trades + equity 到 DB

    拆分前位于 simulator.py:_save_to_db (411-554).
    """
    from ...db.engine import get_engine
    engine = get_engine()
    _ensure_tables(engine)
    _insert_main_row(engine, result)
    _insert_trades(engine, result.run_id, trades)
    _insert_equity(engine, result.run_id, equity_curve)


__all__ = ["save_to_db"]