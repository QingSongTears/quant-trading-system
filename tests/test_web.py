"""
测试用例: F5 Web 可视化 — API 端点 / 页面渲染
对应 Issue #42 [B-02] / 验收清单 TC-F5-001 ~ TC-F5-009
"""
import pytest
pytest.importorskip("httpx")
import json
from datetime import date
from unittest.mock import patch, MagicMock, PropertyMock

import pandas as pd

# FastAPI TestClient
from fastapi.testclient import TestClient


# ============================================================
# 全局 mock — 避免 Web 测试依赖真实数据库
# ============================================================

@pytest.fixture(autouse=True)
def mock_data_repository(monkeypatch):
    """为所有 Web 测试 mock DataRepository，避免需要真实数据库"""
    mock = MagicMock()
    mock.get_data_coverage = MagicMock(return_value={
        "total_stocks": 0, "total_records": 0,
        "date_range": {"start": None, "end": None}
    })
    mock.get_stock_list = MagicMock(return_value=pd.DataFrame())
    mock.get_stock_count = MagicMock(return_value=0)
    mock.get_recent_backtests = MagicMock(return_value=[])
    mock.get_all_strategies = MagicMock(return_value=[])
    mock.get_backtest_result = MagicMock(return_value=None)
    mock.get_download_history = MagicMock(return_value=[])
    mock.init_database = MagicMock()

    # 在所有引用 DataRepository 的地方 mock
    monkeypatch.setattr(
        "src.models.repository.DataRepository",
        lambda *args, **kwargs: mock
    )
    monkeypatch.setattr(
        "src.web.routes.main.DataRepository",
        lambda *args, **kwargs: mock
    )
    monkeypatch.setattr(
        "src.web.routes.api.DataRepository",
        lambda *args, **kwargs: mock
    )

    return mock


# ============================================================
# Web 应用 fixture
# ============================================================

@pytest.fixture
def test_app():
    """创建测试用的 FastAPI 应用"""
    from src.web.app import create_app
    app = create_app()
    return app


@pytest.fixture
def client(test_app):
    """TestClient"""
    return TestClient(test_app)


# ============================================================
# TC-F5-001: 所有页面可访问
# ============================================================

class TestPageAccessibility:
    """页面访问测试"""

    def test_home_page_returns_200(self, client):
        """首页可访问"""
        response = client.get("/")
        assert response.status_code == 200

    def test_data_page_returns_200(self, client):
        """数据管理页可访问"""
        response = client.get("/data")
        assert response.status_code == 200

    def test_backtest_page_returns_200(self, client):
        """回测页可访问"""
        response = client.get("/backtest")
        assert response.status_code == 200

    def test_strategies_page_returns_200(self, client):
        """策略页可访问"""
        response = client.get("/strategies")
        assert response.status_code == 200

    def test_compare_page_returns_200(self, client):
        """对比页可访问"""
        response = client.get("/compare")
        assert response.status_code == 200

    def test_404_page(self, client):
        """404 页面返回"""
        response = client.get("/nonexistent-page-xyz")
        assert response.status_code == 404


# ============================================================
# API 端点测试
# ============================================================

class TestApiEndpoints:
    """API 端点测试"""

    def test_data_coverage_api(self, client):
        """TC-F5-002: 数据覆盖 API"""
        response = client.get("/api/data/coverage")
        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        # 空数据库也会返回结构
        if data["success"]:
            assert "data" in data

    def test_strategies_api(self, client):
        """策略列表 API"""
        response = client.get("/api/strategies")
        assert response.status_code == 200
        data = response.json()
        assert "success" in data

    def test_data_search_api(self, client):
        """数据搜索 API"""
        response = client.get("/api/data/search?q=平安")
        assert response.status_code == 200
        data = response.json()
        assert "success" in data

    def test_download_status_api(self, client):
        """下载状态 API"""
        response = client.get("/api/data/download/status")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "running" in data

    def test_backtest_results_api(self, client):
        """回测结果列表 API"""
        response = client.get("/api/backtest/results?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert "success" in data


# ============================================================
# TC-F5-003: 空状态处理
# ============================================================

class TestEmptyState:
    """空数据库时页面处理"""

    def test_home_empty_database(self, client):
        """空数据库首页不崩溃"""
        response = client.get("/")
        assert response.status_code == 200
        # 页面应包含引导文字
        html = response.text.lower()
        # 检查页面包含内容（宽松匹配）
        assert "量化" in html or "quant" in html or "数据" in html

    def test_backtest_empty_database(self, client):
        """空数据库回测页不崩溃"""
        response = client.get("/backtest")
        assert response.status_code == 200

    def test_compare_no_ids(self, client):
        """对比页无参数不崩溃"""
        response = client.get("/compare")
        assert response.status_code == 200


# ============================================================
# TC-F5-007: AI 标注检查
# ============================================================

class TestAIDisclaimer:
    """AI 标注检查"""

    def test_ai_disclaimer_in_config(self):
        """AI 免责声明在配置中存在"""
        from src.config import get_config
        config = get_config()
        assert "ai_disclaimer" in config
        assert config["ai_disclaimer"]["enabled"] is True

    def test_home_page_has_data_source_info(self, client):
        """首页包含数据源声明"""
        response = client.get("/")
        assert response.status_code == 200
        # 数据源应该在页面中出现
        html = response.text
        assert "AKShare" in html or "东方财富" in html or "数据" in html


# ============================================================
# TC-F5-008: 响应式布局检查 (HTML 结构验证)
# ============================================================

class TestHTMLStructure:
    """HTML 结构验证"""

    def test_home_page_html_structure(self, client):
        """首页包含基本 HTML 结构"""
        response = client.get("/")
        html = response.text
        assert "<!DOCTYPE html>" in html or "<html" in html
        assert "<head" in html
        assert "<body" in html

    def test_navigation_present(self, client):
        """导航栏存在"""
        response = client.get("/")
        html = response.text
        # 应有导航链接（检查常见导航元素）
        has_nav = any(tag in html for tag in ["nav", "navbar", "导航"])
        assert has_nav, "页面应包含导航元素"


# ============================================================
# 配置加载测试
# ============================================================

class TestConfigLoading:
    """配置系统测试"""

    def test_load_config_returns_dict(self):
        """加载配置返回字典"""
        from src.config import load_config
        config = load_config()
        assert isinstance(config, dict)
        assert "system" in config
        assert "database" in config
        assert "backtest" in config

    def test_load_strategies_returns_dict(self):
        """加载策略配置返回字典"""
        from src.config import load_strategies
        strategies = load_strategies()
        assert isinstance(strategies, dict)
        assert "strategies" in strategies

    def test_get_db_url(self):
        """数据库 URL 生成"""
        from src.config import get_db_url
        config = {
            "database": {
                "engine": "sqlite",
                "path": "database/test.db"
            }
        }
        url = get_db_url(config)
        assert url.startswith("sqlite:///")
        assert "test.db" in url

    def test_config_cache(self):
        """配置缓存"""
        from src.config import get_config
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2  # 应返回同一对象


# ============================================================
# 数据验证
# ============================================================

class TestDataValidation:
    """数据质量和格式验证"""

    def test_ohlcv_fields_non_null(self, sample_ohlcv_data):
        """关键字段非空"""
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in sample_ohlcv_data.columns, f"缺少列 {col}"
            assert sample_ohlcv_data[col].notna().all(), f"{col} 有 NaN 值"

    def test_ohlcv_price_logic(self, sample_ohlcv_data):
        """OHLCV 价格逻辑验证"""
        df = sample_ohlcv_data
        assert (df["high"] >= df["low"]).all(), "最高价应 ≥ 最低价"
        assert (df["high"] >= df["open"]).all(), "最高价应 ≥ 开盘价"
        assert (df["high"] >= df["close"]).all(), "最高价应 ≥ 收盘价"
        assert (df["low"] <= df["open"]).all(), "最低价应 ≤ 开盘价"
        assert (df["low"] <= df["close"]).all(), "最低价应 ≤ 收盘价"

    def test_volume_positive(self, sample_ohlcv_data):
        """成交量非负"""
        assert (sample_ohlcv_data["volume"] >= 0).all()


# ============================================================
# 边界场景测试
# ============================================================

class TestBoundaryScenarios:
    """BE 边界场景"""

    def test_backtest_nonexistent_result(self, client):
        """BE-03: 不存在的回测详情"""
        response = client.get("/backtest/99999")
        assert response.status_code == 404

    def test_compare_single_id(self, client):
        """BE-06: 对比页单个回测"""
        response = client.get("/compare?ids=99999")
        assert response.status_code == 200

    def test_compare_invalid_id(self, client):
        """对比页非法 ID"""
        response = client.get("/compare?ids=abc")
        assert response.status_code == 200  # 不崩溃

    def test_compare_radar_abs_regression(self, client, monkeypatch):
        """#50 回归: 雷达图 abs() 在有回撤数据时不崩溃"""
        import json
        from datetime import date as dt_date

        # 构造一个包含 max_drawdown 的 mock 回测结果
        mock_result = MagicMock()
        mock_result.id = 1
        mock_result.strategy = MagicMock()
        mock_result.strategy.name = "双均线交叉"
        mock_result.total_return = 15.5
        mock_result.annual_return = 8.2
        mock_result.sharpe_ratio = 1.2
        mock_result.max_drawdown = -12.5  # 触发 abs() 的关键字段
        mock_result.win_rate = 55.0
        mock_result.profit_factor = 2.1
        mock_result.total_trades = 42
        mock_result.excess_return = 5.3
        mock_result.benchmark_return = 10.2
        mock_result.equity_curve = json.dumps([
            {"date": "2024-01-02", "equity": 100000},
            {"date": "2024-01-03", "equity": 101000},
            {"date": "2024-01-04", "equity": 99500},
        ])
        mock_result.trades_detail = "[]"
        mock_result.monthly_returns = "{}"
        mock_result.cost_config = "{}"
        mock_result.stock_name = "测试股票"
        mock_result.stock_code = "000001"

        # patch DataRepository 返回 mock 结果
        mock_repo = MagicMock()
        mock_repo.get_backtest_result = MagicMock(return_value=mock_result)
        mock_repo.get_recent_backtests = MagicMock(return_value=[mock_result])
        mock_repo.get_data_coverage = MagicMock(return_value={
            "total_stocks": 10, "total_records": 100,
            "date_range": {"start": None, "end": None}
        })
        mock_repo.init_database = MagicMock()

        monkeypatch.setattr(
            "src.web.routes.main.DataRepository",
            lambda *args, **kwargs: mock_repo
        )
        monkeypatch.setattr(
            "src.models.repository.DataRepository",
            lambda *args, **kwargs: mock_repo
        )

        response = client.get("/compare?ids=1")
        assert response.status_code == 200, f"雷达图渲染不应崩溃: {response.text[:200]}"
        assert "双均线交叉" in response.text
        assert "12.5" in response.text or "12" in response.text  # max_drawdown 值出现
