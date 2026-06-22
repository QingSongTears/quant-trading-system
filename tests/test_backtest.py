"""
测试用例: F4 回测引擎
对应 Issue #42 [B-02] / 验收清单 TC-F4-001 ~ TC-F4-006
"""
import pytest
import json
from datetime import date, timedelta

import pandas as pd
import numpy as np

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
            total_return=0.0,
            annual_return=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            total_trades=0,
            annual_volatility=0.0,
            calmar_ratio=0.0,
        )
        d = report.to_dict()
        assert "strategy_name" in d
        assert "total_return" in d
        assert "equity_curve" in d
        assert d["start_date"] == "2024-01-01"

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
            total_return=5.0,
            annual_return=10.0,
            sharpe_ratio=1.5,
            max_drawdown=-3.0,
            win_rate=60.0,
            profit_factor=2.0,
            total_trades=10,
            annual_volatility=15.0,
            calmar_ratio=3.33,
            trades_detail=[
                {"entry_date": "2024-01-02", "pnl": 500}
            ],
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
            total_return=0.0,
            annual_return=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            total_trades=0,
            annual_volatility=0.0,
            calmar_ratio=0.0,
        )
        assert report.equity_curve == []
        assert report.trades_detail == []
        assert report.monthly_returns == {}


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
            strategy_name="测试",
            stock_code="000001", stock_name="测试",
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
            {"date": "2024-01-04", "equity": 95000},  # -5% 回撤
            {"date": "2024-01-05", "equity": 98000},  # 回撤 -2%
            {"date": "2024-01-08", "equity": 105000}, # 新高，回撤 0%
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
# BacktestEngine 指标计算测试 (mock DB)
# ============================================================

@pytest.fixture
def mock_backtest_engine(monkeypatch):
    """创建 mock 的 BacktestEngine（不需要真实数据库）"""
    from unittest.mock import MagicMock
    import src.backtest.engine as eng_mod

    # Mock DataRepository
    mock_repo = MagicMock()
    mock_repo.init_database = MagicMock()

    # Mock get_config
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
    monkeypatch.setattr(eng_mod, "get_config", lambda: mock_config)
    monkeypatch.setattr(eng_mod, "DataRepository", lambda: mock_repo)

    # 创建实例时绕过 DB 初始化
    engine = object.__new__(BacktestEngine)
    engine.repo = mock_repo
    engine.commission = 0.0003
    engine.stamp_duty = 0.0005
    engine.slippage = 0.0001
    engine.benchmark_code = "sh000300"
    engine.risk_free_rate = 0.02
    return engine


class TestBacktestEngineCalculations:
    """回测引擎内部计算逻辑测试"""

    def test_annual_return_calculation(self, mock_backtest_engine):
        """年化收益率计算"""
        engine = mock_backtest_engine
        # 半年 20% 收益 → 年化约 44%
        annual = engine._calc_annual_return(20.0, 125)  # 125 个交易日 = 半年
        assert 40 <= annual <= 50, f"预期 ~44%，实际 {annual:.1f}%"

    def test_annual_return_zero(self, mock_backtest_engine):
        """零收益"""
        engine = mock_backtest_engine
        assert engine._calc_annual_return(0, 250) == 0

    def test_annual_return_negative(self, mock_backtest_engine):
        """负收益"""
        engine = mock_backtest_engine
        annual = engine._calc_annual_return(-10.0, 250)
        assert annual < 0

    def test_sharpe_zero_trades(self, mock_backtest_engine):
        """无交易时夏普比率"""
        engine = mock_backtest_engine
        assert engine._calc_sharpe(
            pd.DataFrame({"Close": [100]}), 100000, 100000, 1
        ) == 0

    def test_volatility_calculation(self, mock_backtest_engine):
        """波动率计算"""
        np.random.seed(42)
        prices = 100 + np.random.randn(100).cumsum()
        # _calc_annual_volatility 需要 stats dict 且包含 _equity_curve
        equity_series = pd.Series(prices, name="Equity")
        stats = {"_equity_curve": equity_series}
        engine = mock_backtest_engine
        vol = engine._calc_annual_volatility(stats)
        assert vol > 0, "波动率应为正数"


# ============================================================
# 边界场景测试
# ============================================================

class TestBacktestEdgeCases:
    """回测引擎边界场景"""

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


# ============================================================
# stock_screener 策略组件测试
# ============================================================

class TestStockScreenerComponents:
    """选股策略核心组件单元测试"""

    def test_risk_manager_init(self):
        """风控管理器初始化"""
        from src.strategies.stock_screener.core.risk_manager import RiskManager
        rm = RiskManager(total_capital=500000)
        assert rm.can_open_position()
        assert rm.max_positions == 5

    def test_risk_manager_position_limit(self):
        """仓位上限控制"""
        from src.strategies.stock_screener.core.risk_manager import RiskManager
        rm = RiskManager(total_capital=1000000)
        rm.max_positions = 2

        rm.add_position("000001", 10.0, 10000)
        rm.add_position("000002", 20.0, 5000)
        assert not rm.can_open_position()

    def test_stop_loss_calculation(self):
        """止损价计算"""
        from src.strategies.stock_screener.core.risk_manager import RiskManager
        rm = RiskManager(total_capital=1000000)
        stop = rm.get_stop_loss_price(10.0)
        # STOP_LOSS_PCT = -0.10, 所以止损价 = 10 * (1-0.10) = 9.0
        assert stop == pytest.approx(9.0, 0.01)

    def test_take_profit_calculation(self):
        """止盈价计算"""
        from src.strategies.stock_screener.core.risk_manager import RiskManager
        rm = RiskManager(total_capital=1000000)
        # 普通票止盈 +15%
        tp_normal = rm.get_take_profit_price(10.0, is_quant_stock=False)
        assert tp_normal == pytest.approx(11.5, 0.01)
        # 量化票止盈 +8%
        tp_quant = rm.get_take_profit_price(10.0, is_quant_stock=True)
        assert tp_quant == pytest.approx(10.8, 0.01)

    def test_screener_config_import(self):
        """验证选股配置可正常导入"""
        from src.strategies.stock_screener.config import (
            MIN_MARKET_CAP, MIN_DAILY_TURNOVER, EMA_FAST, EMA_SLOW,
            QUANT_THRESHOLD, STOP_LOSS_PCT, MAX_POSITIONS,
        )
        assert MIN_MARKET_CAP == 100
        assert EMA_FAST == 20
        assert EMA_SLOW == 60
        assert 0 < QUANT_THRESHOLD <= 1
        assert STOP_LOSS_PCT < 0
        assert MAX_POSITIONS > 0
