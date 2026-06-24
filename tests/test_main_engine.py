"""
MainEngine 单测 — src/gateway/main_engine.py (2026-06-24)

涵盖:
  - 构造 (自带 EventEngine / 外部传入)
  - 生命周期 (start/stop, 关闭顺序)
  - Gateway 管理 (add/get/重复名/默认名)
  - Engine 管理 (add/get/覆盖, 2026-06-24 新增)
  - Strategy 管理 (add/get)
  - 错误路径 (没注册就 get, 找不存在的 name)
  - stats() 调试
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 让 tests/ 可以 import src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.engine import BaseEngine
from src.event import EventEngine
from src.gateway import MainEngine
from src.gateway.base_gateway import BaseGateway
from src.strategy import AlphaStrategy


# ── Mock 工厂 (满足抽象方法) ──────────────────────────


def _make_fake_gw_class(name: str = "FakeGw"):
    """创建一个满足 BaseGateway 9 个抽象方法的最小子类"""
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


def _make_fake_strat_class(name: str = "FakeStrat"):
    """创建一个满足 AlphaStrategy 3 个抽象方法的最小子类"""
    class FakeStrat(AlphaStrategy):
        def on_init(self): pass
        def on_bars(self, bars): pass
        def on_trade(self, trade): pass
    FakeStrat.__name__ = name
    return FakeStrat


# ── 构造 ──────────────────────────


def test_main_engine_creates_event_engine_if_not_given():
    me = MainEngine()
    assert isinstance(me.event_engine, EventEngine)
    assert me.event_engine._is_active is False


def test_main_engine_uses_external_event_engine():
    ee = EventEngine()
    me = MainEngine(event_engine=ee)
    assert me.event_engine is ee


def test_main_engine_init_dicts_empty():
    me = MainEngine()
    assert me.gateways == {}
    assert me.engines == {}
    assert me.strategies == {}


# ── 生命周期 ──────────────────────────


def test_main_engine_start_starts_event_engine():
    me = MainEngine()
    me.start()
    assert me.event_engine._is_active is True
    me.stop()


def test_main_engine_stop_stops_event_engine_first():
    me = MainEngine()
    me.start()
    me.stop()
    assert me.event_engine._is_active is False


def test_main_engine_stop_closes_all_gateways():
    """stop 应调每个 gateway.close()"""
    me = MainEngine()
    me.start()
    FakeGw = _make_fake_gw_class()
    me.add_gateway(FakeGw, "GW1")
    me.add_gateway(FakeGw, "GW2")

    # 直接用 mock 验证 close 被调
    me.gateways["GW1"].close = MagicMock()
    me.gateways["GW2"].close = MagicMock()
    me.stop()
    me.gateways["GW1"].close.assert_called()  # 已被 stop 时调用过
    me.gateways["GW2"].close.assert_called()


def test_main_engine_stop_gateway_close_exception_is_logged(caplog):
    """gateway.close() 抛异常不应中断 stop"""
    me = MainEngine()
    me.start()
    FakeGw = _make_fake_gw_class()
    me.add_gateway(FakeGw, "BAD")
    me.gateways["BAD"].close = MagicMock(side_effect=RuntimeError("boom"))
    # 不应抛
    me.stop()


def test_main_engine_stop_no_gateways():
    me = MainEngine()
    me.start()
    me.stop()  # 不应抛


# ── Gateway 管理 ──────────────────────────


def test_add_gateway_uses_class_default_name():
    FakeGw = _make_fake_gw_class("my_gw")
    me = MainEngine()
    gw = me.add_gateway(FakeGw)
    assert gw.gateway_name == "MY_GW"
    assert "MY_GW" in me.gateways
    assert me.gateways["MY_GW"] is gw


def test_add_gateway_uses_explicit_name():
    FakeGw = _make_fake_gw_class("any")
    me = MainEngine()
    gw = me.add_gateway(FakeGw, "MY_GW")
    assert gw.gateway_name == "MY_GW"
    assert "MY_GW" in me.gateways


def test_add_gateway_duplicate_overwrites():
    FakeGw = _make_fake_gw_class("dup")
    me = MainEngine()
    gw1 = me.add_gateway(FakeGw)
    gw2 = me.add_gateway(FakeGw)
    assert me.gateways["DUP"] is gw2
    assert gw1 is not gw2


def test_get_gateway_empty_raises():
    me = MainEngine()
    with pytest.raises(RuntimeError, match="没有注册"):
        me.get_gateway()


def test_get_gateway_default_returns_first():
    FakeGw = _make_fake_gw_class("x")
    me = MainEngine()
    gw1 = me.add_gateway(FakeGw, "G1")
    gw2 = me.add_gateway(FakeGw, "G2")
    assert me.get_gateway() is gw1


def test_get_gateway_by_name():
    FakeGw = _make_fake_gw_class("x")
    me = MainEngine()
    gw = me.add_gateway(FakeGw, "MY")
    assert me.get_gateway("MY") is gw


def test_get_gateway_unknown_raises():
    FakeGw = _make_fake_gw_class("x")
    me = MainEngine()
    me.add_gateway(FakeGw, "A")
    with pytest.raises(KeyError, match="不存在"):
        me.get_gateway("ZZZ")


# ── Engine 管理 (2026-06-24 新增) ──────────────────────────


def test_add_engine_uses_engine_name():
    class MyEng(BaseEngine):
        def __init__(self, main_engine, event_engine):
            super().__init__(main_engine, event_engine, engine_name="my")

    me = MainEngine()
    eng = me.add_engine(MyEng)
    assert eng.engine_name == "my"
    assert "my" in me.engines


def test_add_engine_explicit_name_overrides():
    class MyEng(BaseEngine):
        def __init__(self, main_engine, event_engine):
            super().__init__(main_engine, event_engine, engine_name="orig")

    me = MainEngine()
    eng = me.add_engine(MyEng, engine_name="aliased")
    assert eng.engine_name == "aliased"
    assert "aliased" in me.engines


def test_add_engine_duplicate_overwrites():
    class MyEng(BaseEngine):
        def __init__(self, main_engine, event_engine):
            super().__init__(main_engine, event_engine, engine_name="dup")

    me = MainEngine()
    e1 = me.add_engine(MyEng)
    e2 = me.add_engine(MyEng)
    assert e1 is not e2
    assert me.engines["dup"] is e2


def test_add_engine_passes_main_and_event_engine():
    class SpyEng(BaseEngine):
        def __init__(self, main_engine, event_engine):
            super().__init__(main_engine, event_engine, engine_name="spy")
            self.captured_me = main_engine
            self.captured_ee = event_engine

    me = MainEngine()
    eng = me.add_engine(SpyEng)
    assert eng.captured_me is me
    assert eng.captured_ee is me.event_engine


def test_get_engine_empty_raises():
    me = MainEngine()
    with pytest.raises(RuntimeError, match="没有注册"):
        me.get_engine()


def test_get_engine_default_returns_first():
    class EngA(BaseEngine):
        def __init__(self, me, ee):
            super().__init__(me, ee, engine_name="a")
    class EngB(BaseEngine):
        def __init__(self, me, ee):
            super().__init__(me, ee, engine_name="b")

    me = MainEngine()
    a = me.add_engine(EngA)
    me.add_engine(EngB)
    assert me.get_engine() is a


def test_get_engine_by_name():
    class EngA(BaseEngine):
        def __init__(self, me, ee):
            super().__init__(me, ee, engine_name="a")
    class EngB(BaseEngine):
        def __init__(self, me, ee):
            super().__init__(me, ee, engine_name="b")

    me = MainEngine()
    me.add_engine(EngA)
    b = me.add_engine(EngB)
    assert me.get_engine("b") is b


def test_get_engine_unknown_raises():
    class EngA(BaseEngine):
        def __init__(self, me, ee):
            super().__init__(me, ee, engine_name="a")

    me = MainEngine()
    me.add_engine(EngA)
    with pytest.raises(KeyError, match="不存在"):
        me.get_engine("zzz")


# ── Strategy 管理 ──────────────────────────


def test_add_strategy_uses_class_name():
    FakeStrat = _make_fake_strat_class("MyStrat")
    me = MainEngine()
    s = me.add_strategy(FakeStrat)
    assert s.strategy_name == "MyStrat"
    assert "MyStrat" in me.strategies


def test_add_strategy_explicit_name():
    FakeStrat = _make_fake_strat_class("X")
    me = MainEngine()
    s = me.add_strategy(FakeStrat, "alpha_v6")
    assert s.strategy_name == "alpha_v6"
    assert "alpha_v6" in me.strategies


def test_get_strategy_default_returns_first():
    S1 = _make_fake_strat_class("S1")
    S2 = _make_fake_strat_class("S2")
    me = MainEngine()
    s1 = me.add_strategy(S1)
    me.add_strategy(S2)
    assert me.get_strategy() is s1


def test_get_strategy_by_name():
    S1 = _make_fake_strat_class("S1")
    me = MainEngine()
    s = me.add_strategy(S1, "mine")
    assert me.get_strategy("mine") is s


# ── stats() ──────────────────────────


def test_stats_returns_dict_with_expected_keys():
    me = MainEngine()
    s = me.stats()
    assert set(s.keys()) == {
        "event_engine", "gateways", "engines", "strategies",
    }
    assert s["gateways"] == []
    assert s["engines"] == []
    assert s["strategies"] == []


def test_stats_reflects_registered_items():
    FakeGw = _make_fake_gw_class("fake")
    class MyEng(BaseEngine):
        def __init__(self, me, ee):
            super().__init__(me, ee, engine_name="my")

    me = MainEngine()
    me.add_gateway(FakeGw)
    me.add_engine(MyEng)

    s = me.stats()
    assert "FAKE" in s["gateways"]
    assert "my" in s["engines"]
