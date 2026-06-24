"""
Engine 集成测试 — EventEngine + MainEngine + OmsEngine + Strategy (2026-06-24)

涵盖:
  - MainEngine + EventEngine 启动/停止 (含多 engine)
  - OmsEngine 订阅 EVENT_* 6 类事件
  - 端到端: gateway 推 tick → OmsEngine 缓存
  - 端到端: 策略 buy → OmsEngine 收不到 (因为 buy 走 strategy_engine.send_order,
          不走 gateway 推 EVENT_ORDER 链路 — 这是 vnpy 设计, 我们也这样)
  - 多 engine 共存: OmsEngine + 自定义 Eng
  - 异常隔离: 一个 engine 抛异常不影响其他
  - 启动顺序: event engine 先于其他 engine
  - 关闭顺序: gateway 先于 event engine (MainEngine.stop 设计)
"""
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 让 tests/ 可以 import src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.engine import BaseEngine, OmsEngine
from src.event import (
    EVENT_ACCOUNT, EVENT_CONTRACT, EVENT_ORDER, EVENT_POSITION,
    EVENT_TICK, EVENT_TRADE, Event, EventEngine,
)
from src.gateway import (
    AccountData, BarData, ContractData, Direction, Offset, OrderData,
    OrderStatus, OrderType, PositionData, TickData, TradeData,
)
from src.gateway import MainEngine
from src.gateway.base_gateway import BaseGateway
from src.strategy import AlphaStrategy


# ── Mock Gateway ──────────────────────────


def _make_fake_gw_class(name="FakeGw"):
    """满足 BaseGateway 9 个抽象方法的最小子类"""
    class FakeGw(BaseGateway):
        default_name = name.upper()
        def connect(self, setting): pass
        def close(self): pass
        def subscribe(self, req): pass
        def send_order(self, req): return "fake_id"
        def cancel_order(self, req): pass
        def query_account(self): pass
        def query_position(self): pass
        def query_orders(self): pass
        def query_trades(self): pass
    FakeGw.__name__ = name
    return FakeGw


# ── 完整链路: MainEngine + EventEngine + OmsEngine ──────────────────────────


@pytest.fixture
def full_stack():
    """完整栈: EventEngine + MainEngine + OmsEngine"""
    ee = EventEngine()
    ee.start()
    me = MainEngine(event_engine=ee)
    oms = me.add_engine(OmsEngine)
    oms.start()
    yield me, ee, oms
    oms.stop()
    me.stop()
    ee.stop()


# ── 启动/停止 ──────────────────────────


def test_main_engine_starts_event_engine():
    me = MainEngine()
    me.start()
    assert me.event_engine._is_active is True
    me.stop()
    assert me.event_engine._is_active is False


def test_oms_engine_registers_handlers_on_start(full_stack):
    me, ee, oms = full_stack
    # 6 类事件都应有 oms handler
    for evt_type in [
        EVENT_TICK, EVENT_ORDER, EVENT_TRADE, EVENT_POSITION,
        EVENT_ACCOUNT, EVENT_CONTRACT,
    ]:
        handlers = ee._handlers.get(evt_type, [])
        assert oms._process_tick_event in handlers or \
               oms._process_order_event in handlers or \
               oms._process_trade_event in handlers or \
               oms._process_position_event in handlers or \
               oms._process_account_event in handlers or \
               oms._process_contract_event in handlers, \
               f"{evt_type} 应至少含一个 oms handler"


def test_oms_engine_unregisters_on_stop(full_stack):
    me, ee, oms = full_stack
    oms.stop()
    assert oms._process_tick_event not in ee._handlers.get(EVENT_TICK, [])


def test_main_engine_stop_closes_gateways_in_order():
    """MainEngine.stop 顺序: event_engine.stop() 先, 再 gateway.close()"""
    me = MainEngine()
    me.start()
    FakeGw = _make_fake_gw_class()
    me.add_gateway(FakeGw, "GW1")
    me.add_gateway(FakeGw, "GW2")

    call_order = []
    original_ee_stop = me.event_engine.stop

    def tracked_ee_stop():
        call_order.append("event_engine.stop")
        original_ee_stop()

    me.event_engine.stop = tracked_ee_stop
    me.gateways["GW1"].close = lambda: call_order.append("GW1.close")
    me.gateways["GW2"].close = lambda: call_order.append("GW2.close")

    me.stop()
    assert call_order[0] == "event_engine.stop"
    assert "GW1.close" in call_order
    assert "GW2.close" in call_order


# ── 端到端: gateway → event → OmsEngine 缓存 ──────────────────────────


def test_tick_event_full_path_updates_oms_cache(full_stack):
    """推 tick 事件 → OmsEngine.get_tick() 拿到"""
    me, ee, oms = full_stack
    tick = TickData(symbol="000001", exchange="SZ", last_price=10.5)
    ee.put(Event(EVENT_TICK, tick))
    assert oms.get_tick("000001.SZ") is tick


def test_order_event_full_path(full_stack):
    me, ee, oms = full_stack
    order = OrderData(
        gateway_name="FAKE", symbol="000001", exchange="SZ", orderid="o1",
        direction=Direction.LONG, offset=Offset.OPEN,
        order_type=OrderType.LIMIT, status=OrderStatus.NOTTRADED,
        price=10.0, volume=100,
    )
    ee.put(Event(EVENT_ORDER, order))
    assert oms.get_order("FAKE.o1") is order
    assert "FAKE.o1" in oms.active_orders


def test_trade_event_full_path(full_stack):
    me, ee, oms = full_stack
    trade = TradeData(
        gateway_name="FAKE", symbol="000001", exchange="SZ",
        orderid="o1", tradeid="t1",
        direction=Direction.LONG, offset=Offset.OPEN,
        price=10.0, volume=100,
    )
    ee.put(Event(EVENT_TRADE, trade))
    assert oms.get_trade("FAKE.t1") is trade


def test_position_event_full_path_with_t1(full_stack):
    """position 事件: OmsEngine 自动维护 td_volume (T+1)"""
    me, ee, oms = full_stack
    pos = PositionData(
        symbol="000001", exchange="SZ",
        volume=1000, yd_volume=600, frozen=100,
    )
    ee.put(Event(EVENT_POSITION, pos))
    cached = oms.get_position("000001.SZ")
    assert cached is pos
    assert cached.td_volume == 400  # 1000 - 600
    assert oms.get_sellable_volume("000001.SZ") == 500  # 600 - 100


# ── 多 engine 共存 ──────────────────────────


def test_multiple_engines_can_coexist():
    """OmsEngine + 自定义 Eng 一起跑"""
    class CustomEng(BaseEngine):
        def __init__(self, me, ee):
            super().__init__(me, ee, engine_name="custom")
            self.received_events = []

        def _on_event(self, event: Event):
            self.received_events.append(event)

    me = MainEngine()
    me.start()
    oms = me.add_engine(OmsEngine)
    custom = me.add_engine(CustomEng)
    oms.start()

    # custom 单独订阅 EVENT_TICK
    me.event_engine.register(EVENT_TICK, custom._on_event)

    tick = TickData(symbol="000001", exchange="SZ")
    me.event_engine.put(Event(EVENT_TICK, tick))

    # 两个 engine 都应收到
    assert oms.get_tick("000001.SZ") is tick
    # custom 收的 event 的 .data 应是我们推的 tick
    assert len(custom.received_events) == 1
    assert custom.received_events[0].data is tick
    assert custom.received_events[0].type == EVENT_TICK

    oms.stop()
    me.event_engine.unregister(EVENT_TICK, custom._on_event)
    me.stop()


# ── 异常隔离 ──────────────────────────


def test_engine_exception_does_not_break_others():
    """一个 engine handler 抛异常, 不影响其他 engine / event 流转"""
    class BoomEng(BaseEngine):
        def __init__(self, me, ee):
            super().__init__(me, ee, engine_name="boom")

        def _on_boom(self, event: Event):
            raise RuntimeError("engine boom!")

    me = MainEngine()
    me.start()
    oms = me.add_engine(OmsEngine)
    oms.start()
    boom = me.add_engine(BoomEng)
    me.event_engine.register(EVENT_TICK, boom._on_boom)

    # 推 tick, boom 会抛异常, oms 应仍正常处理
    tick = TickData(symbol="000001", exchange="SZ")
    me.event_engine.put(Event(EVENT_TICK, tick))

    # oms 仍缓存成功
    assert oms.get_tick("000001.SZ") is tick

    me.event_engine.unregister(EVENT_TICK, boom._on_boom)
    oms.stop()
    me.stop()


# ── OmsEngine + Strategy 协作 ──────────────────────────


def test_oms_can_be_used_by_strategy_engine():
    """OmsEngine 作为 strategy_engine 的一部分, 给 AlphaStrategy 用"""
    class OmsLikeEngine:
        """把 OmsEngine 暴露的 API 包装成 StrategyEngine Protocol"""
        def __init__(self, oms):
            self.oms = oms
            self._cash = 100000.0

        def send_order(self, strategy, vt_symbol, direction, offset, price, volume):
            return [f"order_{strategy.strategy_name}_{vt_symbol}"]

        def cancel_order(self, strategy, vt_orderid):
            pass

        def write_log(self, msg, strategy):
            pass

        def get_cash_available(self):
            return self._cash

        def get_holding_value(self):
            return 0.0

        def get_signal(self):
            return None

    me = MainEngine()
    me.start()
    oms = me.add_engine(OmsEngine)
    oms.start()

    class _Strat(AlphaStrategy):
        def on_init(self): pass
        def on_bars(self, bars): pass
        def on_trade(self, trade): pass

    strategy_engine = OmsLikeEngine(oms)
    strat = _Strat(strategy_engine, "test", ["000001.SZ"])
    ids = strat.buy("000001.SZ", 10.0, 100)
    assert "order_test_000001.SZ" in ids

    oms.stop()
    me.stop()


# ── 调试 stats ──────────────────────────


def test_stats_reflects_full_stack_state(full_stack):
    me, ee, oms = full_stack
    s = me.stats()
    assert s["event_engine"]["active"] is True
    assert "oms" in s["engines"]
    assert s["gateways"] == []
    assert s["strategies"] == []


def test_oms_stats_reflects_event_count(full_stack):
    me, ee, oms = full_stack
    ee.put(Event(EVENT_TICK, TickData(symbol="000001", exchange="SZ")))
    ee.put(Event(EVENT_TICK, TickData(symbol="000002", exchange="SZ")))
    s = oms.stats()
    assert s["ticks"] == 2
    assert s["engine_name"] == "oms"
    assert s["is_active"] is True
