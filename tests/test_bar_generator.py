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
"""
import sys
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path

import pytest

# 让 tests/ 可以 import src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.gateway import BarData, TickData
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
