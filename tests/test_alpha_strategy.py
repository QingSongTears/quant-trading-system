"""
AlphaStrategy 单测 — src/strategy/alpha_strategy.py (2026-06-24)

涵盖:
  - 构造 (strategy_engine/strategy_name/vt_symbols/setting)
  - 抽象方法 (on_init/on_bars/on_trade)
  - 默认回调 (on_start/on_stop)
  - 持仓/订单状态维护
  - 委托包装 (buy/sell/cover/short)
  - set_target / execute_trading 自动调仓
  - 撤单 (cancel_order / cancel_all)
  - 资金/持仓查询 (委托给 strategy_engine)
  - write_log / __repr__
"""
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 让 tests/ 可以 import src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.gateway import (
    BarData, Direction, Offset, OrderData, OrderStatus, OrderType, TradeData,
)
from src.strategy import AlphaStrategy


# ── 测试用 StrategyEngine (满足 Protocol) ──────────────────────────


class _MockEngine:
    """满足 StrategyEngine Protocol 的 mock"""
    def __init__(self):
        self.sent_orders = []
        self.cancelled = []
        self._cash = 100000.0
        self._holding = 50000.0
        self._next_id = 0

    def send_order(self, strategy, vt_symbol, direction, offset, price, volume):
        self._next_id += 1
        orderid = f"{self._next_id}"
        vt_orderid = f"MOCK.{orderid}"
        order = OrderData(
            gateway_name="MOCK", symbol=vt_symbol.split(".")[0],
            exchange=vt_symbol.split(".")[1], orderid=orderid,
            direction=direction, offset=offset, order_type=OrderType.LIMIT,
            status=OrderStatus.SUBMITTING, price=price, volume=volume,
        )
        self.sent_orders.append({
            "vt_orderid": vt_orderid,
            "vt_symbol": vt_symbol,
            "direction": direction,
            "offset": offset,
            "price": price,
            "volume": volume,
        })
        return [vt_orderid]  # send_order 返回 List[str]

    def cancel_order(self, strategy, vt_orderid):
        self.cancelled.append(vt_orderid)

    def write_log(self, msg, strategy):
        pass  # 静默

    def get_cash_available(self):
        return self._cash

    def get_holding_value(self):
        return self._holding

    def get_signal(self):
        return None


# ── 测试用 AlphaStrategy 子类 ──────────────────────────


class _TestStrat(AlphaStrategy):
    """满足 ABC 的最小实现 + 记录回调"""
    def __init__(self, engine, name="test", vt_symbols=None, setting=None):
        super().__init__(engine, name, vt_symbols or [], setting)
        self.bars_received = []
        self.trades_received = []
        self.inited = False
        self.started = False
        self.stopped = False

    def on_init(self):
        self.inited = True

    def on_bars(self, bars):
        self.bars_received.append(bars)

    def on_trade(self, trade):
        self.trades_received.append(trade)


def _make_engine_and_strat(name="test", vt_symbols=None, setting=None):
    engine = _MockEngine()
    strat = _TestStrat(engine, name, vt_symbols, setting)
    return engine, strat


def _bar(code, exchange, close, vt_symbol=None):
    if vt_symbol is None:
        vt_symbol = f"{code}.{exchange}"
    return BarData(
        symbol=code, exchange=exchange,
        datetime=datetime(2024, 1, 1),
        close_price=close,
    )


# ── 构造 ──────────────────────────


def test_init_sets_basic_attrs():
    engine, strat = _make_engine_and_strat(name="my_strat", vt_symbols=["000001.SZ"])
    assert strat.strategy_engine is engine
    assert strat.strategy_name == "my_strat"
    assert strat.vt_symbols == ["000001.SZ"]
    assert strat.inited is False
    assert strat.trading is False


def test_init_with_empty_symbols():
    _, strat = _make_engine_and_strat(vt_symbols=[])
    assert strat.vt_symbols == []


def test_init_applies_setting_overrides():
    """setting dict 自动 setattr 同名字段"""
    engine = _MockEngine()
    strat = _TestStrat(engine, "x", [], setting={"strategy_name": "override_name"})
    assert strat.strategy_name == "override_name"


def test_alpha_strategy_cannot_be_instantiated_directly():
    """AlphaStrategy 是 ABC, 不能直接实例化"""
    engine = _MockEngine()
    with pytest.raises(TypeError, match="abstract"):
        AlphaStrategy(engine, "x", [])


# ── 抽象方法 ──────────────────────────


def test_subclass_must_implement_on_init():
    """on_init 未实现, 子类也不能实例化"""
    class Bad(AlphaStrategy):
        def on_bars(self, bars): pass
        def on_trade(self, t): pass
        # 缺 on_init

    engine = _MockEngine()
    with pytest.raises(TypeError, match="abstract"):
        Bad(engine, "x", [])


def test_subclass_must_implement_on_bars():
    class Bad(AlphaStrategy):
        def on_init(self): pass
        def on_trade(self, t): pass
        # 缺 on_bars

    engine = _MockEngine()
    with pytest.raises(TypeError, match="abstract"):
        Bad(engine, "x", [])


def test_subclass_must_implement_on_trade():
    class Bad(AlphaStrategy):
        def on_init(self): pass
        def on_bars(self, bars): pass
        # 缺 on_trade

    engine = _MockEngine()
    with pytest.raises(TypeError, match="abstract"):
        Bad(engine, "x", [])


# ── 默认回调 ──────────────────────────


def test_on_start_logs_only():
    _, strat = _make_engine_and_strat()
    strat.on_start()  # 默认实现不应抛


def test_on_stop_logs_only():
    _, strat = _make_engine_and_strat()
    strat.on_stop()


# ── 委托更新 ──────────────────────────


def test_update_trade_records_to_trades():
    _, strat = _make_engine_and_strat()
    trade = TradeData(
        gateway_name="MOCK", symbol="000001", exchange="SZ",
        orderid="o1", tradeid="t1",
        direction=Direction.LONG, offset=Offset.OPEN,
        price=10.0, volume=100,
    )
    strat.update_trade(trade)
    assert strat.trades_received == [trade]


def test_update_order_keeps_orders_dict():
    """update_order 应把订单存到 orders 字典 (按 vt_orderid)"""
    _, strat = _make_engine_and_strat()
    o = OrderData(
        gateway_name="MOCK", symbol="000001", exchange="SZ",
        orderid="1", status=OrderStatus.NOTTRADED,
    )
    strat.update_order(o)
    assert strat.orders["MOCK.1"] is o


def test_update_order_discards_on_terminal_status():
    """订单变终态 (ALLTRADED/CANCELLED/REJECTED) 从 active 移除

    注: 加入 active 由 send_order 负责, update_order 只管"移除"
    """
    _, strat = _make_engine_and_strat()
    # 模拟 send_order 后已加入 active
    strat.active_orderids.add("MOCK.1")

    # 推终态回报
    o = OrderData(
        gateway_name="MOCK", symbol="000001", exchange="SZ",
        orderid="1", status=OrderStatus.ALLTRADED,
    )
    strat.update_order(o)
    assert "MOCK.1" not in strat.active_orderids


def test_update_order_keeps_active_for_in_progress_status():
    """非终态回报 (NOTTRADED/PARTTRADED) 不应移除 active"""
    _, strat = _make_engine_and_strat()
    strat.active_orderids.add("MOCK.2")
    o = OrderData(
        gateway_name="MOCK", symbol="000001", exchange="SZ",
        orderid="2", status=OrderStatus.NOTTRADED,
    )
    strat.update_order(o)
    assert "MOCK.2" in strat.active_orderids  # 仍是 active


def test_update_order_handles_object_without_is_active():
    """order 对象没 is_active 属性时, 不应抛"""
    _, strat = _make_engine_and_strat()
    # 简单对象, 无 is_active
    fake_order = type("O", (), {"vt_orderid": "X.1"})()
    strat.update_order(fake_order)  # 不应抛
    assert strat.orders["X.1"] is fake_order


# ── 委托包装 buy/sell/cover/short ──────────────────────────


def test_buy_calls_send_order_with_long_open():
    engine, strat = _make_engine_and_strat()
    ids = strat.buy("000001.SZ", price=10.0, volume=100)
    assert len(ids) == 1
    assert engine.sent_orders[0]["direction"] == Direction.LONG
    assert engine.sent_orders[0]["offset"] == Offset.OPEN


def test_sell_calls_send_order_with_short_close():
    engine, strat = _make_engine_and_strat()
    strat.sell("000001.SZ", price=10.5, volume=100)
    assert engine.sent_orders[0]["direction"] == Direction.SHORT
    assert engine.sent_orders[0]["offset"] == Offset.CLOSE


def test_cover_calls_send_order_with_long_close():
    engine, strat = _make_engine_and_strat()
    strat.cover("000001.SZ", price=10.0, volume=100)
    assert engine.sent_orders[0]["direction"] == Direction.LONG
    assert engine.sent_orders[0]["offset"] == Offset.CLOSE


def test_short_calls_send_order_with_short_open():
    engine, strat = _make_engine_and_strat()
    strat.short("000001.SZ", price=10.0, volume=100)
    assert engine.sent_orders[0]["direction"] == Direction.SHORT
    assert engine.sent_orders[0]["offset"] == Offset.OPEN


# ── 撤单 ──────────────────────────


def test_cancel_order():
    engine, strat = _make_engine_and_strat()
    # 先下单
    strat.buy("000001.SZ", 10.0, 100)
    # 撤单
    strat.cancel_order("order_1")
    assert "order_1" in engine.cancelled


def test_cancel_all_active_orders():
    engine, strat = _make_engine_and_strat()
    # 下 3 单 (send_order 会把 vt_orderid 加到 active_orderids)
    strat.buy("000001.SZ", 10.0, 100)
    strat.buy("000002.SZ", 20.0, 200)
    strat.buy("000003.SZ", 30.0, 300)
    # 撤全部
    strat.cancel_all()
    # send_order mock 返回 ["MOCK.1"], ["MOCK.2"], ["MOCK.3"]
    assert set(engine.cancelled) == {"MOCK.1", "MOCK.2", "MOCK.3"}


def test_cancel_all_no_active():
    engine, strat = _make_engine_and_strat()
    strat.cancel_all()  # 不应抛
    assert engine.cancelled == []


# ── 持仓/目标 ──────────────────────────


def test_get_pos_returns_zero_initially():
    _, strat = _make_engine_and_strat()
    assert strat.get_pos("000001.SZ") == 0.0


def test_set_target_stores_value():
    _, strat = _make_engine_and_strat()
    strat.set_target("000001.SZ", 1000.0)
    assert strat.get_target("000001.SZ") == 1000.0


def test_set_target_can_overwrite():
    _, strat = _make_engine_and_strat()
    strat.set_target("000001.SZ", 1000.0)
    strat.set_target("000001.SZ", 500.0)
    assert strat.get_target("000001.SZ") == 500.0


# ── execute_trading 自动调仓 ──────────────────────────


def test_execute_trading_no_diff_skips():
    """target == pos, 不下单"""
    engine, strat = _make_engine_and_strat()
    strat.set_target("000001.SZ", 1000.0)
    strat.pos_data["000001.SZ"] = 1000.0
    bar = _bar("000001", "SZ", close=10.0)
    strat.execute_trading({"000001.SZ": bar})
    assert engine.sent_orders == []


def test_execute_trading_buy_to_increase():
    """pos=0, target=1000 → buy 1000"""
    engine, strat = _make_engine_and_strat()
    strat.set_target("000001.SZ", 1000.0)
    bar = _bar("000001", "SZ", close=10.0)
    strat.execute_trading({"000001.SZ": bar})
    assert len(engine.sent_orders) == 1
    assert engine.sent_orders[0]["direction"] == Direction.LONG
    assert engine.sent_orders[0]["volume"] == 1000
    # 委托价 = close * (1 + 0.001) = 10.01
    assert abs(engine.sent_orders[0]["price"] - 10.01) < 0.0001


def test_execute_trading_sell_to_decrease():
    """pos=1000, target=500 → sell 500"""
    engine, strat = _make_engine_and_strat()
    strat.set_target("000001.SZ", 500.0)
    strat.pos_data["000001.SZ"] = 1000.0
    bar = _bar("000001", "SZ", close=10.0)
    strat.execute_trading({"000001.SZ": bar})
    assert engine.sent_orders[0]["direction"] == Direction.SHORT
    assert engine.sent_orders[0]["volume"] == 500


def test_execute_trading_cancels_all_first():
    """execute_trading 调 cancel_all"""
    engine, strat = _make_engine_and_strat()
    # 已有 active 单
    strat.active_orderids.add("order_old")
    strat.set_target("000001.SZ", 500.0)
    bar = _bar("000001", "SZ", close=10.0)
    strat.execute_trading({"000001.SZ": bar})
    assert "order_old" in engine.cancelled


# ── 资金/持仓查询 ──────────────────────────


def test_get_cash_available_delegates_to_engine():
    engine, strat = _make_engine_and_strat()
    engine._cash = 12345.0
    assert strat.get_cash_available() == 12345.0


def test_get_holding_value_delegates_to_engine():
    engine, strat = _make_engine_and_strat()
    engine._holding = 67890.0
    assert strat.get_holding_value() == 67890.0


def test_get_portfolio_value_sums_cash_and_holding():
    engine, strat = _make_engine_and_strat()
    engine._cash = 100.0
    engine._holding = 200.0
    assert strat.get_portfolio_value() == 300.0


# ── write_log / __repr__ ──────────────────────────


def test_write_log_delegates_to_engine():
    engine = _MockEngine()
    strat = _TestStrat(engine, "x", [])
    # 不应抛
    strat.write_log("hello")


def test_repr_includes_class_and_name():
    _, strat = _make_engine_and_strat(name="v6")
    r = repr(strat)
    assert "_TestStrat" in r
    assert "v6" in r
    assert "vt_symbols=0" in r
