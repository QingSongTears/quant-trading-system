"""
monitoring.hub — MonitoringHub 统一 facade (ADR-0012 #83)

MonitoringHub 是 monitoring 包对外的统一入口:
  - 持有 PnlCollector / PositionCollector / RiskAlertCollector / AnomalyDetector / AlertDispatcher
  - 提供 .attach(event_engine) 一键订阅全部 4 类事件
  - 提供 .recent_pnl/.recent_positions/.recent_alerts/.health 查询接口 (供 Web 路由用)
  - 可选 sqlite_path 启用 SQLite 持久化 (D3-B)

设计:
  - 单例模式: 一个进程一个 hub (全局共享 EventEngine 状态)
  - 线程安全: 内部 collector / dispatcher 已用 RLock
  - 向后兼容: from src.monitoring import MonitoringHub

向后兼容:
  from src.monitoring import MonitoringHub
  hub = MonitoringHub(event_engine=engine)
  hub.recent_pnl(10)
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional

from ..event import EventEngine
from ..event import EVENT_ANOMALY, EVENT_RISK_ALERT
from ..risk.event_data import RiskAlert
from .alert import AlertDispatcher, DEFAULT_RULES
from .anomaly_detector import AnomalyDetector
from .collector import (
    PnlCollector,
    PositionCollector,
    RiskAlertCollector,
)
from .event_data import (
    AnomalyEvent,
    PnlSnapshot,
    PositionSnapshot,
)
from .store import MetricStore, make_store

logger = logging.getLogger(__name__)


class MonitoringHub:
    """监控统一 facade — 一键绑定 EventEngine + 暴露查询接口

    Args:
        event_engine:    EventEngine (订阅 EVENT_PNL_UPDATE / POSITION / RISK_ALERT / ANOMALY)
        sqlite_path:     SQLite 路径 (None = 仅内存, 默认 None)
        maxlen:          ring buffer 容量 (默认 1000)
    """

    def __init__(
        self,
        event_engine: Optional[EventEngine] = None,
        sqlite_path: Optional[str] = None,
        maxlen: int = 1000,
    ) -> None:
        self._lock = threading.RLock()

        # ── 3 个 collector (Pnl / Position / RiskAlert) ──
        pnl_store = make_store(PnlSnapshot, sqlite_path=sqlite_path, maxlen=maxlen, name="pnl")
        pos_store = make_store(PositionSnapshot, sqlite_path=sqlite_path, maxlen=maxlen, name="positions")
        risk_store = make_store(dict, sqlite_path=sqlite_path, maxlen=maxlen, name="risk_alerts")

        self.pnl_collector = PnlCollector(store=pnl_store)
        self.position_collector = PositionCollector(store=pos_store)
        self.risk_alert_collector = RiskAlertCollector(store=risk_store)

        # ── 异常检测 + 报警 ──
        self.anomaly_detector = AnomalyDetector(event_engine=event_engine)
        self.alert_dispatcher = AlertDispatcher(
            engine=event_engine,
            on_alert=self._on_alert,
        )

        # ── 订阅 EventEngine ──
        self._engine_ref: Optional[EventEngine] = None
        if event_engine is not None:
            self.attach(event_engine)

        self._attached = event_engine is not None

    def attach(self, event_engine: EventEngine) -> None:
        """订阅 5 类监控事件 + 注入 AnomalyDetector + AlertDispatcher

        订阅的事件:
          - EVENT_PNL_UPDATE      → PnlCollector
          - EVENT_POSITION_UPDATE → PositionCollector
          - EVENT_RISK_ALERT      → RiskAlertCollector + AlertDispatcher.evaluate_risk_alert
          - EVENT_ANOMALY         → AlertDispatcher.evaluate_anomaly
        """
        with self._lock:
            self.pnl_collector.attach(event_engine)
            self.position_collector.attach(event_engine)
            self.risk_alert_collector.attach(event_engine)
            self.anomaly_detector.set_event_engine(event_engine)
            # AlertDispatcher 已在 __init__ 时绑 engine, 重新设置
            self.alert_dispatcher._engine = event_engine
            # 订阅 EVENT_RISK_ALERT / EVENT_ANOMALY → AlertDispatcher 评估入口
            event_engine.register(EVENT_RISK_ALERT, self._on_risk_alert_event)
            event_engine.register(EVENT_ANOMALY, self._on_anomaly_event)
            self._engine_ref = event_engine
            self._attached = True

    def detach(self) -> None:
        with self._lock:
            self.pnl_collector.detach()
            self.position_collector.detach()
            self.risk_alert_collector.detach()
            # 解注册 AlertDispatcher 订阅
            if self._engine_ref is not None:
                try:
                    self._engine_ref.unregister(EVENT_RISK_ALERT, self._on_risk_alert_event)
                    self._engine_ref.unregister(EVENT_ANOMALY, self._on_anomaly_event)
                except Exception:  # noqa: BLE001
                    pass
            self._attached = False

    # ── 查询接口 (Web 路由用) ──────────────────────────────────────
    def recent_pnl(self, n: int = 10) -> List[PnlSnapshot]:
        return self.pnl_collector.recent(n)

    def recent_positions(self, n: int = 50) -> List[PositionSnapshot]:
        return self.position_collector.recent(n)

    def recent_risk_alerts(self, n: int = 50) -> List[dict]:
        return self.risk_alert_collector.recent(n)

    def recent_alerts(self, n: int = 50) -> List[dict]:
        return self.alert_dispatcher.recent(n)

    def health(self) -> dict:
        """健康检查 — Web 路由 GET /api/monitoring/health"""
        return {
            "attached": self._attached,
            "pnl_count": len(self.pnl_collector),
            "position_count": len(self.position_collector),
            "risk_alert_count": len(self.risk_alert_collector),
            "alert_count": len(self.alert_dispatcher),
            "anomaly_count": self.anomaly_detector.anomaly_count,
            "pending_orders": self.anomaly_detector.pending_order_count,
        }

    # ── 内部: Anomaly → AlertDispatcher ──────────────────────────────────────
    def _on_anomaly_event(self, event) -> None:
        """订阅 EVENT_ANOMALY → AlertDispatcher 评估"""
        data = event.data
        if isinstance(data, AnomalyEvent):
            self.alert_dispatcher.evaluate_anomaly(data)

    def _on_risk_alert_event(self, event) -> None:
        """订阅 EVENT_RISK_ALERT → AlertDispatcher 评估"""
        data = event.data
        if isinstance(data, RiskAlert):
            self.alert_dispatcher.evaluate_risk_alert(data)

    def _on_alert(self, payload: dict) -> None:
        """AlertDispatcher 触发后, 自定义 hook (留 v3.0 外部通道接入)"""
        logger.info(f"ALERT: {payload['rule']} - {payload['detail']}")

    # ── 清理 ──────────────────────────────────────
    def clear(self) -> None:
        with self._lock:
            self.pnl_collector.clear()
            self.position_collector.clear()
            self.risk_alert_collector.clear()
            self.alert_dispatcher.clear()
            self.anomaly_detector.reset()

    @property
    def attached(self) -> bool:
        return self._attached

    def __repr__(self) -> str:
        return (
            f"<MonitoringHub attached={self._attached} "
            f"pnl={len(self.pnl_collector)} "
            f"positions={len(self.position_collector)} "
            f"alerts={len(self.alert_dispatcher)}>"
        )


# ── 全局 singleton (可选) ──────────────────────────────────────
_hub_singleton: Optional[MonitoringHub] = None
_hub_lock = threading.Lock()


def get_hub(event_engine: Optional[EventEngine] = None) -> MonitoringHub:
    """获取全局 MonitoringHub (单例)

    首次调用时创建; 后续调用返回同一实例.
    """
    global _hub_singleton
    with _hub_lock:
        if _hub_singleton is None:
            _hub_singleton = MonitoringHub(event_engine=event_engine)
        elif event_engine is not None and not _hub_singleton._attached:
            _hub_singleton.attach(event_engine)
        return _hub_singleton


def reset_hub() -> None:
    """重置 singleton (测试用)"""
    global _hub_singleton
    with _hub_lock:
        _hub_singleton = None


__all__ = ["MonitoringHub", "get_hub", "reset_hub"]