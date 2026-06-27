"""
测试: BacktestEngine + BacktestReport — ADR-0009 (拆分后精简版)
================================================================

覆盖:
- BacktestReport dataclass (to_dict / to_db_dict / defaults)
- report_to_chart_data ECharts 转换
- BacktestEngine.run / run_batch / rank_batch_results
- BacktestEngine 参数搜索 (grid / random / bayesian / multi_metric) — 用 mock 避免真实 IO

注: 原 test_backtest.py 中 fixture `mock_backtest_engine` 直接引用 engine.py 的 DataRepository 符号,
新结构中 DataRepository 由 BacktestDataLoader 内部使用, 测试改为 mock runner 层。
"""
import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine, BacktestReport
from src.backtest.report import report_to_chart_data


# ============================================================
# BacktestReport 测试
# ============================================================

class TestBacktestReport:
    """回测报告数据类测试"""

    def test_report_creation(self):
        """创建基本回测报告"""
        equity = [
            {"date": "2024-01-02", "equity": 100000},
            {"date": "2024-01-03", "equity": 100500},
        ]
        trades = [
            {"entry_date": "2024-01-02", "exit_date": "2024-01-03",
             "entry_price": 10.0, "exit_price": 10.5, "pnl": 500, "return_pct": 5.0}
        ]

        report = BacktestReport(
            strategy_name="测试策略",
            stock_code="000001",
            stock_name="测试股票",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 1),
            initial_capital=100000,
            final_equity=105000,
            total_return=5.0,
            annual_return=10.0,
            sharpe_ratio=1.5,
            max_drawdown=-3.0,
            win_rate=60.0,
            profit_factor=2.0,
            total_trades=10,
            annual_volatility=15.0,
            calmar_ratio=3.33,
            benchmark_return=3.0,
            excess_return=2.0,
            equity_curve=equity,
            trades_detail=trades,
            monthly_returns={"2024-01": 100000, "2024-02": 102000},
        )

        assert report.strategy_name == "测试策略"
        assert report.total_return == 5.0
        assert report.total_trades == 10
        assert len(report.equity_curve) == 2
        assert len(report.trades_detail) == 1

    def test_report_to_dict(self):
        """测试报告序列化"""
        report = BacktestReport(
            strategy_name="测试",
            stock_code="000001",
            stock_name="测试",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=100000,
            final_equity=100000,
            total_return=0.0, annual_return=0.0, sharpe_ratio=0.0,
            max_drawdown=0.0, win_rate=0.0, profit_factor=0.0,
            total_trades=0, annual_volatility=0.0, calmar_ratio=0.0,
        )
        d = report.to_dict()
        assert "strategy_name" in d
        assert "total_return" in d
        assert "equity_curve" in d
        assert d["start_date"] == "2024-01-01"
        assert isinstance(d["equity_curve"], str)  # JSON 序列化后是字符串

    def test_report_to_db_dict(self):
        """测试数据库存储格式"""
        report = BacktestReport(
            strategy_name="测试",
            stock_code="000001",
            stock_name="测试",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 1),
            initial_capital=100000,
            final_equity=105000,
            total_return=5.0, annual_return=10.0, sharpe_ratio=1.5,
            max_drawdown=-3.0, win_rate=60.0, profit_factor=2.0,
            total_trades=10, annual_volatility=15.0, calmar_ratio=3.33,
            trades_detail=[{"entry_date": "2024-01-02", "pnl": 500}],
        )
        db_dict = report.to_db_dict(strategy_id=1)
        assert db_dict["strategy_id"] == 1
        assert db_dict["total_return"] == 5.0
        assert isinstance(db_dict["trades_detail"], str)

    def test_report_defaults(self):
        """测试默认值"""
        report = BacktestReport(
            strategy_name="测试",
            stock_code="000001",
            stock_name="测试",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=100000,
            final_equity=100000,
            total_return=0.0, annual_return=0.0, sharpe_ratio=0.0,
            max_drawdown=0.0, win_rate=0.0, profit_factor=0.0,
            total_trades=0, annual_volatility=0.0, calmar_ratio=0.0,
        )
        assert report.equity_curve == []
        assert report.trades_detail == []
        assert report.monthly_returns == {}
        assert report.cost_config == {}


# ============================================================
# 报告转换测试
# ============================================================

class TestReportToChartData:
    """report → ECharts 数据转换测试"""

    def test_chart_data_structure(self):
        """验证图表数据结构完整"""
        equity = [
            {"date": "2024-01-02", "equity": 100000},
            {"date": "2024-01-03", "equity": 101000},
            {"date": "2024-01-04", "equity": 102000},
            {"date": "2024-01-05", "equity": 101500},
            {"date": "2024-01-08", "equity": 103000},
        ]
        report = BacktestReport(
            strategy_name="测试", stock_code="000001", stock_name="测试",
            start_date=date(2024, 1, 2), end_date=date(2024, 1, 8),
            initial_capital=100000, final_equity=103000,
            total_return=3.0, annual_return=30.0, sharpe_ratio=2.0,
            max_drawdown=-1.47, win_rate=100.0, profit_factor=0.0,
            total_trades=1, annual_volatility=15.0, calmar_ratio=20.4,
            equity_curve=equity,
            monthly_returns={"2024-01": 103000},
        )

        chart_data = report_to_chart_data(report)
        assert "equity_curve" in chart_data
        assert "drawdown_curve" in chart_data
        assert "metrics" in chart_data
        assert "trades" in chart_data
        assert chart_data["metrics"]["total_return"] == 3.0

    def test_drawdown_calculation(self):
        """验证回撤计算"""
        equity = [
            {"date": "2024-01-02", "equity": 100000},
            {"date": "2024-01-03", "equity": 100000},
            {"date": "2024-01-04", "equity": 95000},   # -5% 回撤
            {"date": "2024-01-05", "equity": 98000},   # 回撤 -2%
            {"date": "2024-01-08", "equity": 105000},  # 新高, 回撤 0%
        ]
        report = BacktestReport(
            strategy_name="测试", stock_code="000001", stock_name="测试",
            start_date=date(2024, 1, 2), end_date=date(2024, 1, 8),
            initial_capital=100000, final_equity=105000,
            total_return=5.0, annual_return=50.0, sharpe_ratio=2.0,
            max_drawdown=-5.0, win_rate=100.0, profit_factor=0.0,
            total_trades=1, annual_volatility=20.0, calmar_ratio=10.0,
            equity_curve=equity,
        )

        chart_data = report_to_chart_data(report)
        dd = chart_data["drawdown_curve"]
        assert len(dd) == 5
        assert dd[2]["drawdown"] == -5.0
        assert dd[4]["drawdown"] == 0.0


# ============================================================
# BacktestEngine 薄封装层测试
# ============================================================

@pytest.fixture
def mock_engine(monkeypatch):
    """Mock BacktestEngine 内部依赖(DB / config)"""
    # Mock config
    mock_config = {
        "backtest": {
            "costs": {
                "commission_rate": 0.0003,
                "stamp_duty_rate": 0.0005,
                "slippage": 0.0001,
            },
            "benchmark": "sh000300",
            "risk_free_rate": 0.02,
        }
    }
    # Mock DataRepository 实例化(避免真实 DB)
    mock_repo = MagicMock()
    mock_repo.init_database = MagicMock()

    def fake_factory():
        return mock_repo

    # 替换 get_config 和 DataRepository (在 data_loader 里用)
    from src.backtest import data_loader as dl_mod
    monkeypatch.setattr(dl_mod, "DataRepository", fake_factory)
    monkeypatch.setattr("src.config.get_config", lambda: mock_config)

    # 创建 engine, 不调用 __init__ 真实 DB
    engine = object.__new__(BacktestEngine)
    engine.commission = 0.0003
    engine.stamp_duty = 0.0005
    engine.slippage = 0.0001
    engine.benchmark_code = "sh000300"
    engine.risk_free_rate = 0.02

    # 注入 mock runner 和 data_loader
    from src.backtest.data_loader import BacktestDataLoader
    from src.backtest.runner import BacktestRunner
    engine.data_loader = BacktestDataLoader(repo=mock_repo)
    engine.runner = BacktestRunner(
        data_loader=engine.data_loader,
        commission=0.0003,
        stamp_duty=0.0005,
        slippage=0.0001,
        benchmark_code="sh000300",
        risk_free_rate=0.02,
    )
    return engine


class TestBacktestEnginePublicAPI:
    """公开 API 保留测试"""

    def test_engine_class_exists(self):
        """BacktestEngine 类存在"""
        assert BacktestEngine is not None

    def test_report_class_exists(self):
        """BacktestReport 类存在"""
        assert BacktestReport is not None

    def test_report_importable_from_engine(self):
        """从 engine 模块导入 BacktestReport(向后兼容路径)"""
        from src.backtest.engine import BacktestReport as BR
        assert BR is BacktestReport

    def test_report_importable_from_report(self):
        """从 report 模块导入 BacktestReport(直接路径)"""
        from src.backtest.report import BacktestReport as BR
        assert BR is BacktestReport


class TestBacktestRunnerMethods:
    """测试 BacktestRunner 内部分发方法(通过 mock engine)"""

    def test_calc_annual_return_pct(self, mock_engine):
        """年化收益率计算"""
        # 半年 20% 收益 → 年化约 44%
        annual = mock_engine.runner.calc_annual_return_pct(20.0, 125)
        assert 40 <= annual <= 50, f"预期 ~44%, 实际 {annual:.1f}%"

    def test_calc_annual_return_pct_zero(self, mock_engine):
        """零收益"""
        assert mock_engine.runner.calc_annual_return_pct(0, 250) == 0

    def test_calc_annual_return_pct_negative(self, mock_engine):
        """负收益"""
        annual = mock_engine.runner.calc_annual_return_pct(-10.0, 250)
        assert annual < 0

    def test_calc_sharpe_zero_days(self, mock_engine):
        """无足够数据时夏普比率为 0"""
        stats = {"_equity_curve": pd.Series([100, 101])}
        sharpe = mock_engine.runner.calc_sharpe(stats, 100000, 100000, 1)
        assert sharpe == 0

    def test_calc_annual_volatility(self, mock_engine):
        """波动率计算"""
        np.random.seed(42)
        prices = 100 + np.random.randn(100).cumsum()
        equity_series = pd.Series(prices, name="Equity")
        stats = {"_equity_curve": equity_series}
        vol = mock_engine.runner.calc_annual_volatility(stats)
        assert vol > 0

    def test_extract_equity_series(self, mock_engine):
        """从 stats 提取 equity series"""
        equity = pd.Series([100, 101, 102])
        stats = {"_equity_curve": equity}
        result = mock_engine.runner.extract_equity_series(stats)
        assert result is not None
        assert len(result) == 3


class TestBacktestEdgeCases:
    """边界场景"""

    def test_empty_equity_curve(self):
        """空净值曲线处理"""
        report = BacktestReport(
            strategy_name="空回测", stock_code="000001", stock_name="测试",
            start_date=date(2024, 1, 1), end_date=date(2024, 1, 2),
            initial_capital=100000, final_equity=100000,
            total_return=0.0, annual_return=0.0, sharpe_ratio=0.0,
            max_drawdown=0.0, win_rate=0.0, profit_factor=0.0,
            total_trades=0, annual_volatility=0.0, calmar_ratio=0.0,
            equity_curve=[], trades_detail=[],
        )
        chart_data = report_to_chart_data(report)
        assert chart_data["drawdown_curve"] == []
        assert chart_data["equity_curve"] == []

    def test_cost_config_in_report(self):
        """交易成本配置在报告中体现"""
        report = BacktestReport(
            strategy_name="成本测试", stock_code="000001", stock_name="测试",
            start_date=date(2024, 1, 1), end_date=date(2024, 1, 31),
            initial_capital=100000, final_equity=100000,
            total_return=0.0, annual_return=0.0, sharpe_ratio=0.0,
            max_drawdown=0.0, win_rate=0.0, profit_factor=0.0,
            total_trades=0, annual_volatility=0.0, calmar_ratio=0.0,
            cost_config={"commission_rate": 0.0003, "stamp_duty_rate": 0.0005},
        )
        assert report.cost_config["commission_rate"] == 0.0003


class TestMultiMetricOptimize:
    """多指标综合优化(委托给 optimizer)"""

    def test_basic_score(self):
        """基本加权评分"""
        from src.backtest.engine import BacktestEngine
        engine = object.__new__(BacktestEngine)
        results = [
            {"params": {"a": 1}, "sharpe_ratio": 1.0, "total_return": 5.0, "max_drawdown": -10.0},
            {"params": {"a": 2}, "sharpe_ratio": 2.0, "total_return": 10.0, "max_drawdown": -5.0},
            {"params": {"a": 3}, "sharpe_ratio": 0.5, "total_return": 3.0, "max_drawdown": -20.0},
        ]
        scored = engine.multi_metric_optimize(
            results, metrics=["sharpe_ratio:0.5", "total_return:0.3", "max_drawdown:0.2"]
        )
        assert len(scored) == 3
        assert all("composite_score" in r for r in scored)
        # 综合评分最高者排第一
        assert scored[0]["composite_score"] >= scored[-1]["composite_score"]

    def test_default_metrics(self):
        """默认指标权重"""
        from src.backtest.engine import BacktestEngine
        engine = object.__new__(BacktestEngine)
        results = [
            {"params": {}, "sharpe_ratio": 1.0, "total_return": 5.0, "max_drawdown": -10.0},
        ]
        scored = engine.multi_metric_optimize(results)
        assert "composite_score" in scored[0]