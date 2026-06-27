"""
RiskEngine 测试共享 fixtures (VNPY-3, 2026-06-27)

集中度第二波 (#77) 拆分 test_risk_engine.py 时, 把 DummyOrder / DummyTrade
等本地 fixture 提到此 helper, 避免重复 (tests/ 不放 conftest 是为了不污染其他测试)。

用法:
    from tests._risk_fixtures import DummyOrder, DummyTrade, DummyAccount, DummyOrderData, freeze_today
"""
from __future__ import annotations

from datetime import date

from src.gateway.object import OrderStatus


def freeze_today(risk) -> None:
    """阻止 _ensure_daily_reset 把测试注入的统计数据清零"""
    risk._today = date.today()


class DummyOrder:
    """兼容 OrderRequest 的最小下单请求 (无 status, 走 on_trade)"""

    def __init__(self, volume, price, vt_symbol="000001.SZ"):
        self.volume = volume
        self.price = price
        self.vt_symbol = vt_symbol


class DummyOrderData:
    """OrderData-like, 含 status (供 on_order ALLTRADED 检查)"""

    def __init__(
        self,
        status: OrderStatus = OrderStatus.ALLTRADED,
        vt_symbol: str = "000001.SZ",
    ):
        self.status = status
        self.symbol = vt_symbol.split(".")[0]
        self.exchange = vt_symbol.split(".")[1] if "." in vt_symbol else "SZ"
        self.vt_symbol = vt_symbol
        self.volume = 100
        self.price = 10.0


class DummyTrade:
    def __init__(self, volume=100, price=50.0, vt_symbol="000001.SZ", pnl=0.0):
        self.volume = volume
        self.price = price
        self.vt_symbol = vt_symbol
        self.pnl = pnl


class DummyAccount:
    """AccountData-like, balance 字段"""

    def __init__(self, balance: float = 0.0):
        self.balance = balance


__all__ = [
    "freeze_today",
    "DummyOrder",
    "DummyOrderData",
    "DummyTrade",
    "DummyAccount",
]