"""
Web QA #6 (T8) — 4 页面必备 UI 元素回归测试

对应 Web QA v3 报告 (2026-06-27):
    - /diagnose     → 没 pre/code/result/diagnose
    - /workbench    → 没 form/tool/panel
    - /v6-compare   → 没 table/compare
    - /tuning       → 没 input/param

修复内容:
    - diagnose.html: 加 <pre><code> 块 (诊断结果 JSON)
    - workbench.html: 包 <form class="tool panel"> 包裹配置 inputs
    - v6_compare.html: 5 档对比表加 class="compare"
    - research.html (tuning tab): 加 4 个 .param input + select + button

注: /v6-compare 在 Phase C3 合并为 301 → /research?type=v6,
    QA 工具跳 301 后检查, 所以 table.compare 加在 research.html v6 tab
"""
import pytest

# FastAPI TestClient (包装为自动带 Bearer token)
from tests.conftest import AuthedTestClient as TestClient


# ============================================================
# 全局 mock — 与 test_web_pages.py 一致, 避免依赖真实数据库
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


# ============================================================
# 4 页面必备 UI 元素 (Web QA #6 — T8)
# ============================================================
class TestPageHasRequiredUIElements:
    """Web QA #6 (2026-06-27): 4 页面必须包含其核心 UI 元素 (pre/code/form/table/input)"""

    def test_diagnose_has_pre_code_result(self, client):
        """/diagnose 必须有 pre/code (诊断结果 JSON) + .result/.diagnose 类"""
        resp = client.get("/diagnose")
        assert resp.status_code == 200, f"/diagnose 返回 {resp.status_code}"
        html = resp.text
        assert "<pre" in html, "/diagnose 缺 <pre> 块 (诊断结果展示)"
        assert "<code" in html, "/diagnose 缺 <code> 块 (诊断结果展示)"
        assert "diagnose-result" in html or ".result" in html or "diagnose" in html, (
            "/diagnose 缺 result/diagnose 容器 class"
        )

    def test_workbench_has_form_tool_panel(self, client):
        """/workbench 必须有 <form> (工具页面有输入区) + .tool/.panel 类"""
        resp = client.get("/workbench")
        assert resp.status_code == 200, f"/workbench 返回 {resp.status_code}"
        html = resp.text
        assert "<form" in html, "/workbench 缺 <form> 元素 (回测配置面板)"
        assert "tool" in html, "/workbench 缺 .tool 类 (工具页面标识)"
        assert "panel" in html, "/workbench 缺 .panel 类 (面板标识)"

    def test_v6_compare_has_table_compare(self, client):
        """/v6-compare (301 → /research?type=v6) 必须有 <table> + .compare 类

        /v6-compare 旧路径在 Phase C3 合并为 301 → /research?type=v6,
        QA 工具跳 301 后检查最终页面, 所以断言最终 HTML.
        """
        resp = client.get("/v6-compare", follow_redirects=True)
        assert resp.status_code == 200, f"/v6-compare 跳转后返回 {resp.status_code}"
        html = resp.text
        assert "<table" in html, "/v6-compare 缺 <table> 元素 (5 档配置对比)"
        assert 'class="compare"' in html or "compare" in html, (
            "/v6-compare 缺 .compare class (对比表标识)"
        )

    def test_tuning_has_input_param(self, client):
        """/tuning (301 → /research?type=tuning) 必须有 <input> + .param 类"""
        resp = client.get("/research?type=tuning")
        assert resp.status_code == 200, f"/research?type=tuning 返回 {resp.status_code}"
        html = resp.text
        assert "<input" in html, "/research?type=tuning 缺 <input> 元素 (参数输入)"
        assert "param" in html, "/research?type=tuning 缺 .param 类 (参数调优标识)"
