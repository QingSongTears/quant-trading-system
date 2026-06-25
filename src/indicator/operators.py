"""
DataFrame 级别算子 — 高级算子 (2026-06-25)

与 atomic.py 区别:
  - atomic.py 是基础 K 线 → 数值 (如 rsi 0-100)
  - operators.py 是 DataFrame 算子 (如 rolling_mean 返回序列, pct_rank 返回百分比)
  - operators.py 的 indicator.value 通常是 Series/ndarray (不是 scalar)

算子清单:
  - rolling_mean / rolling_std / rolling_sum
  - pct_rank (百分位排名, 用于横截面比较)
  - adx (Average Directional Index, 平均趋向指数)
  - max_dd (回撤, 用于风控/择时)
  - zscore (Z-score 标准化)
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from .protocol import (
    IndicatorResult,
    IndicatorRegistry,
    to_close_series,
    to_ohlc_dataframe,
)


logger = logging.getLogger(__name__)


# ============================================================
#  滚动算子
# ============================================================


@IndicatorRegistry.register("rolling_mean")
class RollingMean:
    """滚动均值 (返回 Series)"""
    name = "rolling_mean"
    version = "v1"
    n_required = 1

    def compute(self, bars, window: int = 20, column: str = "close",
                **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < window:
            return IndicatorResult(self.name, None,
                                    {"window": window, "column": column},
                                    self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {window}")
        s = df[column].rolling(window).mean()
        return IndicatorResult(self.name, s,
                                {"window": window, "column": column},
                                self.version, self.n_required)


@IndicatorRegistry.register("rolling_std")
class RollingStd:
    """滚动标准差 (返回 Series, ddof=1)"""
    name = "rolling_std"
    version = "v1"
    n_required = 1

    def compute(self, bars, window: int = 20, column: str = "close",
                **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < window:
            return IndicatorResult(self.name, None,
                                    {"window": window, "column": column},
                                    self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {window}")
        s = df[column].rolling(window).std(ddof=1)
        return IndicatorResult(self.name, s,
                                {"window": window, "column": column},
                                self.version, self.n_required)


@IndicatorRegistry.register("rolling_sum")
class RollingSum:
    """滚动求和 (返回 Series)"""
    name = "rolling_sum"
    version = "v1"
    n_required = 1

    def compute(self, bars, window: int = 20, column: str = "volume",
                **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < window:
            return IndicatorResult(self.name, None,
                                    {"window": window, "column": column},
                                    self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {window}")
        s = df[column].rolling(window).sum()
        return IndicatorResult(self.name, s,
                                {"window": window, "column": column},
                                self.version, self.n_required)


@IndicatorRegistry.register("rolling_max")
class RollingMax:
    """滚动最大值 (返回 Series) — 用于 max_dd 等"""
    name = "rolling_max"
    version = "v1"
    n_required = 1

    def compute(self, bars, window: int = 60, column: str = "high",
                **_unused) -> IndicatorResult:
        # 接受 Series / DataFrame (1 列) / ndarray 1D
        if isinstance(bars, pd.Series):
            s = bars
            actual_col = s.name if hasattr(s, "name") else column
        elif isinstance(bars, pd.DataFrame):
            if column not in bars.columns:
                return IndicatorResult(self.name, None, {"window": window, "column": column},
                                        self.version, self.n_required,
                                        error=f"column {column!r} not in DataFrame")
            s = bars[column]
            actual_col = column
        elif hasattr(bars, "__iter__"):
            s = pd.Series(list(bars))
            actual_col = column
        else:
            return IndicatorResult(self.name, None, {"window": window, "column": column},
                                    self.version, self.n_required,
                                    error=f"unsupported input type: {type(bars).__name__}")
        if len(s) < window:
            return IndicatorResult(self.name, None, {"window": window, "column": actual_col},
                                    self.version, self.n_required,
                                    error=f"insufficient: have {len(s)}, need {window}")
        return IndicatorResult(self.name, s.rolling(window).max(),
                                {"window": window, "column": actual_col},
                                self.version, self.n_required)


# ============================================================
#  排名/标准化
# ============================================================


@IndicatorRegistry.register("pct_rank")
class PctRank:
    """百分位排名 (0-1) — 用于横截面比较

    返回 Series: 第 i 个值表示当前在 window 范围内的百分位
    """
    name = "pct_rank"
    version = "v1"
    n_required = 1

    def compute(self, bars, window: int = 60, column: str = "close",
                **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < window:
            return IndicatorResult(self.name, None,
                                    {"window": window, "column": column},
                                    self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {window}")

        def _rank(x):
            if len(x) < 2:
                return 0.5
            return (x.rank(method="average").iloc[-1] - 1) / (len(x) - 1)

        s = df[column].rolling(window).apply(_rank, raw=False)
        return IndicatorResult(self.name, s,
                                {"window": window, "column": column},
                                self.version, self.n_required)


@IndicatorRegistry.register("zscore")
class Zscore:
    """Z-score 标准化: (x - mean) / std (返回 Series)"""
    name = "zscore"
    version = "v1"
    n_required = 1

    def compute(self, bars, window: int = 20, column: str = "close",
                **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < window:
            return IndicatorResult(self.name, None,
                                    {"window": window, "column": column},
                                    self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {window}")
        mean = df[column].rolling(window).mean()
        std = df[column].rolling(window).std(ddof=1)
        s = (df[column] - mean) / std
        return IndicatorResult(self.name, s,
                                {"window": window, "column": column},
                                self.version, self.n_required)


# ============================================================
#  风险/趋势
# ============================================================


@IndicatorRegistry.register("max_dd")
class MaxDrawdown:
    """滚动最大回撤 (返回 Series) — 基于 high 列

    第 i 个值表示从 i-period 内的最高点回撤的百分比 (负数)
    """
    name = "max_dd"
    version = "v1"
    n_required = 1

    def compute(self, bars, period: int = 60, column: str = "high",
                **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < period:
            return IndicatorResult(self.name, None,
                                    {"period": period, "column": column},
                                    self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {period}")
        rolling_max = df[column].rolling(period).max()
        # 回撤 = (current - max) / max, 负数
        dd = (df[column] - rolling_max) / rolling_max * 100
        return IndicatorResult(self.name, dd,
                                {"period": period, "column": column},
                                self.version, self.n_required)


@IndicatorRegistry.register("adx")
class AdxIndicator:
    """ADX (Average Directional Index, 平均趋向指数) 0-100

    算法:
      +DM / -DM → DI+ / DI- → DX → ADX (n 周期平滑)
    """
    name = "adx"
    version = "v1"
    n_required = 28  # 2 * period + safety

    def compute(self, bars, period: int = 14, **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < period * 2 + 1:
            return IndicatorResult(self.name, None, {"period": period},
                                    self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {period * 2 + 1}")

        h = df["high"].values
        l = df["low"].values
        c = df["close"].values

        # 1. +DM / -DM
        h_diff = h[1:] - h[:-1]  # h[i+1] - h[i] (向上)
        l_diff = l[:-1] - l[1:]  # l[i] - l[i+1] (向下, l[i] > l[i+1] 时正)
        plus_dm = np.where((h_diff > l_diff) & (h_diff > 0), h_diff, 0.0)
        minus_dm = np.where((l_diff > h_diff) & (l_diff > 0), l_diff, 0.0)

        # 2. TR (True Range)
        tr1 = h[1:] - l[1:]
        tr2 = np.abs(h[1:] - c[:-1])
        tr3 = np.abs(l[1:] - c[:-1])
        tr = np.maximum(np.maximum(tr1, tr2), tr3)

        # 3. Wilder 平滑 (n 周期) — 等价于 n 周期 SMA 但递归, 节省计算
        #    Wilder 平滑本质 = n 周期 SMA (与 SMA 等价)
        #    smoothed[0] = sum(arr[:n]) / n  (n 周期平均)
        def _wilder_smooth(arr, n):
            if len(arr) < n:
                return np.array([])
            smoothed = np.empty(len(arr) - n + 1)
            smoothed[0] = arr[:n].sum() / n  # n 周期均值 (不是 sum)
            for i in range(1, len(smoothed)):
                smoothed[i] = smoothed[i - 1] - smoothed[i - 1] / n + arr[n + i - 1] / n
            return smoothed

        atr_smooth = _wilder_smooth(tr, period)
        plus_dm_smooth = _wilder_smooth(plus_dm, period)
        minus_dm_smooth = _wilder_smooth(minus_dm, period)

        # 4. +DI / -DI
        plus_di = 100 * plus_dm_smooth / atr_smooth
        minus_di = 100 * minus_dm_smooth / atr_smooth

        # 5. DX
        di_sum = plus_di + minus_di
        di_diff = np.abs(plus_di - minus_di)
        dx = np.where(di_sum == 0, 0, 100 * di_diff / di_sum)

        # 6. ADX = Wilder 平滑 DX
        adx = _wilder_smooth(dx, period)

        # 拼回原长度 (前面是 NaN, 后面是 adx)
        # 长度计算:
        #   tr/dm 砍 1 → len = n-1
        #   wilder smooth 砍 period → len = n-1-period+1 = n-period
        #   wilder smooth dx 砍 period → len = n-period-period+1 = n-2*period+1
        #   对应原 df 索引: 第 (2*period-1) 个 (因为 tr/dm 砍 1, smooth 砍 period)
        full = np.full(len(df), np.nan)
        offset = 2 * period - 1
        if len(adx) > 0 and offset + len(adx) <= len(full):
            full[offset:offset + len(adx)] = adx

        return IndicatorResult(self.name, pd.Series(full),
                                {"period": period}, self.version, self.n_required)


__all__ = [
    "RollingMean", "RollingStd", "RollingSum", "RollingMax",
    "PctRank", "Zscore",
    "MaxDrawdown", "AdxIndicator",
]
