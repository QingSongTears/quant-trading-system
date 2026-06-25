"""
src.indicator — 行情指标模块 (2026-06-25 重构)

子模块:
  - protocol.py   :  Indicator 协议 + IndicatorRegistry + IndicatorResult
  - atomic.py     :  17 个原子指标 (sma/ema/macd/rsi/kdj/boll/...) — 从 ArrayManager 迁算法
  - operators.py  :  DataFrame 级别高级算子 (rolling_mean/pct_rank/adx/max_dd/...)
  - bar_generator.py :  BarGenerator (低→高 K 线合成) + bars_from_lower 工具

设计:
  - 统一接口: 所有指标都通过 IndicatorRegistry.get("name").compute(bars, **params) 调用
  - 训练/推理分布一致: 算法集中, 同名同版本不会漂移
  - BarGenerator 保留: K 线合成与指标无关, 独立模块

典型用法:
    from src.indicator import IndicatorRegistry

    # 1. 一次性 import 触发注册
    from src.indicator import atomic, operators  # noqa

    # 2. 调用指标
    rsi = IndicatorRegistry.get("rsi").compute(close, n=14)
    print(rsi.value)  # 0-100

    # 3. 列出已注册指标
    print(IndicatorRegistry.list())
"""
from __future__ import annotations

# 协议 + 工具
from .protocol import (
    Indicator,
    IndicatorRegistry,
    IndicatorResult,
    to_close_series,
    to_ohlc_dataframe,
)

# 注册所有 atomic + operators 算子 (import 即触发)
from . import atomic  # noqa: F401
from . import operators  # noqa: F401

# BarGenerator (K 线合成, 与指标无关)
from .bar_generator import BarGenerator, INTERVAL_TO_MINUTES, bars_from_lower

__all__ = [
    # 协议
    "Indicator",
    "IndicatorRegistry",
    "IndicatorResult",
    "to_close_series",
    "to_ohlc_dataframe",
    # BarGenerator
    "BarGenerator",
    "INTERVAL_TO_MINUTES",
    "bars_from_lower",
]
