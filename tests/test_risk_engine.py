"""测试: VNPY-3 — RiskEngine 风控骨架"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from src.risk.engine import RiskEngine, RiskConfig
from src.event import Event, EVENT_ORDER, EVENT_TRADE


def _freeze_today(risk: RiskEngine) -> None:
    """阻止 _ensure_daily_reset 把测试注入的统计数据清零"""
    risk._today = date.today()


class DummyOrder:
    def __init__(self, volume, price, vt_symbol="000001.SZ"):
        self.volume = volume
        self.price = price
        self.vt_symbol = vt_symbol


class DummyTrade:
    def __init__(self, volume=100, price=50.0, vt_symbol="000001.SZ", pnl=0.0):
        self.volume = volume
        self.price = price
        self.vt_symbol = vt_symbol
        self.pnl = pnl


class TestRiskConfig:
    def test_defaults(self):
        c = RiskConfig()
        assert c.max_order_pct == 0.20
        assert c.max_daily_trades == 50
        assert c.max_daily_drawdown_pct == 5.0

    def test_custom(self):
        c = RiskConfig(max_order_pct=0.10, max_daily_trades=10)
        assert c.max_order_pct == 0.10
        assert c.max_daily_trades == 10


class TestRiskEngine:
    def test_init(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        assert risk.config.max_order_pct == 0.20
        event_engine.register.assert_any_call(EVENT_ORDER, risk.on_order)
        event_engine.register.assert_any_call(EVENT_TRADE, risk.on_trade)

    def test_check_order_passes(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_order_volume=10000))
        req = DummyOrder(volume=100, price=50)
        ok, msg = risk.check_order(req)
        assert ok
        assert msg == ""

    def test_check_order_fails_volume(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_order_volume=1000))
        req = DummyOrder(volume=5000, price=50)
        ok, msg = risk.check_order(req)
        assert not ok
        assert "超单笔最大股数" in msg

    def test_check_order_fails_amount(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_order_amount=100_000))
        req = DummyOrder(volume=1000, price=200)  # 20 万
        ok, msg = risk.check_order(req)
        assert not ok
        assert "超单笔最大金额" in msg

    def test_check_order_fails_positions(self):
        """新开仓受 max_positions 限制, 已持仓的股票可加仓"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_positions=2))
        risk._positions = {"000002.SZ": 100, "000003.SZ": 100, "000004.SZ": 100}
        # 第四只新开仓应失败
        req = DummyOrder(volume=100, price=10, vt_symbol="000005.SZ")
        ok, msg = risk.check_order(req)
        assert not ok
        assert "持仓数" in msg

    def test_add_to_existing_position_passes(self):
        """已持仓的股票加仓不受 max_positions 限制"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_positions=2, max_order_volume=10000))
        risk._positions = {"000001.SZ": 100, "000002.SZ": 100}
        # 给 000001 加仓
        req = DummyOrder(volume=100, price=10, vt_symbol="000001.SZ")
        ok, _ = risk.check_order(req)
        assert ok

    def test_check_daily_limit_passes(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        ok, msg = risk.check_daily_limit()
        assert ok

    def test_check_daily_limit_fails_trades(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_daily_trades=3))
        _freeze_today(risk)
        risk._daily_trades = 5  # 已超
        ok, msg = risk.check_daily_limit()
        assert not ok
        assert "交易次数" in msg

    def test_check_daily_limit_fails_loss(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_daily_loss=1000))
        _freeze_today(risk)
        risk._daily_pnl = -2000  # 亏损超限
        ok, msg = risk.check_daily_limit()
        assert not ok
        assert "亏损" in msg

    def test_check_daily_limit_drawdown_circuit_breaker(self):
        """日回撤熔断: 净值从 peak 跌超 max_daily_drawdown_pct"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_daily_drawdown_pct=5.0))
        _freeze_today(risk)
        risk._daily_peak = 100_000
        risk._daily_pnl = -8000  # -8%
        ok, msg = risk.check_daily_limit()
        assert not ok
        assert "回撤" in msg

    def test_on_order_increments_daily(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_order(Event(EVENT_ORDER))
        risk.on_order(Event(EVENT_ORDER))
        assert risk._daily_trades == 2

    def test_on_trade_updates_position(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(volume=100, price=10, vt_symbol="000001.SZ")))
        assert risk._positions["000001.SZ"] == 100
        # 再来一笔 200 股
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(volume=200, price=10, vt_symbol="000001.SZ")))
        assert risk._positions["000001.SZ"] == 300

    def test_on_trade_updates_pnl_and_peak(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(pnl=500)))
        assert risk._daily_pnl == 500
        assert risk._daily_peak == 500
        # 后续亏损不拉低 peak
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(pnl=-200)))
        assert risk._daily_pnl == 300
        assert risk._daily_peak == 500

    def test_daily_reset_crosses_midnight(self):
        """日期切换时 daily 统计应自动复位"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_daily_trades=3))
        risk._today = date(2020, 1, 1)  # 旧日期
        risk._daily_trades = 999
        # 调用任意方法触发 _ensure_daily_reset
        ok, _ = risk.check_daily_limit()
        assert ok  # 应该复位并通过
        assert risk._daily_trades == 0

    def test_get_stats(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        stats = risk.get_stats()
        assert "daily_trades" in stats
        assert stats["daily_trades"] == 0
        assert "max_daily_trades" in stats
        assert "position_count" in stats

    def test_event_registration_uses_correct_types(self):
        """确认订阅的是 EVENT_ORDER / EVENT_TRADE, 不是字符串"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        call_args = [c.args for c in event_engine.register.call_args_list]
        event_types = [a[0] for a in call_args]
        assert EVENT_ORDER in event_types
        assert EVENT_TRADE in event_types
