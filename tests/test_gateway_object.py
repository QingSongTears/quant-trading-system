"""
Gateway 数据对象单测

涵盖:
  - 所有 dataclass 默认值不爆
  - vt_symbol / vt_orderid 拼接正确
  - OrderData.is_active 状态枚举映射
  - BaseData.__post_init__ extra 字典初始化
  - 枚举常量值稳定
"""
from datetime import datetime

from src.gateway.object import (
    ACTIVE_STATUSES,
    AccountData,
    BarData,
    BaseData,
    CancelRequest,
    ContractData,
    Direction,
    Offset,
    OrderData,
    OrderRequest,
    OrderStatus,
    OrderType,
    PositionData,
    SubscribeRequest,
    TickData,
    TradeData,
)


# ── 枚举值稳定 ──────────────────────────


def test_direction_values():
    assert Direction.LONG.value == "多"
    assert Direction.SHORT.value == "空"


def test_offset_values():
    assert Offset.OPEN.value == "开"
    assert Offset.CLOSE.value == "平"
    assert Offset.CLOSE_TODAY.value == "平今"
    assert Offset.CLOSE_YESTERDAY.value == "平昨"


def test_order_type_values():
    assert OrderType.LIMIT.value == "限价"
    assert OrderType.MARKET.value == "市价"
    assert OrderType.BEST.value == "最优"


def test_order_status_values():
    assert OrderStatus.SUBMITTING.value == "提交中"
    assert OrderStatus.NOTTRADED.value == "未成交"
    assert OrderStatus.PARTTRADED.value == "部分成交"
    assert OrderStatus.ALLTRADED.value == "全部成交"
    assert OrderStatus.CANCELLED.value == "已撤单"
    assert OrderStatus.REJECTED.value == "已拒绝"


def test_active_statuses_set():
    assert OrderStatus.SUBMITTING in ACTIVE_STATUSES
    assert OrderStatus.NOTTRADED in ACTIVE_STATUSES
    assert OrderStatus.PARTTRADED in ACTIVE_STATUSES
    assert OrderStatus.ALLTRADED not in ACTIVE_STATUSES
    assert OrderStatus.CANCELLED not in ACTIVE_STATUSES
    assert OrderStatus.REJECTED not in ACTIVE_STATUSES


# ── BaseData 基础 ──────────────────────────


def test_basedata_extra_auto_init():
    """BaseData.__post_init__ 应自动初始化 extra 为 {}"""
    t = TickData()
    assert t.extra == {}
    assert isinstance(t.extra, dict)


def test_basedata_extra_init_false():
    """extra 字段声明 init=False, 不接受构造参数, 仅 post_init 自动初始化"""
    import dataclasses

    f = dataclasses.fields(BaseData)
    extra_field = next(f for f in f if f.name == "extra")
    assert extra_field.init is False

    # 用户不能通过 __init__ 传 extra
    import pytest
    with pytest.raises(TypeError):
        TickData(extra={"custom": 1})


def test_basedata_gateway_name_default():
    t = TickData()
    assert t.gateway_name == ""


# ── TickData ──────────────────────────


def test_tick_data_defaults():
    t = TickData()
    assert t.symbol == ""
    assert t.exchange == ""
    assert isinstance(t.datetime, datetime)
    assert t.last_price == 0.0
    assert t.bid_price_1 == 0.0
    assert t.ask_price_1 == 0.0


def test_tick_data_vt_symbol():
    t = TickData(symbol="000001", exchange="SZ")
    assert t.vt_symbol == "000001.SZ"


def test_tick_data_vt_symbol_with_bj():
    t = TickData(symbol="830799", exchange="BJ")
    assert t.vt_symbol == "830799.BJ"


# ── BarData ──────────────────────────


def test_bar_data_defaults():
    b = BarData()
    assert b.symbol == ""
    assert b.exchange == ""
    assert b.interval == "1d"  # 默认日线
    assert isinstance(b.datetime, datetime)
    assert b.close_price == 0.0


def test_bar_data_vt_symbol():
    b = BarData(symbol="600519", exchange="SH")
    assert b.vt_symbol == "600519.SH"


# ── OrderRequest ──────────────────────────


def test_order_request_required_fields():
    req = OrderRequest(
        symbol="000001",
        exchange="SZ",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        order_type=OrderType.LIMIT,
        price=10.5,
        volume=1000,
    )
    assert req.symbol == "000001"
    assert req.exchange == "SZ"
    assert req.direction is Direction.LONG
    assert req.offset is Offset.OPEN
    assert req.order_type is OrderType.LIMIT
    assert req.price == 10.5
    assert req.volume == 1000
    assert req.reference == ""


def test_order_request_defaults():
    req = OrderRequest(
        symbol="000001",
        exchange="SZ",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        order_type=OrderType.MARKET,
    )
    assert req.price == 0.0
    assert req.volume == 0
    assert req.reference == ""


# ── OrderData ──────────────────────────


def test_order_data_defaults():
    o = OrderData()
    assert o.symbol == ""
    assert o.orderid == ""
    assert o.direction is None
    assert o.status is None
    assert o.volume == 0.0
    assert o.traded == 0.0
    assert o.left == 0.0


def test_order_data_vt_symbol():
    o = OrderData(symbol="000001", exchange="SZ")
    assert o.vt_symbol == "000001.SZ"


def test_order_data_vt_orderid():
    o = OrderData(gateway_name="XTP", orderid="12345")
    assert o.vt_orderid == "XTP.12345"


def test_order_data_is_active_for_each_status():
    for status, expected in [
        (OrderStatus.SUBMITTING, True),
        (OrderStatus.NOTTRADED, True),
        (OrderStatus.PARTTRADED, True),
        (OrderStatus.ALLTRADED, False),
        (OrderStatus.CANCELLED, False),
        (OrderStatus.REJECTED, False),
    ]:
        o = OrderData(status=status)
        assert o.is_active is expected, f"status={status}"


def test_order_data_is_active_when_status_none():
    """status=None 时, is_active 应为 False (in ACTIVE_STATUSES 为 False)"""
    o = OrderData(status=None)
    assert o.is_active is False


# ── TradeData ──────────────────────────


def test_trade_data_defaults():
    t = TradeData()
    assert t.symbol == ""
    assert t.tradeid == ""
    assert t.direction is None
    assert t.price == 0.0
    assert t.volume == 0.0


def test_trade_data_vt_symbol_and_orderid():
    t = TradeData(
        gateway_name="XTP",
        symbol="000001",
        exchange="SZ",
        orderid="o123",
        tradeid="t456",
    )
    assert t.vt_symbol == "000001.SZ"
    assert t.vt_orderid == "XTP.o123"


# ── PositionData ──────────────────────────


def test_position_data_defaults():
    p = PositionData()
    assert p.symbol == ""
    assert p.direction is None
    assert p.volume == 0.0
    assert p.frozen == 0.0
    assert p.price == 0.0
    assert p.pnl == 0.0


def test_position_data_vt_symbol():
    p = PositionData(symbol="000001", exchange="SZ")
    assert p.vt_symbol == "000001.SZ"


def test_position_data_yd_volume_present():
    """2026-06-24 OmsEngine 上线: 调研 P1-8 已补齐 (T+1 关键字段)

    PositionData.yd_volume 字段用于昨日持仓, A 股 T+1 规则关键
    """
    p = PositionData()
    assert hasattr(p, "yd_volume")
    assert p.yd_volume == 0.0  # 默认值


def test_position_data_td_volume_auto_clamped_to_zero():
    """td_volume 是只读派生字段, 由 OmsEngine 维护

    PositionData 自身不主动算 td_volume (避免 dataclass 复杂化)
    """
    p = PositionData()
    # td_volume 字段存在 (OmsEngine 会维护)
    assert hasattr(p, "td_volume")
    assert p.td_volume == 0.0


def test_position_data_available_volume_property():
    """A 股可卖数量 = max(0, yd_volume - frozen)"""
    # 标准场景
    p = PositionData(volume=1000, yd_volume=600, frozen=100)
    assert p.available_volume == 500

    # 冻结 > 昨仓, 不可卖
    p2 = PositionData(volume=1000, yd_volume=100, frozen=200)
    assert p2.available_volume == 0  # max(0, 100-200) = 0

    # 无冻结
    p3 = PositionData(volume=1000, yd_volume=1000, frozen=0)
    assert p3.available_volume == 1000


# ── AccountData ──────────────────────────


def test_account_data_defaults():
    a = AccountData()
    assert a.accountid == ""
    assert a.balance == 0.0
    assert a.frozen == 0.0
    assert a.available == 0.0
    assert a.commission == 0.0
    assert a.margin == 0.0


# ── ContractData ──────────────────────────


def test_contract_data_defaults():
    c = ContractData()
    assert c.product == "STOCK"
    assert c.size == 1
    assert c.pricetick == 0.01
    assert c.min_volume == 100


def test_contract_data_vt_symbol():
    c = ContractData(symbol="000001", exchange="SZ")
    assert c.vt_symbol == "000001.SZ"


# ── SubscribeRequest / CancelRequest ──────────────────────────


def test_subscribe_request_vt_symbol():
    s = SubscribeRequest(symbol="000001", exchange="SZ")
    assert s.vt_symbol == "000001.SZ"


def test_subscribe_request_default_exchange():
    s = SubscribeRequest(symbol="000001")
    assert s.exchange == ""


def test_cancel_request_defaults():
    c = CancelRequest(orderid="o123")
    assert c.orderid == "o123"
    assert c.symbol == ""
    assert c.exchange == ""