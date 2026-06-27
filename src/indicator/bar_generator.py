"""
BarGenerator — K 线合成器 (借鉴 vnpy.trader.utility.BarGenerator, 2026-06-24)

设计目标:
  - 从低周期 K 线合成高周期 (1m → 5m → 15m → 1h → 1d)
  - 从 tick 合成 1m K 线 (OHLCV 累计)
  - 支持窗口 (window=N: 每 N 根低周期合成 1 根高周期)
  - 支持小时窗口 (1h/4h 等)
  - 可嵌套 (1m→5m→30m, 1m→15m→1h)
  - A 股适配: 日 K 周末/节假日无数据, 5m/15m 仅交易时段 09:30-11:30 13:00-15:00
  - (P3.3, 2026-06-27) 事件订阅: 可注册到 EventEngine, 自动响应 EVENT_TICK / EVENT_BAR

设计简化 (vs vnpy):
  - vnpy 支持 tick-level 1m 合成 (需 last_price/bid/ask 实时刷新)
  - 本项目主要用于回测, 1m 数据从 LocalDatafeed 直接拉, BarGenerator 专注"低 → 高" 合成

典型用法:
    # ── 1. 手动 push 模式 (回测) ──
    def on_5m_bar(bar: BarData):
        print(bar.close_price)

    bg_5m = BarGenerator(on_bar=on_5m_bar, window=5, interval="5m")
    for bar_1m in data_mgr.datafeed.get_bars("000001.SZ", "1m"):
        bg_5m.update_bar(bar_1m)

    # ── 2. 事件订阅模式 (实时, P3.3) ──
    from src.event import EventEngine
    engine = EventEngine()
    engine.start()

    bg_5m = BarGenerator(on_bar=on_5m_bar, window=5, interval="5m")
    bg_5m.subscribe(engine, vt_symbol="000001.SZ")
    # 现在 engine.put(Event(EVENT_BAR, bar)) 会自动触发 on_bar

    # ── 3. 嵌套 (跨级) ──
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
from typing import TYPE_CHECKING, Callable, List, Optional

from ..gateway import BarData, TickData

if TYPE_CHECKING:
    from ..event import Event, EventEngine

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

        # ── 事件订阅状态 (P3.3, 2026-06-27) ──
        self._event_engine: Optional["EventEngine"] = None
        self._vt_symbol_filter: Optional[str] = None
        self._interval_filter: Optional[str] = None

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
    #  事件订阅 (P3.3, 2026-06-27)
    # ─────────────────────────────────────────

    def subscribe(
        self,
        event_engine: "EventEngine",
        vt_symbol: Optional[str] = None,
        interval: Optional[str] = None,
    ) -> None:
        """订阅 EventEngine, 自动响应 EVENT_TICK / EVENT_BAR

        Args:
            event_engine: EventEngine 实例
            vt_symbol: 可选过滤 (e.g. "000001.SZ"), None = 不过滤, 所有 vt_symbol 都响应
            interval: 可选过滤 (e.g. "1m"), None = 不过滤, 仅 EVENT_BAR 生效
                     (tick 没有 interval 字段, 无需过滤)

        多次 subscribe 以前一次为准 (后调用的覆盖前面的, 旧 engine 不会被注销,
        调用方需自己保证不重复订阅)。

        用法:
            engine = EventEngine(); engine.start()
            bg = BarGenerator(on_bar=on_bar, window=5, interval="5m")
            bg.subscribe(engine, vt_symbol="000001.SZ")
            # 现在 engine.put(Event(EVENT_BAR, bar)) 会触发 bg.on_bar
        """
        # 延迟导入: 防循环依赖 (event_engine → ... → indicator)
        from ..event import EVENT_BAR, EVENT_TICK

        self._event_engine = event_engine
        self._vt_symbol_filter = vt_symbol
        self._interval_filter = interval

        event_engine.register(EVENT_TICK, self._on_event_tick)
        event_engine.register(EVENT_BAR, self._on_event_bar)
        logger.info(
            f"BarGenerator.subscribe: engine={event_engine.engine_name}, "
            f"vt_symbol={vt_symbol!r}, interval={interval!r}, "
            f"bg.interval={self.interval}"
        )

    def unsubscribe(self, event_engine: "EventEngine") -> None:
        """注销回调 (防内存泄漏)

        若未 subscribe 过, 此方法为 no-op。

        Args:
            event_engine: 必须与 subscribe 时同一个 engine 实例
        """
        # 延迟导入
        from ..event import EVENT_BAR, EVENT_TICK

        if self._event_engine is None:
            logger.debug("BarGenerator.unsubscribe: 未订阅, no-op")
            return

        # 注销回调, 防内存泄漏
        event_engine.unregister(EVENT_TICK, self._on_event_tick)
        event_engine.unregister(EVENT_BAR, self._on_event_bar)

        # 清状态
        self._event_engine = None
        self._vt_symbol_filter = None
        self._interval_filter = None
        logger.info("BarGenerator.unsubscribe: 已注销 EVENT_TICK / EVENT_BAR 回调")

    def _on_event_tick(self, event: "Event") -> None:
        """EVENT_TICK 回调 — 过滤后调 update_tick

        event.data 为 None → 静默忽略 (不抛异常)
        vt_symbol 不匹配 → 静默忽略
        """
        tick = event.data
        if tick is None:
            return
        if self._vt_symbol_filter is not None:
            vt = getattr(tick, "vt_symbol", "")
            if vt != self._vt_symbol_filter:
                return
        self.update_tick(tick)

    def _on_event_bar(self, event: "Event") -> None:
        """EVENT_BAR 回调 — 过滤后调 update_bar

        event.data 为 None → 静默忽略
        vt_symbol 不匹配 → 静默忽略
        interval 不匹配 → 静默忽略
        """
        bar = event.data
        if bar is None:
            return
        if self._vt_symbol_filter is not None:
            vt = getattr(bar, "vt_symbol", "")
            if vt != self._vt_symbol_filter:
                return
        if self._interval_filter is not None:
            bar_interval = getattr(bar, "interval", "")
            if bar_interval != self._interval_filter:
                return
        self.update_bar(bar)

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
