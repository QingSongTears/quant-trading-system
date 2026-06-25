"""
URL 重定向单测 (2026-06-25 Phase C3)

覆盖 C3a/b/c 合并:
  - C3a: /backtest-lab / /backtest-view → /workbench?mode=
  - C3b: /strategy-compare → /compare?type=strategy
  - C3c: /v5 / /v6-compare / /tuning / /ic / /dim-compare → /research?type=

同时验证新合并入口仍 200 OK:
  - /workbench, /workbench?mode=lab, /workbench?mode=view
  - /compare, /compare?type=strategy
  - /research, /research?type=v5/v6/tuning/ic/dim
"""
import pytest
from fastapi.testclient import TestClient

from src.web.app import create_app


@pytest.fixture(scope="module")
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def auth_headers():
    from src.web.auth import get_api_key
    return {"Authorization": f"Bearer {get_api_key()}"}


# ============================================================
# C3a: workbench 三合一
# ============================================================


class TestWorkbenchRedirects:
    """Phase C3a: /backtest-lab/view → /workbench?mode="""

    def test_backtest_lab_redirects_to_workbench_lab(self, client, auth_headers):
        r = client.get("/backtest-lab", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == "/workbench?mode=lab"

    def test_backtest_view_redirects_to_workbench_view(self, client, auth_headers):
        r = client.get("/backtest-view", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == "/workbench?mode=view"

    def test_workbench_default_returns_200(self, client, auth_headers):
        r = client.get("/workbench", headers=auth_headers)
        assert r.status_code == 200

    def test_workbench_lab_returns_200(self, client, auth_headers):
        r = client.get("/workbench?mode=lab", headers=auth_headers)
        assert r.status_code == 200
        # mode 标识在页面里
        assert "Lab 模式" in r.text or "lab" in r.text.lower()

    def test_workbench_view_returns_200(self, client, auth_headers):
        r = client.get("/workbench?mode=view", headers=auth_headers)
        assert r.status_code == 200

    def test_workbench_invalid_mode_defaults(self, client, auth_headers):
        r = client.get("/workbench?mode=invalid", headers=auth_headers)
        assert r.status_code == 200


# ============================================================
# C3b: compare
# ============================================================


class TestCompareRedirects:
    """Phase C3b: /strategy-compare → /compare?type=strategy"""

    def test_strategy_compare_redirects(self, client, auth_headers):
        r = client.get("/strategy-compare", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == "/compare?type=strategy"

    def test_compare_default_returns_200(self, client, auth_headers):
        r = client.get("/compare", headers=auth_headers)
        assert r.status_code == 200

    def test_compare_strategy_returns_200(self, client, auth_headers):
        r = client.get("/compare?type=strategy", headers=auth_headers)
        assert r.status_code == 200


# ============================================================
# C3c: research 五合一
# ============================================================


class TestResearchRedirects:
    """Phase C3c: /v5/v6-compare/tuning/ic/dim-compare → /research?type="""

    @pytest.mark.parametrize("old_path,target_type", [
        ("/v5", "v5"),
        ("/v6-compare", "v6"),
        ("/tuning", "tuning"),
        ("/ic", "ic"),
        ("/dim-compare", "dim"),
    ])
    def test_redirect_to_research(self, client, auth_headers, old_path, target_type):
        r = client.get(old_path, headers=auth_headers, follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == f"/research?type={target_type}"

    @pytest.mark.parametrize("research_type", ["v5", "v6", "tuning", "ic", "dim"])
    def test_research_tab_returns_200(self, client, auth_headers, research_type):
        r = client.get(f"/research?type={research_type}", headers=auth_headers)
        assert r.status_code == 200
        # tab 高亮在 class 里
        assert "active" in r.text

    def test_research_invalid_type_defaults(self, client, auth_headers):
        r = client.get("/research?type=invalid", headers=auth_headers)
        assert r.status_code == 200  # 应 fallback 到 v5


# ============================================================
# 删模板后 redirect 仍工作
# ============================================================


class TestDeletedTemplatesStillRedirect:
    """Phase C2 batch 2: 8 老模板已删, 但 redirect 路由在 main.py, 仍 301"""

    def test_backtest_lab_no_template_works(self, client, auth_headers):
        """backtest-lab.html 删了, 但 main.py 路由 /backtest-lab → 301 workbench?mode=lab"""
        import os
        assert not os.path.exists("src/web/templates/backtest-lab.html"), \
            "backtest-lab.html 应已删"
        r = client.get("/backtest-lab", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 301  # 路由仍 301

    def test_v5_no_template_works(self, client, auth_headers):
        import os
        assert not os.path.exists("src/web/templates/v5.html"), "v5.html 应已删"
        r = client.get("/v5", headers=auth_headers, follow_redirects=False)
        assert r.status_code == 301
