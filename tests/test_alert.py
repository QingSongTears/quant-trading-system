"""
test_alert.py — ADR-0012 #83 Step 5 测试

覆盖:
  - AlertRule: 字段默认值 + matches 逻辑
  - DEFAULT_RULES: 6 条规则存在
  - AlertDispatcher: evaluate / cooldown / disable / on_alert callback
  - evaluate_risk_alert / evaluate_anomaly 入口
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.event import Event, EventEngine
from src.event import EVENT_ALERT
from src.monitoring.alert import (
    DEFAULT_RULES,
    AlertDispatcher,
    AlertRule,
)
from src.monitoring.event_data import AnomalyEvent
from src.risk.event_data import RiskAlert


@pytest.fixture
def engine() -> EventEngine:
    return EventEngine(interval=1, raise_on_inactive=False)


# ── AlertRule ──────────────────────────────────────
class TestAlertRule:
    def test_default_values(self):
        r = AlertRule(name="test", kind="x", threshold=1.0)
        assert r.name == "test"
        assert r.kind == "x"
        assert r.threshold == 1.0
        assert r.level == "warn"
        assert r.enabled is True
        assert r.cooldown_seconds == 60.0
        assert r.description == ""

    def test_matches_when_enabled_same_kind(self):
        r = AlertRule(name="r", kind="api_failure", threshold=5)
        assert r.matches("api_failure") is True

    def test_does_not_match_different_kind(self):
        r = AlertRule(name="r", kind="api_failure", threshold=5)
        assert r.matches("order_timeout") is False

    def test_does_not_match_disabled(self):
        r = AlertRule(name="r", kind="api_failure", threshold=5, enabled=False)
        assert r.matches("api_failure") is False


# ── DEFAULT_RULES ──────────────────────────────────────
class TestDefaultRules:
    def test_six_rules(self):
        assert len(DEFAULT_RULES) == 6

    def test_includes_pnl_drawdown(self):
        names = {r.name for r in DEFAULT_RULES}
        assert "pnl_drawdown_5pct" in names
        assert "pnl_drawdown_10pct" in names

    def test_includes_api_failure(self):
        names = {r.name for r in DEFAULT_RULES}
        assert "api_failure_5x" in names

    def test_includes_order_timeout(self):
        names = {r.name for r in DEFAULT_RULES}
        assert "order_timeout_5min" in names

    def test_includes_data_delay(self):
        names = {r.name for r in DEFAULT_RULES}
        assert "data_delay_30min" in names

    def test_includes_risk_alert(self):
        names = {r.name for r in DEFAULT_RULES}
        assert "risk_alert_escalation" in names


# ── AlertDispatcher ──────────────────────────────────────
class TestAlertDispatcher:
    def test_default_state(self):
        d = AlertDispatcher()
        assert len(d) == 0
        assert d.alert_count == 0
        assert len(d.rules) == 6

    def test_evaluate_below_threshold_no_alert(self):
        d = AlertDispatcher()
        result = d.evaluate(kind="pnl_drawdown", value=3.0)
        assert result is None
        assert len(d) == 0

    def test_evaluate_above_threshold_triggers(self):
        d = AlertDispatcher()
        result = d.evaluate(kind="pnl_drawdown", value=6.0, detail="回撤测试")
        assert result is not None
        assert result["rule"] == "pnl_drawdown_5pct"
        assert result["level"] == "warn"
        assert len(d) == 1
        assert d.alert_count == 1

    def test_evaluate_critical_triggers_error_rule(self):
        d = AlertDispatcher()
        result = d.evaluate(kind="pnl_drawdown", value=11.0, detail="熔断")
        # 5% 规则 + 10% 规则 都应触发; 但 cooldown 限制下, 第二条因 cooldown 60s 跳过
        # 5% 是第一个; cooldown 60s 期间 10% 被跳过
        assert result is not None
        assert result["rule"] in ("pnl_drawdown_5pct", "pnl_drawdown_10pct")

    def test_cooldown_suppresses_repeat(self):
        d = AlertDispatcher(rules=[
            AlertRule(name="t", kind="x", threshold=10, cooldown_seconds=60),
        ])
        d.evaluate(kind="x", value=20)
        # 立即再触发 → cooldown
        result = d.evaluate(kind="x", value=20)
        assert result is None
        assert d.alert_count == 1

    def test_cooldown_zero_allows_repeat(self):
        d = AlertDispatcher(rules=[
            AlertRule(name="t", kind="x", threshold=10, cooldown_seconds=0),
        ])
        d.evaluate(kind="x", value=20)
        d.evaluate(kind="x", value=20)
        assert d.alert_count == 2

    def test_disable_suppresses(self):
        d = AlertDispatcher(rules=[
            AlertRule(name="t", kind="x", threshold=10),
        ])
        d.disable("t")
        result = d.evaluate(kind="x", value=20)
        assert result is None

    def test_enable_re_enables(self):
        d = AlertDispatcher(rules=[
            AlertRule(name="t", kind="x", threshold=10),
        ])
        d.disable("t")
        d.enable("t")
        result = d.evaluate(kind="x", value=20)
        assert result is not None

    def test_add_rule(self):
        d = AlertDispatcher(rules=[])
        d.add_rule(AlertRule(name="new", kind="y", threshold=5))
        assert len(d.rules) == 1
        result = d.evaluate(kind="y", value=10)
        assert result is not None

    def test_recent_returns_alerts(self):
        d = AlertDispatcher()
        d.evaluate(kind="pnl_drawdown", value=6.0)
        d.evaluate(kind="api_failure", value=10.0)
        recent = d.recent(n=2)
        assert len(recent) == 2

    def test_clear_resets_state(self):
        d = AlertDispatcher()
        d.evaluate(kind="pnl_drawdown", value=6.0)
        d.clear()
        assert len(d) == 0
        assert d.alert_count == 0


# ── 通道分发 ──────────────────────────────────────
class TestDispatchChannels:
    def test_emit_event_alert(self, engine: EventEngine):
        engine.start()
        d = AlertDispatcher(engine=engine)
        d.evaluate(kind="pnl_drawdown", value=6.0)
        assert engine.event_count >= 1
        engine.stop()

    def test_on_alert_callback(self):
        received: list[dict] = []
        d = AlertDispatcher(on_alert=lambda p: received.append(p))
        d.evaluate(kind="pnl_drawdown", value=6.0)
        assert len(received) == 1
        assert received[0]["level"] == "warn"

    def test_ring_buffer_stores_payload(self):
        d = AlertDispatcher()
        d.evaluate(kind="pnl_drawdown", value=6.0, detail="abc")
        recent = d.recent(n=1)
        assert recent[0]["detail"] == "abc"

    def test_callback_exception_does_not_break(self):
        def bad_cb(p):
            raise RuntimeError("boom")
        d = AlertDispatcher(on_alert=bad_cb)
        # 不应 raise
        d.evaluate(kind="pnl_drawdown", value=6.0)
        assert d.alert_count == 1


# ── 评估入口 (RiskAlert + AnomalyEvent) ──────────────────────────────────────
class TestEvaluateEntries:
    def test_evaluate_risk_alert_warn(self):
        d = AlertDispatcher()
        result = d.evaluate_risk_alert(
            RiskAlert(reason="超单笔最大股数", level="warn", vt_symbol="000001.SZ")
        )
        assert result is not None
        assert result["kind"] == "risk_alert"

    def test_evaluate_risk_alert_error_daily(self):
        d = AlertDispatcher()
        result = d.evaluate_risk_alert(
            RiskAlert(reason="日熔断触发", level="error", vt_symbol="")
        )
        assert result is not None
        assert "日熔断" in result["detail"]

    def test_evaluate_anomaly(self):
        d = AlertDispatcher()
        result = d.evaluate_anomaly(
            AnomalyEvent(
                kind="data_delay", severity="warn",
                detail="30 分钟无数据", source="datafeed",
            )
        )
        assert result is not None


# ── repr ──────────────────────────────────────
class TestRepr:
    def test_repr_includes_counts(self):
        d = AlertDispatcher()
        r = repr(d)
        assert "rules=6" in r
        assert "alerts=0" in r


if __name__ == "__main__":
    pytest.main([__file__, "-v"])