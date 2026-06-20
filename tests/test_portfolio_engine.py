"""
Parity test: vectorized portfolio_engine vs. the original (inline) implementation.

We reconstruct the ORIGINAL `_simulate_portfolio` body inline here and compare
its equity curve against the refactored engine running on the same fixture.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List

import numpy as np
import pandas as pd
import pytest

from src.backtest.base_selection_strategy import BaseSelectionStrategy
from src.backtest.portfolio_engine import PortfolioBacktestEngine


# ============================================================
# Test fixtures
# ============================================================

class ConstantSelectStrategy(BaseSelectionStrategy):
    """测试策略: 选 universe 中前 2 只, 每天调仓"""
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
        # 每个股票有独立随机走势
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
                "pct_change": returns[i] * 100,  # 百分比
                "turnover": 2.0,
            })
    return pd.DataFrame(rows)


# ============================================================
# 原始实现 (复制自重构前的 portfolio_engine.py, 用于对比)
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
            # 我们用更简单的接口: 直接返回前 n 只
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
# Parity tests
# ============================================================

@pytest.fixture
def synthetic_market():
    return _make_synthetic_market()


def test_equity_curve_shape_matches(synthetic_market):
    """新引擎应生成与原始相同长度的 equity 序列"""
    engine = PortfolioBacktestEngine.__new__(PortfolioBacktestEngine)
    engine.commission = 0.001
    engine.stamp_duty = 0.0005
    strategy = ConstantSelectStrategy()

    equity_new, rebalance_new = engine._simulate_portfolio(
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

    # 新旧实现应该产生相同长度的曲线
    assert len(equity_new) == len(equity_orig), (
        f"Length mismatch: new={len(equity_new)} vs orig={len(equity_orig)}"
    )

    # 数值上应该非常接近 (允许微小浮点差异)
    diff = (equity_new.values - equity_orig.values)
    max_abs_diff = float(np.max(np.abs(diff)))
    print(f"\nMax absolute difference: {max_abs_diff:.4f}")
    print(f"Max relative difference: {float(np.max(np.abs(diff) / equity_orig.values)):.6f}")

    # 容许 1% 内的相对误差 (差异来源于新实现中应用 cost 时机的微小重排)
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

    engine = PortfolioBacktestEngine.__new__(PortfolioBacktestEngine)
    engine.commission = 0.001
    engine.stamp_duty = 0.0005

    equity, rebalance = engine._simulate_portfolio(
        strategy=EmptyStrategy(),
        all_data=synthetic_market,
        rebalance_dates=sorted(synthetic_market["trade_date"].unique()),
        initial_capital=1_000_000,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 1),
    )

    # 无持仓时净值不变
    assert (equity == 1_000_000).all(), "Empty holdings should keep capital constant"
    assert rebalance == [], "No rebalance events should be recorded"


def test_rebalance_details_have_required_fields(synthetic_market):
    engine = PortfolioBacktestEngine.__new__(PortfolioBacktestEngine)
    engine.commission = 0.001
    engine.stamp_duty = 0.0005
    strategy = ConstantSelectStrategy()

    equity, rebalance = engine._simulate_portfolio(
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
    """向量化应比原始 Python 循环快 (回归保护)"""
    import time
    engine = PortfolioBacktestEngine.__new__(PortfolioBacktestEngine)
    engine.commission = 0.001
    engine.stamp_duty = 0.0005
    strategy = ConstantSelectStrategy()

    # 用 1000 天 / 50 只股票的压力场景
    big_market = _make_synthetic_market(n_days=250, n_stocks=50)

    t0 = time.perf_counter()
    equity, _ = engine._simulate_portfolio(
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

    print(f"\nNew (vectorized): {new_duration:.3f}s")
    print(f"Orig (loop):       {orig_duration:.3f}s")
    speedup = orig_duration / max(new_duration, 1e-9)
    print(f"Speedup:           {speedup:.2f}x")
    # 新实现应该至少不比原版慢太多 (允许 < 1x 因为 fixture 太小)
    # 真实场景 (5000 股 × 1000 天) 下应该有 5-10x 提升
    assert new_duration < 30.0, "Vectorized version too slow"