"""
风控层 — 止损、止盈、仓位管理
"""

import pandas as pd
from typing import Dict, List
from ..config import (
    STOP_LOSS_PCT, QUANT_TAKE_PROFIT_PCT, NORMAL_TAKE_PROFIT_PCT,
    MAX_POSITIONS, MAX_SINGLE_POSITION_PCT, MAX_TOTAL_POSITION_PCT,
    NORMAL_BREAK_EMA_EXIT, QUANT_MAX_HOLD_DAYS,
)


class RiskManager:
    """风控管理器"""

    def __init__(self, total_capital: float = 1000000):  # 默认 100 万
        self.total_capital = total_capital
        self.positions: List[Dict] = []
        self.max_positions = MAX_POSITIONS
        self.max_single_pct = MAX_SINGLE_POSITION_PCT
        self.max_total_pct = MAX_TOTAL_POSITION_PCT

    def can_open_position(self) -> bool:
        """是否还能开仓"""
        return len(self.positions) < self.max_positions

    def calc_position_size(self, is_quant_stock: bool = False) -> float:
        """
        计算单票可买金额
        - 正常票：总资金的 20%
        - 量化票：总资金的 15%（降低仓位）
        """
        pct = self.max_single_pct * (0.75 if is_quant_stock else 1.0)
        return self.total_capital * pct

    def get_stop_loss_price(self, entry_price: float) -> float:
        """计算止损价"""
        return entry_price * (1 + STOP_LOSS_PCT)  # STOP_LOSS_PCT 是负数

    def get_take_profit_price(self, entry_price: float, is_quant_stock: bool = False) -> float:
        """计算止盈价"""
        pct = QUANT_TAKE_PROFIT_PCT if is_quant_stock else NORMAL_TAKE_PROFIT_PCT
        return entry_price * (1 + pct)

    def should_exit(self, code: str, current_price: float, ema20: float = None) -> Dict:
        """
        检查持仓是否需要离场
        返回：{should_exit: bool, reason: str}
        """
        position = None
        for p in self.positions:
            if p["code"] == code:
                position = p
                break

        if not position:
            return {"should_exit": False, "reason": "无持仓"}

        entry = position["entry_price"]
        is_quant = position.get("is_quant_stock", False)
        hold_days = position.get("hold_days", 0)

        # 止损检查
        stop_price = self.get_stop_loss_price(entry)
        if current_price <= stop_price:
            loss_pct = (current_price - entry) / entry * 100
            return {
                "should_exit": True,
                "reason": f"🛑 止损触发 (买入{entry}, 现价{current_price}, 亏损{loss_pct:.1f}%)",
            }

        # 止盈检查
        tp_price = self.get_take_profit_price(entry, is_quant)
        if current_price >= tp_price:
            gain_pct = (current_price - entry) / entry * 100
            return {
                "should_exit": True,
                "reason": f"✅ 止盈触发 (买入{entry}, 现价{current_price}, 盈利{gain_pct:.1f}%)",
            }

        # 量化票持仓超时
        if is_quant and hold_days >= QUANT_MAX_HOLD_DAYS:
            return {
                "should_exit": True,
                "reason": f"⏰ 量化票持仓满{hold_days}日，到期离场",
            }

        # 正常票跌破 EMA20
        if NORMAL_BREAK_EMA_EXIT and not is_quant and ema20:
            if current_price < ema20:
                return {
                    "should_exit": True,
                    "reason": f"📉 跌破EMA20({ema20:.2f})离场",
                }

        return {"should_exit": False, "reason": "继续持有"}

    def add_position(self, code: str, entry_price: float, shares: int,
                     is_quant_stock: bool = False) -> Dict:
        """添加持仓"""
        position = {
            "code": code,
            "entry_price": entry_price,
            "shares": shares,
            "is_quant_stock": is_quant_stock,
            "hold_days": 0,
            "stop_loss": self.get_stop_loss_price(entry_price),
            "take_profit": self.get_take_profit_price(entry_price, is_quant_stock),
        }
        self.positions.append(position)
        return position

    def update_positions(self, current_prices: Dict[str, float]):
        """更新持仓天数"""
        for p in self.positions:
            if p["code"] in current_prices:
                p["hold_days"] += 1
                p["current_price"] = current_prices[p["code"]]
                p["unrealized_pnl_pct"] = (
                    (p["current_price"] - p["entry_price"]) / p["entry_price"] * 100
                )

    def get_summary(self) -> Dict:
        """持仓摘要"""
        if not self.positions:
            return {"total_positions": 0, "positions": []}

        return {
            "total_positions": len(self.positions),
            "positions": [
                {
                    "code": p["code"],
                    "entry": p["entry_price"],
                    "current": p.get("current_price", p["entry_price"]),
                    "pnl_pct": round(p.get("unrealized_pnl_pct", 0), 2),
                    "hold_days": p["hold_days"],
                    "is_quant": p["is_quant_stock"],
                }
                for p in self.positions
            ],
        }
