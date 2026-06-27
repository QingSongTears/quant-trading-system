"""
simulator.signal_adapter — 信号适配 (ADR-0011 #82)

从 simulator.py 拆分:
  - SignalAdapter (从 DB backtest_result 解析 equity_curve)
  - Signal dataclass
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class Signal:
    """统一信号格式"""
    code: str
    date: str          # YYYY-MM-DD
    action: str        # BUY / SELL / HOLD
    price: float       # 信号发出时价格
    confidence: float  # 0-1
    source: str        # 模型名称


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

        signals: list[Signal] = []
        for row in rows:
            try:
                curve = json.loads(row["equity_curve"]) if row["equity_curve"] else []
                for pt in curve:
                    if isinstance(pt, dict) and "trade" in pt:
                        t = pt["trade"]
                        signals.append(Signal(
                            code=code,
                            date=pt.get("date", ""),
                            action=t.get("type", ""),
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
        """获取单日信号——简化为根据回测结果区间判断

        返回一个中立 HOLD 信号 (简化实现, 可作为后续接入真实模型的骨架).
        """
        return Signal(
            code=code, date=trade_date, action="HOLD",
            price=0, confidence=0, source=f"strategy_{strategy_id}",
        )


__all__ = ["Signal", "SignalAdapter"]