"""
src.indicator — 行情指标模块 (借鉴 vnpy.trader.utility, 2026-06-24)

子模块:
  - bar_generator.py  :  BarGenerator (低→高 K 线合成) + bars_from_lower 工具
  - array_manager.py  :  ArrayManager (K 线 → numpy + 17+ 指标)

典型用法:
    from src.indicator import BarGenerator, ArrayManager, bars_from_lower

    # 1. 拉数据
    from src.data import data_mgr
    bars = data_mgr.datafeed.get_bars("000001.SZ", "1d", count=60)

    # 2a. 一次性批量合成
    bars_5m = bars_from_lower(bars, window=5, interval="5m")  # 不太适合日 K

    # 2b. 实时累计 (实盘 / step-by-step 回测)
    bg = BarGenerator(on_bar=on_1m, window=5, interval="5m", on_window_bar=on_5m)

    # 3. 算指标
    am = ArrayManager(size=60)
    for bar in bars:
        am.update_bar(bar)
    print(am.sma(20))    # MA20
    print(am.macd())     # (DIF, DEA, MACD)
    print(am.rsi(14))    # RSI14
"""
from __future__ import annotations

from .bar_generator import BarGenerator, INTERVAL_TO_MINUTES, bars_from_lower
from .array_manager import ArrayManager

__all__ = [
    "BarGenerator",
    "INTERVAL_TO_MINUTES",
    "bars_from_lower",
    "ArrayManager",
]
