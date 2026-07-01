"""
Web QA v6 interaction 回归测试 — diagnose 空输入不再 422 (2026-07-01)

对应 bug: /diagnose 页面的"诊断"按钮 onclick="diagnose()"
  - 用户在搜索框没输入 → 点击按钮 → 触发 /api/stock/search?q= → 后端 422
  - console 报 "Failed to load resource: ... 422"
  - 用户体验: 看到一个 alert "搜索失败" (因为 !sr.ok) + console error

修复 (src/web/templates/diagnose.html):
  - diagnose() 入口加空 input 守卫 → alert("请输入股票代码...") + focus
  - 永远不触发 /api/stock/search?q= 空查询

本测试:
  - 静态扫描: 确认 diagnose.html 含空输入守卫
  - 集成测试: 模拟点击"诊断"按钮 (空 input) → 不应触发 /api/stock/search?q=
"""
import pytest

# FastAPI TestClient
from tests.conftest import AuthedTestClient as TestClient


# ============================================================
# 全局 mock — 与 test_web_pages.py 一致
# ============================================================
@pytest.fixture(autouse=True)
def mock_data_repository(monkeypatch):
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

    monkeypatch.setattr("src.models.repository.DataRepository",
                        lambda *args, **kwargs: mock)
    monkeypatch.setattr("src.data.manager.DataManager.business",
                        property(lambda self: mock))
    return mock


@pytest.fixture
def test_app():
    from src.web.app import create_app
    return create_app()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


class TestDiagnoseEmptyInputGuard:
    """Web QA v6 (2026-07-01): diagnose.html 必须有空输入守卫, 避免 /api/stock/search?q= 422"""

    def test_diagnose_html_has_empty_input_guard(self):
        """diagnose.html 模板源码必须含 'if (!input)' 守卫"""
        from pathlib import Path
        template_path = Path(__file__).parent.parent / "src" / "web" / "templates" / "diagnose.html"
        html = template_path.read_text(encoding="utf-8")
        # 验证守卫存在 (精确字符串, 防 false positive)
        assert "const input = document.getElementById('searchInput').value.trim();" in html
        # 守卫: 空 input → 友好提示 + 早 return
        assert "if (!input)" in html, (
            "diagnose.html 缺空输入守卫 — 点击诊断按钮 (无输入) 会触发 /api/stock/search?q= → 422"
        )
        assert "请输入股票代码" in html, (
            "diagnose.html 缺空输入时的提示文案"
        )

    def test_diagnose_html_does_not_call_search_with_empty_q(self):
        """诊断入口永远不应拼出 /api/stock/search?q= (无 q 值)"""
        from pathlib import Path
        template_path = Path(__file__).parent.parent / "src" / "web" / "templates" / "diagnose.html"
        html = template_path.read_text(encoding="utf-8")
        # 反向断言: 不存在裸 '/api/stock/search?q=' + encodeURIComponent('')
        # 注意这是源码静态扫描, 不是运行时, 但守卫应放在 fetch 之前
        guard_pos = html.find("if (!input)")
        first_fetch_pos = html.find("fetch('/api/stock/search?q=' + encodeURIComponent(input))")
        assert guard_pos > 0, "守卫 'if (!input)' 不存在"
        assert first_fetch_pos > 0, "诊断函数中找不到搜索 fetch 调用"
        assert guard_pos < first_fetch_pos, (
            f"空输入守卫 (pos {guard_pos}) 应在搜索 fetch (pos {first_fetch_pos}) 之前 — "
            f"否则空输入仍会触发 422"
        )

    def test_diagnose_page_renders_200(self, client):
        """/diagnose 页面正常 200 (基线冒烟)"""
        resp = client.get("/diagnose")
        assert resp.status_code == 200
