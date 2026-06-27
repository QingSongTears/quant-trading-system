"""
测试: VNPY-1 — V6 继承 EquityStrategy 可行性验证
"""
import sys
import pytest
from datetime import date

from src.strategies.v6_reversal_selection import V6ReversalSelectionStrategy
from src.strategy.equity_strategy import EquityStrategy
from src.strategy.alpha_strategy import AlphaStrategy
from src.backtest.strategy_engine import BacktestStrategyEngine


class TestV6InheritsEquityStrategy:
    """验证 V6 继承 EquityStrategy 可行"""

    def test_v6_is_subclass(self):
        """V6 是 EquityStrategy 子类"""
        assert issubclass(V6ReversalSelectionStrategy, EquityStrategy)
        assert issubclass(V6ReversalSelectionStrategy, AlphaStrategy)

    def test_v6_can_instantiate(self):
        """V6 可以实例化 (提供 strategy_engine=None)"""
        strategy = V6ReversalSelectionStrategy(
            strategy_engine=None,
            strategy_name="V6_Test",
            vt_symbols=["000001.SZ"],
        )
        assert strategy.strategy_name == "V6_Test"
        assert hasattr(strategy, "engine")  # DB engine

    def test_v6_has_required_methods(self):
        """V6 实现所有 EquityStrategy 要求的抽象方法"""
        strategy = V6ReversalSelectionStrategy(
            strategy_engine=None, strategy_name="V6", vt_symbols=[],
        )
        # 必须有的方法
        assert callable(strategy.on_init)
        assert callable(strategy.on_bars)
        assert callable(strategy.on_trade)
        assert callable(strategy.generate_signals)

    def test_v6_generate_signals_format(self):
        """generate_signals() 返回 DataFrame[vt_symbol, signal]"""
        strategy = V6ReversalSelectionStrategy(
            strategy_engine=None, strategy_name="V6", vt_symbols=[],
        )
        df = strategy.generate_signals()
        assert df.empty or set(["vt_symbol", "signal"]).issubset(set(df.columns))

    def test_engine_add_and_drive(self):
        """BacktestStrategyEngine 能加 V6 并驱动 on_bars()"""
        engine = BacktestStrategyEngine(initial_cash=1_000_000)
        strategy = engine.add_strategy(
            V6ReversalSelectionStrategy,
            strategy_name="V6_Smoke",
            vt_symbols=["600519.SH", "000001.SZ"],
            setting={"top_k": 2, "rebalance_days": 1, "n_stocks": 2},
        )
        # 加载一天数据
        bars = engine.load_bars(date(2025, 1, 15))
        # 即使 bars 为空也能跑 (空 bars → on_bars 直接 return)
        strategy.on_bars(bars)
        assert strategy.strategy_name == "V6_Smoke"
        report = engine.get_report()
        assert "initial_cash" in report
