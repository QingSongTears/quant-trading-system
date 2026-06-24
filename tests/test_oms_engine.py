"""
OmsEngine 单测 — src/engine/oms.py (借鉴 vnpy 4.4, 2026-06-24)

涵盖:
  - OmsEngine 注册到 MainEngine + start/stop 事件订阅/解订阅
  - 6 类事件处理 (tick/order/trade/position/account/contract)
  - 单条查询 API (get_tick / get_order / get_position 等)
  - 批量查询 API (get_all_*)
  - 活动订单索引 (active_orders)
  - A 股 T+1: get_sellable_volume / has_long_position
  - PositionData.yd_volume / td_volume / available_volume
"""
from typing import List

import pytest

from src.engine import OmsEngine
from src.event import (
    EVENT_ACCOUNT, EVENT_CONTRACT, EVENT_ORDER, EVENT_POSITION,
    EVENT_TICK, EVENT_TRADE, Event, EventEngine,
)
from src.gateway import (
    AccountData, ContractData, Direction, Offset, OrderData, OrderStatus,
    OrderType, PositionData, TickData, TradeData,
)
from src.gateway import MainEngine


@pytest.fixture
def oms_engine():
    """每个测试一个全新的 OmsEngine + EventEngine"""
    eng = EventEngine()
    eng.start()
    me = MainEngine(event_engine=eng)
    oms = me.add_engine(OmsEngine)
    oms.start()
    yield oms, eng
    oms.stop()
    eng.stop()


# ── 启动 / 停止 ──────────────────────────


def test_oms_can_be_added_to_main_engine():
    me = MainEngine()
    oms = me.add_engine(OmsEngine)
    assert "oms" in me.engines
    assert oms in me.engines.values()


def test_oms_engine_name_default():
    me = MainEngine()
    oms = me.add_engine(OmsEngine)
    assert oms.engine_name == "oms"


def test_oms_starts_subscribes_to_events(oms_engine):
    """start 后 EventEngine 应注册了 6 个 OmsEngine 的 handler"""
    oms, eng = oms_engine
    # EVENT_TICK 应有 2 个 handler: 之前可能注册的 + oms 的
    tick_handlers = eng._handlers.get(EVENT_TICK, [])
    assert oms._process_tick_event in tick_handlers


def test_oms_stop_unsubscribes(oms_engine):
    oms, eng = oms_engine
    oms.stop()
    # stop 后, OmsEngine 的 handler 应被移除
    assert oms._process_tick_event not in eng._handlers.get(EVENT_TICK, [])


def test_oms_start_without_event_engine_raises():
    """无 event_engine 时 start 应 raise (没法订阅)"""
    from src.engine import BaseEngine

    class LonelyOms(OmsEngine):
        def __init__(self):
            # 显式传 None event_engine
            super().__init__(main_engine=None, event_engine=None)

    lonely = LonelyOms()
    with pytest.raises(RuntimeError, match="event_engine"):
        lonely.start()


# ── 6 类事件处理 ──────────────────────────


def test_tick_event_updates_cache(oms_engine):
    oms, _ = oms_engine
    tick = TickData(symbol="000001", exchange="SZ", last_price=10.5)
    oms._process_tick_event(Event(EVENT_TICK, tick))
    cached = oms.get_tick("000001.SZ")
    assert cached is tick
    assert cached.last_price == 10.5


def test_order_event_updates_cache(oms_engine):
    oms, _ = oms_engine
    order = OrderData(
        gateway_name="XTP", symbol="000001", exchange="SZ", orderid="o1",
        direction=Direction.LONG, offset=Offset.OPEN,
        order_type=OrderType.LIMIT, status=OrderStatus.NOTTRADED,
        price=10.0, volume=100,
    )
    oms._process_order_event(Event(EVENT_ORDER, order))
    assert oms.get_order("XTP.o1") is order


def test_trade_event_updates_cache(oms_engine):
    oms, _ = oms_engine
    trade = TradeData(
        gateway_name="XTP", symbol="000001", exchange="SZ",
        orderid="o1", tradeid="t1",
        direction=Direction.LONG, offset=Offset.OPEN,
        price=10.0, volume=100,
    )
    oms._process_trade_event(Event(EVENT_TRADE, trade))
    assert oms.get_trade("XTP.t1") is trade


def test_position_event_updates_cache_and_td_volume(oms_engine):
    """position 事件应自动维护 td_volume = volume - yd_volume"""
    oms, _ = oms_engine
    pos = PositionData(
        symbol="000001", exchange="SZ",
        volume=1000, yd_volume=600, frozen=100,
    )
    oms._process_position_event(Event(EVENT_POSITION, pos))
    cached = oms.get_position("000001.SZ")
    assert cached is pos
    assert cached.td_volume == 400  # 1000 - 600 自动算
    assert cached.available_volume == 500  # 600 - 100


def test_position_event_clamps_td_volume_to_zero(oms_engine):
    """volume < yd_volume 时, td_volume 应为 0 (非负)"""
    oms, _ = oms_engine
    pos = PositionData(
        symbol="000001", exchange="SZ",
        volume=500, yd_volume=600,  # 异常: 昨仓 > 总持仓
    )
    oms._process_position_event(Event(EVENT_POSITION, pos))
    assert oms.get_position("000001.SZ").td_volume == 0


def test_account_event_updates_cache(oms_engine):
    oms, _ = oms_engine
    acc = AccountData(accountid="12345", balance=100000, available=95000)
    oms._process_account_event(Event(EVENT_ACCOUNT, acc))
    assert oms.get_account("12345") is acc


def test_account_default_returns_first(oms_engine):
    """无 vt_accountid 时返回第一个 account (A 股通常只有一个)"""
    oms, _ = oms_engine
    oms._process_account_event(Event(EVENT_ACCOUNT, AccountData(accountid="A1")))
    oms._process_account_event(Event(EVENT_ACCOUNT, AccountData(accountid="A2")))
    default = oms.get_account()
    assert default is not None
    assert default.accountid in ("A1", "A2")


def test_account_default_returns_none_when_empty(oms_engine):
    oms, _ = oms_engine
    assert oms.get_account() is None


def test_contract_event_updates_cache(oms_engine):
    oms, _ = oms_engine
    contract = ContractData(
        symbol="000001", exchange="SZ", name="平安银行",
    )
    oms._process_contract_event(Event(EVENT_CONTRACT, contract))
    assert oms.get_contract("000001.SZ") is contract


# ── 活动订单索引 ──────────────────────────


def test_active_order_index_tracks_active_status(oms_engine):
    oms, _ = oms_engine
    # 提交中 → 活动
    o1 = OrderData(gateway_name="XTP", orderid="o1", status=OrderStatus.SUBMITTING)
    oms._process_order_event(Event(EVENT_ORDER, o1))
    assert "XTP.o1" in oms.active_orders

    # 部分成交 → 仍活动
    o2 = OrderData(gateway_name="XTP", orderid="o2", status=OrderStatus.PARTTRADED)
    oms._process_order_event(Event(EVENT_ORDER, o2))
    assert "XTP.o2" in oms.active_orders

    # 全部成交 → 不活动, 移出
    o1.status = OrderStatus.ALLTRADED
    oms._process_order_event(Event(EVENT_ORDER, o1))
    assert "XTP.o1" not in oms.active_orders

    # 撤单 → 不活动, 移出
    o2.status = OrderStatus.CANCELLED
    oms._process_order_event(Event(EVENT_ORDER, o2))
    assert "XTP.o2" not in oms.active_orders


def test_get_all_active_orders(oms_engine):
    oms, _ = oms_engine
    # 3 个活动 + 2 个终态
    for i, status in enumerate([
        OrderStatus.NOTTRADED, OrderStatus.PARTTRADED, OrderStatus.SUBMITTING,
        OrderStatus.ALLTRADED, OrderStatus.CANCELLED,
    ]):
        order = OrderData(gateway_name="XTP", orderid=f"o{i}", status=status)
        oms._process_order_event(Event(EVENT_ORDER, order))

    active = oms.get_all_active_orders()
    assert len(active) == 3
    active_ids = {o.orderid for o in active}
    assert active_ids == {"o0", "o1", "o2"}


# ── 批量查询 ──────────────────────────


def test_get_all_positions(oms_engine):
    oms, _ = oms_engine
    for sym in ["000001", "000002", "600519"]:
        pos = PositionData(symbol=sym, exchange="SZ" if sym.startswith("0") else "SH")
        oms._process_position_event(Event(EVENT_POSITION, pos))
    assert len(oms.get_all_positions()) == 3


def test_get_all_ticks(oms_engine):
    oms, _ = oms_engine
    for i in range(5):
        tick = TickData(symbol=f"00000{i}", exchange="SZ")
        oms._process_tick_event(Event(EVENT_TICK, tick))
    assert len(oms.get_all_ticks()) == 5


# ── A 股 T+1 工具 ──────────────────────────


def test_get_sellable_volume_uses_yd_minus_frozen(oms_engine):
    oms, _ = oms_engine
    pos = PositionData(
        symbol="000001", exchange="SZ",
        volume=1000, yd_volume=600, frozen=100,
    )
    oms._process_position_event(Event(EVENT_POSITION, pos))
    assert oms.get_sellable_volume("000001.SZ") == 500


def test_get_sellable_volume_no_position_returns_zero(oms_engine):
    oms, _ = oms_engine
    assert oms.get_sellable_volume("000001.SZ") == 0.0


def test_has_long_position_true_when_volume_positive(oms_engine):
    oms, _ = oms_engine
    pos = PositionData(symbol="000001", exchange="SZ", volume=100)
    oms._process_position_event(Event(EVENT_POSITION, pos))
    assert oms.has_long_position("000001.SZ") is True


def test_has_long_position_false_when_no_position(oms_engine):
    oms, _ = oms_engine
    assert oms.has_long_position("000001.SZ") is False


# ── 端到端: 模拟一个完整成交链路 ──────────────────────────


def test_full_trade_lifecycle_e2e(oms_engine):
    """完整链路: subscribe → on_contract → on_order → on_trade → on_position"""
    oms, _ = oms_engine

    # 1. 合约回报
    contract = ContractData(symbol="000001", exchange="SZ", name="平安银行")
    oms._process_contract_event(Event(EVENT_CONTRACT, contract))

    # 2. 委托回报 (提交中)
    order = OrderData(
        gateway_name="XTP", symbol="000001", exchange="SZ", orderid="o100",
        direction=Direction.LONG, offset=Offset.OPEN,
        order_type=OrderType.LIMIT, status=OrderStatus.NOTTRADED,
        price=10.0, volume=1000,
    )
    oms._process_order_event(Event(EVENT_ORDER, order))
    assert oms.get_order("XTP.o100") is order
    assert len(oms.get_all_active_orders()) == 1

    # 3. 成交回报
    trade = TradeData(
        gateway_name="XTP", symbol="000001", exchange="SZ",
        orderid="o100", tradeid="t100",
        direction=Direction.LONG, offset=Offset.OPEN,
        price=10.05, volume=1000,
    )
    oms._process_trade_event(Event(EVENT_TRADE, trade))

    # 4. 持仓回报 (假设全部昨仓, 简化场景)
    pos = PositionData(
        symbol="000001", exchange="SZ",
        volume=1000, yd_volume=1000, frozen=0,
    )
    oms._process_position_event(Event(EVENT_POSITION, pos))

    # 5. 订单状态变为全部成交
    order.status = OrderStatus.ALLTRADED
    oms._process_order_event(Event(EVENT_ORDER, order))
    assert len(oms.get_all_active_orders()) == 0  # 已从 active 移除

    # 6. 验证完整缓存
    assert oms.get_contract("000001.SZ") is contract
    assert oms.get_trade("XTP.t100") is trade
    assert oms.get_position("000001.SZ").volume == 1000
    assert oms.get_sellable_volume("000001.SZ") == 1000  # 全部可卖


# ── 调试 ──────────────────────────


def test_stats_returns_counters(oms_engine):
    oms, _ = oms_engine
    s = oms.stats()
    assert s["engine_name"] == "oms"
    assert s["is_active"] is True
    assert s["ticks"] == 0
    assert s["orders"] == 0
    # 全部 7 个字段
    expected_keys = {
        "engine_name", "is_active", "ticks", "orders", "trades",
        "positions", "accounts", "contracts", "active_orders",
    }
    assert set(s.keys()) == expected_keys


# ── 端到端: 真实事件总线 (不直接调 _process_xxx) ──────────────────────────


def test_real_event_bus_dispatches_to_oms(oms_engine):
    """验证 EventEngine.put() 真能触发 OmsEngine 缓存更新

    (区别于直接调 _process_xxx_event)
    """
    oms, eng = oms_engine
    # 推 tick 事件
    tick = TickData(symbol="600519", exchange="SH", last_price=1800.0)
    eng.put(Event(EVENT_TICK, tick))
    # OmsEngine 应收到并缓存
    assert oms.get_tick("600519.SH") is tick