"""
ArrayManager — K 线 → numpy 数组 + 17+ 技术指标 (借鉴 vnpy.trader.utility.ArrayManager, 2026-06-24)

设计目标:
  - 内部维护 numpy 数组 (open/high/low/close/volume/turnover)
  - 提供 17+ 指标方法 (MA/EMA/MACD/RSI/KDJ/Bollinger/ATR/OBV/...)
  - 增量更新 (update_bar 推 1 根, 内部数组 append)
  - 充分历史数据才能计算, 否则返 None / 空数组

典型用法:
    from src.indicator import ArrayManager
    from src.data import data_mgr

    bars = data_mgr.datafeed.get_bars("000001.SZ", "1d", count=60)
    am = ArrayManager(size=60)
    for bar in bars:
        am.update_bar(bar)

    # 17+ 指标可用
    print(am.sma(20))       # 简单移动平均
    print(am.ema(12))       # 指数移动平均
    print(am.macd())        # (DIF, DEA, MACD)
    print(am.rsi(14))       # 相对强弱
    print(am.kdj(9, 3, 3))  # (K, D, J)
    print(am.boll(20, 2))   # (mid, upper, lower)
    print(am.atr(14))       # 平均真实波幅
    print(am.obv())         # 能量潮

参考:
  - vnpy 4.4 ArrayManager
  - 经典技术分析 (Murphy / Pring)
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np

from ..gateway import BarData

logger = logging.getLogger(__name__)


class ArrayManager:
    """
    K 线 → numpy 数组 + 技术指标计算

    Args:
        size: 最多保留多少根 K 线 (FIFO, 超过自动 pop)

    Attributes (内部 numpy 数组):
        open / high / low / close / volume / turnover
        datetime (object array)

    指标方法 (17+):
        sma / ema / macd / boll / donchian
        rsi / kdj / wr / cci / roc
        atr / std / natr
        obv / mfi
        tr / dm
    """

    def __init__(self, size: int = 100) -> None:
        if size < 1:
            raise ValueError(f"size 必须 >= 1, 当前 {size}")
        self.size: int = size
        self.count: int = 0  # 已 push 的 K 线数

        # numpy 数组 (dtype=object for datetime)
        self.open: np.ndarray = np.zeros(size, dtype=np.float64)
        self.high: np.ndarray = np.zeros(size, dtype=np.float64)
        self.low: np.ndarray = np.zeros(size, dtype=np.float64)
        self.close: np.ndarray = np.zeros(size, dtype=np.float64)
        self.volume: np.ndarray = np.zeros(size, dtype=np.float64)
        self.turnover: np.ndarray = np.zeros(size, dtype=np.float64)
        self.datetime: np.ndarray = np.empty(size, dtype=object)

    # ─────────────────────────────────────────
    #  数据更新
    # ─────────────────────────────────────────

    def update_bar(self, bar: BarData) -> None:
        """推 1 根 bar, 内部 FIFO 更新"""
        # 把数组左移 1 位 (FIFO)
        self.open[:-1] = self.open[1:]
        self.high[:-1] = self.high[1:]
        self.low[:-1] = self.low[1:]
        self.close[:-1] = self.close[1:]
        self.volume[:-1] = self.volume[1:]
        self.turnover[:-1] = self.turnover[1:]
        self.datetime[:-1] = self.datetime[1:]

        # 末尾写入新 bar
        self.open[-1] = bar.open_price
        self.high[-1] = bar.high_price
        self.low[-1] = bar.low_price
        self.close[-1] = bar.close_price
        self.volume[-1] = bar.volume
        self.turnover[-1] = bar.turnover
        self.datetime[-1] = bar.datetime

        if self.count < self.size:
            self.count += 1

    def update_bars(self, bars: List[BarData]) -> None:
        """批量推多根 bar (一次性 init 或回填)"""
        for bar in bars:
            self.update_bar(bar)

    def reset(self) -> None:
        """重置 (清空所有数据)"""
        self.open[:] = 0.0
        self.high[:] = 0.0
        self.low[:] = 0.0
        self.close[:] = 0.0
        self.volume[:] = 0.0
        self.turnover[:] = 0.0
        self.datetime[:] = None
        self.count = 0

    # ── 切片便捷 ──────────────────────────

    def _last_n(self, n: int) -> Tuple[np.ndarray, ...]:
        """取最近 n 根的 (open, high, low, close)"""
        return (
            self.open[-n:],
            self.high[-n:],
            self.low[-n:],
            self.close[-n:],
        )

    def _check_window(self, n: int) -> bool:
        """检查是否有足够数据计算 n 周期指标"""
        return self.count >= n

    # ─────────────────────────────────────────
    #  趋势指标 (5)
    # ─────────────────────────────────────────

    def sma(self, n: int) -> Optional[float]:
        """简单移动平均 (Simple Moving Average)"""
        if not self._check_window(n):
            return None
        return float(self.close[-n:].mean())

    def ema(self, n: int) -> Optional[float]:
        """指数移动平均 (Exponential Moving Average), 用 pandas-style 平滑"""
        if not self._check_window(n):
            return None
        alpha = 2.0 / (n + 1)
        # 从最早一根开始递归
        series = self.close[-n:]
        ema_val = series[0]
        for v in series[1:]:
            ema_val = alpha * v + (1 - alpha) * ema_val
        return float(ema_val)

    def macd(
        self,
        fast: int = 12, slow: int = 26, signal: int = 9,
    ) -> Optional[Tuple[float, float, float]]:
        """MACD: (DIF, DEA, MACD柱)
        DIF = EMA(fast) - EMA(slow)
        DEA = EMA(DIF, signal)
        MACD = 2 * (DIF - DEA)
        """
        # 简化: 用最近 fast+slow+signal 根数据算
        need = slow + signal + 5
        if not self._check_window(need):
            return None

        # 用 pandas-style EMA (递归)
        def _ema(arr, n):
            alpha = 2.0 / (n + 1)
            v = arr[0]
            for x in arr[1:]:
                v = alpha * x + (1 - alpha) * v
            return v

        close = self.close[-need:]
        ema_fast = _ema(close, fast)
        ema_slow = _ema(close, slow)
        dif = ema_fast - ema_slow

        # DEA = EMA(DIF, signal), 需要历史 DIF 序列
        # 简化: 取最近 signal 根 close 算的 DIF 序列, 递归
        dif_series = []
        for i in range(signal):
            sub = close[-(slow + i):]
            dif_series.append(_ema(sub, fast) - _ema(sub, slow))
        dea = _ema(np.array(dif_series), signal)

        macd_val = 2 * (dif - dea)
        return (float(dif), float(dea), float(macd_val))

    def boll(
        self, n: int = 20, dev: float = 2.0,
    ) -> Optional[Tuple[float, float, float]]:
        """布林带: (mid, upper, lower)"""
        if not self._check_window(n):
            return None
        mid = float(self.close[-n:].mean())
        std = float(self.close[-n:].std(ddof=0))
        upper = mid + dev * std
        lower = mid - dev * std
        return (mid, upper, lower)

    def donchian(
        self, n: int = 20,
    ) -> Optional[Tuple[float, float]]:
        """唐奇安通道: (upper, lower) - 期间内最高/最低"""
        if not self._check_window(n):
            return None
        return (float(self.high[-n:].max()), float(self.low[-n:].min()))

    # ─────────────────────────────────────────
    #  动量指标 (5)
    # ─────────────────────────────────────────

    def rsi(self, n: int = 14) -> Optional[float]:
        """相对强弱指数 (Relative Strength Index) 0-100"""
        if not self._check_window(n + 1):
            return None
        diffs = np.diff(self.close[-(n + 1):])
        gains = np.where(diffs > 0, diffs, 0)
        losses = np.where(diffs < 0, -diffs, 0)
        avg_gain = gains.mean()
        avg_loss = losses.mean()
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return float(100 - 100 / (1 + rs))

    def kdj(
        self, n: int = 9, m1: int = 3, m2: int = 3,
    ) -> Optional[Tuple[float, float, float]]:
        """KDJ: (K, D, J)
        RSV = (close - low_n) / (high_n - low_n) * 100
        K = SMA(RSV, m1)
        D = SMA(K, m2)
        J = 3K - 2D
        """
        if not self._check_window(n):
            return None
        # 取最近 n 根
        _, hh, ll, cc = self._last_n(n)
        high_n = float(hh.max())
        low_n = float(ll.min())
        if high_n == low_n:
            rsv = 50.0
        else:
            rsv = (float(cc[-1]) - low_n) / (high_n - low_n) * 100

        # K/D 累积 (从第 1 根 rsv 开始递归)
        # 简化: 假设历史 K=50, D=50
        k_prev, d_prev = 50.0, 50.0
        k = (m1 - 1) / m1 * k_prev + 1 / m1 * rsv
        d = (m2 - 1) / m2 * d_prev + 1 / m2 * k
        j = 3 * k - 2 * d
        return (float(k), float(d), float(j))

    def wr(self, n: int = 14) -> Optional[float]:
        """Williams %R: -100 ~ 0, 越接近 0 越超买"""
        if not self._check_window(n):
            return None
        _, hh, ll, cc = self._last_n(n)
        high_n = float(hh.max())
        low_n = float(ll.min())
        if high_n == low_n:
            return -50.0
        return float((high_n - float(cc[-1])) / (high_n - low_n) * -100)

    def cci(self, n: int = 14) -> Optional[float]:
        """CCI (Commodity Channel Index)"""
        if not self._check_window(n):
            return None
        o, h, l, c = self._last_n(n)
        tp = (h + l + c) / 3  # typical price
        ma = tp.mean()
        md = np.abs(tp - ma).mean()
        if md == 0:
            return 0.0
        cci_val = (tp[-1] - ma) / (0.015 * md)
        return float(cci_val)

    def roc(self, n: int = 12) -> Optional[float]:
        """变动率 (Rate of Change) %"""
        if not self._check_window(n + 1):
            return None
        prev = self.close[-(n + 1)]
        curr = self.close[-1]
        if prev == 0:
            return 0.0
        return float((curr - prev) / prev * 100)

    # ─────────────────────────────────────────
    #  波动指标 (3)
    # ─────────────────────────────────────────

    def atr(self, n: int = 14) -> Optional[float]:
        """平均真实波幅 (Average True Range)"""
        if not self._check_window(n + 1):
            return None
        tr = self._tr_series()
        return float(tr[-n:].mean())

    def std(self, n: int = 20) -> Optional[float]:
        """close 的 N 周期标准差"""
        if not self._check_window(n):
            return None
        return float(self.close[-n:].std(ddof=0))

    def natr(self, n: int = 14) -> Optional[float]:
        """归一化 ATR (ATR / close * 100)"""
        atr_val = self.atr(n)
        if atr_val is None or self.close[-1] == 0:
            return None
        return float(atr_val / self.close[-1] * 100)

    # ─────────────────────────────────────────
    #  成交量指标 (2)
    # ─────────────────────────────────────────

    def obv(self) -> Optional[float]:
        """能量潮 (On-Balance Volume) - 累计 OBV"""
        if self.count < 2:
            return None
        diffs = np.diff(self.close[:self.count])
        obv = 0.0
        for i, d in enumerate(diffs):
            if d > 0:
                obv += self.volume[i + 1]
            elif d < 0:
                obv -= self.volume[i + 1]
        return float(obv)

    def mfi(self, n: int = 14) -> Optional[float]:
        """资金流量指标 (Money Flow Index) 0-100"""
        if not self._check_window(n + 1):
            return None
        o, h, l, c, v = (
            self.open[-(n + 1):], self.high[-(n + 1):], self.low[-(n + 1):],
            self.close[-(n + 1):], self.volume[-(n + 1):],
        )
        tp = (h + l + c) / 3
        mf = tp * v
        diffs = np.diff(c)
        pos_mf = mf[1:][diffs > 0].sum()
        neg_mf = mf[1:][diffs < 0].sum()
        if neg_mf == 0:
            return 100.0
        mf_ratio = pos_mf / neg_mf
        return float(100 - 100 / (1 + mf_ratio))

    # ─────────────────────────────────────────
    #  辅助: TR / DM (其他指标的输入)
    # ─────────────────────────────────────────

    def _tr_series(self) -> np.ndarray:
        """真实波幅 (True Range) 序列, 长度 = count - 1"""
        if self.count < 2:
            return np.array([])
        h, l, c = self.high[1:self.count], self.low[1:self.count], self.close[:self.count - 1]
        tr1 = h - l
        tr2 = np.abs(h - c)
        tr3 = np.abs(l - c)
        return np.maximum(np.maximum(tr1, tr2), tr3)

    def tr(self) -> Optional[float]:
        """最近一根的 TR"""
        trs = self._tr_series()
        if len(trs) == 0:
            return None
        return float(trs[-1])

    def dm(self) -> Optional[Tuple[float, float]]:
        """最近一根的方向移动 (Directional Movement): (+DM, -DM)"""
        if self.count < 2:
            return None
        h_up = self.high[-1] - self.high[-2]
        l_down = self.low[-2] - self.low[-1]
        plus_dm = float(h_up if h_up > l_down and h_up > 0 else 0)
        minus_dm = float(l_down if l_down > h_up and l_down > 0 else 0)
        return (plus_dm, minus_dm)

    # ─────────────────────────────────────────
    #  调试
    # ─────────────────────────────────────────

    @property
    def last_close(self) -> float:
        """最近一根收盘价"""
        return float(self.close[-1]) if self.count > 0 else 0.0

    def __repr__(self) -> str:
        return (
            f"<ArrayManager size={self.size} count={self.count} "
            f"last_close={self.last_close:.2f}>"
        )


__all__ = ["ArrayManager"]
