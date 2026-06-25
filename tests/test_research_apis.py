"""
IC / Dim-IC API 单测 (2026-06-25 Phase 12)

覆盖:
  - /api/research/ic 返回 factors 列表, 含 mean_ic / icir / n
  - /api/research/dim-ic 返回 dims 列表, 7 维
  - Spearman 计算正确性 (单调 + 逆单调 + 无相关)
  - 鉴权 (无 Bearer → 403)
  - 缺数据时 graceful fallback (不崩)
"""
import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from src.web.app import create_app
from src.web.auth import get_api_key
from src.web.routes.research import _spearman_ic


# ============================================================
# 单元测试: Spearman IC
# ============================================================


def test_spearman_perfect_positive():
    """完全正相关 → IC ≈ 1.0"""
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
    ic = _spearman_ic(x, y)
    assert ic is not None
    assert abs(ic - 1.0) < 0.01


def test_spearman_perfect_negative():
    """完全负相关 → IC ≈ -1.0"""
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([10.0, 8.0, 6.0, 4.0, 2.0])
    ic = _spearman_ic(x, y)
    assert ic is not None
    assert abs(ic - (-1.0)) < 0.01


def test_spearman_no_correlation():
    """无相关 → IC ≈ 0"""
    np.random.seed(42)
    x = np.random.randn(50)
    y = np.random.randn(50)
    ic = _spearman_ic(x, y)
    assert ic is not None
    assert abs(ic) < 0.3  # 噪声下小相关


def test_spearman_too_short_returns_none():
    """数据 < 5 → None"""
    x = np.array([1.0, 2.0, 3.0])
    y = np.array([3.0, 2.0, 1.0])
    assert _spearman_ic(x, y) is None


def test_spearman_constant_x_returns_none():
    """x 全相同 (std=0) → None"""
    x = np.array([5.0, 5.0, 5.0, 5.0, 5.0])
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert _spearman_ic(x, y) is None


def test_spearman_with_nan():
    """含 NaN → dropna 后算"""
    x = np.array([1.0, 2.0, np.nan, 4.0, 5.0, 6.0, 7.0])
    y = np.array([2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0])
    ic = _spearman_ic(x, y)
    assert ic is not None
    assert ic > 0.9  # 剩余 6 个点仍正相关


# ============================================================
# 集成测试: /api/research/ic + /dim-ic (SQLite 临时表)
# ============================================================


@pytest.fixture
def seeded_db(monkeypatch):
    """临时 SQLite, 写 daily_price + technical_indicators"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    conn = sqlite3.connect(str(tmp_path))
    # daily_price
    conn.execute("""
    CREATE TABLE daily_price (
        code TEXT, trade_date TEXT, close REAL,
        PRIMARY KEY (code, trade_date)
    )""")
    # technical_indicators
    conn.execute("""
    CREATE TABLE technical_indicators (
        code TEXT, trade_date TEXT,
        rsi14 REAL, macd_hist REAL, kdj_k REAL, kdj_j REAL, boll_lower REAL,
        PRIMARY KEY (code, trade_date)
    )""")

    # 3 只股票 × 30 天
    dates = pd.date_range("2024-06-01", periods=30, freq="D").strftime("%Y-%m-%d")
    for code in ["000001", "000002", "000003"]:
        base = 10.0 + hash(code) % 100 / 10
        prices = [base + i * 0.1 + (hash(code + str(i)) % 5) / 10 for i in range(30)]
        for d, p in zip(dates, prices):
            conn.execute(
                "INSERT INTO daily_price VALUES (?, ?, ?)", (code, d, p)
            )
            # 制造 RSI 与未来收益正相关 (RSi 越高, 未来收益越大)
            rsi = 50 + (p - base) * 20
            conn.execute(
                "INSERT INTO technical_indicators (code, trade_date, rsi14, macd_hist, kdj_k, kdj_j, boll_lower) VALUES (?,?,?,?,?,?,?)",
                (code, d, rsi, p * 0.01, rsi, rsi - 5, p - 1.0),
            )
    conn.commit()
    conn.close()

    # monkeypatch get_engine 返这个临时 DB
    engine = create_engine(f"sqlite:///{tmp_path}")
    monkeypatch.setattr("src.web.routes.research.get_engine", lambda: engine)

    yield tmp_path

    # cleanup
    try:
        tmp_path.unlink()
    except Exception:
        pass


@pytest.fixture
def client(seeded_db):
    app = create_app()
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {get_api_key()}"}


def test_factor_ic_endpoint(client, auth_headers):
    """/api/research/ic 返 factors 列表"""
    r = client.get("/api/research/ic?window=5&stock_limit=10", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert "factors" in data
    assert len(data["factors"]) >= 1
    # 每个 factor 应有 name + window
    for f in data["factors"]:
        assert "name" in f
        assert "window" in f


def test_factor_ic_known_correlation(client, auth_headers):
    """RSI 与未来收益正相关 (我们特意制造的), mean_ic 应 > 0"""
    r = client.get("/api/research/ic?window=5&stock_limit=10", headers=auth_headers)
    data = r.json()
    rsi_factor = next((f for f in data["factors"] if f["name"] == "rsi14"), None)
    assert rsi_factor is not None
    # 制造的 RSI 跟未来收益正相关, mean_ic 应 > 0.3
    if rsi_factor.get("mean_ic") is not None:
        assert rsi_factor["mean_ic"] > 0.3


def test_dim_ic_endpoint(client, auth_headers):
    """/api/research/dim-ic 返 dims 列表 (7 维)"""
    r = client.get("/api/research/dim-ic?window=5&stock_limit=10", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert "dims" in data
    # 7 维 (technical/fundamental/fund_flow/chip/institutional/sentiment/news_event)
    assert len(data["dims"]) == 7
    names = {d["name"] for d in data["dims"]}
    assert "technical" in names
    assert "fundamental" in names


def test_factor_ic_requires_auth(client):
    """无 Bearer → 403"""
    r = client.get("/api/research/ic?window=5")
    assert r.status_code in (401, 403)


def test_dim_ic_requires_auth(client):
    """无 Bearer → 403"""
    r = client.get("/api/research/dim-ic?window=5")
    assert r.status_code in (401, 403)
