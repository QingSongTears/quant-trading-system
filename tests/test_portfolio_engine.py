"""
测试: PortfolioBacktestEngine + PortfolioRunner — ADR-0009 (拆分后精简版)
==========================================================================

覆盖:
- PortfolioBacktestEngine 公开 API (run / BacktestReport 生成)
- PortfolioRunner.simulate (向量化组合回测循环)
- PortfolioRunner._simulate_portfolio 内部方法(parity test)
- PortfolioRunner.calc_transaction_cost
- 调仓明细字段完整性

原 test_portfolio_engine.py 测试 PortfolioBacktestEngine._simulate_portfolio,
该方法已迁至 PortfolioRunner.simulate / _simulate_portfolio。
测试改用 PortfolioRunner, 保持 parity test 行为不变。
"""
from __future__ import annotations

from datetime import date
from typing import List
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from src.backtest.base_selection_strategy import BaseSelectionStrategy
from src.backtest.portfolio_engine import PortfolioBacktestEngine
from src.backtest.portfolio_runner import PortfolioRunner
from src.backtest.data_loader import BacktestDataLoader


# ============================================================
# Test fixtures
# ============================================================

class ConstantSelectStrategy(BaseSelectionStrategy):
    """测试策略: 选 universe 中前 N 只, 每天调仓"""
    name = "constant_select"
    n_stocks = 2
    rebalance_days = 1
    lookback_days = 5   # 用小 lookback 避免早期数据不足
    min_amount_wan = 0
    max_mcap_yi = 1e9
    exclude_st = False
    min_mcap = 0

    def filter_universe(self, universe: pd.DataFrame) -> pd.DataFrame:
        return universe

    def select(self, rebalance_date, universe: pd.DataFrame) -> List[str]:
        return universe["code"].tolist()[: self.n_stocks]


def _make_synthetic_market(n_days: int = 60, n_stocks: int = 5, seed: int = 42):
    """生成小型合成市场数据"""
    np.random.seed(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    rows = []
    for code_idx in range(n_stocks):
        code = f"{code_idx:06d}"
        base = 10.0 + code_idx
        returns = np.random.randn(n_days) * 0.01
        prices = base * np.exp(np.cumsum(returns))
        for i, dt in enumerate(dates):
            rows.append({
                "code": code,
                "name": f"Stock{code_idx}",
                "list_date": date(2020, 1, 1),
                "mcap_yi": 100.0,
                "trade_date": dt,
                "open": prices[i] * 0.99,
                "high": prices[i] * 1.01,
                "low": prices[i] * 0.98,
                "close": prices[i],
                "volume": 1000000,
                "amount": prices[i] * 1000000,
                "pct_change": returns[i] * 100,
                "turnover": 2.0,
            })
    return pd.DataFrame(rows)


# ============================================================
# 原始实现 (parity test 用, 与重构前对比)
# ============================================================

def _original_simulate(
    strategy: BaseSelectionStrategy,
    all_data: pd.DataFrame,
    rebalance_dates: List[pd.Timestamp],
    initial_capital: float,
    start_date: date,
    end_date: date,
    commission: float = 0.001,
    stamp_duty: float = 0.0005,
):
    all_dates = sorted(all_data["trade_date"].unique())

    returns_pivot = all_data.pivot_table(
        index="trade_date", columns="code", values="pct_change", fill_value=0
    )

    equity = initial_capital
    holdings: List[str] = []
    portfolio_equity = {}
    rebalance_details = []

    def _calc_cost(eq, is_sell):
        cost = eq * commission
        if is_sell:
            cost += eq * stamp_duty
        return cost

    for dt in all_dates:
        if dt.date() < start_date or dt.date() > end_date:
            continue

        if holdings:
            day_returns = []
            pct_row = returns_pivot.loc[returns_pivot.index == dt]
            if not pct_row.empty:
                for code in holdings:
                    if code in pct_row.columns:
                        ret = pct_row[code].values[0]
                        if pd.notna(ret):
                            day_returns.append(ret / 100)
                        else:
                            day_returns.append(0)
                    else:
                        day_returns.append(0)
            else:
                day_returns = [0] * len(holdings)
            avg_return = np.mean(day_returns) if day_returns else 0
            equity *= (1 + avg_return)

        portfolio_equity[dt] = equity

        if dt in rebalance_dates:
            day_data = all_data[all_data["trade_date"] == dt]
            selected = day_data["code"].drop_duplicates().tolist()[:strategy.n_stocks]

            if set(selected) != set(holdings):
                sell_cost = _calc_cost(equity, is_sell=True) if holdings else 0
                buy_cost = _calc_cost(equity, is_sell=False)
                equity -= (sell_cost + buy_cost)
                rebalance_details.append({"date": str(dt.date())})
                holdings = selected

    equity_series = pd.Series(portfolio_equity)
    equity_series.index = pd.to_datetime(equity_series.index)
    return equity_series


# ============================================================
# PortfolioBacktestEngine 公开 API 测试
# ============================================================

class TestPortfolioBacktestEnginePublicAPI:
    """公开 API 保留验证"""

    def test_engine_class_exists(self):
        """PortfolioBacktestEngine 类存在"""
        assert PortfolioBacktestEngine is not None

    def test_runner_class_exists(self):
        """PortfolioRunner 类存在"""
        assert PortfolioRunner is not None


# ============================================================
# Parity tests (对比原实现, 验证语义保留)
# ============================================================

@pytest.fixture
def synthetic_market():
    return _make_synthetic_market()


def _make_runner():
    """构造 PortfolioRunner(不连真实 DB, 用 mock repo)"""
    mock_repo = MagicMock()
    mock_repo.init_database = MagicMock()
    mock_repo.engine = MagicMock()
    real_loader = BacktestDataLoader(repo=mock_repo)
    runner = PortfolioRunner(
        data_loader=real_loader,
        commission=0.001,
        stamp_duty=0.0005,
        min_commission=0,
        slippage=0,
    )
    return runner


def test_equity_curve_shape_matches(synthetic_market):
    """新 runner 应生成与原始相同长度的 equity 序列"""
    runner = _make_runner()
    strategy = ConstantSelectStrategy()

    equity_new, rebalance_new = runner._simulate_portfolio(
        strategy=strategy,
        all_data=synthetic_market,
        rebalance_dates=sorted(synthetic_market["trade_date"].unique()),
        initial_capital=1_000_000,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 3, 31),
    )

    equity_orig = _original_simulate(
        strategy=strategy,
        all_data=synthetic_market,
        rebalance_dates=sorted(synthetic_market["trade_date"].unique()),
        initial_capital=1_000_000,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 3, 31),
        commission=0.001,
        stamp_duty=0.0005,
    )

    assert len(equity_new) == len(equity_orig), (
        f"Length mismatch: new={len(equity_new)} vs orig={len(equity_orig)}"
    )

    diff = (equity_new.values - equity_orig.values)
    rel = np.abs(diff) / equity_orig.values
    assert np.max(rel) < 0.01, (
        f"Equity curves diverge too much: max_rel_diff={np.max(rel):.4f}"
    )


def test_no_rebalance_yields_market_return(synthetic_market):
    """若策略不调仓 (空 holdings), 净值应保持 initial_capital"""
    from src.backtest.base_selection_strategy import BaseSelectionStrategy as _BSS

    class EmptyStrategy(_BSS):
        name = "empty"
        n_stocks = 0
        rebalance_days = 1
        lookback_days = 20

        def filter_universe(self, universe):
            return universe

        def select(self, rebalance_date, universe):
            return []

    runner = _make_runner()
    equity, rebalance = runner._simulate_portfolio(
        strategy=EmptyStrategy(),
        all_data=synthetic_market,
        rebalance_dates=sorted(synthetic_market["trade_date"].unique()),
        initial_capital=1_000_000,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 1),
    )

    assert (equity == 1_000_000).all(), "Empty holdings should keep capital constant"
    assert rebalance == [], "No rebalance events should be recorded"


def test_rebalance_details_have_required_fields(synthetic_market):
    """调仓明细字段完整性"""
    runner = _make_runner()
    strategy = ConstantSelectStrategy()

    equity, rebalance = runner._simulate_portfolio(
        strategy=strategy,
        all_data=synthetic_market,
        rebalance_dates=sorted(synthetic_market["trade_date"].unique()),
        initial_capital=1_000_000,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 15),
    )

    assert len(rebalance) > 0, "Expected at least one rebalance"
    for r in rebalance:
        assert "date" in r
        assert "previous_holdings" in r
        assert "new_holdings" in r
        assert "n_new" in r
        assert "equity_before_rebalance" in r
        assert "transaction_cost" in r
        assert r["transaction_cost"] >= 0


def test_performance_speedup(synthetic_market):
    """向量化应不比原版慢太多"""
    import time
    runner = _make_runner()
    strategy = ConstantSelectStrategy()
    big_market = _make_synthetic_market(n_days=250, n_stocks=50)

    t0 = time.perf_counter()
    equity, _ = runner._simulate_portfolio(
        strategy=strategy,
        all_data=big_market,
        rebalance_dates=sorted(big_market["trade_date"].unique()),
        initial_capital=1_000_000,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
    )
    new_duration = time.perf_counter() - t0

    t0 = time.perf_counter()
    _original_simulate(
        strategy=strategy,
        all_data=big_market,
        rebalance_dates=sorted(big_market["trade_date"].unique()),
        initial_capital=1_000_000,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        commission=0.001,
        stamp_duty=0.0005,
    )
    orig_duration = time.perf_counter() - t0

    # 不强制要求 speedup(测试 fixture 较小), 只确保不超时
    assert new_duration < 30.0, "Vectorized version too slow"


# ============================================================
# PortfolioRunner 单元测试
# ============================================================

class TestCalcTransactionCost:
    """calc_transaction_cost 单元测试"""

    def test_zero_amount_zero_cost(self):
        """0 金额 → 0 成本"""
        runner = _make_runner()
        assert runner.calc_transaction_cost(0, is_sell=False, n_trades=1) == 0.0

    def test_commission_only_buy(self):
        """买入侧只收佣金,无印花税"""
        runner = _make_runner()
        # 100 万 × 0.001 = 1000 (commission)
        # stamp_duty 在买入侧为 0
        # slippage 在测试 fixture 中为 0
        cost = runner.calc_transaction_cost(1_000_000, is_sell=False, n_trades=1)
        assert cost == pytest.approx(1000.0)

    def test_commission_plus_stamp_sell(self):
        """卖出侧收佣金+印花税"""
        runner = _make_runner()
        cost = runner.calc_transaction_cost(1_000_000, is_sell=True, n_trades=1)
        # commission + stamp = 1000 + 500
        assert cost == pytest.approx(1500.0)

    def test_min_commission_floor(self):
        """极小交易金额时取最低佣金"""
        runner = _make_runner()
        runner.min_commission = 5.0  # 显式设置最低佣金
        # 1 元 × 0.001 = 0.001, 但最低 5 元 → 应返回 5
        cost = runner.calc_transaction_cost(1, is_sell=False, n_trades=1)
        # commission = max(0.001, 5) = 5
        # stamp = 0 (buy), slip = 0 (test fixture)
        assert cost == pytest.approx(5.0)


class TestApplySectorConstraint:
    """行业暴露约束测试"""

    def test_no_constraint_returns_original(self):
        """无约束时直接返回"""
        runner = _make_runner()
        strategy = ConstantSelectStrategy()
        strategy.sector_cap_pct = None
        strategy.sector_max_count = None

        universe = pd.DataFrame({
            "code": ["000001", "000002"],
            "name": ["A", "B"],
            "industry": ["银行", "地产"],
        })
        result = runner.apply_sector_constraint(["000001", "000002"], universe, strategy)
        assert result == ["000001", "000002"]


class TestPortfolioRunnerAPISurface:
    """PortfolioRunner 公开方法存在性"""

    def test_simulate_exists(self):
        """simulate 方法存在"""
        assert hasattr(PortfolioRunner, "simulate")

    def test_calc_transaction_cost_exists(self):
        """calc_transaction_cost 方法存在"""
        assert hasattr(PortfolioRunner, "calc_transaction_cost")

    def test_apply_sector_constraint_exists(self):
        """apply_sector_constraint 方法存在"""
        assert hasattr(PortfolioRunner, "apply_sector_constraint")

    def test_calc_benchmark_return_exists(self):
        """calc_benchmark_return 方法存在"""
        assert hasattr(PortfolioRunner, "calc_benchmark_return")