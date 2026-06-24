"""
模拟交易引擎
============
信号 → 仓位计算 → 止盈止损 → T+1/涨跌停检查 → 执行 → 记录
"""

from __future__ import annotations
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import text

import pandas as pd

from ..backtest.astock_strategy import (
    AStockRules, get_prev_close, get_stock_atr, round_lot,
)
from .trading.config import TradingConfig
from .trading.position_sizer import calc_position_size
from .trading.stop_loss import (
    check_stop_loss, check_take_profit, check_time_stop,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ── 数据结构 ──────────────────────────────────────────

@dataclass
class Signal:
    """统一信号格式"""
    code: str
    date: str          # YYYY-MM-DD
    action: str        # BUY / SELL / HOLD
    price: float       # 信号发出时价格
    confidence: float  # 0-1
    source: str        # 模型名称

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
class SimulationResult:
    """模拟交易结果"""
    run_id: str = ""
    model: str = ""
    config: dict = field(default_factory=dict)
    status: str = "running"  # running / done / error
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = 0.0
    final_capital: float = 0.0
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    profit_factor: float = 0.0
    error: str = ""

# ── 信号适配器 ──────────────────────────────────────

class SignalAdapter:
    """从 DB 获取信号——适配现有回测结果和预测数据"""

    def __init__(self, source: str = "backtest"):
        self.source = source

    def get_signals(
        self, code: str, start_date: str, end_date: str,
        strategy_id: int = 1,
    ) -> list[Signal]:
        """获取某个股票在日期范围内的信号
        
        strategy_id: 1-7 对应7个策略
        """
        from src.data import data_mgr
        rows = data_mgr.query(
            """SELECT stock_code, start_date, end_date, total_return,
                      sharpe_ratio, win_rate, total_trades, equity_curve
               FROM backtest_result
               WHERE strategy_id=:sid AND stock_code=:code
                 AND start_date >= :start AND end_date <= :end
               ORDER BY start_date
               LIMIT 1""",
            {"sid": strategy_id, "code": code, "start": start_date, "end": end_date},
        )

        if not rows:
            return []

        # 从 equity_curve 解析信号点
        signals = []
        for row in rows:
            try:
                curve = json.loads(row["equity_curve"]) if row["equity_curve"] else []
                for pt in curve:
                    if isinstance(pt, dict) and "trade" in pt:
                        t = pt["trade"]
                        signals.append(Signal(
                            code=code,
                            date=pt.get("date", ""),
                            action=t.get("type", ""),  # BUY/SELL
                            price=float(t.get("price", 0)),
                            confidence=0.5,
                            source=f"strategy_{strategy_id}",
                        ))
            except Exception:
                pass

        return signals

    def get_daily_signal(
        self, code: str, trade_date: str,
        strategy_id: int = 6,
    ) -> Signal:
        """获取单日信号——简化为根据回测结果区间判断"""
        # 简化实现：返回一个中立信号
        return Signal(
            code=code, date=trade_date, action="HOLD",
            price=0, confidence=0, source=f"strategy_{strategy_id}",
        )

# ── 模拟交易引擎 ──────────────────────────────────────

class Simulator:
    """模拟交易引擎"""

    def __init__(self, config: TradingConfig | None = None):
        self.config = config or TradingConfig()
        self.signal_adapter = SignalAdapter()
        self.astock = AStockRules()

        # 运行时状态
        self.run_id = ""
        self.capital = self.config.initial_capital
        self.positions: dict[str, Position] = {}       # code → Position
        self.trades: list[TradeRecord] = []
        self.equity_curve: list[dict] = []             # 每日净值
        self.daily_pnl: list[float] = []

    def run(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        strategy_id: int = 6,
    ) -> SimulationResult:
        """运行模拟交易

        由于现有信号模型跑全量数据较复杂，此实现使用 K 线数据
        配合简单的技术规则生成信号（可作为后续接入真实模型的骨架）。
        """
        self.run_id = uuid.uuid4().hex[:12]
        self.capital = self.config.initial_capital
        self.positions = {}
        self.trades = []
        self.equity_curve = []
        result = SimulationResult(
            run_id=self.run_id,
            model=f"strategy_{strategy_id}",
            config=self.config.to_dict(),
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.config.initial_capital,
            status="running",
        )

        try:
            for code in codes:
                self._simulate_one(code, start_date, end_date, strategy_id)

            # 计算绩效
            self._calc_performance(result)
            result.status = "done"

        except Exception as e:
            result.status = "error"
            result.error = str(e)

        # 保存到 DB
        self._save_to_db(result)
        return result

    def _simulate_one(
        self, code: str, start: str, end: str,
        strategy_id: int,
    ):
        """模拟单只股票"""
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
        pending_sell = False  # T+1: 今日买入标记，次日才能卖

        for i, row in enumerate(rows):
            trade_date = row["trade_date"]
            open_p = row.get("open") or 0
            high = row.get("high") or 0
            low = row.get("low") or 0
            close = row.get("close") or 0
            pct_chg = row.get("pct_change") or 0

            prev_close = close / (1 + pct_chg / 100) if pct_chg != 0 else close
            atr = get_stock_atr(code, trade_date) or (close * 0.03)  # 兜底ATR

            # === 持仓状态更新 ===
            if pos.size > 0:
                pos.current_price = close
                pos.market_value = pos.size * close
                pos.unrealized_pnl = pos.market_value - pos.size * pos.cost_basis
                pos.profit_pct = (close / pos.cost_basis - 1) * 100
                if close > pos.highest_price:
                    pos.highest_price = close

                # 止盈止损检查
                stop_result = check_stop_loss(
                    close, pos.entry_price, atr, pos.highest_price, self.config
                )
                tp_result = check_take_profit(
                    close, pos.entry_price, atr, pos.highest_price, self.config
                )
                exit_reason = ""
                if stop_result.triggered:
                    exit_reason = stop_result.reason
                elif tp_result.triggered:
                    exit_reason = tp_result.reason

                if exit_reason:
                    # 检查跌停能否卖出
                    can, _ = self.astock.can_sell(close, prev_close)
                    if can:
                        self._close_position(pos, close, trade_date, exit_reason, atr)

            # === 买入逻辑（仅在无持仓时）===
            if pos.size == 0:
                # 简单买入条件：当日收盘涨幅>0 且 不是涨停
                can_buy, _ = self.astock.can_buy(close, prev_close)
                if pct_chg > 0 and can_buy:
                    shares = calc_position_size(
                        self.capital, close, atr=atr, config=self.config
                    )
                    if shares >= 100 and shares * close <= self.capital:
                        cost = shares * close
                        comm = max(cost * 0.00025, 5)
                        slip = self.astock.calc_slippage(close, 10, True)
                        actual_cost = shares * slip + comm
                        if actual_cost <= self.capital:
                            self.capital -= actual_cost
                            pos = Position(
                                code=code, size=shares, cost_basis=slip,
                                entry_date=trade_date, entry_price=slip,
                                highest_price=close, current_price=close,
                                market_value=shares * close,
                            )

            # === 记录每日净值 ===
            total_value = self.capital + pos.market_value if pos.size > 0 else self.capital
            self.equity_curve.append({
                "date": trade_date, "code": code,
                "capital": round(self.capital, 2),
                "position_value": round(pos.market_value, 2) if pos.size > 0 else 0,
                "total": round(total_value, 2),
            })

    def _close_position(
        self, pos: Position, exit_price: float,
        exit_date: str, reason: str, atr: float,
    ):
        """平仓"""
        slippage = self.astock.calc_slippage(exit_price, 10, False)
        sell_amount = pos.size * slippage
        buy_amount = pos.size * pos.cost_basis

        comm = max(buy_amount * 0.00025, 5) + max(sell_amount * 0.00025, 5)
        tax = sell_amount * 0.0005
        total_cost = comm + tax

        net_pnl = sell_amount - buy_amount - total_cost
        pnl_pct = (slippage / pos.cost_basis - 1) * 100

        entry_dt = datetime.strptime(pos.entry_date, "%Y-%m-%d")
        exit_dt = datetime.strptime(exit_date, "%Y-%m-%d")
        holding_days = (exit_dt - entry_dt).days

        self.capital += sell_amount - comm - tax

        self.trades.append(TradeRecord(
            trade_id=uuid.uuid4().hex[:8],
            run_id=self.run_id,
            code=pos.code,
            direction="SELL",
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
        ))

    def _calc_performance(self, result: SimulationResult):
        """计算绩效指标"""
        if not self.equity_curve:
            result.final_capital = self.capital
            return

        start_val = result.initial_capital
        end_val = self.equity_curve[-1]["total"]
        result.final_capital = end_val

        total_return = (end_val / start_val - 1) * 100
        result.total_return = round(total_return, 2)

        # 年化收益
        days = len(self.equity_curve)
        if days > 0:
            annual = ((1 + total_return / 100) ** (252 / max(days, 1)) - 1) * 100
            result.annual_return = round(annual, 2)

        # 简单夏普（假设无风险利率0）
        returns = []
        prev_total = start_val
        for pt in self.equity_curve:
            ret = (pt["total"] / prev_total - 1) if prev_total > 0 else 0
            returns.append(ret)
            prev_total = pt["total"]

        if len(returns) > 1:
            import numpy as np
            avg_ret = np.mean(returns)
            std_ret = np.std(returns, ddof=0)
            if std_ret > 0:
                result.sharpe_ratio = round((avg_ret / std_ret) * (252 ** 0.5), 2)

        # 最大回撤
        peak = start_val
        max_dd = 0
        for pt in self.equity_curve:
            if pt["total"] > peak:
                peak = pt["total"]
            dd = (peak - pt["total"]) / peak * 100
            max_dd = max(max_dd, dd)
        result.max_drawdown = round(max_dd, 2)

        # 胜率
        closed_trades = [t for t in self.trades if t.direction == "SELL"]
        result.total_trades = len(closed_trades)
        if closed_trades:
            wins = sum(1 for t in closed_trades if t.net_pnl > 0)
            result.win_rate = round(wins / len(closed_trades) * 100, 1)

            # 盈亏比
            total_win = sum(t.net_pnl for t in closed_trades if t.net_pnl > 0)
            total_loss = sum(abs(t.net_pnl) for t in closed_trades if t.net_pnl <= 0)
            if total_loss > 0:
                result.profit_factor = round(total_win / total_loss, 2)

    def _save_to_db(self, result: SimulationResult):
        """保存结果到 DB (用 SQLAlchemy engine, 统一连接管理)"""
        from ..db.engine import get_engine
        from sqlalchemy import text as _t

        engine = get_engine()

        # 建表（如不存在）
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
        with engine.connect() as conn:
            for ddl in _DDL:
                conn.execute(_t(ddl))
            conn.commit()

        with engine.connect() as conn:
            # 写入 simulation 主表
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

            # 写入交易记录
            for t in self.trades:
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

            # 写入净值曲线（抽样：每5条存1条）
            for i, pt in enumerate(self.equity_curve):
                if i % 5 == 0:
                    conn.execute(
                        _t(
                            "INSERT INTO simulation_equity "
                            "(run_id, date, code, capital, position_value, total) "
                            "VALUES (:run_id, :date, :code, :capital, "
                            ":position_value, :total)"
                        ),
                        {
                            "run_id": result.run_id,
                            "date": pt["date"],
                            "code": pt["code"],
                            "capital": pt["capital"],
                            "position_value": pt["position_value"],
                            "total": pt["total"],
                        },
                    )

            conn.commit()

