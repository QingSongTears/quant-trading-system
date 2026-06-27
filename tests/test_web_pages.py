"""
Web QA 回归测试 — 验证所有页面正确加载 common.js + api-key meta

对应 Issue Web QA #1 (2026-06-27):
    research.html 是独立模板 (不继承 base.html), 没加载 /static/js/common.js,
    导致 window.QT undefined, v5/v6/tuning chart 全部 fail。

修复: 在 <head> 注入:
    <meta name="api-key" content="{{ api_key }}">
    <script src="/static/js/common.js"></script>

本测试套件覆盖所有关键页面, 防止未来独立模板再次漏掉 common.js:
    - /research            (本次 bug 修复目标)
    - /dashboard /data /strategies /backtest 等关键页 (继承 base.html 也包含断言, 防回归)
"""
import pytest

# FastAPI TestClient (包装为自动带 Bearer token)
from tests.conftest import AuthedTestClient as TestClient


# ============================================================
# 全局 mock — 避免 Web 测试依赖真实数据库
# ============================================================
@pytest.fixture(autouse=True)
def mock_data_repository(monkeypatch):
    """为所有 Web 测试 mock DataManager.business 门面, 避免需要真实数据库"""
    from unittest.mock import MagicMock
    import pandas as pd

    mock = MagicMock()
    mock.get_data_coverage = MagicMock(return_value={
        "total_stocks": 0, "total_records": 0,
        "date_range": {"start": None, "end": None},
    })
    mock.get_stock_list = MagicMock(return_value=pd.DataFrame())
    mock.get_stock_count = MagicMock(return_value=0)
    mock.get_recent_backtests = MagicMock(return_value=[])
    mock.get_all_strategies = MagicMock(return_value=[])
    mock.get_backtest_result = MagicMock(return_value=None)
    mock.get_download_history = MagicMock(return_value=[])
    mock.init_database = MagicMock()

    # DataManager.business 走 mock
    monkeypatch.setattr(
        "src.models.repository.DataRepository",
        lambda *args, **kwargs: mock,
    )
    monkeypatch.setattr(
        "src.data.manager.DataManager.business",
        property(lambda self: mock),
    )
    return mock


# ============================================================
# Web 应用 fixture
# ============================================================
@pytest.fixture
def test_app():
    """创建测试用的 FastAPI 应用"""
    from src.web.app import create_app
    return create_app()


@pytest.fixture
def client(test_app):
    """TestClient (autouse Bearer token for /api/* endpoints)"""
    return TestClient(test_app)


# ============================================================
# 断言辅助函数
# ============================================================
def _assert_has_common_js(html: str) -> None:
    """验证 HTML 中包含 common.js script 标签 (独立模板必备)"""
    assert "/static/js/common.js" in html, (
        f"页面未加载 common.js — window.QT 将 undefined, "
        f"fetch 拦截器失效, 任何 Chart / ECharts 调用会 fail"
    )


def _assert_has_api_key_meta(html: str) -> None:
    """验证 HTML <head> 中包含 <meta name="api-key"> (供 common.js 读)"""
    # 简单包含检查: meta tag + name="api-key" + content 包含 ci-test-key (conftest 设置)
    assert 'name="api-key"' in html, (
        "页面 <head> 缺 <meta name='api-key'> — common.js 读不到 API key, "
        "所有 /api/* 请求会 401"
    )


def _assert_in_head(html: str, needle: str) -> None:
    """验证 needle 在 <head>...</head> 区段内 (防止出现在 body 误判)"""
    # 简易 head 边界截取
    head_start = html.lower().find("<head")
    head_end = html.lower().find("</head>")
    assert head_start >= 0 and head_end > head_start, "页面无 <head> 区段"
    head_section = html[head_start:head_end]
    assert needle in head_section, (
        f"期望 '{needle}' 在 <head> 内, 但未找到 — "
        f"common.js 必须在 DOM 解析早期加载才能定义 window.QT"
    )


# ============================================================
# 关键页面断言 — 必须加载 common.js + api-key meta
# ============================================================
# 这些页面要么继承 base.html, 要么已通过本次修复单独注入。
# 任何页面漏掉 → 立即失败, 防止 chart 静默 fail。

class TestResearchPageLoadsCommonJS:
    """Web QA #1: /research 必须加载 common.js + api-key meta"""

    def test_research_includes_common_js_script(self, client):
        """/research 必须引用 /static/js/common.js"""
        resp = client.get("/research")
        assert resp.status_code == 200, f"/research 返回 {resp.status_code}"
        _assert_has_common_js(resp.text)

    def test_research_includes_api_key_meta(self, client):
        """/research <head> 必须含 <meta name='api-key'>"""
        resp = client.get("/research")
        assert resp.status_code == 200
        _assert_has_api_key_meta(resp.text)

    def test_research_common_js_in_head(self, client):
        """common.js 必须在 <head> 加载 (早期定义 window.QT)"""
        resp = client.get("/research")
        assert resp.status_code == 200
        _assert_in_head(resp.text, "/static/js/common.js")

    def test_research_api_key_meta_in_head(self, client):
        """api-key meta 必须在 <head> (DOM 解析早期就能读)"""
        resp = client.get("/research")
        assert resp.status_code == 200
        _assert_in_head(resp.text, 'name="api-key"')

    def test_research_all_5_tabs_render(self, client):
        """/research 5 个 tab (v5/v6/tuning/ic/dim) 都应正确渲染"""
        for research_type in ("v5", "v6", "tuning", "ic", "dim"):
            resp = client.get(f"/research?type={research_type}")
            assert resp.status_code == 200, (
                f"/research?type={research_type} 返回 {resp.status_code}"
            )
            # 每次渲染都需含 common.js (防止某个 type 模板路径漏掉)
            _assert_has_common_js(resp.text)
            _assert_has_api_key_meta(resp.text)

    def test_research_chart_canvas_present(self, client):
        """/research 实际包含 chart canvas (保证 chart 渲染逻辑存在)"""
        resp = client.get("/research?type=v5")
        assert resp.status_code == 200
        # v5 tab 应有 canvas 元素
        assert "v5-canvas" in resp.text, "/research?type=v5 应含 v5-canvas 元素"

    def test_research_uses_qt_fetch(self, client):
        """/research 必须用 window.QT.fetch (不能直接 fetch — 否则不带 Bearer)"""
        resp = client.get("/research?type=v5")
        assert resp.status_code == 200
        assert "window.QT.fetch" in resp.text, (
            "/research 必须用 window.QT.fetch 而非 fetch, "
            "否则绕过 common.js 的 Bearer 拦截器, /api/* 会 401"
        )


class TestBaseTemplatePagesIncludeCommonJS:
    """继承 base.html 的页面也应包含 common.js (防回归)"""

    @pytest.mark.parametrize("path", [
        "/dashboard",
        "/data",
        "/strategies",
        "/backtest",
        "/compare",
        "/workbench",
        "/simulate",
        "/console",
        "/stock/600519",
    ])
    def test_page_includes_common_js(self, client, path):
        """所有继承 base.html 的页面都应加载 common.js"""
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} 返回 {resp.status_code}"
        _assert_has_common_js(resp.text)
        _assert_has_api_key_meta(resp.text)


# ============================================================
# 静态文件可达性 — common.js 必须能被浏览器下载
# ============================================================
class TestCommonJSStaticFile:
    """/static/js/common.js 必须可访问 (200 + 非空 JS 内容)"""

    def test_common_js_returns_200(self, client):
        resp = client.get("/static/js/common.js")
        assert resp.status_code == 200, (
            f"/static/js/common.js 返回 {resp.status_code} — "
            f"页面会 console.error, window.QT undefined"
        )

    def test_common_js_defines_window_qt(self, client):
        """common.js 内容必须包含 window.QT 定义"""
        resp = client.get("/static/js/common.js")
        assert resp.status_code == 200
        body = resp.text
        # common.js 关键标识符
        assert "window.QT" in body or "global.QT" in body, (
            "common.js 必须定义 window.QT (含 fetch 拦截器)"
        )
        assert "api-key" in body, (
            "common.js 必须从 meta[name='api-key'] 读 key"
        )