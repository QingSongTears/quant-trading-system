"""
monitoring.collector — 监控事件订阅器 (ADR-0012 #83 D4-A)

3 个 collector 类 — 把 EventEngine 事件转为 store 内的指标:
  - PnlCollector:      订阅 EVENT_PNL_UPDATE → 写入 PnlSnapshot store
  - PositionCollector: 订阅 EVENT_POSITION_UPDATE → 写入 PositionSnapshot store
  - RiskAlertCollector: 订阅 EVENT_RISK_ALERT → 写入 RiskAlert (用通用 metric store)

设计:
  - 每个 collector 持有 1 个 MetricStore 实例 (PnlSnapshot / PositionSnapshot / RiskAlert)
  - 提供 .attach(event_engine) 注册 handler; .detach(event_engine) 解注册
  - 提供 .recent(n) / .__len__() / .clear() 透传到 store
  - handler 内部 try/except — 监控失败不能影响业务事件流

向后兼容:
  from src.monitoring.collector import PnlCollector, PositionCollector, RiskAlertCollector
"""
from __future__ import annotations

import logging
from typing import Optional

from ..event import Event, EventEngine
from ..risk.event_data import RiskAlert
from .event_data import PnlSnapshot, PositionSnapshot
from .store import InMemoryBuffer, MetricStore

logger = logging.getLogger(__name__)


# ── PnlCollector ──────────────────────────────────────
class PnlCollector:
    """EVENT_PNL_UPDATE 订阅器 → PnlSnapshot store"""

    def __init__(self, store: Optional[MetricStore[PnlSnapshot]] = None):
        self._store = store or InMemoryBuffer[PnlSnapshot](maxlen=1000, name="pnl")
        self._event_engine: Optional[EventEngine] = None
        self._count = 0

    def attach(self, event_engine: EventEngine) -> None:
        """订阅 EVENT_PNL_UPDATE"""
        from ..event import EVENT_PNL_UPDATE
        self._event_engine = event_engine
        event_engine.register(EVENT_PNL_UPDATE, self._on_event)

    def detach(self) -> None:
        """解注册"""
        if self._event_engine is None:
            return
        from ..event import EVENT_PNL_UPDATE
        try:
            self._event_engine.unregister(EVENT_PNL_UPDATE, self._on_event)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"PnlCollector 解注册失败: {e}")

    def _on_event(self, event: Event) -> None:
        try:
            data = event.data
            if isinstance(data, PnlSnapshot):
                self._store.append(data)
                self._count += 1
            else:
                logger.debug(
                    f"PnlCollector 收到非 PnlSnapshot: {type(data).__name__}"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"PnlCollector._on_event 失败: {e}")

    def recent(self, n: int = 10):
        return self._store.recent(n)

    def clear(self) -> None:
        self._store.clear()
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return f"<PnlCollector size={len(self)} count={self._count}>"


# ── PositionCollector ──────────────────────────────────────
class PositionCollector:
    """EVENT_POSITION_UPDATE 订阅器 → PositionSnapshot store"""

    def __init__(self, store: Optional[MetricStore[PositionSnapshot]] = None):
        self._store = store or InMemoryBuffer[PositionSnapshot](maxlen=1000, name="positions")
        self._event_engine: Optional[EventEngine] = None
        self._count = 0

    def attach(self, event_engine: EventEngine) -> None:
        from ..event import EVENT_POSITION_UPDATE
        self._event_engine = event_engine
        event_engine.register(EVENT_POSITION_UPDATE, self._on_event)

    def detach(self) -> None:
        if self._event_engine is None:
            return
        from ..event import EVENT_POSITION_UPDATE
        try:
            self._event_engine.unregister(EVENT_POSITION_UPDATE, self._on_event)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"PositionCollector 解注册失败: {e}")

    def _on_event(self, event: Event) -> None:
        try:
            data = event.data
            if isinstance(data, PositionSnapshot):
                self._store.append(data)
                self._count += 1
            else:
                logger.debug(
                    f"PositionCollector 收到非 PositionSnapshot: {type(data).__name__}"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"PositionCollector._on_event 失败: {e}")

    def recent(self, n: int = 10):
        return self._store.recent(n)

    def clear(self) -> None:
        self._store.clear()
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return f"<PositionCollector size={len(self)} count={self._count}>"


# ── RiskAlertCollector ──────────────────────────────────────
class RiskAlertCollector:
    """EVENT_RISK_ALERT 订阅器 → 通用 dict store (RiskAlert 用 dict 存储)

    注意:
      - RiskAlert 已有专用 store (src/risk/event_data.py 是 dataclass 定义)
      - 监控侧仅做"事件流镜像" — 把 RiskAlert 转 dict 存入监控 store
      - 用 InMemoryBuffer[dict] 简化 (避免引入新的 dataclass)
    """

    def __init__(self, store: Optional[MetricStore[dict]] = None):
        self._store = store or InMemoryBuffer[dict](maxlen=1000, name="risk_alerts")
        self._event_engine: Optional[EventEngine] = None
        self._count = 0

    def attach(self, event_engine: EventEngine) -> None:
        from ..event import EVENT_RISK_ALERT
        self._event_engine = event_engine
        event_engine.register(EVENT_RISK_ALERT, self._on_event)

    def detach(self) -> None:
        if self._event_engine is None:
            return
        from ..event import EVENT_RISK_ALERT
        try:
            self._event_engine.unregister(EVENT_RISK_ALERT, self._on_event)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"RiskAlertCollector 解注册失败: {e}")

    def _on_event(self, event: Event) -> None:
        try:
            data = event.data
            if isinstance(data, RiskAlert):
                payload = {
                    "reason": data.reason,
                    "level": data.level,
                    "vt_symbol": data.vt_symbol,
                    "timestamp": data.timestamp.isoformat()
                    if hasattr(data.timestamp, "isoformat")
                    else str(data.timestamp),
                }
            elif isinstance(data, dict):
                payload = dict(data)
            else:
                payload = {"raw": str(data)}
            self._store.append(payload)
            self._count += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"RiskAlertCollector._on_event 失败: {e}")

    def recent(self, n: int = 10):
        return self._store.recent(n)

    def clear(self) -> None:
        self._store.clear()
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return f"<RiskAlertCollector size={len(self)} count={self._count}>"


__all__ = [
    "PnlCollector",
    "PositionCollector",
    "RiskAlertCollector",
]