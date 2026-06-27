"""
测试 /api/stock/quote + /api/stock/kline 的数据库兜底逻辑
2026-06-27: westock 拉取失败时, 自动降级到 daily_price 表
"""
import pytest
from src.web.app import create_app
from tests.conftest import AuthedTestClient


@pytest.fixture(scope="module")
def client():
    return AuthedTestClient(create_app())


# ============== /api/stock/quote 兜底 ==============

def test_quote_returns_200(client):
    r = client.get("/api/stock/quote?code=000001")
    assert r.status_code == 200


def test_quote_response_structure(client):
    r = client.get("/api/stock/quote?code=000001")
    data = r.json()
    assert "success" in data
    assert data["success"] is True
    assert "data" in data
    body = data["data"]
    # 必须字段
    for k in ("code", "name", "date", "price", "prev_close", "open", "high", "low", "volume", "change", "change_pct"):
        assert k in body, f"missing {k}"
    assert body["price"] > 0
    assert body["open"] > 0
    assert body["high"] >= body["low"]


def test_quote_invalid_code_returns_error(client):
    r = client.get("/api/stock/quote?code=999999")
    # 不存在时可能 200 with success=false, 也可能 404 — 都允许
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.json()["success"] is False


# ============== /api/stock/kline 兜底 ==============

def test_kline_returns_200(client):
    r = client.get("/api/stock/kline?code=000001&period=day&limit=5")
    assert r.status_code == 200


def test_kline_response_structure(client):
    r = client.get("/api/stock/kline?code=000001&period=day&limit=5")
    data = r.json()
    assert data["success"] is True
    assert "data" in data
    body = data["data"]
    assert "candles" in body
    assert "volumes" in body
    candles = body["candles"]
    assert len(candles) > 0, "应当从 daily_price 兜底拉到数据"
    # 单根 candle: [date, open, close, low, high]
    for c in candles:
        assert len(c) == 5, f"candle 应有 5 字段, 实际 {c}"
        assert isinstance(c[0], str)  # date
        assert c[1] > 0 and c[2] > 0   # open, close
        assert c[4] >= c[3]            # high >= low


def test_kline_period_week(client):
    """周线 endpoint 应被允许 (即使数据为日线聚合仍可读)"""
    r = client.get("/api/stock/kline?code=000001&period=week&limit=10")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True


def test_kline_invalid_period_rejected(client):
    """非法 period 应被 query validator 拒绝"""
    r = client.get("/api/stock/kline?code=000001&period=hour")
    assert r.status_code == 422  # FastAPI validation


def test_kline_limit_clamped(client):
    """limit 上限 500"""
    r = client.get("/api/stock/kline?code=000001&period=day&limit=999")
    assert r.status_code == 422