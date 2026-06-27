"""
RiskAlert — 风控告警事件载荷 (VNPY-3, ADR-0007 D3)

check_order() / check_daily_limit() 返回 (False, msg) 时, 同步通过
EVENT_RISK_ALERT 推送一条 RiskAlert, 让 UI / 监控可消费 (vnpy 风格双通道:
默认 hard reject + soft event).

字段:
  reason:    触发原因 (msg 文案, 与 check_* 返回值一致)
  level:     告警级别 — info(占位, 当前未触发) / warn(下单前拦截) / error(日熔断)
  vt_symbol: 触发标的 (日熔断可为空)
  timestamp: 触发时间 (默认 now, 方便前端展示)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


RiskAlertLevel = Literal["info", "warn", "error"]


@dataclass
class RiskAlert:
    """风控告警事件载荷 (EVENT_RISK_ALERT 的 data)"""
    reason: str = ""
    level: RiskAlertLevel = "warn"
    vt_symbol: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    def __repr__(self) -> str:
        sym = f" {self.vt_symbol}" if self.vt_symbol else ""
        return (
            f"<RiskAlert level={self.level}{sym} "
            f"reason={self.reason[:40]!r}>"
        )


__all__ = ["RiskAlert", "RiskAlertLevel"]