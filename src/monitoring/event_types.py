"""
monitoring.event_types — 监控事件常量 (ADR-0012 #83)

4 个监控事件常量 (新增到 src/event/__init__.py 也导出):
  - EVENT_PNL_UPDATE       PnL 推送 (simulator 每日推送)
  - EVENT_POSITION_UPDATE  持仓推送 (simulator 持仓变化时)
  - EVENT_ANOMALY          异常事件 (数据延迟/API 失败/订单超时)
  - EVENT_ALERT            报警事件 (AlertRule 触发后 put)

EVENT_RISK_ALERT 复用 (ADR-0007 D3 已存在, 不在本文件重复声明)

设计:
  - 本文件只是"重新导出 + 集中常量", 避免散落在多文件
  - monitoring 包外部使用: from src.event import EVENT_PNL_UPDATE 等
  - 本文件内部使用: from src.monitoring.event_types import EVENT_PNL_UPDATE
"""
from __future__ import annotations

from ..event import (
    EVENT_ANOMALY,
    EVENT_ALERT,
    EVENT_PNL_UPDATE,
    EVENT_POSITION_UPDATE,
)


__all__ = [
    "EVENT_PNL_UPDATE",
    "EVENT_POSITION_UPDATE",
    "EVENT_ANOMALY",
    "EVENT_ALERT",
]