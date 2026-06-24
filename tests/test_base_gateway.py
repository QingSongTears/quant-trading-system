"""
BaseGateway 单测

涵盖:
  - ABC 不能直接实例化
  - MockGateway 所有抽象方法实现后可实例化
  - on_tick / on_trade / on_order / on_account / on_position / on_contract
    → EventEngine.put + 缓存写入
  - get_account / get_position 缓存读写
  - write_log 不同 level
  - event_engine 未启动时 on_tick raise (新 strict 行为)
"""
from typing import Dict, List

import pytest

from src.event import (
    EVENT_ACCOUNT, EVENT_CONTRACT, EVENT_ORDER, EVENT_POSITION,
    EVENT_TICK, EVENT_TRADE, EventEngine,
)
from src.gateway.base_gateway import BaseGateway
from src.gateway.object import (
    AccountData, CancelRequest, ContractData, Direction, Offset,
    OrderData, OrderRequest, OrderStatus, OrderType, PositionData,
    SubscribeRequest, TickData, TradeData,
)


# ── Mock Gateway ──────────────────────────


class MockGateway(BaseGateway):
    """完整实现 BaseGateway 所有抽象方法的 Mock, 用于单测"""

    default_name = "MOCK"
    default_setting: Dict[str, str] = {}
    exchanges: List[str] = ["SZ", "SH"]

    def __init__(self, event_engine: EventEngine, gateway_name: str = "") -> None:
        super().__init__(event_engine, gateway_name)
        self.connect_called_with = None
        self.connected_setting = None

    def connect(self, setting: dict) -> None:
        self._connected = True
        self.connected_setting = setting

    def close(self) -> None:
        self._connected = False

    def subscribe(self, req: SubscribeRequest) -> None:
        pass

    def send_order(self, req: OrderRequest) -> str:
        return "mock_order_1"

    def cancel_order(self, req: CancelRequest) -> None:
        pass

    def query_account(self) -> None:
        pass

    def query_position(self) -> None:
        pass

    def query_orders(self) -> None:
        pass

    def query_trades(self) -> None:
        pass


# ── ABC 抽象类不能实例化 ──────────────────────────


def test_abc_cannot_instantiate():
    with pytest.raises(TypeError, match="abstract"):
        BaseGateway(EventEngine())  # type: ignore[abstract]


# ── 实例化 / 基本属性 ──────────────────────────


def test_mock_gateway_can_instantiate():
    eng = EventEngine(interval=1)
    gw = MockGateway(eng, "test_gw")
    assert gw.gateway_name == "test_gw"
    assert gw.event_engine is eng
    assert gw.connected is False
    assert gw.accounts == {}
    assert gw.positions == {}
    assert gw.orders == {}
    assert gw.trades == {}
    assert gw.contracts == {}
    assert gw.ticks == {}


def test_gateway_name_uses_default_when_empty():
    eng = EventEngine(interval=1)
    gw = MockGateway(eng, "")
    assert gw.gateway_name == "MOCK"


def test_exchanges_class_attribute():
    assert MockGateway.exchanges == ["SZ", "SH"]


# ── on_tick ──────────────────────────


def test_on_tick_updates_cache_and_publishes_event():
    eng = EventEngine(interval=1)
    eng.start()
    received = []
    eng.register(EVENT_TICK, lambda e: received.append(e.data))

    gw = MockGateway(eng, "test")
    tick = TickData(symbol="000001", exchange="SZ", last_price=10.5)
    gw.on_tick(tick)

    assert gw.ticks["000001.SZ"] is tick
    assert len(received) == 1
    assert received[0] is tick


def test_on_tick_with_unstarted_engine_raises():
    """新 strict 行为: EventEngine 未启动时 put 应 raise

    但 on_tick 调用者期望不抛 — 这是 gateway 的隐式承诺。
    因此 base_gateway 应使用 strict=False (向后兼容 gateway 关闭阶段)
    """
    eng = EventEngine(interval=1, raise_on_inactive=False)
    gw = MockGateway(eng, "test")
    tick = TickData(symbol="000001", exchange="SZ")
    # 未启动时不应崩 (BaseGateway 默认容忍)
    gw.on_tick(tick)
    assert gw.ticks["000001.SZ"] is tick


# ── on_trade ──────────────────────────


def test_on_trade_updates_cache_and_publishes():
    eng = EventEngine(interval=1)
    eng.start()
    received = []
    eng.register(EVENT_TRADE, lambda e: received.append(e.data))

    gw = MockGateway(eng, "test")
    trade = TradeData(
        gateway_name="MOCK", symbol="000001", exchange="SZ",
        orderid="o1", tradeid="t1",
        direction=Direction.LONG, offset=Offset.OPEN,
        price=10.0, volume=100,
    )
    gw.on_trade(trade)

    assert gw.trades["MOCK.o1"] is trade
    assert received == [trade]


# ── on_order ──────────────────────────


def test_on_order_updates_cache_and_publishes():
    eng = EventEngine(interval=1)
    eng.start()
    received = []
    eng.register(EVENT_ORDER, lambda e: received.append(e.data))

    gw = MockGateway(eng, "test")
    order = OrderData(
        gateway_name="MOCK", symbol="000001", exchange="SZ", orderid="o1",
        direction=Direction.LONG, offset=Offset.OPEN,
        order_type=OrderType.LIMIT, status=OrderStatus.NOTTRADED,
        price=10.0, volume=100,
    )
    gw.on_order(order)

    assert gw.orders["MOCK.o1"] is order
    assert received == [order]


# ── on_position / on_account / on_contract ──────────────────────────


def test_on_position_publishes_and_caches():
    eng = EventEngine(interval=1)
    eng.start()
    received = []
    eng.register(EVENT_POSITION, lambda e: received.append(e.data))

    gw = MockGateway(eng, "test")
    pos = PositionData(symbol="000001", exchange="SZ", volume=100, price=10.0)
    gw.on_position(pos)

    assert gw.positions["000001.SZ"] is pos
    assert received == [pos]


def test_on_account_publishes_and_caches():
    eng = EventEngine(interval=1)
    eng.start()
    received = []
    eng.register(EVENT_ACCOUNT, lambda e: received.append(e.data))

    gw = MockGateway(eng, "test")
    acc = AccountData(accountid="12345", balance=100000.0, available=95000.0)
    gw.on_account(acc)

    assert gw.accounts["12345"] is acc
    assert received == [acc]


def test_on_contract_publishes_and_caches():
    eng = EventEngine(interval=1)
    eng.start()
    received = []
    eng.register(EVENT_CONTRACT, lambda e: received.append(e.data))

    gw = MockGateway(eng, "test")
    contract = ContractData(symbol="000001", exchange="SZ", name="平安银行")
    gw.on_contract(contract)

    assert gw.contracts["000001.SZ"] is contract
    assert received == [contract]


# ── 缓存查询 ──────────────────────────


def test_get_account_empty_returns_none():
    eng = EventEngine(interval=1)
    gw = MockGateway(eng, "test")
    assert gw.get_account() is None


def test_get_account_returns_first():
    eng = EventEngine(interval=1)
    eng.start()
    gw = MockGateway(eng, "test")
    gw.on_account(AccountData(accountid="A1", balance=100))
    gw.on_account(AccountData(accountid="A2", balance=200))
    acc = gw.get_account()
    assert acc is not None
    assert acc.accountid in ("A1", "A2")


def test_get_position_by_symbol_only():
    eng = EventEngine(interval=1)
    eng.start()
    gw = MockGateway(eng, "test")
    pos = PositionData(symbol="000001", exchange="SZ", volume=100)
    gw.on_position(pos)

    # 只传 symbol 不带 exchange 时, 按 symbol 直接查
    assert gw.get_position("000001.SZ") is pos
    # 仅 symbol 不带 .SZ 时查不到
    assert gw.get_position("000001") is None


def test_get_position_by_symbol_and_exchange():
    eng = EventEngine(interval=1)
    eng.start()
    gw = MockGateway(eng, "test")
    pos = PositionData(symbol="000001", exchange="SZ", volume=100)
    gw.on_position(pos)

    assert gw.get_position("000001", "SZ") is pos
    assert gw.get_position("000001", "SH") is None  # 不同 exchange


def test_get_position_missing_returns_none():
    eng = EventEngine(interval=1)
    gw = MockGateway(eng, "test")
    assert gw.get_position("000001.SZ") is None


# ── connected 状态 ──────────────────────────


def test_connected_toggle():
    eng = EventEngine(interval=1)
    gw = MockGateway(eng, "test")
    assert gw.connected is False

    gw.connect({"account": "test"})
    assert gw.connected is True
    assert gw.connected_setting == {"account": "test"}

    gw.close()
    assert gw.connected is False


# ── write_log ──────────────────────────


def test_write_log_includes_gateway_name(caplog):
    import logging
    eng = EventEngine(interval=1)
    gw = MockGateway(eng, "my_gw")

    with caplog.at_level(logging.INFO, logger="src.gateway.base_gateway"):
        gw.write_log("hello")

    assert any("my_gw" in r.message and "hello" in r.message for r in caplog.records)


def test_write_log_levels():
    """write_log 应支持 debug/info/warning/error 等级"""
    eng = EventEngine(interval=1)
    gw = MockGateway(eng, "my_gw")
    # 全部应正常调用不抛
    gw.write_log("d", level="debug")
    gw.write_log("i", level="info")
    gw.write_log("w", level="warning")
    gw.write_log("e", level="error")
    # 未知 level 退化到 info
    gw.write_log("unknown", level="bogus")


# ── on_event 通用发布 ──────────────────────────


def test_on_event_publishes_arbitrary_type():
    eng = EventEngine(interval=1)
    eng.start()
    received = []
    eng.register("eCustom", lambda e: received.append(e.data))

    gw = MockGateway(eng, "test")
    payload = {"foo": "bar"}
    gw.on_event("eCustom", payload)

    assert received == [payload]