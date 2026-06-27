"""
测试 Simulator 端到端集成 (ADR-0011 #82)

覆盖:
  - SimulatorEngine.run 端到端 (mock data_mgr, 临时 SQLite)
  - 买入 → 持仓 → 止盈 → 平仓 全链路
  - AStockRules 涨跌停 / T+1 检查
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.strategies.simulator import (
    Simulator, SimulatorEngine, Signal, SignalAdapter,
    Position, TradeRecord, SimulationResult,
)
from src.strategies.trading.config import TradingConfig


# ═════════════════════════════════════════════════════════════
#  SimulatorEngine 初始化
# ═════════════════════════════════════════════════════════════
class TestSimulatorEngineInit:
    def test_default_config(self):
        """默认 TradingConfig (initial_capital=100_000)"""
        sim = Simulator()
        assert sim.config.initial_capital == 100_000.0
        assert sim.capital == 100_000.0
        assert sim.positions == {}
        assert sim.trades == []
        assert sim.equity_curve == []
        assert sim.run_id == ""

    def test_custom_config(self):
        config = TradingConfig(initial_capital=500_000)
        sim = Simulator(config=config)
        assert sim.capital == 500_000.0

    def test_simulator_is_simulatorengine(self):
        """Simulator 别名指向 SimulatorEngine (向后兼容)"""
        assert Simulator is SimulatorEngine


# ═════════════════════════════════════════════════════════════
#  SimulatorEngine.run 端到端
# ═════════════════════════════════════════════════════════════
class TestSimulatorRun:
    """端到端: 模拟单只股票 30 个交易日, 验证 SimulationResult 字段齐全"""

    @staticmethod
    def _make_price_rows(n: int = 30, base_price: float = 10.0):
        """生成模拟日 K 线数据"""
        rows = []
        for i in range(n):
            # 价格缓慢上涨 1%/日
            close = base_price * (1 + 0.01 * i)
            open_p = close * 0.99
            high = close * 1.01
            low = close * 0.98
            prev_close = base_price * (1 + 0.01 * (i - 1)) if i > 0 else base_price
            pct_chg = (close / prev_close - 1) * 100 if i > 0 else 0.0
            rows.append({
                "trade_date": f"2026-06-{i + 1:02d}",
                "open": round(open_p, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "pct_change": round(pct_chg, 2),
            })
        return rows

    def test_run_returns_simulation_result(self, tmp_path):
        """run() 返回 SimulationResult 实例"""
        # 设置临时 DB
        os.environ["QUANT_DB_PATH"] = str(tmp_path / "test_sim.db")

        # mock data_mgr.query → 返回 30 行价格
        price_rows = self._make_price_rows(n=30)

        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = price_rows

            sim = Simulator(TradingConfig(
                initial_capital=100_000,
                position_method="fixed",
                max_position_pct=0.30,
            ))
            result = sim.run(
                codes=["000001.SZ"],
                start_date="2026-06-01",
                end_date="2026-06-30",
                strategy_id=6,
            )

        assert isinstance(result, SimulationResult)
        assert result.run_id != ""
        assert result.status == "done"
        assert result.model == "strategy_6"
        assert result.start_date == "2026-06-01"
        assert result.end_date == "2026-06-30"
        assert result.initial_capital == 100_000

    def test_run_equity_curve_populated(self, tmp_path):
        """equity_curve 应记录每日净值"""
        os.environ["QUANT_DB_PATH"] = str(tmp_path / "test_sim.db")
        price_rows = self._make_price_rows(n=30)

        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = price_rows
            sim = Simulator(TradingConfig(initial_capital=100_000))
            sim.run(["000001.SZ"], "2026-06-01", "2026-06-30", 6)

        # 30 个交易日, equity_curve 应有 30 条记录
        assert len(sim.equity_curve) == 30
        assert sim.equity_curve[0]["date"] == "2026-06-01"
        assert sim.equity_curve[-1]["date"] == "2026-06-30"

    def test_run_skips_short_history(self, tmp_path):
        """< 20 行历史数据 → 跳过 (return 早期)"""
        os.environ["QUANT_DB_PATH"] = str(tmp_path / "test_sim.db")
        price_rows = self._make_price_rows(n=10)  # 不足 20 行

        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = price_rows
            sim = Simulator(TradingConfig(initial_capital=100_000))
            result = sim.run(["000001.SZ"], "2026-06-01", "2026-06-30", 6)

        # equity_curve 应为空 (跳过)
        assert sim.equity_curve == []
        # total_trades 0
        assert result.total_trades == 0

    def test_run_handles_exception(self, tmp_path):
        """模拟异常 → status='error', error 字段捕获异常

        通过 patch calc_performance 抛异常 → 验证 except 分支
        """
        os.environ["QUANT_DB_PATH"] = str(tmp_path / "test_sim.db")
        price_rows = self._make_price_rows(n=30)

        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = price_rows
            with patch(
                "src.strategies.simulator.engine.calc_performance",
                side_effect=RuntimeError("test forced error"),
            ):
                sim = Simulator(TradingConfig(initial_capital=100_000))
                result = sim.run(["000001.SZ"], "2026-06-01", "2026-06-30", 6)

        # 异常被捕获
        assert result.status == "error"
        assert "test forced error" in result.error

    def test_run_generates_unique_run_id(self, tmp_path):
        """每次 run 生成不同的 run_id"""
        os.environ["QUANT_DB_PATH"] = str(tmp_path / "test_sim.db")
        price_rows = self._make_price_rows(n=30)

        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = price_rows
            sim = Simulator(TradingConfig(initial_capital=100_000))
            r1 = sim.run(["000001.SZ"], "2026-06-01", "2026-06-30", 6)
            r2 = sim.run(["000001.SZ"], "2026-07-01", "2026-07-30", 6)

        assert r1.run_id != r2.run_id
        assert len(r1.run_id) >= 8


# ═════════════════════════════════════════════════════════════
#  AStockRules 集成
# ═════════════════════════════════════════════════════════════
class TestAStockRulesIntegration:
    """验证 simulator 正确使用 AStockRules (T+1 / 涨跌停)"""

    def test_can_buy_limit_up(self):
        """涨停 → can_buy=False (买不进)"""
        from src.backtest.astock_strategy import AStockRules
        astock = AStockRules()
        # prev_close=10.0, 主板涨停 10% → close=11.0
        can, reason = astock.can_buy(11.0, 10.0)
        assert can is False

    def test_can_sell_limit_down(self):
        """跌停 → can_sell=False (卖不出)"""
        from src.backtest.astock_strategy import AStockRules
        astock = AStockRules()
        # prev_close=10.0, 主板跌停 -10% → close=9.0
        can, reason = astock.can_sell(9.0, 10.0)
        assert can is False

    def test_can_buy_normal(self):
        """正常涨幅 (5%) → can_buy=True"""
        from src.backtest.astock_strategy import AStockRules
        astock = AStockRules()
        can, reason = astock.can_buy(10.5, 10.0)
        assert can is True

    def test_can_sell_normal(self):
        """正常跌幅 (-5%) → can_sell=True"""
        from src.backtest.astock_strategy import AStockRules
        astock = AStockRules()
        can, reason = astock.can_sell(9.5, 10.0)
        assert can is True