"""
预测面板 4 端点单测 (2026-06-26)

覆盖:
  - /api/predict/stats: 空表/缺表 graceful degrade
  - /api/predict/{code}: 扁平 schema 校验 (与 param_server 一致)
  - /api/predict/history: 缺表返 available=False
  - /api/predict/verify: 缺 all_7d_scores.json graceful degrade
"""
import pytest
from fastapi.testclient import TestClient

from src.web.app import create_app
from tests.conftest import TEST_API_KEY

TEST_AUTH_HEADER = {"Authorization": f"Bearer {TEST_API_KEY}"}


@pytest.fixture(scope="module")
def client():
    app = create_app()
    return TestClient(app)


# ============================================================
# T1: /api/predict/stats 缺表 graceful degrade
# ============================================================
def test_predict_stats_handles_missing_table(client):
    """prediction_record 表被 drop 时, stats 应返 200 + available=False 而非 500"""
    from sqlalchemy.exc import OperationalError
    from src.web.routes import api as api_mod

    real_query = api_mod.get_data_manager

    class _BoomMgr:
        def query(self, sql, params=None):
            raise OperationalError("SELECT ...", None, Exception("no such table: prediction_record"))

    try:
        api_mod.get_data_manager = lambda: _BoomMgr()
        r = client.get("/api/predict/stats", headers=TEST_AUTH_HEADER)
        assert r.status_code == 200, f"应 200, 实际 {r.status_code}: {r.text[:200]}"
        body = r.json()
        assert body.get("total") == 0
        assert body.get("hit_rate") == 0
        assert body.get("bin_stats") == []
        assert body.get("available") is False
        assert "reason" in body and "prediction_record" in body["reason"]
    finally:
        api_mod.get_data_manager = real_query


# ============================================================
# T2: /api/predict/{code} 扁平 schema 校验
# ============================================================
def test_predict_for_stock_returns_flat_schema(client):
    """/api/predict/{code} 应返扁平字段 (与 predict_dashboard.html 一致)"""
    r = client.get("/api/predict/000001", headers=TEST_AUTH_HEADER)
    assert r.status_code == 200, f"应 200, 实际 {r.status_code}: {r.text[:200]}"
    body = r.json()
    # 必须有扁平字段
    for key in ("code", "pred_month", "as_of_date", "pred_proba_up", "signal",
                "dim_scores", "auc"):
        assert key in body, f"缺扁平字段: {key}"
    # 不能包裹 data/success (旧 schema)
    assert "data" not in body, "应扁平 schema, 不应有 data 包裹"
    assert "success" not in body, "应扁平 schema, 不应有 success 包裹"
    # dim_scores 必须有 8 个 key
    assert isinstance(body["dim_scores"], dict)
    for dim in ("tech", "fundam", "fund", "institutional", "lh_institutional",
                "sentiment", "news_event", "chip"):
        assert dim in body["dim_scores"], f"dim_scores 缺 {dim}"
    # 信号在合法集合内
    assert body["signal"] in ("买入", "中性", "回避")


# ============================================================
# T3: /api/predict/history 缺表 graceful degrade
# ============================================================
def test_predict_history_handles_missing_table(client):
    """prediction_record 不存在时, history 应返 200 + available=False"""
    from sqlalchemy.exc import OperationalError
    from src.web.routes import api as api_mod

    real_query = api_mod.get_data_manager

    class _BoomMgr:
        def query(self, sql, params=None):
            raise OperationalError("SELECT ...", None, Exception("no such table: prediction_record"))

    try:
        api_mod.get_data_manager = lambda: _BoomMgr()
        r = client.get("/api/predict/history?code=000001&limit=10", headers=TEST_AUTH_HEADER)
        assert r.status_code == 200
        body = r.json()
        assert body.get("total") == 0
        assert body.get("results") == []
        assert body.get("available") is False
    finally:
        api_mod.get_data_manager = real_query


# ============================================================
# T4: /api/predict/verify 缺 all_7d_scores.json graceful degrade
# ============================================================
def test_predict_verify_handles_missing_scores_file(client, monkeypatch):
    """all_7d_scores.json 不存在时, verify 应返 available=False"""
    import src.web.routes.api as api_mod

    # 直接 mock _Path 让 scores_path 检查返回 False
    real_Path = api_mod._Path

    class _FakePath:
        def __init__(self, _p):
            self._p = _p

        def exists(self):
            return False

    try:
        monkeypatch.setattr(api_mod, "_Path", _FakePath)
        r = client.get("/api/predict/verify", headers=TEST_AUTH_HEADER)
        assert r.status_code == 200
        body = r.json()
        assert body.get("updated") == 0
        assert body.get("available") is False
        assert "all_7d_scores.json" in body.get("reason", "")
    finally:
        monkeypatch.undo()
