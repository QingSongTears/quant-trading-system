"""
A股规则策略基类
==============
在 backtesting.py Strategy 之上包装 A 股特色规则：
- T+1 交易：今日买入次日才能卖出
- 涨跌停限制：涨停买不进，跌停卖不出
- 佣金：默认万 2.5，最低 5 元
- 印花税：卖出时千 0.5
- 100 股整数倍
- 滑点：默认 5bps
"""

from __future__ import annotations
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_prev_close(code: str, trade_date: str) -> Optional[float]:
    """获取前一日收盘价（用于计算涨跌停价格）"""
    try:
        db = PROJECT_ROOT / "database" / "quant.db"
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT close FROM daily_price WHERE code=? AND trade_date<? "
            "ORDER BY trade_date DESC LIMIT 1",
            (code, trade_date)
        ).fetchone()
        conn.close()
        return float(row[0]) if row else None
    except Exception:
        return None


def get_stock_atr(code: str, trade_date: str, period: int = 14) -> Optional[float]:
    """从 technical_indicators 表获取 ATR 值"""
    try:
        db = PROJECT_ROOT / "database" / "quant.db"
        conn = sqlite3.connect(str(db))
        # 尝试从 technical_indicators 获取
        row = conn.execute(
            "SELECT atr FROM technical_indicators WHERE code=? AND trade_date<=? "
            "ORDER BY trade_date DESC LIMIT 1",
            (code, trade_date)
        ).fetchone()
        conn.close()
        if row and row[0]:
            return float(row[0])
        return None
    except Exception:
        return None


def calc_commission(amount: float, rate: float = 0.00025, minimum: float = 5.0) -> float:
    """佣金计算：万 rate，最低 minimum 元"""
    return max(amount * rate, minimum)


def calc_stamp_tax(amount: float, rate: float = 0.0005) -> float:
    """印花税：卖出时千 rate（卖出才收）"""
    return amount * rate


def round_lot(shares: int) -> int:
    """向下取整到 100 的整数倍"""
    return (shares // 100) * 100


def calc_limit_prices(prev_close: float, board: str = "main") -> tuple[float, float]:
    """计算涨跌停价格
    
    Args:
        prev_close: 前收盘价
        board: main=主板±10%, star=科创板±20%, gem=创业板±20%, bj=北交所±30%
    Returns:
        (limit_up, limit_down)
    """
    limits = {"main": 0.1, "star": 0.2, "gem": 0.2, "bj": 0.3}
    pct = limits.get(board, 0.1)
    limit_up = round(prev_close * (1 + pct), 2)
    limit_down = round(prev_close * (1 - pct), 2)
    return limit_up, limit_down


class AStockRules:
    """A 股规则检查器 — 无状态，纯函数集合"""

    @staticmethod
    def check_t1(entry_date: str, current_date: str) -> bool:
        """T+1 检查：今日 >= 买入日次日 才能卖出"""
        from datetime import datetime as dt
        entry = dt.strptime(entry_date, "%Y-%m-%d").date()
        current = dt.strptime(current_date, "%Y-%m-%d").date()
        return current > entry  # 买入次日及之后才能卖

    @staticmethod
    def check_limit_up(price: float, prev_close: float, board: str = "main") -> bool:
        """涨停检查：price >= limit_up 时无法买入"""
        limit_up, _ = calc_limit_prices(prev_close, board)
        return price >= limit_up

    @staticmethod
    def check_limit_down(price: float, prev_close: float, board: str = "main") -> bool:
        """跌停检查：price <= limit_down 时无法卖出"""
        _, limit_down = calc_limit_prices(prev_close, board)
        return price <= limit_down

    @staticmethod
    def calc_slippage(price: float, bps: float = 10.0, is_buy: bool = True) -> float:
        """滑点：买入上浮 bps，卖出下浮 bps（默认 10bps=0.1%）"""
        if is_buy:
            return price * (1 + bps / 10000)
        else:
            return price * (1 - bps / 10000)

    @staticmethod
    def can_buy(
        price: float, prev_close: float, board: str = "main",
        is_limit_up: bool = False,
    ) -> tuple[bool, str]:
        """是否可以买入"""
        if is_limit_up or AStockRules.check_limit_up(price, prev_close, board):
            return False, "涨停或涨停附近，无法买入"
        return True, ""

    @staticmethod
    def can_sell(
        price: float, prev_close: float, board: str = "main",
        is_limit_down: bool = False,
    ) -> tuple[bool, str]:
        """是否可以卖出"""
        if is_limit_down or AStockRules.check_limit_down(price, prev_close, board):
            return False, "跌停或跌停附近，无法卖出"
        return True, ""

    @staticmethod
    def calc_trade_cost(
        buy_amount: float,
        sell_amount: float,
        commission_rate: float = 0.00025,
        stamp_tax_rate: float = 0.0005,
    ) -> dict:
        """计算交易成本"""
        buy_comm = calc_commission(buy_amount, commission_rate)
        sell_comm = calc_commission(sell_amount, commission_rate)
        stamp_tax = calc_stamp_tax(sell_amount, stamp_tax_rate)
        total_cost = buy_comm + sell_comm + stamp_tax
        return {
            "buy_commission": round(buy_comm, 2),
            "sell_commission": round(sell_comm, 2),
            "stamp_tax": round(stamp_tax, 2),
            "total_cost": round(total_cost, 2),
        }
