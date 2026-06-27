"""
monitoring.alert — 报警规则 + 多通道分发 (ADR-0012 #83 D2-B)

3 类组件:
  - AlertRule (数据类): 阈值规则 (kind + level + threshold + 描述)
  - AlertDispatcher: 多通道分发 (日志 + 事件 + Web UI)
  - 默认规则 (DEFAULT_RULES): 6 条常见阈值

设计:
  - AlertDispatcher 接收 AnomalyEvent / RiskAlert → 评估规则 → 触发 put EVENT_ALERT
  - 默认通道: logger.warning (日志) + EventEngine.put(EVENT_ALERT) (事件) + ring buffer (Web UI)
  - 外部通道 (微信/邮件) 留 v3.0 实盘 ADR-0013

向后兼容:
  from src.monitoring.alert import AlertRule, AlertDispatcher, DEFAULT_RULES
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

from ..event import Event, EventEngine
from ..risk.event_data import RiskAlert
from .event_data import AnomalyEvent
from .store import InMemoryBuffer, MetricStore

logger = logging.getLogger(__name__)


# ── AlertRule ──────────────────────────────────────
@dataclass
class AlertRule:
    """报警规则

    字段:
      name:        规则名 (用于 logging + UI 显示)
      kind:        触发类型 ("pnl_drawdown" / "api_failure" / "order_timeout" / ...)
      threshold:   阈值 (单位由 kind 决定: 百分比 / 次数 / 秒数)
      level:       严重级别 (info / warn / error)
      enabled:     是否启用 (默认 True, 可临时关)
      cooldown_seconds: 同一规则触发冷却 (默认 60s, 防刷屏)
      description: 规则描述 (UI 显示)
    """
    name: str
    kind: str
    threshold: float
    level: str = "warn"
    enabled: bool = True
    cooldown_seconds: float = 60.0
    description: str = ""

    def matches(self, kind: str) -> bool:
        return self.enabled and self.kind == kind


# ── 默认规则 (6 条常见阈值, v2.x 保守配置) ─────────────────────────────
DEFAULT_RULES: List[AlertRule] = [
    AlertRule(
        name="pnl_drawdown_5pct",
        kind="pnl_drawdown",
        threshold=5.0,
        level="warn",
        description="日内 PnL 回撤 ≥ 5%",
    ),
    AlertRule(
        name="pnl_drawdown_10pct",
        kind="pnl_drawdown",
        threshold=10.0,
        level="error",
        description="日内 PnL 回撤 ≥ 10% (熔断线)",
    ),
    AlertRule(
        name="api_failure_5x",
        kind="api_failure",
        threshold=5,
        level="error",
        description="API 连续失败 ≥ 5 次",
    ),
    AlertRule(
        name="order_timeout_5min",
        kind="order_timeout",
        threshold=300,
        level="warn",
        description="订单超时 ≥ 5 分钟未成交",
    ),
    AlertRule(
        name="data_delay_30min",
        kind="data_delay",
        threshold=1800,
        level="warn",
        description="数据延迟 ≥ 30 分钟",
    ),
    AlertRule(
        name="risk_alert_escalation",
        kind="risk_alert",
        threshold=1,
        level="error",
        cooldown_seconds=10,
        description="风控告警 (下单拦截 / 日熔断)",
    ),
]


# ── AlertDispatcher ──────────────────────────────────────
class AlertDispatcher:
    """报警多通道分发 (日志 + 事件 + Web UI)

    Args:
        rules:     报警规则列表 (默认 DEFAULT_RULES)
        store:     报警存储 (Web UI 拉取, 默认 InMemoryBuffer)
        engine:    EventEngine (用于 put EVENT_ALERT)

    通道:
      1. logger.warning (按 level)
      2. EventEngine.put(EVENT_ALERT) (内部事件流)
      3. ring buffer store (Web UI 拉取)
      4. 自定义 on_alert callback (可选, v3.0 外部通道接入点)
    """

    def __init__(
        self,
        rules: Optional[List[AlertRule]] = None,
        store: Optional[MetricStore[dict]] = None,
        engine: Optional[EventEngine] = None,
        on_alert: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._rules = list(rules) if rules is not None else list(DEFAULT_RULES)
        self._store = store or InMemoryBuffer[dict](maxlen=1000, name="alerts")
        self._engine = engine
        self._on_alert = on_alert

        # kind -> 上次触发时间 (cooldown 用)
        self._last_triggered: dict[str, datetime] = {}
        self._lock = threading.RLock()
        self._alert_count: int = 0

    # ── 规则管理 ──────────────────────────────────────
    def add_rule(self, rule: AlertRule) -> None:
        with self._lock:
            self._rules.append(rule)

    def disable(self, name: str) -> None:
        for r in self._rules:
            if r.name == name:
                r.enabled = False
                return

    def enable(self, name: str) -> None:
        for r in self._rules:
            if r.name == name:
                r.enabled = True
                return

    @property
    def rules(self) -> List[AlertRule]:
        return list(self._rules)

    # ── 评估入口 ──────────────────────────────────────
    def evaluate(
        self,
        kind: str,
        value: float,
        detail: str = "",
        source: str = "",
    ) -> Optional[dict]:
        """评估 kind + value, 命中规则则触发并返回 alert payload"""
        now = datetime.now()
        for rule in self._rules:
            if not rule.matches(kind):
                continue
            if value < rule.threshold:
                continue
            # cooldown 检查
            with self._lock:
                last = self._last_triggered.get(rule.name)
                if (
                    last is not None
                    and (now - last).total_seconds() < rule.cooldown_seconds
                ):
                    continue
                self._last_triggered[rule.name] = now

            payload = self._build_payload(
                rule=rule, value=value, detail=detail, source=source, now=now,
            )
            self._dispatch(payload)
            return payload
        return None

    def evaluate_risk_alert(self, alert: RiskAlert) -> Optional[dict]:
        """风控告警入口 (复用 evaluate 路径)"""
        # vt_symbol 空 = 日熔断, level=error; 否则单笔拦截, level=warn
        level_value = 2 if alert.level == "error" else 1
        return self.evaluate(
            kind="risk_alert",
            value=level_value,
            detail=alert.reason,
            source=alert.vt_symbol or "RiskEngine.daily",
        )

    def evaluate_anomaly(self, event: AnomalyEvent) -> Optional[dict]:
        """异常事件入口 — AnomalyDetector 已基于阈值触发, 这里直接传 threshold 越过

        注意: AnomalyDetector 已经在内部按阈值 emit EVENT_ANOMALY;
        AlertDispatcher 收到后只需按 kind 匹配规则 (任何 value > 0 视为命中).
        """
        # 直接调用 evaluate, 把 value 设大以越过所有阈值判断
        return self.evaluate(
            kind=event.kind,
            value=float("inf"),  # AnomalyDetector 已触发 → 这里强制命中
            detail=event.detail,
            source=event.source,
        )

    # ── 内部: 分发 + 存储 ──────────────────────────────────────
    def _build_payload(
        self,
        rule: AlertRule,
        value: float,
        detail: str,
        source: str,
        now: datetime,
    ) -> dict:
        return {
            "rule": rule.name,
            "kind": rule.kind,
            "level": rule.level,
            "threshold": rule.threshold,
            "value": value,
            "detail": detail,
            "source": source,
            "timestamp": now.isoformat(),
        }

    def _dispatch(self, payload: dict) -> None:
        """3 通道分发: 日志 + 事件 + ring buffer"""
        self._alert_count += 1

        # 1. 日志
        level_map = {"info": logger.info, "warn": logger.warning, "error": logger.error}
        log_fn = level_map.get(payload["level"], logger.warning)
        log_fn(
            f"ALERT[{payload['rule']}] {payload['detail']} "
            f"(value={payload['value']}, threshold={payload['threshold']})"
        )

        # 2. ring buffer (Web UI)
        try:
            self._store.append(payload)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"AlertDispatcher 写 store 失败: {e}")

        # 3. 事件
        if self._engine is not None:
            try:
                from ..event import EVENT_ALERT
                self._engine.put(Event(EVENT_ALERT, payload))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"EVENT_ALERT 推送失败: {e}")

        # 4. 自定义 callback (外部通道接入点)
        if self._on_alert is not None:
            try:
                self._on_alert(payload)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"on_alert callback 失败: {e}")

    # ── 查询 ──────────────────────────────────────
    def recent(self, n: int = 10):
        return self._store.recent(n)

    def clear(self) -> None:
        self._store.clear()
        self._alert_count = 0
        with self._lock:
            self._last_triggered.clear()

    @property
    def alert_count(self) -> int:
        return self._alert_count

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return (
            f"<AlertDispatcher rules={len(self._rules)} "
            f"alerts={self._alert_count} store_size={len(self._store)}>"
        )


__all__ = [
    "AlertRule",
    "AlertDispatcher",
    "DEFAULT_RULES",
]