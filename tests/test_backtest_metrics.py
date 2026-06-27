"""
测试: backtest/metrics.py — ADR-0009
====================================

覆盖 backtest/metrics.py 薄封装层的所有公开函数:
- calc_sharpe_from_equity
- calc_volatility_from_equity
- calc_max_drawdown_pct
- calc_annual_return_pct
- calc_calmar_ratio
- calc_win_rate
- calc_profit_factor
- calc_benchmark_return
- compute_metrics (一站式入口)
"""
import numpy as np
import pandas as pd
import pytest

from src.backtest.metrics import (
    calc_sharpe_from_equity,
    calc_volatility_from_equity,
    calc_max_drawdown_pct,
    calc_annual_return_pct,
    calc_calmar_ratio,
    calc_win_rate,
    calc_profit_factor,
    calc_benchmark_return,
    compute_metrics,
)


class TestSharpeFromEquity:
    """夏普比率从权益曲线计算"""

    def test_basic_uptrend_positive_sharpe(self):
        """上涨趋势夏普为正"""
        np.random.seed(42)
        equity = pd.Series(100 + np.cumsum(np.random.randn(100) * 0.5 + 0.1))
        sharpe = calc_sharpe_from_equity(equity, risk_free=0.02)
        assert sharpe > 0

    def test_constant_equity_zero_sharpe(self):
        """常值权益 → 0 夏普(波动率为 0)"""
        equity = pd.Series([100.0] * 50)
        sharpe = calc_sharpe_from_equity(equity)
        assert sharpe == 0.0

    def test_too_short_returns_zero(self):
        """数据不足 2 个点 → 0"""
        equity = pd.Series([100.0])
        assert calc_sharpe_from_equity(equity) == 0.0

    def test_none_returns_zero(self):
        """None 输入 → 0"""
        assert calc_sharpe_from_equity(None) == 0.0


class TestVolatilityFromEquity:
    """年化波动率"""

    def test_positive_volatility(self):
        """随机走势波动率 > 0"""
        np.random.seed(42)
        equity = pd.Series(100 + np.cumsum(np.random.randn(100)))
        vol = calc_volatility_from_equity(equity)
        assert vol > 0

    def test_constant_zero_volatility(self):
        """常值 → 0"""
        equity = pd.Series([100.0] * 50)
        assert calc_volatility_from_equity(equity) == 0.0


class TestMaxDrawdownPct:
    """最大回撤(%)"""

    def test_peak_to_trough(self):
        """从 100 跌到 80 → -20%"""
        equity = np.array([100, 110, 80, 90])
        dd = calc_max_drawdown_pct(equity)
        assert dd == pytest.approx(-27.27, abs=0.01)

    def test_no_drawdown_zero(self):
        """持续上涨 → 0"""
        equity = np.array([100, 110, 120, 130])
        assert calc_max_drawdown_pct(equity) == 0.0

    def test_empty_returns_zero(self):
        """空数据 → 0"""
        assert calc_max_drawdown_pct(np.array([])) == 0.0


class TestAnnualReturnPct:
    """年化收益率(%)"""

    def test_one_year(self):
        """1 年 20% → 20% 年化"""
        # 250 个交易日 ≈ 1 年
        ret = calc_annual_return_pct(20.0, 250)
        assert ret == pytest.approx(20.0, abs=0.5)

    def test_half_year(self):
        """半年 20% → 年化约 44%"""
        ret = calc_annual_return_pct(20.0, 125)
        assert 40 <= ret <= 50

    def test_zero_days_zero(self):
        """0 天 → 0"""
        assert calc_annual_return_pct(10.0, 0) == 0.0


class TestCalmarRatio:
    """卡玛比率"""

    def test_basic(self):
        """15% / 25% = 0.6"""
        assert calc_calmar_ratio(15.0, -25.0) == pytest.approx(0.6)

    def test_zero_dd_zero(self):
        """回撤为 0 → 0"""
        assert calc_calmar_ratio(15.0, 0.0) == 0.0


class TestWinRate:
    """胜率(%)"""

    def test_all_positive(self):
        """全正收益 → 100%"""
        returns = np.array([0.01, 0.02, 0.005, 0.03])
        assert calc_win_rate(returns) == 100.0

    def test_all_negative(self):
        """全负收益 → 0%"""
        returns = np.array([-0.01, -0.02, -0.005, -0.03])
        assert calc_win_rate(returns) == 0.0

    def test_mixed(self):
        """混合 → 50%"""
        returns = np.array([0.01, -0.01, 0.02, -0.02])
        assert calc_win_rate(returns) == 50.0


class TestProfitFactor:
    """盈亏比"""

    def test_greater_than_one(self):
        """盈利 > 亏损"""
        returns = np.array([0.05, 0.03, -0.01, -0.005])
        pf = calc_profit_factor(returns)
        assert pf > 1

    def test_no_losses_zero(self):
        """无亏损 → 0(约定)"""
        returns = np.array([0.01, 0.02])
        assert calc_profit_factor(returns) == 0.0


class TestBenchmarkReturn:
    """基准指数收益率(%)"""

    def test_uptrend_positive(self):
        """基准上涨 → 正"""
        closes = pd.Series([100, 105, 110, 115])
        ret = calc_benchmark_return(closes, None, None)
        assert ret == pytest.approx(15.0)

    def test_downtrend_negative(self):
        """基准下跌 → 负"""
        closes = pd.Series([100, 95, 90])
        ret = calc_benchmark_return(closes, None, None)
        assert ret == pytest.approx(-10.0)

    def test_empty_zero(self):
        """空数据 → 0"""
        assert calc_benchmark_return(pd.Series([]), None, None) == 0.0

    def test_none_zero(self):
        """None → 0"""
        assert calc_benchmark_return(None, None, None) == 0.0


class TestComputeMetrics:
    """compute_metrics 一站式入口"""

    def test_basic_equity_curve(self):
        """基本权益曲线产出全部字段"""
        np.random.seed(42)
        equity = pd.Series(
            100000 * (1 + np.cumsum(np.random.randn(250) * 0.005 + 0.0005)),
            index=pd.date_range("2024-01-01", periods=250, freq="B"),
        )
        result = compute_metrics(
            equity_series=equity,
            initial_capital=100000,
            trading_days=250,
            risk_free=0.02,
        )

        assert "total_return" in result
        assert "annual_return" in result
        assert "sharpe_ratio" in result
        assert "max_drawdown" in result
        assert "annual_volatility" in result
        assert "calmar_ratio" in result
        assert "win_rate" in result
        assert "profit_factor" in result
        assert "final_equity" in result

        # final_equity 应接近最后一天
        assert abs(result["final_equity"] - equity.iloc[-1]) < 1

    def test_empty_returns_zeros(self):
        """空数据返回零值"""
        result = compute_metrics(
            equity_series=pd.Series(dtype=float),
            initial_capital=100000,
            trading_days=0,
        )
        assert result["total_return"] == 0.0
        assert result["final_equity"] == 0.0

    def test_none_returns_zeros(self):
        """None 返回零值"""
        result = compute_metrics(
            equity_series=None,
            initial_capital=100000,
            trading_days=250,
        )
        assert result["total_return"] == 0.0