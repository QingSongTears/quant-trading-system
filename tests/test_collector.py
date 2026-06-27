"""
test_collector.py — ADR-0012 #83 Step 2 测试

覆盖:
  - PnlCollector / PositionCollector / RiskAlertCollector:
    attach/detach, _on_event 类型校验, recent, clear, __len__
"""
from __future__ import annotations

from datetime import datetime

import pytest

from src.event import Event, EventEngine
from src.event import (
    EVENT_PNL_UPDATE,
    EVENT_POSITION_UPDATE,
    EVENT_RISK_ALERT,
)
from src.monitoring.collector import (
    PnlCollector,
    PositionCollector,
    RiskAlertCollector,
)
from src.monitoring.event_data import PnlSnapshot, PositionSnapshot
from src.risk.event_data import RiskAlert


@pytest.fixture
def engine() -> EventEngine:
    return EventEngine(interval=1, raise_on_inactive=False)


# ── PnlCollector ──────────────────────────────────────
class TestPnlCollector:
    def test_default_store(self):
        c = PnlCollector()
        assert len(c) == 0
        assert c.count == 0

    def test_attach_receives_event(self, engine: EventEngine):
        engine.start()
        c = PnlCollector()
        c.attach(engine)
        engine.put(Event(EVENT_PNL_UPDATE, PnlSnapshot(total_value=100.0)))
        assert len(c) == 1
        assert c.count == 1
        engine.stop()

    def test_ignores_non_pnl_payload(self, engine: EventEngine):
        engine.start()
        c = PnlCollector()
        c.attach(engine)
        engine.put(Event(EVENT_PNL_UPDATE, "not a snapshot"))
        assert len(c) == 0
        engine.stop()

    def test_recent_returns_n_latest(self, engine: EventEngine):
        engine.start()
        c = PnlCollector()
        c.attach(engine)
        for i in range(5):
            engine.put(Event(EVENT_PNL_UPDATE, PnlSnapshot(total_value=float(i))))
        recent = c.recent(n=3)
        assert len(recent) == 3
        assert recent[0].total_value == 4.0
        engine.stop()

    def test_clear(self, engine: EventEngine):
        engine.start()
        c = PnlCollector()
        c.attach(engine)
        engine.put(Event(EVENT_PNL_UPDATE, PnlSnapshot(total_value=1.0)))
        assert len(c) == 1
        c.clear()
        assert len(c) == 0
        assert c.count == 0
        engine.stop()

    def test_detach(self, engine: EventEngine):
        engine.start()
        c = PnlCollector()
        c.attach(engine)
        c.detach()
        engine.put(Event(EVENT_PNL_UPDATE, PnlSnapshot(total_value=1.0)))
        assert len(c) == 0
        engine.stop()


# ── PositionCollector ──────────────────────────────────────
class TestPositionCollector:
    def test_default_store(self):
        c = PositionCollector()
        assert len(c) == 0

    def test_attach_receives_event(self, engine: EventEngine):
        engine.start()
        c = PositionCollector()
        c.attach(engine)
        engine.put(Event(
            EVENT_POSITION_UPDATE,
            PositionSnapshot(vt_symbol="000001.SZ", size=1000),
        ))
        assert len(c) == 1
        engine.stop()

    def test_ignores_non_position_payload(self, engine: EventEngine):
        engine.start()
        c = PositionCollector()
        c.attach(engine)
        engine.put(Event(EVENT_POSITION_UPDATE, {"raw": "dict"}))
        assert len(c) == 0
        engine.stop()


# ── RiskAlertCollector ──────────────────────────────────────
class TestRiskAlertCollector:
    def test_default_store(self):
        c = RiskAlertCollector()
        assert len(c) == 0

    def test_attach_receives_risk_alert(self, engine: EventEngine):
        engine.start()
        c = RiskAlertCollector()
        c.attach(engine)
        engine.put(Event(
            EVENT_RISK_ALERT,
            RiskAlert(reason="超单笔最大股数", level="warn", vt_symbol="000001.SZ"),
        ))
        assert len(c) == 1
        recent = c.recent(n=1)
        assert recent[0]["level"] == "warn"
        assert recent[0]["reason"] == "超单笔最大股数"
        engine.stop()

    def test_attach_receives_dict(self, engine: EventEngine):
        engine.start()
        c = RiskAlertCollector()
        c.attach(engine)
        engine.put(Event(EVENT_RISK_ALERT, {"reason": "raw dict", "level": "error"}))
        assert len(c) == 1
        engine.stop()

    def test_clear(self, engine: EventEngine):
        engine.start()
        c = RiskAlertCollector()
        c.attach(engine)
        engine.put(Event(EVENT_RISK_ALERT, RiskAlert(reason="x")))
        c.clear()
        assert len(c) == 0
        engine.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])