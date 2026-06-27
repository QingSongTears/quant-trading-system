"""
BarGenerator 单测 — src/indicator/bar_generator.py (2026-06-24)

涵盖:
  - 构造 (window / interval 校验)
  - update_bar() 单根触发 on_bar
  - update_bar() 多根累计到 window, 触发 on_window_bar
  - OHLCV 累计正确 (高/低/收/量)
  - 嵌套 (bg_1m → bg_5m, 自动把 window_bar 喂给上级)
  - 重置 (reset)
  - update_tick() (合成 1m bar)
  - bars_from_lower 批量工具
  - 错误路径 (window<1, 未知 interval)

新增 (P3.3, 2026-06-27):
  - subscribe() 把 bg 接到 EventEngine, 自动响应 EVENT_TICK / EVENT_BAR
  - unsubscribe() 注销回调 (防内存泄漏)
  - vt_symbol / interval 过滤
  - event.data=None 静默忽略
"""
import sys
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 让 tests/ 可以 import src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.gateway import BarData, TickData
from src.event import EVENT_BAR, EVENT_TICK, Event, EventEngine
from src.indicator import BarGenerator, INTERVAL_TO_MINUTES, bars_from_lower


# ── 工具 ──────────────────────────


def _bar(code, exchange, dt, o=10.0, h=11.0, l=9.5, c=10.5, vol=1000, turnover=10500.0, interval="1m"):
    return BarData(
        symbol=code, exchange=exchange,
        datetime=dt, interval=interval,
        open_price=o, high_price=h, low_price=l, close_price=c,
        volume=vol, turnover=turnover,
    )


# ── 构造 ──────────────────────────


def test_init_default_window_is_1():
    bg = BarGenerator(on_bar=lambda b: None)
    assert bg.window == 1
    assert bg.interval == "1m"


def test_init_rejects_unknown_interval():
    with pytest.raises(ValueError, match="未知 interval"):
        BarGenerator(on_bar=lambda b: None, interval="3m")


def test_init_rejects_window_less_than_1():
    with pytest.raises(ValueError, match="window"):
        BarGenerator(on_bar=lambda b: None, window=0)


def test_interval_to_minutes_constant():
    assert INTERVAL_TO_MINUTES["1m"] == 1
    assert INTERVAL_TO_MINUTES["5m"] == 5
    assert INTERVAL_TO_MINUTES["15m"] == 15
    assert INTERVAL_TO_MINUTES["30m"] == 30
    assert INTERVAL_TO_MINUTES["1h"] == 60
    assert INTERVAL_TO_MINUTES["4h"] == 240
    assert INTERVAL_TO_MINUTES["1d"] == 1440


# ── update_bar 单根触发 ──────────────────────────


def test_update_bar_triggers_on_bar_per_call():
    """每根 update_bar 都触发 on_bar"""
    calls = []
    bg = BarGenerator(on_bar=calls.append, window=5, interval="5m")
    for i in range(3):
        bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 30 + i)))
    assert len(calls) == 3


def test_update_bar_does_not_fire_window_bar_until_full():
    """window=5, 推 4 根不应触发 on_window_bar"""
    fires = []
    bg = BarGenerator(
        on_bar=lambda b: None,
        window=5, interval="5m",
        on_window_bar=fires.append,
    )
    for i in range(4):
        bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 30 + i)))
    assert fires == []


def test_update_bar_fires_window_bar_at_full_window():
    """window=5, 推 5 根触发一次 on_window_bar"""
    fires = []
    bg = BarGenerator(
        on_bar=lambda b: None,
        window=5, interval="5m",
        on_window_bar=fires.append,
    )
    for i in range(5):
        bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 30 + i)))
    assert len(fires) == 1


def test_update_bar_fires_window_bar_twice_for_10_bars():
    """window=5, 推 10 根触发 2 次 on_window_bar, 然后重置"""
    fires = []
    bg = BarGenerator(
        on_bar=lambda b: None,
        window=5, interval="5m",
        on_window_bar=fires.append,
    )
    for i in range(10):
        bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 30 + i)))
    assert len(fires) == 2


# ── OHLCV 累计正确性 ──────────────────────────


def test_window_bar_ohlcv_accumulation():
    """5 根 1m 合成 1 根 5m, OHLCV 正确"""
    fires = []
    bg = BarGenerator(
        on_bar=lambda b: None,
        window=5, interval="5m",
        on_window_bar=fires.append,
    )
    # 5 根, 每根 OHLCV 显式给
    bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 30), o=10.0, h=10.5, l=9.8, c=10.2, vol=100, turnover=1000.0))
    bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 31), o=10.2, h=10.8, l=10.0, c=10.5, vol=200, turnover=2000.0))
    bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 32), o=10.5, h=11.2, l=10.3, c=10.8, vol=300, turnover=3000.0))
    bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 33), o=10.8, h=10.9, l=9.5, c=9.6, vol=400, turnover=4000.0))
    bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 34), o=9.6, h=10.1, l=9.4, c=10.0, vol=500, turnover=5000.0))

    assert len(fires) == 1
    bar = fires[0]
    # 开盘 = 第 1 根 open
    assert bar.open_price == 10.0
    # 最高 = max of all high
    assert bar.high_price == 11.2
    # 最低 = min of all low
    assert bar.low_price == 9.4
    # 收盘 = 最后一根 close
    assert bar.close_price == 10.0
    # 量 = sum
    assert bar.volume == 1500
    # 金额 = sum
    assert bar.turnover == 15000.0


def test_window_bar_dt_uses_first_bar():
    """window bar 的 datetime 用第一根"""
    fires = []
    bg = BarGenerator(
        on_bar=lambda b: None,
        window=3, interval="5m",
        on_window_bar=fires.append,
    )
    bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 30)))
    bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 31)))
    bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 32)))
    assert fires[0].datetime == datetime(2024, 1, 1, 9, 30)


# ── 嵌套 ──────────────────────────


def test_nested_5m_to_30m():
    """bg_5m (window=5) → bg_30m (window=6), 即 30m 由 6 根 5m 合成"""
    upper_fires = []
    bg_30m = BarGenerator(
        on_bar=lambda b: None,
        window=6, interval="30m",
        on_window_bar=upper_fires.append,
    )
    bg_5m = BarGenerator(
        on_bar=lambda b: None,
        window=5, interval="5m",
        on_window_bar=bg_30m.update_bar,
    )

    # 推 30 根 1m → 6 根 5m → 1 根 30m
    for i in range(30):
        bg_5m.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 30 + i)))
    assert len(upper_fires) == 1


# ── reset / 状态查询 ──────────────────────────


def test_reset_clears_window_state():
    bg = BarGenerator(on_bar=lambda b: None, window=5, interval="5m")
    bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 30)))
    assert bg.is_in_window
    assert bg.bars_in_window == 1
    bg.reset()
    assert not bg.is_in_window
    assert bg.bars_in_window == 0


def test_repr_includes_interval_and_window():
    bg = BarGenerator(on_bar=lambda b: None, window=5, interval="5m")
    bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 30)))
    bg.update_bar(_bar("000001", "SZ", datetime(2024, 1, 1, 9, 31)))
    r = repr(bg)
    assert "BarGenerator" in r
    assert "5m" in r
    assert "5" in r  # window
    assert "bars_in_window=2" in r


# ── update_tick ──────────────────────────


def test_update_tick_synthesizes_1m_bar():
    """tick 推一次应触发 on_bar 一次"""
    calls = []
    bg = BarGenerator(on_bar=calls.append, window=1, interval="1m")
    tick = TickData(
        symbol="000001", exchange="SZ",
        datetime=datetime(2024, 1, 1, 9, 30, 15),
        last_price=10.5, last_volume=100, turnover=1050.0,
    )
    bg.update_tick(tick)
    assert len(calls) == 1
    bar = calls[0]
    assert bar.close_price == 10.5
    assert bar.symbol == "000001"
    assert bar.interval == "1m"


# ── bars_from_lower 批量 ──────────────────────────


def test_bars_from_lower_empty():
    assert bars_from_lower([], window=5, interval="5m") == []


def test_bars_from_lower_basic():
    """5 根 1m → 1 根 5m"""
    bars_1m = [
        _bar("000001", "SZ", datetime(2024, 1, 1, 9, 30 + i),
             o=10.0 + i * 0.1, c=10.0 + i * 0.1)
        for i in range(5)
    ]
    result = bars_from_lower(bars_1m, window=5, interval="5m")
    assert len(result) == 1
    assert result[0].open_price == 10.0
    assert result[0].close_price == 10.4
    assert result[0].interval == "5m"


def test_bars_from_lower_10_to_2_5m():
    """10 根 1m → 2 根 5m"""
    bars_1m = [
        _bar("000001", "SZ", datetime(2024, 1, 1, 9, 30 + i), o=10.0 + i * 0.1)
        for i in range(10)
    ]
    result = bars_from_lower(bars_1m, window=5, interval="5m")
    assert len(result) == 2


def test_bars_from_lower_drops_remainder():
    """不足 window 的末尾丢弃 (10 根 → 2 根 5m, 剩 0; 11 根 → 2 根 5m, 剩 1 根丢)"""
    bars_1m = [
        _bar("000001", "SZ", datetime(2024, 1, 1, 9, 30 + i), o=10.0)
        for i in range(11)
    ]
    result = bars_from_lower(bars_1m, window=5, interval="5m")
    assert len(result) == 2  # 11 / 5 = 2 remainder 1, 丢


def test_bars_from_lower_high_low():
    bars_1m = [
        _bar("000001", "SZ", datetime(2024, 1, 1, 9, 30), h=10.0, l=9.0),
        _bar("000001", "SZ", datetime(2024, 1, 1, 9, 31), h=11.0, l=8.0),
        _bar("000001", "SZ", datetime(2024, 1, 1, 9, 32), h=12.0, l=7.0),
        _bar("000001", "SZ", datetime(2024, 1, 1, 9, 33), h=10.0, l=9.0),
        _bar("000001", "SZ", datetime(2024, 1, 1, 9, 34), h=11.0, l=8.0),
    ]
    result = bars_from_lower(bars_1m, window=5, interval="5m")
    assert result[0].high_price == 12.0
    assert result[0].low_price == 7.0


# ── 事件订阅 (P3.3, 2026-06-27) ──────────────────────────


def test_subscribe_to_event_engine():
    """subscribe 后, engine.put(EVENT_BAR) 触发 on_bar"""
    calls = []
    bg = BarGenerator(on_bar=calls.append, window=1, interval="1m")

    engine = EventEngine(interval=1)
    engine.start()
    bg.subscribe(engine)

    bar = _bar("000001", "SZ", datetime(2024, 1, 1, 9, 30), o=10.0, c=10.5)
    engine.put(Event(EVENT_BAR, bar))

    assert len(calls) == 1
    assert calls[0].close_price == 10.5

    bg.unsubscribe(engine)
    engine.stop()


def test_subscribe_with_vt_symbol_filter():
    """subscribe(vt_symbol="000001.SZ") 只响应匹配的 vt_symbol"""
    calls = []
    bg = BarGenerator(on_bar=calls.append, window=1, interval="1m")

    engine = EventEngine(interval=1)
    engine.start()
    bg.subscribe(engine, vt_symbol="000001.SZ")

    # 不匹配 — 应忽略
    bar_other = _bar("000002", "SZ", datetime(2024, 1, 1, 9, 30), c=20.0)
    engine.put(Event(EVENT_BAR, bar_other))

    # 匹配 — 应触发
    bar_match = _bar("000001", "SZ", datetime(2024, 1, 1, 9, 31), c=10.5)
    engine.put(Event(EVENT_BAR, bar_match))

    assert len(calls) == 1
    assert calls[0].close_price == 10.5

    bg.unsubscribe(engine)
    engine.stop()


def test_unsubscribe_removes_callback():
    """unsubscribe 后, unregister 已被调用且 put 不再触发"""
    engine = EventEngine(interval=1)
    engine.start()

    calls = []
    bg = BarGenerator(on_bar=calls.append, window=1, interval="1m")
    bg.subscribe(engine)

    # 验证 register 调用次数
    assert len(engine._handlers[EVENT_BAR]) == 1
    assert len(engine._handlers[EVENT_TICK]) == 1

    bg.unsubscribe(engine)

    # 注销后 _handlers 中应无 bg 的回调
    assert len(engine._handlers.get(EVENT_BAR, [])) == 0
    assert len(engine._handlers.get(EVENT_TICK, [])) == 0

    # 注销后 put 不触发
    bar = _bar("000001", "SZ", datetime(2024, 1, 1, 9, 30), c=10.5)
    engine.put(Event(EVENT_BAR, bar))
    assert calls == []

    # 未订阅时 unsubscribe 为 no-op, 不报错
    bg.unsubscribe(engine)

    engine.stop()


def test_event_bar_with_mismatched_interval_ignored():
    """subscribe(interval="1m") 只响应 1m bar, 5m/1d 等被忽略"""
    calls = []
    bg = BarGenerator(on_bar=calls.append, window=1, interval="1m")

    engine = EventEngine(interval=1)
    engine.start()
    bg.subscribe(engine, interval="1m")

    # 5m bar — 不匹配 interval 过滤
    bar_5m = _bar("000001", "SZ", datetime(2024, 1, 1, 9, 30),
                 c=11.0, interval="5m")
    engine.put(Event(EVENT_BAR, bar_5m))

    # 1d bar — 不匹配
    bar_1d = _bar("000001", "SZ", datetime(2024, 1, 1, 9, 30),
                  c=12.0, interval="1d")
    engine.put(Event(EVENT_BAR, bar_1d))

    # 1m bar — 匹配
    bar_1m = _bar("000001", "SZ", datetime(2024, 1, 1, 9, 30),
                  c=10.5, interval="1m")
    engine.put(Event(EVENT_BAR, bar_1m))

    assert len(calls) == 1
    assert calls[0].close_price == 10.5
    assert calls[0].interval == "1m"

    bg.unsubscribe(engine)
    engine.stop()


def test_event_data_none_ignored():
    """event.data=None 静默忽略 (不抛异常, 不触发 on_bar)"""
    calls = []
    bg = BarGenerator(on_bar=calls.append, window=1, interval="1m")

    engine = EventEngine(interval=1)
    engine.start()
    bg.subscribe(engine)

    # data=None — 应静默忽略
    engine.put(Event(EVENT_BAR, None))
    engine.put(Event(EVENT_TICK, None))

    # 正常 data — 应触发
    bar = _bar("000001", "SZ", datetime(2024, 1, 1, 9, 30), c=10.5)
    engine.put(Event(EVENT_BAR, bar))

    assert len(calls) == 1
    assert calls[0].close_price == 10.5

    bg.unsubscribe(engine)
    engine.stop()


def test_subscribe_via_mock_engine_does_not_require_real_start():
    """subscribe 仅调 register, 无需 engine 启动; 用 MagicMock 验证 register 调用"""
    bg = BarGenerator(on_bar=lambda b: None, window=5, interval="5m")

    mock_engine = MagicMock()
    mock_engine.engine_name = "MockEngine"

    bg.subscribe(mock_engine, vt_symbol="000001.SZ", interval="1m")

    # 验证 register 调用了 EVENT_TICK 和 EVENT_BAR
    assert mock_engine.register.call_count == 2
    register_types = [c.args[0] for c in mock_engine.register.call_args_list]
    assert EVENT_TICK in register_types
    assert EVENT_BAR in register_types
