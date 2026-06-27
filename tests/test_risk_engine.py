"""测试: VNPY-3 — RiskEngine 风控骨架 (含 ADR-0007 修复)"""
from datetime import date
from unittest.mock import MagicMock

import pytest

from src.risk.engine import RiskEngine, RiskConfig
from src.event import Event, EVENT_ORDER, EVENT_TRADE, EVENT_ACCOUNT
from src.gateway.object import OrderStatus


def _freeze_today(risk: RiskEngine) -> None:
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


# ═════════════════════════════════════════════════════════════
#  RiskConfig
# ═════════════════════════════════════════════════════════════
class TestRiskConfig:
    def test_defaults(self):
        """默认阈值 (PR2.2 起小数化, ADR-0007 修复 2)"""
        c = RiskConfig()
        assert c.max_order_pct == 0.20
        assert c.max_daily_trades == 50
        assert c.max_daily_drawdown == 0.05  # 5% → 0.05 小数
        assert c.initial_balance == 0.0      # 兜底默认 0

    def test_custom(self):
        c = RiskConfig(
            max_order_pct=0.10,
            max_daily_trades=10,
            max_daily_drawdown=0.03,
            initial_balance=200_000,
        )
        assert c.max_order_pct == 0.10
        assert c.max_daily_drawdown == 0.03
        assert c.initial_balance == 200_000


# ═════════════════════════════════════════════════════════════
#  RiskEngine.__init__
# ═════════════════════════════════════════════════════════════
class TestRiskEngine:
    def test_init(self):
        """__init__ 订阅 EVENT_ORDER / EVENT_TRADE / EVENT_ACCOUNT"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        assert risk.config.max_order_pct == 0.20
        registered = [c.args[0] for c in event_engine.register.call_args_list]
        assert EVENT_ORDER in registered
        assert EVENT_TRADE in registered
        assert EVENT_ACCOUNT in registered

    def test_event_registration_uses_correct_types(self):
        """确认订阅的是 EVENT_* 常量, 不是字符串"""
        event_engine = MagicMock()
        RiskEngine(event_engine)
        call_args = [c.args for c in event_engine.register.call_args_list]
        event_types = [a[0] for a in call_args]
        assert EVENT_ORDER in event_types
        assert EVENT_TRADE in event_types


# ═════════════════════════════════════════════════════════════
#  check_order — 单笔控制 (4 步)
# ═════════════════════════════════════════════════════════════
class TestCheckOrderVolume:
    """步骤 1: 单笔股数上限"""

    def test_passes_under_limit(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_order_volume=10000))
        req = DummyOrder(volume=100, price=50)
        ok, msg = risk.check_order(req)
        assert ok and msg == ""

    def test_fails_over_limit(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_order_volume=1000))
        req = DummyOrder(volume=5000, price=50)
        ok, msg = risk.check_order(req)
        assert not ok and "超单笔最大股数" in msg


class TestCheckOrderAmount:
    """步骤 2: 单笔金额上限"""

    def test_passes_under_limit(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_order_amount=10_000_000))
        req = DummyOrder(volume=1000, price=50)  # 5万
        ok, _ = risk.check_order(req)
        assert ok

    def test_fails_over_limit(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_order_amount=100_000))
        req = DummyOrder(volume=1000, price=200)  # 20万
        ok, msg = risk.check_order(req)
        assert not ok and "超单笔最大金额" in msg


class TestCheckOrderPositionCount:
    """步骤 3: 最大持仓数 (新开仓受限, 已持仓加仓放行)"""

    def test_fails_new_position_over_limit(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_positions=2))
        risk._positions = {
            "000002.SZ": 100, "000003.SZ": 100, "000004.SZ": 100,
        }
        req = DummyOrder(volume=100, price=10, vt_symbol="000005.SZ")
        ok, msg = risk.check_order(req)
        assert not ok and "持仓数" in msg

    def test_passes_add_to_existing(self):
        """已持仓的股票加仓不受 max_positions 限制"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(max_positions=2, max_order_volume=10000),
        )
        risk._positions = {"000001.SZ": 100, "000002.SZ": 100}
        req = DummyOrder(volume=100, price=10, vt_symbol="000001.SZ")
        ok, _ = risk.check_order(req)
        assert ok


class TestCheckOrderPctLimit:
    """步骤 4: 单股仓位比例 (ADR-0007 修复 2 新增)"""

    def test_passes_when_balance_zero(self):
        """账户余额未知 (initial_balance=0) → 跳过比例校验, 不报错"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(initial_balance=0))
        # amount=1M, 在 max_order_amount=5M 之内, 只剩比例校验
        req = DummyOrder(volume=1000, price=1000, vt_symbol="000001.SZ")
        ok, msg = risk.check_order(req)
        # 通过: 没有余额就跳过比例校验
        assert ok, msg

    def test_fails_over_pct(self):
        """amount/account > max_order_pct → 拒绝"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(initial_balance=100_000, max_order_pct=0.20),
        )
        # 50,000 / 100,000 = 50% > 20%
        req = DummyOrder(volume=1000, price=50, vt_symbol="000001.SZ")
        ok, msg = risk.check_order(req)
        assert not ok and "仓位比例" in msg

    def test_passes_under_pct(self):
        """amount/account <= max_order_pct → 通过"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(initial_balance=1_000_000, max_order_pct=0.20),
        )
        # 50,000 / 1,000,000 = 5% < 20%
        req = DummyOrder(volume=1000, price=50, vt_symbol="000001.SZ")
        ok, _ = risk.check_order(req)
        assert ok


# ═════════════════════════════════════════════════════════════
#  check_daily_limit — 日内熔断
# ═════════════════════════════════════════════════════════════
class TestCheckDailyLimit:
    def test_passes_when_clean(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        ok, msg = risk.check_daily_limit()
        assert ok and msg == ""

    def test_fails_trades(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_daily_trades=3))
        _freeze_today(risk)
        risk._daily_trades = 5
        ok, msg = risk.check_daily_limit()
        assert not ok and "交易次数" in msg

    def test_fails_loss(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_daily_loss=1000))
        _freeze_today(risk)
        risk._daily_pnl = -2000
        ok, msg = risk.check_daily_limit()
        assert not ok and "亏损" in msg

    def test_drawdown_circuit_breaker(self):
        """日回撤熔断 (小数化后: max_daily_drawdown=0.05 = 5%)"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_daily_drawdown=0.05))
        _freeze_today(risk)
        risk._daily_peak = 100_000
        risk._daily_pnl = -8000  # (100k - (-8k)) / 100k = 108% 远超 5%
        ok, msg = risk.check_daily_limit()
        assert not ok and "回撤" in msg

    def test_drawdown_decimal_threshold(self):
        """小数阈值: 0.03 (3%) 边界正确"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_daily_drawdown=0.03))
        _freeze_today(risk)
        risk._daily_peak = 100_000
        risk._daily_pnl = 96_500  # 回撤 3.5% > 3%
        ok, msg = risk.check_daily_limit()
        assert not ok and "回撤" in msg

    def test_drawdown_below_threshold_passes(self):
        """回撤未达阈值 → 通过"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_daily_drawdown=0.05))
        _freeze_today(risk)
        risk._daily_peak = 100_000
        risk._daily_pnl = 96_000  # 回撤 4% < 5%
        ok, _ = risk.check_daily_limit()
        assert ok


# ═════════════════════════════════════════════════════════════
#  on_order — 仅 ALLTRADED 累加 (ADR-0007 修复 4)
# ═════════════════════════════════════════════════════════════
class TestOnOrderOnlyAllTraded:
    def test_alltraded_increments(self):
        """ALLTRADED 状态 → _daily_trades += 1"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_order(Event(EVENT_ORDER, DummyOrderData(status=OrderStatus.ALLTRADED)))
        assert risk._daily_trades == 1

    def test_submitting_no_increment(self):
        """SUBMITTING 状态 → 不累加"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_order(Event(EVENT_ORDER, DummyOrderData(status=OrderStatus.SUBMITTING)))
        assert risk._daily_trades == 0

    def test_nottraded_no_increment(self):
        """NOTTRADED (挂单中) → 不累加"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_order(Event(EVENT_ORDER, DummyOrderData(status=OrderStatus.NOTTRADED)))
        assert risk._daily_trades == 0

    def test_parttraded_no_increment(self):
        """PARTTRADED (部分成交) → 不累加 (避免重复计入, 由 on_trade 累加)"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_order(Event(EVENT_ORDER, DummyOrderData(status=OrderStatus.PARTTRADED)))
        assert risk._daily_trades == 0

    def test_cancelled_no_increment(self):
        """CANCELLED (已撤单) → 不累加 (无交易发生)"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_order(Event(EVENT_ORDER, DummyOrderData(status=OrderStatus.CANCELLED)))
        assert risk._daily_trades == 0

    def test_rejected_no_increment(self):
        """REJECTED (已拒绝) → 不累加"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_order(Event(EVENT_ORDER, DummyOrderData(status=OrderStatus.REJECTED)))
        assert risk._daily_trades == 0

    def test_none_data_no_increment(self):
        """event.data=None → 不累加 (容错)"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_order(Event(EVENT_ORDER))
        assert risk._daily_trades == 0

    def test_two_alltraded_increments_two(self):
        """两笔 ALLTRADED → +2"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_order(Event(EVENT_ORDER, DummyOrderData(status=OrderStatus.ALLTRADED)))
        risk.on_order(Event(EVENT_ORDER, DummyOrderData(status=OrderStatus.ALLTRADED)))
        assert risk._daily_trades == 2


# ═════════════════════════════════════════════════════════════
#  on_trade — 持仓 / PnL 维护
# ═════════════════════════════════════════════════════════════
class TestOnTrade:
    def test_updates_position(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(volume=100, vt_symbol="000001.SZ")))
        assert risk._positions["000001.SZ"] == 100
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(volume=200, vt_symbol="000001.SZ")))
        assert risk._positions["000001.SZ"] == 300

    def test_updates_pnl_and_peak(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(pnl=500)))
        assert risk._daily_pnl == 500
        assert risk._daily_peak == 500
        # 后续亏损不拉低 peak
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(pnl=-200)))
        assert risk._daily_pnl == 300
        assert risk._daily_peak == 500


# ═════════════════════════════════════════════════════════════
#  on_account — 余额注入 + 注销回调 (ADR-0007 D1②)
# ═════════════════════════════════════════════════════════════
class TestOnAccount:
    def test_initial_balance_fallback(self):
        """未收到 EVENT_ACCOUNT → 使用 config.initial_balance 兜底"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(initial_balance=200_000),
        )
        assert risk._account_balance == 200_000
        assert risk._account_subscribed is True

    def test_account_event_injects_balance(self):
        """收到有效 EVENT_ACCOUNT → 锁定余额"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(initial_balance=0))
        risk.on_account(Event(EVENT_ACCOUNT, DummyAccount(balance=500_000)))
        assert risk._account_balance == 500_000

    def test_account_event_unregisters(self):
        """首次收到后注销回调 (省 CPU)"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_account(Event(EVENT_ACCOUNT, DummyAccount(balance=100_000)))
        event_engine.unregister.assert_called_once()
        assert risk._account_subscribed is False

    def test_account_zero_balance_keeps_subscribed(self):
        """balance <= 0 → 不锁定, 继续监听"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(initial_balance=10))
        risk.on_account(Event(EVENT_ACCOUNT, DummyAccount(balance=0)))
        assert risk._account_balance == 10  # 保留兜底
        assert risk._account_subscribed is True

    def test_account_none_data_noop(self):
        """event.data=None → 容错返回"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(initial_balance=10_000))
        risk.on_account(Event(EVENT_ACCOUNT))
        assert risk._account_balance == 10_000
        assert risk._account_subscribed is True

    def test_account_unregister_failure_keeps_subscribed(self):
        """注销失败 → 不影响主流程, 继续监听"""
        event_engine = MagicMock()
        event_engine.unregister.side_effect = RuntimeError("mock failure")
        risk = RiskEngine(event_engine)
        # 不抛异常
        risk.on_account(Event(EVENT_ACCOUNT, DummyAccount(balance=100_000)))
        assert risk._account_balance == 100_000
        # 注销失败, 但状态保留 (下次 on_account 会再尝试)


# ═════════════════════════════════════════════════════════════
#  日初复位 / 统计
# ═════════════════════════════════════════════════════════════
class TestDailyReset:
    def test_crosses_midnight_resets(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_daily_trades=3))
        risk._today = date(2020, 1, 1)
        risk._daily_trades = 999
        ok, _ = risk.check_daily_limit()
        assert ok
        assert risk._daily_trades == 0


class TestGetStats:
    def test_returns_expected_keys(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        stats = risk.get_stats()
        assert stats["daily_trades"] == 0
        assert stats["max_daily_trades"] == 50
        assert stats["max_daily_drawdown"] == 0.05
        assert stats["position_count"] == 0
        assert stats["account_balance"] == 0.0
        assert "today" in stats


# ═════════════════════════════════════════════════════════════
#  RiskAlert 数据类 — 见 tests/test_event_data.py
# ═════════════════════════════════════════════════════════════


if __name__ == "__main__":
    pytest.main([__file__, "-v"])