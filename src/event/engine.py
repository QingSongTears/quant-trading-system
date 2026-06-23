"""
EventEngine — 简化版 vnpy EventEngine

借鉴 vnpy 4.4 的 EventEngine 设计, 适配本项目场景:
  - vnpy 用 Queue + Worker Thread 异步派发
  - 本项目用同步派发 (callback 直接执行), 因为事件量不大且方便调试

主要差异:
  - vnpy: 异步 Queue + Worker
  - 我们: 同步 dict[handler_list] 派发 (handler 异常时记录但不影响后续)

用法:
    from src.event import EventEngine, Event, EVENT_TICK

    engine = EventEngine(interval=1)  # 1秒定时器
    engine.register(EVENT_TICK, on_tick_handler)
    engine.start()

    # 推送事件
    engine.put(Event(EVENT_TICK, tick_data))
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """事件对象 (借鉴 vnpy.Event)"""
    type: str
    data: Any = None
    timestamp: datetime = field(default_factory=datetime.now)

    def __repr__(self) -> str:
        return f"Event(type={self.type!r}, data={type(self.data).__name__})"


# Handler 类型: Callable[[Event], None]
HandlerType = Callable[[Event], None]


class EventEngine:
    """
    事件引擎 — 同步派发版 (借鉴 vnpy)

    与 vnpy 不同:
      - vnpy 用 Queue + Worker Thread 异步, 我们同步 (handler 直接调用)
      - vnpy 有 _general_handlers 接收所有事件, 我们也支持
      - vnpy timer 单独线程, 我们也单独

    为何同步:
      - 简化调试 (handler 异常立即可见)
      - 避免多线程 race condition
      - A 股事件量小 (每秒 < 100 个 tick), 同步足够
    """

    def __init__(self, interval: int = 1) -> None:
        """
        Args:
            interval: 定时器间隔(秒), 默认 1
        """
        self._interval: int = interval
        self._handlers: Dict[str, List[HandlerType]] = defaultdict(list)
        self._general_handlers: List[HandlerType] = []

        self._active: bool = False
        self._timer_thread: Optional[threading.Thread] = None

        # 统计
        self._event_count: int = 0
        self._error_count: int = 0

    # ─────────────────────────────────────────
    #  注册 / 解注册
    # ─────────────────────────────────────────

    def register(self, type: str, handler: HandlerType) -> None:
        """注册特定类型事件的处理函数"""
        if handler not in self._handlers[type]:
            self._handlers[type].append(handler)
            logger.debug(f"注册 handler: {type} -> {handler.__name__}")

    def unregister(self, type: str, handler: HandlerType) -> None:
        """解注册"""
        if handler in self._handlers[type]:
            self._handlers[type].remove(handler)

    def register_general(self, handler: HandlerType) -> None:
        """注册所有事件的通用处理函数 (类似 vnpy _general_handlers)"""
        if handler not in self._general_handlers:
            self._general_handlers.append(handler)

    def unregister_general(self, handler: HandlerType) -> None:
        """解注册通用处理函数"""
        if handler in self._general_handlers:
            self._general_handlers.remove(handler)

    # ─────────────────────────────────────────
    #  事件发布 (核心)
    # ─────────────────────────────────────────

    def put(self, event: Event) -> None:
        """
        发布事件, 同步派发给所有匹配的 handler

        handler 异常被捕获并记录, 不影响其他 handler
        """
        if not self._active:
            logger.warning(f"Engine 未启动, 事件丢弃: {event.type}")
            return

        self._event_count += 1

        # 派发给特定类型 handlers
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                self._error_count += 1
                logger.error(
                    f"Handler {handler.__name__} 处理 {event.type} 失败: {e}",
                    exc_info=True,
                )

        # 派发给通用 handlers
        for handler in self._general_handlers:
            try:
                handler(event)
            except Exception as e:
                self._error_count += 1
                logger.error(
                    f"General handler {handler.__name__} 处理 {event.type} 失败: {e}",
                    exc_info=True,
                )

    # ─────────────────────────────────────────
    #  生命周期
    # ─────────────────────────────────────────

    def start(self) -> None:
        """启动事件引擎 (开始处理定时器事件)"""
        if self._active:
            return
        self._active = True
        self._timer_thread = threading.Thread(
            target=self._run_timer, name="EventEngine.timer", daemon=True,
        )
        self._timer_thread.start()
        logger.info(f"EventEngine 启动 (定时器间隔={self._interval}s)")

    def stop(self) -> None:
        """停止事件引擎"""
        self._active = False
        if self._timer_thread:
            self._timer_thread.join(timeout=2)
        logger.info(
            f"EventEngine 停止 (累计 {self._event_count} 事件, "
            f"{self._error_count} 错误)"
        )

    def _run_timer(self) -> None:
        """定时器线程: 每 interval 秒推送一个 TIMER 事件"""
        # 延迟导入避免循环
        from . import EVENT_TIMER
        while self._active:
            time.sleep(self._interval)
            if self._active:
                self.put(Event(EVENT_TIMER))

    # ─────────────────────────────────────────
    #  统计 / 调试
    # ─────────────────────────────────────────

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def error_count(self) -> int:
        return self._error_count

    def stats(self) -> dict:
        return {
            "active": self._active,
            "event_count": self._event_count,
            "error_count": self._error_count,
            "registered_handlers": {
                t: len(handlers) for t, handlers in self._handlers.items()
            },
            "general_handlers": len(self._general_handlers),
        }
