"""
src.indicator — 行情指标模块 (借鉴 vnpy.trader.utility, 2026-06-24)

子模块:
  - bar_generator.py  :  BarGenerator (低→高 K 线合成) + bars_from_lower 工具
  - array_manager.py  :  ArrayManager (K 线 → numpy + 17+ 指标) — TODO

典型用法:
    from src.indicator import BarGenerator, bars_from_lower

    # 1m → 5m 一次性合成
    bars_1m = data_mgr.datafeed.get_bars("000001.SZ", "1m")
    bars_5m = bars_from_lower(bars_1m, window=5, interval="5m")

    # 实时: 每根 1m 推, 自动累计 5m
    bg = BarGenerator(on_bar=on_1m, on_window_bar=on_5m, window=5, interval="5m")
    bg.update_bar(bar_1m)
"""
from __future__ import annotations

from .bar_generator import BarGenerator, INTERVAL_TO_MINUTES, bars_from_lower

__all__ = [
    "BarGenerator",
    "INTERVAL_TO_MINUTES",
    "bars_from_lower",
]
