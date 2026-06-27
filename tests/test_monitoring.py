"""
test_monitoring_routes.py — ADR-0012 #83 Step 7 Web 路由测试

覆盖:
  - GET /api/monitoring/pnl        返回最近 PnL 快照
  - GET /api/monitoring/positions  返回最近持仓快照
  - GET /api/monitoring/alerts     返回最近报警
  - GET /api/monitoring/risk-alerts 返回最近风控告警
  - GET /api/monitoring/health     返回 hub 健康状态
  - Bearer token 必填 (未带 token → 403)
"""
from __future__ import annotations

from datetime import datetime

import pytest
pytest.importorskip("httpx")

from tests.conftest import AuthedTestClient as TestClient

from src.event import Event, EventEngine
from src.event import (
    EVENT_PNL_UPDATE,
    EVENT_POSITION_UPDATE,
    EVENT_RISK_ALERT,
)
from src.monitoring import (
    AnomalyEvent,
    PnlSnapshot,
    PositionSnapshot,
)
from src.monitoring.hub import reset_hub
from src.risk.event_data import RiskAlert


@pytest.fixture
def client():
    """Authed TestClient + reset hub singleton"""
    from src.web.app import create_app
    reset_hub()
    app = create_app()
    c = TestClient(app)
    yield c
    reset_hub()


@pytest.fixture
def engine():
    return EventEngine(interval=1, raise_on_inactive=False)


# ── 4 端点 ──────────────────────────────────────
class TestMonitoringRoutes:
    def test_pnl_endpoint_returns_data(self, client, engine):
        engine.start()
        from src.monitoring.hub import get_hub
        hub = get_hub(event_engine=engine)
        engine.put(Event(EVENT_PNL_UPDATE, PnlSnapshot(total_value=100.0)))
        engine.put(Event(EVENT_PNL_UPDATE, PnlSnapshot(total_value=200.0)))

        resp = client.get("/api/monitoring/pnl?n=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 2
        engine.stop()

    def test_positions_endpoint(self, client, engine):
        engine.start()
        from src.monitoring.hub import get_hub
        get_hub(event_engine=engine)
        engine.put(Event(
            EVENT_POSITION_UPDATE,
            PositionSnapshot(vt_symbol="000001.SZ", size=100),
        ))
        resp = client.get("/api/monitoring/positions?n=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 1
        assert body["data"][0]["vt_symbol"] == "000001.SZ"
        engine.stop()

    def test_alerts_endpoint(self, client, engine):
        engine.start()
        from src.monitoring.hub import get_hub
        get_hub(event_engine=engine)
        # 风控告警 → 触发 risk_alert_escalation 规则
        engine.put(Event(
            EVENT_RISK_ALERT,
            RiskAlert(reason="日熔断", level="error", vt_symbol=""),
        ))
        resp = client.get("/api/monitoring/alerts?n=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) >= 1
        engine.stop()

    def test_risk_alerts_endpoint(self, client, engine):
        engine.start()
        from src.monitoring.hub import get_hub
        get_hub(event_engine=engine)
        engine.put(Event(
            EVENT_RISK_ALERT,
            RiskAlert(reason="超单笔最大股数", level="warn", vt_symbol="000001.SZ"),
        ))
        resp = client.get("/api/monitoring/risk-alerts?n=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) == 1
        engine.stop()

    def test_health_endpoint(self, client, engine):
        engine.start()
        from src.monitoring.hub import get_hub
        get_hub(event_engine=engine)
        resp = client.get("/api/monitoring/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["attached"] is True
        assert "pnl_count" in body["data"]
        assert "alert_count" in body["data"]
        engine.stop()


# ── Bearer token 必填 ──────────────────────────────────────
class TestAuthRequired:
    def test_no_token_returns_403(self, client):
        """无 Bearer token → 403/401"""
        from fastapi.testclient import TestClient as RawTestClient
        from src.web.app import create_app
        app = create_app()
        raw = RawTestClient(app)
        resp = raw.get("/api/monitoring/health")
        # 403 (HTTPBearer 默认) 或 401
        assert resp.status_code in (401, 403)

    def test_with_token_returns_200(self, client):
        """带 Bearer token → 200"""
        resp = client.get("/api/monitoring/health")
        assert resp.status_code == 200


# ── 边界 ──────────────────────────────────────
class TestEdgeCases:
    def test_n_too_large_clamped(self, client):
        """n > 1000 → 自动 clamp (FastAPI Query validation)"""
        resp = client.get("/api/monitoring/pnl?n=10000")
        # FastAPI Query(le=1000) → 422 校验失败
        assert resp.status_code == 422

    def test_n_too_small_rejected(self, client):
        """n < 1 → 422"""
        resp = client.get("/api/monitoring/pnl?n=0")
        assert resp.status_code == 422

    def test_empty_data_returns_empty_list(self, client):
        """hub 暂无数据 → 返回空列表"""
        resp = client.get("/api/monitoring/pnl?n=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])