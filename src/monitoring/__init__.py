"""
monitoring 包 (ADR-0012 #83) — v3.0 实盘化前置

公开 API:
  - 数据结构: PnlSnapshot / PositionSnapshot / AnomalyEvent
  - 事件类型: EVENT_PNL_UPDATE / EVENT_POSITION_UPDATE / EVENT_ANOMALY / EVENT_ALERT
  - 持久化:   MetricStore (抽象) / InMemoryBuffer / SqliteStore
  - 采集器:   PnlCollector / PositionCollector
  - 异常检测: AnomalyDetector
  - 报警:     AlertRule / AlertDispatcher

结构 (按职责拆分, 单文件 ≤ 200 行):
  - event_data.py:    3 个 dataclass (PnlSnapshot / PositionSnapshot / AnomalyEvent)
  - event_types.py:   4 个 EVENT 常量 re-export
  - store.py:         MetricStore 抽象 + InMemoryBuffer + SqliteStore
  - collector.py:     PnlCollector / PositionCollector (EventEngine handler)
  - anomaly_detector.py: AnomalyDetector (数据延迟/API 失败/订单超时)
  - alert.py:         AlertRule + AlertDispatcher (阈值规则 + 多通道分发)
  - hub.py:           MonitoringHub (统一 facade — 启动/订阅/查询)

向后兼容:
  from src.monitoring import (
      PnlSnapshot, PositionSnapshot, AnomalyEvent,
      MonitoringHub, InMemoryBuffer, AlertRule,
  )
  全部可用 (从子模块 re-export).
"""
from __future__ import annotations

# 公开数据结构
from .event_data import (
    AnomalyEvent,
    AnomalyKind,
    PnlSnapshot,
    PositionSnapshot,
)

# 事件常量 (从 src.event re-export, 集中入口)
from .event_types import (
    EVENT_ALERT,
    EVENT_ANOMALY,
    EVENT_PNL_UPDATE,
    EVENT_POSITION_UPDATE,
)


__all__ = [
    # 数据结构
    "PnlSnapshot",
    "PositionSnapshot",
    "AnomalyEvent",
    "AnomalyKind",
    # 事件常量
    "EVENT_PNL_UPDATE",
    "EVENT_POSITION_UPDATE",
    "EVENT_ANOMALY",
    "EVENT_ALERT",
]