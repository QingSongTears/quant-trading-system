"""
test_hub.py — ADR-0012 #83 Step 7 测试

覆盖:
  - MonitoringHub: attach/detach/recent/health/clear
  - get_hub / reset_hub singleton
  - 与 EventEngine 集成 (EVENT_PNL_UPDATE / POSITION_UPDATE / RISK_ALERT / ANOMALY 全链路)
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.event import Event, EventEngine
from src.event import (
    EVENT_PNL_UPDATE,
    EVENT_POSITION_UPDATE,
    EVENT_RISK_ALERT,
    EVENT_ANOMALY,
)
from src.monitoring import (
    AnomalyEvent,
    PnlSnapshot,
    PositionSnapshot,
)
from src.monitoring.hub import MonitoringHub, get_hub, reset_hub
from src.risk.event_data import RiskAlert


@pytest.fixture
def engine() -> EventEngine:
    return EventEngine(interval=1, raise_on_inactive=False)


# ── MonitoringHub ──────────────────────────────────────
class TestMonitoringHub:
    def test_default_state(self, engine: EventEngine):
        hub = MonitoringHub(event_engine=engine)
        engine.start()
        h = hub.health()
        assert h["attached"] is True
        assert h["pnl_count"] == 0
        assert h["position_count"] == 0
        assert h["risk_alert_count"] == 0
        engine.stop()

    def test_no_event_engine(self):
        hub = MonitoringHub()
        assert hub.attached is False
        h = hub.health()
        assert h["attached"] is False

    def test_attach_later(self, engine: EventEngine):
        engine.start()
        hub = MonitoringHub()
        assert hub.attached is False
        hub.attach(engine)
        assert hub.attached is True
        engine.stop()

    def test_recent_pnl(self, engine: EventEngine):
        engine.start()
        hub = MonitoringHub(event_engine=engine)
        engine.put(Event(EVENT_PNL_UPDATE, PnlSnapshot(total_value=100.0)))
        engine.put(Event(EVENT_PNL_UPDATE, PnlSnapshot(total_value=200.0)))
        recent = hub.recent_pnl(n=2)
        assert len(recent) == 2
        engine.stop()

    def test_recent_positions(self, engine: EventEngine):
        engine.start()
        hub = MonitoringHub(event_engine=engine)
        engine.put(Event(
            EVENT_POSITION_UPDATE,
            PositionSnapshot(vt_symbol="000001.SZ", size=100),
        ))
        recent = hub.recent_positions(n=10)
        assert len(recent) == 1
        engine.stop()

    def test_recent_risk_alerts(self, engine: EventEngine):
        engine.start()
        hub = MonitoringHub(event_engine=engine)
        engine.put(Event(
            EVENT_RISK_ALERT,
            RiskAlert(reason="超单笔最大股数", level="warn", vt_symbol="000001.SZ"),
        ))
        recent = hub.recent_risk_alerts(n=10)
        assert len(recent) == 1
        assert recent[0]["level"] == "warn"
        engine.stop()

    def test_recent_alerts_empty_initially(self, engine: EventEngine):
        engine.start()
        hub = MonitoringHub(event_engine=engine)
        assert hub.recent_alerts(n=10) == []
        engine.stop()

    def test_anomaly_triggers_alert_via_default_rules(self, engine: EventEngine):
        engine.start()
        hub = MonitoringHub(event_engine=engine)
        # 触发数据延迟异常 → default rule data_delay_30min 触发 → alert
        engine.put(Event(
            EVENT_ANOMALY,
            AnomalyEvent(kind="data_delay", severity="warn", detail="延迟", source="datafeed"),
        ))
        # AlertDispatcher 应 emit 1 条 alert
        assert len(hub.recent_alerts(n=10)) >= 1
        engine.stop()

    def test_risk_alert_escalates_to_alert(self, engine: EventEngine):
        engine.start()
        hub = MonitoringHub(event_engine=engine)
        engine.put(Event(
            EVENT_RISK_ALERT,
            RiskAlert(reason="日熔断", level="error", vt_symbol=""),
        ))
        # risk_alert_escalation 规则触发
        assert len(hub.recent_alerts(n=10)) >= 1
        engine.stop()

    def test_clear_resets_all(self, engine: EventEngine):
        engine.start()
        hub = MonitoringHub(event_engine=engine)
        engine.put(Event(EVENT_PNL_UPDATE, PnlSnapshot(total_value=1.0)))
        engine.put(Event(
            EVENT_RISK_ALERT,
            RiskAlert(reason="x"),
        ))
        hub.clear()
        h = hub.health()
        assert h["pnl_count"] == 0
        assert h["risk_alert_count"] == 0
        engine.stop()

    def test_repr_includes_counts(self, engine: EventEngine):
        engine.start()
        hub = MonitoringHub(event_engine=engine)
        r = repr(hub)
        assert "attached=True" in r
        engine.stop()


# ── get_hub singleton ──────────────────────────────────────
class TestGetHub:
    def setup_method(self):
        reset_hub()

    def teardown_method(self):
        reset_hub()

    def test_get_hub_returns_singleton(self, engine: EventEngine):
        engine.start()
        h1 = get_hub(event_engine=engine)
        h2 = get_hub()
        assert h1 is h2
        engine.stop()

    def test_get_hub_no_engine_returns_unattached(self):
        h = get_hub()
        assert h.attached is False

    def test_reset_hub_clears_singleton(self, engine: EventEngine):
        engine.start()
        h1 = get_hub(event_engine=engine)
        reset_hub()
        h2 = get_hub()
        assert h1 is not h2
        engine.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])