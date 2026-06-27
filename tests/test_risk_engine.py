"""测试: VNPY-3 — RiskEngine 风控骨架"""
import pytest
from unittest.mock import MagicMock

from src.risk.engine import RiskEngine, RiskConfig


class DummyOrder:
    def __init__(self, volume, price, vt_symbol="000001.SZ"):
        self.volume = volume
        self.price = price
        self.vt_symbol = vt_symbol


class TestRiskConfig:
    def test_defaults(self):
        c = RiskConfig()
        assert c.max_order_pct == 0.20
        assert c.max_daily_trades == 50

    def test_custom(self):
        c = RiskConfig(max_order_pct=0.10, max_daily_trades=10)
        assert c.max_order_pct == 0.10
        assert c.max_daily_trades == 10


class TestRiskEngine:
    def test_init(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        assert risk.config.max_order_pct == 0.20

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

    def test_check_daily_limit_passes(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        ok, msg = risk.check_daily_limit()
        assert ok

    def test_get_stats(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        stats = risk.get_stats()
        assert "daily_trades" in stats
        assert stats["daily_trades"] == 0
