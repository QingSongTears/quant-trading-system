"""
Atomic 指标 — 17 算子 (从 ArrayManager 1:1 移植算法, 2026-06-25)

设计:
  - 每个算子是一个类, 用 @IndicatorRegistry.register("name") 注册
  - 实现 compute(bars, **params) -> IndicatorResult
  - 算法 1:1 复制 ArrayManager 的实现 (macd DEA 全序列递推 / kdj 持续状态 / boll ddof=1)
  - 状态类指标 (KDJ) 是 instance 维护 _k/_d, 同 ArrayManager 一致

使用:
    from src.indicator.atomic import *  # 触发注册
    from src.indicator.protocol import IndicatorRegistry

    rsi = IndicatorRegistry.get("rsi").compute(close, period=14)
    print(rsi.value)  # 0-100

输入 bars 接受: pd.Series / pd.DataFrame / np.ndarray / list[BarData] / list[dict]
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from .protocol import (
    IndicatorResult,
    IndicatorRegistry,
    to_close_series,
    to_ohlc_dataframe,
)


logger = logging.getLogger(__name__)


# ============================================================
#  趋势指标 (5)
# ============================================================


@IndicatorRegistry.register("sma")
class SmaIndicator:
    """简单移动平均 (Simple Moving Average)

    算子本身无状态. compute 返回最近 n 根 close 的均值.
    """
    name = "sma"
    version = "v1"
    n_required = 1

    def compute(self, bars, n: int = 20, **_unused) -> IndicatorResult:
        close = to_close_series(bars)
        if len(close) < n:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error=f"insufficient: have {len(close)}, need {n}")
        return IndicatorResult(self.name, float(close.iloc[-n:].mean()),
                                {"n": n}, self.version, self.n_required)


@IndicatorRegistry.register("ema")
class EmaIndicator:
    """指数移动平均 (Exponential Moving Average)

    算法: 从最早一根开始递归, alpha = 2/(n+1)
    (与同花顺/TradingView 一致)
    """
    name = "ema"
    version = "v1"
    n_required = 1

    def compute(self, bars, n: int = 12, **_unused) -> IndicatorResult:
        close = to_close_series(bars)
        if len(close) < n:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error=f"insufficient: have {len(close)}, need {n}")
        alpha = 2.0 / (n + 1)
        series = close.iloc[-n:].values
        ema_val = series[0]
        for v in series[1:]:
            ema_val = alpha * v + (1 - alpha) * ema_val
        return IndicatorResult(self.name, float(ema_val),
                                {"n": n}, self.version, self.n_required)


@IndicatorRegistry.register("macd")
class MacdIndicator:
    """MACD: (DIF, DEA, MACD柱)

    DIF = EMA(fast) - EMA(slow)
    DEA = EMA(DIF, signal) — 全 DIF 序列递推
    MACD = 2 * (DIF - DEA)

    (与 ArrayManager 一致, 修 2026-06-25 P0 bug)
    """
    name = "macd"
    version = "v1"
    n_required = 35  # 26 (slow) + 9 (signal)

    def compute(self, bars, fast: int = 12, slow: int = 26, signal: int = 9, **_unused) -> IndicatorResult:
        close = to_close_series(bars)
        need = slow + signal
        if len(close) < need:
            return IndicatorResult(self.name, None,
                                    {"fast": fast, "slow": slow, "signal": signal},
                                    self.version, self.n_required,
                                    error=f"insufficient: have {len(close)}, need {need}")

        # 取最近 need 根, 在此序列上递推
        arr = close.iloc[-need:].values

        # 1. EMA(fast) 和 EMA(slow) 全序列递推
        alpha_fast = 2.0 / (fast + 1)
        alpha_slow = 2.0 / (slow + 1)
        ema_fast_seq = np.empty(need, dtype=np.float64)
        ema_slow_seq = np.empty(need, dtype=np.float64)
        ema_fast_seq[0] = arr[0]
        ema_slow_seq[0] = arr[0]
        for i in range(1, need):
            ema_fast_seq[i] = alpha_fast * arr[i] + (1 - alpha_fast) * ema_fast_seq[i - 1]
            ema_slow_seq[i] = alpha_slow * arr[i] + (1 - alpha_slow) * ema_slow_seq[i - 1]

        # 2. DIF 全序列
        dif_seq = ema_fast_seq - ema_slow_seq
        dif = float(dif_seq[-1])

        # 3. DEA = EMA(DIF, signal) — 对 DIF 全序列递推
        alpha_sig = 2.0 / (signal + 1)
        dea_val = dif_seq[0]
        for x in dif_seq[1:]:
            dea_val = alpha_sig * x + (1 - alpha_sig) * dea_val

        macd_val = 2 * (dif - dea_val)
        return IndicatorResult(
            self.name, (float(dif), float(dea_val), float(macd_val)),
            {"fast": fast, "slow": slow, "signal": signal},
            self.version, self.n_required,
        )


@IndicatorRegistry.register("boll")
class BollIndicator:
    """布林带: (mid, upper, lower)

    修 2026-06-25: 用 ddof=1 (样本标准差, 跟 vnpy/TradingView 一致)
    """
    name = "boll"
    version = "v1"
    n_required = 20

    def compute(self, bars, n: int = 20, dev: float = 2.0, **_unused) -> IndicatorResult:
        close = to_close_series(bars)
        if len(close) < n:
            return IndicatorResult(self.name, None, {"n": n, "dev": dev},
                                    self.version, self.n_required,
                                    error=f"insufficient: have {len(close)}, need {n}")
        last_n = close.iloc[-n:]
        mid = float(last_n.mean())
        std = float(last_n.std(ddof=1))  # 修: 样本标准差
        upper = mid + dev * std
        lower = mid - dev * std
        return IndicatorResult(
            self.name, (mid, upper, lower),
            {"n": n, "dev": dev}, self.version, self.n_required,
        )


@IndicatorRegistry.register("donchian")
class DonchianIndicator:
    """唐奇安通道: (upper, lower) - 期间内最高/最低"""
    name = "donchian"
    version = "v1"
    n_required = 20

    def compute(self, bars, n: int = 20, **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < n:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {n}")
        upper = float(df["high"].iloc[-n:].max())
        lower = float(df["low"].iloc[-n:].min())
        return IndicatorResult(self.name, (upper, lower), {"n": n},
                                self.version, self.n_required)


# ============================================================
#  动量指标 (5)
# ============================================================


@IndicatorRegistry.register("rsi")
class RsiIndicator:
    """相对强弱指数 (Relative Strength Index) 0-100

    算法: 平均涨幅 / 平均跌幅, 平滑用简单均值
    (与 ArrayManager 一致; 注意 TradingView 用 Wilder 平滑,
     这里与 ArrayManager 行为完全一致, 验证过)
    """
    name = "rsi"
    version = "v1"
    n_required = 15  # 14 (period) + 1 (diff)

    def compute(self, bars, n: int = 14, **_unused) -> IndicatorResult:
        close = to_close_series(bars)
        if len(close) < n + 1:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error=f"insufficient: have {len(close)}, need {n + 1}")
        arr = close.iloc[-(n + 1):].values
        diffs = np.diff(arr)
        gains = np.where(diffs > 0, diffs, 0)
        losses = np.where(diffs < 0, -diffs, 0)
        avg_gain = gains.mean()
        avg_loss = losses.mean()
        if avg_loss == 0:
            value = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            value = 100 - 100 / (1 + rs)
        return IndicatorResult(self.name, float(value), {"n": n},
                                self.version, self.n_required)


@IndicatorRegistry.register("kdj")
class KdjIndicator:
    """KDJ: (K, D, J) — **有状态** (instance 维护 _k/_d 持续递推)

    RSV = (close - low_n) / (high_n - low_n) * 100
    K = SMA(RSV, m1)  → 持续递推
    D = SMA(K, m2)    → 持续递推
    J = 3K - 2D

    修 2026-06-25: 之前每次重算都重置 K=50/D=50, 前 50 根 KDJ 错
    现在通过 instance 字段 self._k / self._d 持续递推
    """
    name = "kdj"
    version = "v1"
    n_required = 9

    def __init__(self) -> None:
        # 持续状态: 默认中性 50 (行业惯例, 无前值时)
        self._k: float = 50.0
        self._d: float = 50.0
        # 已推根数 (用于重置)
        self._count: int = 0

    def reset(self) -> None:
        """重置 KDJ 状态 (切换股票时必须调)"""
        self._k = 50.0
        self._d = 50.0
        self._count = 0

    def compute(self, bars, n: int = 9, m1: int = 3, m2: int = 3,
                 reset: bool = False, **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < n:
            return IndicatorResult(self.name, None,
                                    {"n": n, "m1": m1, "m2": m2},
                                    self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {n}")

        if reset:
            self._k = 50.0
            self._d = 50.0
            self._count = 0

        # 取最近 n 根
        hh = df["high"].iloc[-n:].values
        ll = df["low"].iloc[-n:].values
        cc = df["close"].iloc[-n:].values

        high_n = float(hh.max())
        low_n = float(ll.min())
        if high_n == low_n:
            rsv = 50.0
        else:
            rsv = (float(cc[-1]) - low_n) / (high_n - low_n) * 100

        # 持续递推 K / D
        self._k = (m1 - 1) / m1 * self._k + 1 / m1 * rsv
        self._d = (m2 - 1) / m2 * self._d + 1 / m2 * self._k
        j = 3 * self._k - 2 * self._d
        self._count += 1
        return IndicatorResult(
            self.name, (float(self._k), float(self._d), float(j)),
            {"n": n, "m1": m1, "m2": m2}, self.version, self.n_required,
        )


@IndicatorRegistry.register("wr")
class WrIndicator:
    """Williams %R: -100 ~ 0, 越接近 0 越超买"""
    name = "wr"
    version = "v1"
    n_required = 14

    def compute(self, bars, n: int = 14, **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < n:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {n}")
        hh = df["high"].iloc[-n:].values
        ll = df["low"].iloc[-n:].values
        cc = df["close"].iloc[-1]
        high_n = float(hh.max())
        low_n = float(ll.min())
        if high_n == low_n:
            value = -50.0
        else:
            value = (high_n - float(cc)) / (high_n - low_n) * -100
        return IndicatorResult(self.name, float(value), {"n": n},
                                self.version, self.n_required)


@IndicatorRegistry.register("cci")
class CciIndicator:
    """CCI (Commodity Channel Index)"""
    name = "cci"
    version = "v1"
    n_required = 14

    def compute(self, bars, n: int = 14, **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < n:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {n}")
        h = df["high"].iloc[-n:].values
        l = df["low"].iloc[-n:].values
        c = df["close"].iloc[-n:].values
        tp = (h + l + c) / 3
        ma = tp.mean()
        md = np.abs(tp - ma).mean()
        if md == 0:
            value = 0.0
        else:
            value = (tp[-1] - ma) / (0.015 * md)
        return IndicatorResult(self.name, float(value), {"n": n},
                                self.version, self.n_required)


@IndicatorRegistry.register("roc")
class RocIndicator:
    """变动率 (Rate of Change) %"""
    name = "roc"
    version = "v1"
    n_required = 13  # 12 (period) + 1 (prev)

    def compute(self, bars, n: int = 12, **_unused) -> IndicatorResult:
        close = to_close_series(bars)
        if len(close) < n + 1:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error=f"insufficient: have {len(close)}, need {n + 1}")
        prev = close.iloc[-(n + 1)]
        curr = close.iloc[-1]
        if prev == 0:
            value = 0.0
        else:
            value = (curr - prev) / prev * 100
        return IndicatorResult(self.name, float(value), {"n": n},
                                self.version, self.n_required)


# ============================================================
#  波动指标 (3)
# ============================================================


@IndicatorRegistry.register("atr")
class AtrIndicator:
    """平均真实波幅 (Average True Range)"""
    name = "atr"
    version = "v1"
    n_required = 15  # 14 (period) + 1 (diff)

    def compute(self, bars, n: int = 14, **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < n + 1:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {n + 1}")
        tr = _tr_series(df)
        if len(tr) < n:
            value = 0.0
        else:
            value = float(tr[-n:].mean())
        return IndicatorResult(self.name, value, {"n": n},
                                self.version, self.n_required)


@IndicatorRegistry.register("std")
class StdIndicator:
    """close 的 N 周期标准差 — 修 2026-06-25: ddof=1 样本标准差"""
    name = "std"
    version = "v1"
    n_required = 20

    def compute(self, bars, n: int = 20, **_unused) -> IndicatorResult:
        close = to_close_series(bars)
        if len(close) < n:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error=f"insufficient: have {len(close)}, need {n}")
        return IndicatorResult(self.name, float(close.iloc[-n:].std(ddof=1)),
                                {"n": n}, self.version, self.n_required)


@IndicatorRegistry.register("natr")
class NatrIndicator:
    """归一化 ATR (ATR / close * 100)"""
    name = "natr"
    version = "v1"
    n_required = 15

    def compute(self, bars, n: int = 14, **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < n + 1:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {n + 1}")
        tr = _tr_series(df)
        if len(tr) < n:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error="tr_series empty")
        atr_val = float(tr[-n:].mean())
        close = float(df["close"].iloc[-1])
        if close == 0:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error="close=0")
        return IndicatorResult(self.name, float(atr_val / close * 100),
                                {"n": n}, self.version, self.n_required)


# ============================================================
#  成交量指标 (2)
# ============================================================


@IndicatorRegistry.register("obv")
class ObvIndicator:
    """能量潮 (On-Balance Volume) - 累计 OBV"""
    name = "obv"
    version = "v1"
    n_required = 2

    def compute(self, bars, **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < 2:
            return IndicatorResult(self.name, None, {}, self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need 2")
        closes = df["close"].values
        volumes = df["volume"].values
        obv = 0.0
        for i in range(1, len(closes)):
            if closes[i] > closes[i - 1]:
                obv += volumes[i]
            elif closes[i] < closes[i - 1]:
                obv -= volumes[i]
        return IndicatorResult(self.name, float(obv), {}, self.version, self.n_required)


@IndicatorRegistry.register("mfi")
class MfiIndicator:
    """资金流量指标 (Money Flow Index) 0-100"""
    name = "mfi"
    version = "v1"
    n_required = 15

    def compute(self, bars, n: int = 14, **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < n + 1:
            return IndicatorResult(self.name, None, {"n": n}, self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need {n + 1}")
        h = df["high"].iloc[-(n + 1):].values
        l = df["low"].iloc[-(n + 1):].values
        c = df["close"].iloc[-(n + 1):].values
        v = df["volume"].iloc[-(n + 1):].values
        tp = (h + l + c) / 3
        mf = tp * v
        diffs = np.diff(c)
        pos_mf = mf[1:][diffs > 0].sum()
        neg_mf = mf[1:][diffs < 0].sum()
        if neg_mf == 0:
            value = 100.0
        else:
            mf_ratio = pos_mf / neg_mf
            value = 100 - 100 / (1 + mf_ratio)
        return IndicatorResult(self.name, float(value), {"n": n},
                                self.version, self.n_required)


# ============================================================
#  辅助: TR / DM
# ============================================================


@IndicatorRegistry.register("tr")
class TrIndicator:
    """最近一根的真实波幅 (True Range)"""
    name = "tr"
    version = "v1"
    n_required = 2

    def compute(self, bars, **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < 2:
            return IndicatorResult(self.name, None, {}, self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need 2")
        trs = _tr_series(df)
        if len(trs) == 0:
            return IndicatorResult(self.name, None, {}, self.version, self.n_required,
                                    error="tr empty")
        return IndicatorResult(self.name, float(trs[-1]), {}, self.version, self.n_required)


@IndicatorRegistry.register("dm")
class DmIndicator:
    """最近一根的方向移动 (Directional Movement): (+DM, -DM)"""
    name = "dm"
    version = "v1"
    n_required = 2

    def compute(self, bars, **_unused) -> IndicatorResult:
        df = to_ohlc_dataframe(bars)
        if len(df) < 2:
            return IndicatorResult(self.name, None, {}, self.version, self.n_required,
                                    error=f"insufficient: have {len(df)}, need 2")
        h_up = float(df["high"].iloc[-1] - df["high"].iloc[-2])
        l_down = float(df["low"].iloc[-2] - df["low"].iloc[-1])
        plus_dm = h_up if h_up > l_down and h_up > 0 else 0
        minus_dm = l_down if l_down > h_up and l_down > 0 else 0
        return IndicatorResult(self.name, (float(plus_dm), float(minus_dm)),
                                {}, self.version, self.n_required)


# ============================================================
#  辅助函数
# ============================================================


def _tr_series(df) -> np.ndarray:
    """真实波幅 (True Range) 序列, 长度 = len(df) - 1"""
    if len(df) < 2:
        return np.array([])
    h = df["high"].iloc[1:].values
    l = df["low"].iloc[1:].values
    c_prev = df["close"].iloc[:-1].values
    tr1 = h - l
    tr2 = np.abs(h - c_prev)
    tr3 = np.abs(l - c_prev)
    return np.maximum(np.maximum(tr1, tr2), tr3)


__all__ = [
    # 趋势
    "SmaIndicator", "EmaIndicator", "MacdIndicator", "BollIndicator", "DonchianIndicator",
    # 动量
    "RsiIndicator", "KdjIndicator", "WrIndicator", "CciIndicator", "RocIndicator",
    # 波动
    "AtrIndicator", "StdIndicator", "NatrIndicator",
    # 成交量
    "ObvIndicator", "MfiIndicator",
    # 辅助
    "TrIndicator", "DmIndicator",
]
