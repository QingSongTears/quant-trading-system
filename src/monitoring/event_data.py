"""
monitoring.event_data — 监控事件载荷 (ADR-0012 #83)

3 个 dataclass 作为监控事件的 payload:
  - PnlSnapshot:    PnL 快照 (EVENT_PNL_UPDATE 的 data)
  - PositionSnapshot: 持仓快照 (EVENT_POSITION_UPDATE 的 data)
  - AnomalyEvent:   异常事件 (EVENT_ANOMALY 的 data)

设计:
  - 全部用 @dataclass, 与 src/risk/event_data.py:RiskAlert 风格一致
  - 时间戳默认 datetime.now() (UTC-free, 本地时区, 与 vnpy 一致)
  - PnlSnapshot.daily_pnl 与 PositionSnapshot.unrealized_pnl 都用"元"为单位
  - AnomalyEvent.kind 是 Literal, 防止乱传字符串
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


# ── 异常事件类型 (ADR-0012 D1) ─────────────────────────────
AnomalyKind = Literal["data_delay", "api_failure", "order_timeout"]


@dataclass
class PnlSnapshot:
    """PnL 快照 (EVENT_PNL_UPDATE 的 data)

    字段:
      timestamp:    快照时间
      total_value:  总权益 (现金 + 持仓市值)
      cash:         现金
      position_value: 持仓市值
      unrealized_pnl: 持仓浮动盈亏
      realized_pnl:  当日已实现盈亏
      position_count: 当前持仓数量
      run_id:       模拟运行 ID (simulator 推送时填, 其它场景为空)
    """
    timestamp: datetime = field(default_factory=datetime.now)
    total_value: float = 0.0
    cash: float = 0.0
    position_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    position_count: int = 0
    run_id: str = ""

    def __repr__(self) -> str:
        return (
            f"<PnlSnapshot total={self.total_value:,.2f} "
            f"unrealized={self.unrealized_pnl:+,.2f} "
            f"positions={self.position_count}>"
        )


@dataclass
class PositionSnapshot:
    """持仓快照 (EVENT_POSITION_UPDATE 的 data)

    字段:
      timestamp:      快照时间
      vt_symbol:      合约 (000001.SZ, vnpy 命名)
      size:           持仓股数 (正=多头)
      cost_basis:     成本价
      current_price:  当前价
      market_value:   市值
      unrealized_pnl: 浮动盈亏
      profit_pct:     盈亏比例 (%)
      holding_days:   持仓天数
    """
    timestamp: datetime = field(default_factory=datetime.now)
    vt_symbol: str = ""
    size: int = 0
    cost_basis: float = 0.0
    current_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    profit_pct: float = 0.0
    holding_days: int = 0

    def __repr__(self) -> str:
        return (
            f"<PositionSnapshot {self.vt_symbol} "
            f"size={self.size} pnl={self.unrealized_pnl:+,.2f}>"
        )


@dataclass
class AnomalyEvent:
    """异常事件 (EVENT_ANOMALY 的 data)

    字段:
      kind:      异常类型 (data_delay / api_failure / order_timeout)
      severity:  严重级别 (info / warn / error)
      detail:    详细描述 (含阈值 + 实际值)
      source:    异常来源 (e.g. "BarGenerator" / "datafeed" / "OmsEngine")
      timestamp: 触发时间
    """
    kind: AnomalyKind = "data_delay"
    severity: Literal["info", "warn", "error"] = "warn"
    detail: str = ""
    source: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    def __repr__(self) -> str:
        return (
            f"<AnomalyEvent {self.kind} {self.severity} "
            f"src={self.source!r} detail={self.detail[:40]!r}>"
        )


__all__ = [
    "AnomalyKind",
    "PnlSnapshot",
    "PositionSnapshot",
    "AnomalyEvent",
]