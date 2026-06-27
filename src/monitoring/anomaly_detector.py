"""
monitoring.anomaly_detector — 异常检测 (ADR-0012 #83 D1)

3 类异常检测 (D1 全量覆盖):
  - data_delay:    数据延迟 (距最近一根 K 线时间差 > 阈值)
  - api_failure:   API 失败 (datafeed / simulator / order 连续失败次数 > 阈值)
  - order_timeout: 订单超时 (订单状态长时间未变化 > 阈值)

设计:
  - AnomalyDetector 是单实例 (与 EventEngine 绑定)
  - 提供 .record_data(ts) / .record_api_success() / .record_api_failure() /
        .record_order_sent(id) / .record_order_filled(id) / .record_order_rejected(id)
    5 类手动触发接口
  - 检测逻辑在 .tick() 中跑 (每秒或事件驱动); 触发阈值时 put EVENT_ANOMALY
  - 默认阈值保守: 数据延迟 30min / API 失败 5 次 / 订单超时 5min
  - 阈值可配置 (构造参数)

向后兼容:
  from src.monitoring.anomaly_detector import AnomalyDetector
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional

from ..event import Event, EventEngine
from .event_data import AnomalyEvent

logger = logging.getLogger(__name__)


# ── 默认阈值 ──────────────────────────────────────
DEFAULT_DATA_DELAY_SECONDS = 30 * 60        # 数据延迟 30 分钟
DEFAULT_API_FAILURE_THRESHOLD = 5            # 连续 API 失败 5 次
DEFAULT_API_FAILURE_RESET_SECONDS = 60       # 60 秒无新失败 → 计数清零
DEFAULT_ORDER_TIMEOUT_SECONDS = 5 * 60       # 订单超时 5 分钟


class AnomalyDetector:
    """3 类异常检测器 (单实例绑定 EventEngine)

    Args:
        event_engine:    EventEngine (用于 put EVENT_ANOMALY)
        data_delay_seconds:    数据延迟阈值 (默认 30 min)
        api_failure_threshold: API 连续失败阈值 (默认 5 次)
        api_failure_reset_seconds: API 失败计数重置 (默认 60s)
        order_timeout_seconds:  订单超时阈值 (默认 5 min)
    """

    def __init__(
        self,
        event_engine: Optional[EventEngine] = None,
        data_delay_seconds: int = DEFAULT_DATA_DELAY_SECONDS,
        api_failure_threshold: int = DEFAULT_API_FAILURE_THRESHOLD,
        api_failure_reset_seconds: int = DEFAULT_API_FAILURE_RESET_SECONDS,
        order_timeout_seconds: int = DEFAULT_ORDER_TIMEOUT_SECONDS,
    ) -> None:
        self._event_engine = event_engine
        self._data_delay_seconds = data_delay_seconds
        self._api_failure_threshold = api_failure_threshold
        self._api_failure_reset_seconds = api_failure_reset_seconds
        self._order_timeout_seconds = order_timeout_seconds

        # ── 状态 ──
        self._last_data_time: Optional[datetime] = None
        self._api_failure_count: int = 0
        self._last_api_failure_time: Optional[datetime] = None

        # order_id -> 发送时间
        self._order_sent_at: Dict[str, datetime] = {}

        self._lock = threading.RLock()
        self._anomaly_count: int = 0

    def set_event_engine(self, event_engine: EventEngine) -> None:
        """绑定 EventEngine (可选, 不传则只统计不推送)"""
        self._event_engine = event_engine

    # ── 数据延迟接口 ──────────────────────────────────────
    def record_data(self, ts: Optional[datetime] = None) -> None:
        """记录一根新数据 (K 线 / tick); ts 默认 now"""
        with self._lock:
            self._last_data_time = ts or datetime.now()
            self._api_failure_count = 0  # 数据正常 → 复位 API 失败计数

    def check_data_delay(self) -> bool:
        """检查数据延迟; 返回 True = 已超阈值, 已 emit EVENT_ANOMALY

        可由定时器每秒调用, 或手动触发
        """
        with self._lock:
            if self._last_data_time is None:
                return False
            elapsed = (datetime.now() - self._last_data_time).total_seconds()
            if elapsed > self._data_delay_seconds:
                self._emit(
                    kind="data_delay",
                    severity="warn",
                    detail=(
                        f"数据延迟 {elapsed:.0f}s > 阈值 {self._data_delay_seconds}s"
                    ),
                    source="datafeed",
                )
                return True
            return False

    # ── API 失败接口 ──────────────────────────────────────
    def record_api_failure(self, source: str = "unknown", detail: str = "") -> None:
        """记录一次 API 失败; 连续失败超阈值 → emit EVENT_ANOMALY"""
        now = datetime.now()
        with self._lock:
            # 超过 reset 窗口未失败 → 复位计数
            if (
                self._last_api_failure_time is not None
                and (now - self._last_api_failure_time).total_seconds()
                > self._api_failure_reset_seconds
            ):
                self._api_failure_count = 0
            self._api_failure_count += 1
            self._last_api_failure_time = now

            if self._api_failure_count >= self._api_failure_threshold:
                self._emit(
                    kind="api_failure",
                    severity="error",
                    detail=(
                        f"{source} 连续失败 {self._api_failure_count} 次"
                        + (f" ({detail})" if detail else "")
                    ),
                    source=source,
                )

    def record_api_success(self) -> None:
        """记录一次 API 成功 → 复位失败计数"""
        with self._lock:
            self._api_failure_count = 0
            self._last_api_failure_time = None

    # ── 订单超时接口 ──────────────────────────────────────
    def record_order_sent(self, order_id: str) -> None:
        """记录订单发送时间 (用于后续超时检测)"""
        with self._lock:
            self._order_sent_at[order_id] = datetime.now()

    def record_order_filled(self, order_id: str) -> None:
        """订单成交 → 从超时检测池中删除"""
        with self._lock:
            self._order_sent_at.pop(order_id, None)

    def record_order_rejected(self, order_id: str) -> None:
        """订单被拒 / 撤单 → 从超时检测池中删除"""
        with self._lock:
            self._order_sent_at.pop(order_id, None)

    def check_order_timeout(self) -> int:
        """检查订单超时; 返回超时订单数, 并 emit EVENT_ANOMALY

        可由定时器每分钟调用
        """
        now = datetime.now()
        timed_out: list[str] = []
        with self._lock:
            for order_id, sent_at in list(self._order_sent_at.items()):
                elapsed = (now - sent_at).total_seconds()
                if elapsed > self._order_timeout_seconds:
                    timed_out.append(order_id)
                    del self._order_sent_at[order_id]
        for order_id in timed_out:
            self._emit(
                kind="order_timeout",
                severity="warn",
                detail=(
                    f"订单 {order_id} 挂单 {self._order_timeout_seconds}s 未成交"
                ),
                source="OmsEngine",
            )
        return len(timed_out)

    # ── 定时器入口 (可选) ──────────────────────────────────────
    def tick(self) -> None:
        """定时器回调入口 — 每秒或每分钟调用

        - 每秒调用: 数据延迟检查
        - 每分钟调用: 订单超时检查
        (本方法本身不做限流, 由调用方按需频度)
        """
        self.check_data_delay()

    # ── 内部 emit ──────────────────────────────────────
    def _emit(self, kind: str, severity: str, detail: str, source: str) -> None:
        """put EVENT_ANOMALY; 无 event_engine 时只 log + 计数"""
        self._anomaly_count += 1
        event = AnomalyEvent(
            kind=kind,  # type: ignore[arg-type]
            severity=severity,  # type: ignore[arg-type]
            detail=detail,
            source=source,
        )
        logger.warning(f"异常检测 {kind} {severity}: {detail} (src={source})")
        if self._event_engine is None:
            return
        try:
            from ..event import EVENT_ANOMALY
            self._event_engine.put(Event(EVENT_ANOMALY, event))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"EVENT_ANOMALY 推送失败: {e}")

    # ── 状态查询 ──────────────────────────────────────
    @property
    def anomaly_count(self) -> int:
        return self._anomaly_count

    @property
    def pending_order_count(self) -> int:
        with self._lock:
            return len(self._order_sent_at)

    def reset(self) -> None:
        """清空所有状态 (测试用)"""
        with self._lock:
            self._last_data_time = None
            self._api_failure_count = 0
            self._last_api_failure_time = None
            self._order_sent_at.clear()
            self._anomaly_count = 0

    def __repr__(self) -> str:
        return (
            f"<AnomalyDetector anomalies={self._anomaly_count} "
            f"pending_orders={self.pending_order_count}>"
        )


__all__ = ["AnomalyDetector"]