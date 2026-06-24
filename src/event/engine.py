"""
EventEngine — 简化版 vnpy EventEngine

借鉴 vnpy 4.4 的 EventEngine 设计, 适配本项目场景:
  - vnpy 用 Queue + Worker Thread 异步派发
  - 本项目用同步派发 (callback 直接执行), 因为事件量不大且方便调试

主要差异:
  - vnpy: 异步 Queue + Worker
  - 我们: 同步 dict[handler_list] 派发 (handler 异常时记录但不影响后续)

线程安全 (2026-06-24 修复):
  - 加 RLock 保护 _handlers / _general_handlers / 计数器读写
  - 定时器用 threading.Event 替代 bool flag, stop 后毫秒级退出
  - put 默认 strict=True, 未启动时 raise RuntimeError (防"丢 tick 不报错")

架构 (2026-06-24):
  - 继承 BaseEngine (src/engine/base.py), 统一 is_active 状态机
  - 保留 _active 作为"定时器线程在跑"标志 (BaseEngine.is_active 共用同一字段)

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
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from ..engine import BaseEngine

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


class EventEngine(BaseEngine):
    """
    事件引擎 — 同步派发版 (借鉴 vnpy)

    与 vnpy 不同:
      - vnpy 用 Queue + Worker Thread 异步, 我们同步 (handler 直接调用)
      - vnpy 有 _general_handlers 接收所有事件, 我们也支持
      - vnpy timer 单独线程, 我们也单独

    为何同步:
      - 简化调试 (handler 异常立即可见)
      - A 股事件量小 (每秒 < 100 个 tick), 同步足够
      - 同步派发下 RLock 已足够, 无需 Queue + worker

    继承 BaseEngine (2026-06-24):
      - engine_name 固定为 "EventEngine"
      - is_active 复用 BaseEngine 状态机
    """

    def __init__(
        self, interval: int = 1, raise_on_inactive: bool = True,
    ) -> None:
        """
        Args:
            interval: 定时器间隔(秒), 默认 1
            raise_on_inactive: put 未启动时是否 raise (默认 True)
                - True: raise RuntimeError (推荐, 防"丢 tick 不报错")
                - False: warning + return (向后兼容, 仅 shutdown 阶段使用)
        """
        # BaseEngine init (engine_name="EventEngine" 固定)
        super().__init__(engine_name="EventEngine")

        self._interval: int = interval
        self._raise_on_inactive: bool = raise_on_inactive
        self._handlers: Dict[str, List[HandlerType]] = defaultdict(list)
        self._general_handlers: List[HandlerType] = []

        # 用 Event 替代 bool flag, 支持 wait() 毫秒级唤醒
        self._stop_event: threading.Event = threading.Event()
        self._timer_thread: Optional[threading.Thread] = None

        # 保护 _handlers / _general_handlers / 计数器 / _active 读写
        self._lock: threading.RLock = threading.RLock()

        # 统计
        self._event_count: int = 0
        self._error_count: int = 0

    # ─────────────────────────────────────────
    #  注册 / 解注册
    # ─────────────────────────────────────────

    def register(self, type: str, handler: HandlerType) -> None:
        """注册特定类型事件的处理函数"""
        with self._lock:
            if handler not in self._handlers[type]:
                self._handlers[type].append(handler)
        logger.debug(f"注册 handler: {type} -> {handler.__name__}")

    def unregister(self, type: str, handler: HandlerType) -> None:
        """解注册"""
        with self._lock:
            handlers = self._handlers.get(type)
            if handlers and handler in handlers:
                handlers.remove(handler)

    def register_general(self, handler: HandlerType) -> None:
        """注册所有事件的通用处理函数 (类似 vnpy _general_handlers)"""
        with self._lock:
            if handler not in self._general_handlers:
                self._general_handlers.append(handler)

    def unregister_general(self, handler: HandlerType) -> None:
        """解注册通用处理函数"""
        with self._lock:
            if handler in self._general_handlers:
                self._general_handlers.remove(handler)

    # ─────────────────────────────────────────
    #  事件发布 (核心)
    # ─────────────────────────────────────────

    def put(self, event: Event, strict: Optional[bool] = None) -> None:
        """
        发布事件, 同步派发给所有匹配的 handler

        Args:
            event: 事件对象
            strict: 覆盖 raise_on_inactive 配置, 决定未启动时是否 raise
                - None (默认): 用 self._raise_on_inactive
                - True: 强制 raise
                - False: 强制 warning + return

        Raises:
            RuntimeError: 引擎未启动且 strict/effective_raise 为 True

        handler 异常被捕获并记录, 不影响其他 handler
        """
        effective_raise = self._raise_on_inactive if strict is None else strict

        with self._lock:
            if not self._is_active:
                if effective_raise:
                    raise RuntimeError(
                        f"EventEngine 未启动, 无法 put({event.type!r}); "
                        f"请先调用 engine.start()"
                    )
                logger.warning(f"Engine 未启动, 事件丢弃: {event.type}")
                return

            self._event_count += 1
            # 拷贝 handler 列表, 防 handler 内部 unregister 改大小
            handlers = list(self._handlers.get(event.type, ()))
            general = list(self._general_handlers)

            # 锁内同步派发 (handler 通常很快, < 1ms; A 股场景可接受)
            for handler in handlers:
                try:
                    handler(event)
                except Exception as e:
                    self._error_count += 1
                    logger.error(
                        f"Handler {handler.__name__} 处理 {event.type} 失败: {e}",
                        exc_info=True,
                    )

            for handler in general:
                try:
                    handler(event)
                except Exception as e:
                    self._error_count += 1
                    logger.error(
                        f"General handler {handler.__name__} "
                        f"处理 {event.type} 失败: {e}",
                        exc_info=True,
                    )

    # ─────────────────────────────────────────
    #  生命周期
    # ─────────────────────────────────────────

    def start(self) -> None:
        """启动事件引擎 (开始处理定时器事件)

        继承 BaseEngine: 调 super().start() 设 is_active=True + log
        """
        with self._lock:
            if self._is_active:
                return
            self._stop_event.clear()
            thread = threading.Thread(
                target=self._run_timer, name="EventEngine.timer", daemon=True,
            )
            self._timer_thread = thread
        thread.start()
        # BaseEngine 标记 is_active=True + log
        super().start()
        logger.info(f"定时器间隔={self._interval}s")

    def stop(self, timeout: float = 2.0) -> None:
        """
        停止事件引擎

        Args:
            timeout: 等待定时器线程退出的最大秒数, 默认 2

        继承 BaseEngine: 调 super().stop() 设 is_active=False + log
        """
        # 先 set, 让 wait() 立即返回
        self._stop_event.set()
        with self._lock:
            # 立即设 is_active=False, 防 timer 线程下一次循环 put
            self._is_active = False
        if self._timer_thread:
            self._timer_thread.join(timeout=timeout)
            if self._timer_thread.is_alive():
                logger.warning(
                    f"定时器线程未在 {timeout}s 内退出, 将强杀"
                )
        # BaseEngine log + 状态最终确认
        super().stop()
        logger.info(
            f"累计 {self._event_count} 事件, {self._error_count} 错误"
        )

    def _run_timer(self) -> None:
        """定时器线程: 每 interval 秒推送一个 TIMER 事件

        用 _stop_event.wait(interval) 替代 time.sleep:
          - 正常: 超时返回 False, put 一个 TIMER 事件
          - 停止: set 后立即返回 True, break
        """
        # 延迟导入避免循环
        from . import EVENT_TIMER
        while not self._stop_event.is_set():
            # wait 返回 True 表示 stop_event 被 set (要退出)
            if self._stop_event.wait(self._interval):
                break
            # 二次校验, 防止 wait 被信号打断
            if self._stop_event.is_set():
                break
            with self._lock:
                if not self._is_active:
                    break
            self.put(Event(EVENT_TIMER))

    # ─────────────────────────────────────────
    #  统计 / 调试
    # ─────────────────────────────────────────

    @property
    def event_count(self) -> int:
        with self._lock:
            return self._event_count

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._error_count

    def stats(self) -> dict:
        with self._lock:
            return {
                "active": self._is_active,
                "event_count": self._event_count,
                "error_count": self._error_count,
                "registered_handlers": {
                    t: len(handlers)
                    for t, handlers in self._handlers.items()
                },
                "general_handlers": len(self._general_handlers),
            }
