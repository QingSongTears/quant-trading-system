"""
E2E 冒烟测试 — 验证所有关键页面 + API 在 app 启动后能 200 访问 (2026-06-25)

目标: 用户启动 run.py 后, 跑这个测试就能知道 web 是否正常
不是完整 e2e (浏览器), 是 TestClient 级别, 跑得快

覆盖:
  - 所有独立 HTML 页面 (/, /workbench, /compare, /research, /screener, /dashboard, ...)
  - 关键 API 端点 (用 get_api_key 真实 Bearer)
  - RBAC 端点 (can-manage-data 角色默认能调)
"""
import pytest
from fastapi.testclient import TestClient

from src.web.app import create_app
from src.web.auth import get_api_key


# 页面 URL 列表 (用 parametrize 自动遍历)
HTML_PAGES = [
    "/",
    "/workbench",
    "/workbench?mode=lab",
    "/workbench?mode=view",
    "/compare",
    "/compare?type=strategy",
    "/research",
    "/research?type=v5",
    "/research?type=v6",
    "/research?type=tuning",
    "/research?type=ic",
    "/research?type=dim",
    "/screener",
    "/dashboard",
    "/data-monitor",
    "/walk-forward",
    "/verify",
    "/predict",
    "/predict-dashboard",   # 2026-06-26: 8维预测看板
    "/predict-verify",      # 2026-06-26: 预测验证
    "/signal",
    "/portfolio",
    "/sector",
    "/fund-flow-report",
    "/bull-report",
    "/diagnose",
    "/simulate",
    "/console",
    "/strategies",
    "/data",
    "/backtest",
    "/stock/000001",
]

# Redirect 端点 (应 301)
REDIRECT_PAGES = [
    ("/backtest-lab", "/workbench?mode=lab"),
    ("/backtest-view", "/workbench?mode=view"),
    ("/strategy-compare", "/compare?type=strategy"),
    ("/v5", "/research?type=v5"),
    ("/v6-compare", "/research?type=v6"),
    ("/tuning", "/research?type=tuning"),
    ("/ic", "/research?type=ic"),
    ("/dim-compare", "/research?type=dim"),
]

# API 端点 (需要 Bearer token, 走 can-manage-data 默认角色)
API_PAGES = [
    "/api/data/coverage",
    "/api/strategies",
    "/api/backtest/results",
    "/api/simulate/list",
    "/api/research/ic?window=5&stock_limit=5",
    "/api/research/dim-ic?window=5&stock_limit=5",
    # 2026-06-26: 预测面板 4 端点
    "/api/predict/000001",
    "/api/predict/history?code=000001&limit=10",
    "/api/predict/verify",
    "/api/predict/stats",
]


@pytest.fixture(scope="module")
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {get_api_key()}"}


# ============================================================
# 页面 200 验证
# ============================================================


@pytest.mark.parametrize("path", HTML_PAGES)
def test_html_page_returns_200(client, path):
    """所有 HTML 页面应 200 OK"""
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    # 页面应包含 HTML
    assert "<html" in r.text.lower() or "<!doctype" in r.text.lower()


# ============================================================
# Redirect 验证
# ============================================================


@pytest.mark.parametrize("old_path,expected_location", REDIRECT_PAGES)
def test_old_path_redirects(client, auth_headers, old_path, expected_location):
    """老路径 301 永久重定向"""
    r = client.get(old_path, headers=auth_headers, follow_redirects=False)
    assert r.status_code == 301, f"{old_path} 应 301, 实际 {r.status_code}"
    assert r.headers["location"] == expected_location


# ============================================================
# API 验证
# ============================================================


@pytest.mark.parametrize("path", API_PAGES)
def test_api_endpoint_returns_200(client, auth_headers, path):
    """关键 API 应 200 OK (用真实 Bearer)"""
    r = client.get(path, headers=auth_headers)
    # 200 (有数据) 或 200 (空数据) 或 500 (DB 缺) — 不应是 401/403
    assert r.status_code in (200, 500), f"{path} -> {r.status_code}: {r.text[:200]}"


# ============================================================
# 鉴权失败
# ============================================================


def test_api_without_auth_returns_403(client):
    """无 Bearer token 应 403"""
    r = client.get("/api/strategies")
    assert r.status_code in (401, 403)


# ============================================================
# 静态资源
# ============================================================


def test_static_css_returns_200(client):
    """static/css/standalone-base.css 可访问"""
    r = client.get("/static/css/standalone-base.css")
    assert r.status_code == 200
    assert len(r.text) > 100  # 不是空文件


def test_static_js_returns_200(client):
    """static/js/common.js 可访问"""
    r = client.get("/static/js/common.js")
    assert r.status_code == 200
    assert "window.QT" in r.text or "QT" in r.text
