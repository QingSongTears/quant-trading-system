"""
BarGenerator — K 线合成器 (借鉴 vnpy.trader.utility.BarGenerator, 2026-06-24)

设计目标:
  - 从低周期 K 线合成高周期 (1m → 5m → 15m → 1h → 1d)
  - 从 tick 合成 1m K 线 (OHLCV 累计)
  - 支持窗口 (window=N: 每 N 根低周期合成 1 根高周期)
  - 支持小时窗口 (1h/4h 等)
  - 可嵌套 (1m→5m→30m, 1m→15m→1h)
  - A 股适配: 日 K 周末/节假日无数据, 5m/15m 仅交易时段 09:30-11:30 13:00-15:00

设计简化 (vs vnpy):
  - vnpy 支持 tick-level 1m 合成 (需 last_price/bid/ask 实时刷新)
  - 本项目主要用于回测, 1m 数据从 LocalDatafeed 直接拉, BarGenerator 专注"低 → 高" 合成

典型用法:
    # 1m → 5m 合成
    def on_5m_bar(bar: BarData):
        print(bar.close_price)

    bg_5m = BarGenerator(on_bar=on_5m_bar, window=5, interval="5m")
    for bar_1m in data_mgr.datafeed.get_bars("000001.SZ", "1m"):
        bg_5m.update_bar(bar_1m)

    # 嵌套: 1m → 5m → 30m
    bg_30m = BarGenerator(on_bar=on_30m_bar, window=6, interval="30m")
    bg_5m = BarGenerator(
        on_bar=on_5m_bar,
        window=5,
        interval="5m",
        on_window_bar=bg_30m.update_bar,  # 嵌套
    )
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from ..gateway import BarData, TickData

logger = logging.getLogger(__name__)


# ── Interval 时间窗口 (单位: 分钟) ──────────────────────────


INTERVAL_TO_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


class BarGenerator:
    """
    K 线合成器 (借鉴 vnpy 4.4 trader.utility.BarGenerator, A 股适配版)

    两种用法:
      1. update_tick(tick)  从 tick 合成 1m K 线
      2. update_bar(bar)    从低周期 K 线合成高周期 (1m→5m, 1m→1h 等)

    嵌套:
      - on_window_bar=other_bg.update_bar 把本级窗口 BarData 喂给上级
      - 典型: bg_1m → bg_5m → bg_30m

    Args:
        on_bar: 1 根 K 线 (无论是 tick 合成还是 push 进来) 完成时回调
        window: 多少根"低周期"合成 1 根"高周期" (默认 1, 即不合成)
        interval: 合成的高周期 interval (默认 "1m", 需在 INTERVAL_TO_MINUTES 中)
        on_window_bar: 高周期 K 线完成时回调 (用于嵌套到上级)
    """

    def __init__(
        self,
        on_bar: Callable[[BarData], None],
        window: int = 1,
        interval: str = "1m",
        on_window_bar: Optional[Callable[[BarData], None]] = None,
    ) -> None:
        if interval not in INTERVAL_TO_MINUTES:
            raise ValueError(
                f"未知 interval: {interval!r} "
                f"(支持: {list(INTERVAL_TO_MINUTES.keys())})"
            )
        if window < 1:
            raise ValueError(f"window 必须 >= 1, 当前 {window}")

        self.on_bar: Callable = on_bar
        self.window: int = window
        self.interval: str = interval
        self.on_window_bar: Optional[Callable] = on_window_bar

        # 当前正在累计的高周期 K 线 (None = 还没开始)
        self._window_bar: Optional[BarData] = None
        # 当前高周期 K 线已累计多少根低周期
        self._window_count: int = 0

    # ─────────────────────────────────────────
    #  主入口
    # ─────────────────────────────────────────

    def update_tick(self, tick: TickData) -> None:
        """从 tick 合成 1m K 线 (OHLCV 累计)

        A 股 tick 实际没有 1m 切片数据, 此方法保留供未来对接实时行情
        """
        bar = BarData(
            symbol=tick.symbol,
            exchange=tick.exchange,
            datetime=datetime.combine(
                tick.datetime.date(),
                _floor_minute(tick.datetime, minutes=self._interval_minutes()),
            ),
            interval=self.interval,
            gateway_name=tick.gateway_name,
            open_price=tick.last_price,
            high_price=tick.last_price,
            low_price=tick.last_price,
            close_price=tick.last_price,
            volume=tick.last_volume,
            turnover=tick.turnover,
            open_interest=tick.open_interest,
        )
        self.update_bar(bar)

    def update_bar(self, bar: BarData) -> None:
        """从低周期 K 线 update, 累计到本级窗口

        Args:
            bar: 低周期 BarData (e.g. 1m), 完成后会:
                 1. 调 on_bar(bar)  (每根 1m 都触发)
                 2. 累计到本级窗口, 满 window 根后生成 window_bar, 调 on_window_bar
        """
        # 每根进来的 bar 都触发 on_bar
        self.on_bar(bar)

        # 累计到窗口
        if self._window_bar is None:
            self._window_bar = self._copy_bar(bar)
            self._window_count = 1
        else:
            self._accumulate(bar)
            self._window_count += 1

        # 窗口满 → 生成 window_bar
        if self._window_count >= self.window:
            self._finish_window()

    # ─────────────────────────────────────────
    #  内部辅助
    # ─────────────────────────────────────────

    def _interval_minutes(self) -> int:
        return INTERVAL_TO_MINUTES[self.interval]

    def _copy_bar(self, bar: BarData) -> BarData:
        """深拷贝 bar (dataclass replace)"""
        from dataclasses import replace
        return replace(bar)

    def _accumulate(self, bar: BarData) -> None:
        """把新 bar 的 OHLCV 累计到 _window_bar"""
        wb = self._window_bar
        assert wb is not None
        wb.high_price = max(wb.high_price, bar.high_price)
        wb.low_price = min(wb.low_price, bar.low_price)
        wb.close_price = bar.close_price
        wb.volume += bar.volume
        wb.turnover += bar.turnover
        if bar.open_interest:
            wb.open_interest = bar.open_interest

    def _finish_window(self) -> None:
        """窗口已满, 输出 window_bar"""
        if self.on_window_bar is not None:
            self.on_window_bar(self._window_bar)
        # 重置
        self._window_bar = None
        self._window_count = 0

    # ─────────────────────────────────────────
    #  状态查询 / 控制
    # ─────────────────────────────────────────

    def reset(self) -> None:
        """重置内部状态 (丢弃当前未完成的窗口)"""
        self._window_bar = None
        self._window_count = 0

    @property
    def is_in_window(self) -> bool:
        """是否正在累计一个窗口 (还没完成)"""
        return self._window_bar is not None

    @property
    def bars_in_window(self) -> int:
        """当前窗口已累计多少根"""
        return self._window_count

    def __repr__(self) -> str:
        # 防御: _window_bar 可能是 replace 出来的临时对象 (没有 interval)
        try:
            count = self._window_count
        except AttributeError:
            return "<BarGenerator (uninitialized)>"
        return (
            f"<BarGenerator interval={self.interval} window={self.window} "
            f"bars_in_window={count}>"
        )


# ── 工具函数 ──────────────────────────


def _floor_minute(dt: datetime, minutes: int) -> datetime.time:
    """把 datetime 向下取整到 N 分钟边界"""
    floored = dt.replace(second=0, microsecond=0)
    floored = floored - timedelta(
        minutes=floored.minute % minutes,
    )
    return floored.time()


# ── 便捷批量处理 ──────────────────────────


def bars_from_lower(
    bars: List[BarData],
    window: int,
    interval: str,
) -> List[BarData]:
    """
    一次性把低周期 bars 合成高周期 (utility, 不调回调)

    Args:
        bars: 低周期 K 线 (按时间升序)
        window: 多少根合成 1 根
        interval: 输出 interval

    Returns:
        高周期 BarData 列表 (满 window 根的, 末尾不足 window 的丢弃)

    Example:
        bars_1m = data_mgr.datafeed.get_bars("000001.SZ", "1m")
        bars_5m = bars_from_lower(bars_1m, window=5, interval="5m")
    """
    result: List[BarData] = []
    if not bars or window < 1:
        return result

    # 简单实现: 用第一个 bar 的 OHLCV 作 init, 后续累计
    for i in range(0, len(bars) - window + 1, window):
        chunk = bars[i : i + window]
        merged = BarData(
            symbol=chunk[0].symbol,
            exchange=chunk[0].exchange,
            interval=interval,
            datetime=chunk[0].datetime,
            gateway_name=chunk[0].gateway_name,
            open_price=chunk[0].open_price,
            high_price=max(b.high_price for b in chunk),
            low_price=min(b.low_price for b in chunk),
            close_price=chunk[-1].close_price,
            volume=sum(b.volume for b in chunk),
            turnover=sum(b.turnover for b in chunk),
        )
        result.append(merged)
    return result


__all__ = [
    "BarGenerator",
    "INTERVAL_TO_MINUTES",
    "bars_from_lower",
]
