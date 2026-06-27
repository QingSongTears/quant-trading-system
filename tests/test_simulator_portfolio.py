"""
测试 simulator portfolio (ADR-0011 #82)

覆盖:
  - close_position: P&L / 手续费 / 印花税 / holding_days
  - calc_performance: total_return / sharpe / max_drawdown / win_rate / profit_factor
"""
from __future__ import annotations

import pytest

from src.strategies.simulator import (
    Position, TradeRecord, SimulationResult,
)
from src.strategies.simulator.portfolio import (
    close_position, calc_performance,
)


# ═════════════════════════════════════════════════════════════
#  close_position
# ═════════════════════════════════════════════════════════════
class TestClosePosition:
    def test_basic_profit(self):
        """盈利平仓: 卖出价 > 成本价 → net_pnl > 0"""
        pos = Position(
            code="000001.SZ", size=1000,
            cost_basis=10.0, entry_date="2026-06-01",
        )
        trade = close_position(
            pos=pos, exit_price=11.0, exit_date="2026-06-15",
            reason="take_profit", atr=0.3,
            slippage_bps=5.0, run_id="run_001",
        )
        assert trade.run_id == "run_001"
        assert trade.code == "000001.SZ"
        assert trade.direction == "SELL"
        assert trade.exit_reason == "take_profit"
        assert trade.entry_date == "2026-06-01"
        assert trade.entry_price == 10.0
        assert trade.entry_size == 1000
        # 净盈亏 = 卖 - 买 - 手续费 - 印花税 (盈利方向)
        assert trade.net_pnl > 0
        assert trade.pnl > 0

    def test_basic_loss(self):
        """亏损平仓: 卖出价 < 成本价 → net_pnl < 0"""
        pos = Position(
            code="000001.SZ", size=1000,
            cost_basis=10.0, entry_date="2026-06-01",
        )
        trade = close_position(
            pos=pos, exit_price=9.0, exit_date="2026-06-15",
            reason="stop_loss", atr=0.3,
            slippage_bps=5.0, run_id="run_002",
        )
        assert trade.exit_reason == "stop_loss"
        assert trade.net_pnl < 0
        assert trade.pnl < 0

    def test_holding_days(self):
        """holding_days = exit_date - entry_date"""
        pos = Position(
            code="000001.SZ", size=100, cost_basis=10.0,
            entry_date="2026-01-01",
        )
        trade = close_position(
            pos=pos, exit_price=11.0, exit_date="2026-01-21",
            reason="take_profit", atr=0.3,
            slippage_bps=5.0, run_id="r",
        )
        assert trade.holding_days == 20

    def test_commission_minimum(self):
        """小金额: 触发最低 5 元手续费"""
        pos = Position(
            code="000001.SZ", size=100, cost_basis=5.0,
            entry_date="2026-06-01",
        )
        trade = close_position(
            pos=pos, exit_price=5.5, exit_date="2026-06-15",
            reason="take_profit", atr=0.1,
            slippage_bps=5.0, run_id="r",
        )
        # 买入 500 元 → 佣金 max(500*0.00025, 5) = max(0.125, 5) = 5
        # 卖出 550 元 → 佣金 5
        # 印花税 550 * 0.0005 = 0.275
        # 总成本 ≈ 10.275
        assert trade.commission == pytest.approx(10.0, abs=0.01)

    def test_stamp_tax_sell_side(self):
        """印花税仅在卖出时收取 (千 0.5)"""
        pos = Position(
            code="000001.SZ", size=1000, cost_basis=10.0,
            entry_date="2026-06-01",
        )
        trade = close_position(
            pos=pos, exit_price=11.0, exit_date="2026-06-15",
            reason="take_profit", atr=0.3,
            slippage_bps=0,  # 无滑点便于计算
            run_id="r",
        )
        # sell_amount = 1000 * 11.0 = 11000
        # tax = 11000 * 0.0005 = 5.5
        assert trade.stamp_tax == pytest.approx(5.5, abs=0.01)

    def test_slippage_field(self):
        """slippage 字段记录滑点 = exit_price_with_slippage - exit_price

        实现 round(..., 2) → -0.005 舍入到 0.0 (banker's rounding)
        """
        pos = Position(
            code="000001.SZ", size=1000, cost_basis=10.0,
            entry_date="2026-06-01",
        )
        trade = close_position(
            pos=pos, exit_price=10.0, exit_date="2026-06-15",
            reason="take_profit", atr=0.3,
            slippage_bps=5.0, run_id="r",
        )
        # slippage = round(10.0 * 0.9995 - 10.0, 2) = round(-0.005, 2) = 0.0 (banker's)
        assert trade.slippage in (0.0, -0.0)

    def test_slippage_field_visible(self):
        """滑点显著时 (bps=100) → slippage 字段可见非零"""
        pos = Position(
            code="000001.SZ", size=1000, cost_basis=10.0,
            entry_date="2026-06-01",
        )
        trade = close_position(
            pos=pos, exit_price=10.0, exit_date="2026-06-15",
            reason="take_profit", atr=0.3,
            slippage_bps=100, run_id="r",
        )
        # slippage = round(10.0 * 0.99 - 10.0, 2) = round(-0.1, 2) = -0.1
        assert trade.slippage == pytest.approx(-0.1, abs=0.01)

    def test_trade_id_unique(self):
        """trade_id 不为空且唯一"""
        pos = Position(code="000001.SZ", size=100, cost_basis=10.0,
                       entry_date="2026-06-01")
        t1 = close_position(pos, 11.0, "2026-06-15", "tp", 0.3,
                            5.0, "r1")
        t2 = close_position(pos, 11.0, "2026-06-15", "tp", 0.3,
                            5.0, "r1")
        assert t1.trade_id != ""
        assert t2.trade_id != ""
        assert t1.trade_id != t2.trade_id


# ═════════════════════════════════════════════════════════════
#  calc_performance
# ═════════════════════════════════════════════════════════════
class TestCalcPerformance:
    def test_empty_equity_curve(self):
        """空净值曲线 → final_capital 保持, 其他 0"""
        result = SimulationResult()
        result.initial_capital = 100_000
        calc_performance(
            result,
            initial_capital=100_000,
            final_capital=95_000,
            equity_curve=[],
            trades=[],
        )
        assert result.final_capital == 95_000
        assert result.total_return == 0.0
        assert result.max_drawdown == 0.0
        assert result.win_rate == 0.0

    def test_total_return(self):
        """总收益 = (end/start - 1) * 100"""
        result = SimulationResult(initial_capital=100_000)
        equity_curve = [
            {"date": "2026-06-01", "total": 100_000},
            {"date": "2026-06-30", "total": 115_000},
        ]
        calc_performance(
            result, 100_000, 115_000, equity_curve, []
        )
        assert result.total_return == pytest.approx(15.0)
        assert result.final_capital == 115_000

    def test_annual_return(self):
        """年化收益: (1 + r)^(252/days) - 1"""
        result = SimulationResult(initial_capital=100_000)
        # 60 个交易日, 期末 110000 → r = 10%
        equity_curve = [{"date": f"2026-{i:02d}-01", "total": 100_000 + i * 166.67}
                        for i in range(1, 61)]
        equity_curve[-1]["total"] = 110_000
        calc_performance(result, 100_000, 110_000, equity_curve, [])
        # 总收益 10%, 60 天 → 年化 ≈ (1.10)^(252/60) - 1 ≈ 0.473 ≈ 47.3%
        assert 40 < result.annual_return < 55

    def test_max_drawdown(self):
        """最大回撤: 峰值到谷值的最大百分比"""
        result = SimulationResult(initial_capital=100_000)
        equity_curve = [
            {"date": "d1", "total": 100_000},  # 起点
            {"date": "d2", "total": 120_000},  # 峰值
            {"date": "d3", "total": 96_000},   # 谷值 (回撤 20%)
            {"date": "d4", "total": 110_000},
        ]
        calc_performance(result, 100_000, 110_000, equity_curve, [])
        # max_dd = (120_000 - 96_000) / 120_000 * 100 = 20%
        assert result.max_drawdown == pytest.approx(20.0)

    def test_win_rate(self):
        """胜率 = 盈利单数 / 总单数"""
        result = SimulationResult()
        trades = [
            TradeRecord(direction="SELL", net_pnl=100),
            TradeRecord(direction="SELL", net_pnl=200),
            TradeRecord(direction="SELL", net_pnl=-50),
            TradeRecord(direction="BUY", net_pnl=0),  # 不计入胜率 (非 SELL)
        ]
        equity_curve = [{"date": "d1", "total": 100_000}]
        calc_performance(result, 100_000, 100_000, equity_curve, trades)
        # 3 个 SELL, 2 盈利 → 66.7%
        assert result.total_trades == 3
        assert result.win_rate == pytest.approx(66.7, abs=0.1)

    def test_profit_factor(self):
        """盈亏比 = 总盈利 / 总亏损"""
        result = SimulationResult()
        trades = [
            TradeRecord(direction="SELL", net_pnl=300),
            TradeRecord(direction="SELL", net_pnl=200),
            TradeRecord(direction="SELL", net_pnl=-100),
            TradeRecord(direction="SELL", net_pnl=-100),
        ]
        equity_curve = [{"date": "d1", "total": 100_000}]
        calc_performance(result, 100_000, 100_000, equity_curve, trades)
        # 500 / 200 = 2.5
        assert result.profit_factor == pytest.approx(2.5)

    def test_sharpe_ratio_calculated(self):
        """Sharpe 在有波动时计算"""
        result = SimulationResult()
        # 模拟波动: 总收益持续但有日内波动
        equity_curve = [
            {"date": f"d{i}", "total": 100_000 + i * 10 + (i % 3) * 50}
            for i in range(20)
        ]
        calc_performance(result, 100_000, equity_curve[-1]["total"],
                         equity_curve, [])
        # 有 std > 0, 应该算出 sharpe
        assert result.sharpe_ratio != 0.0

    def test_no_profit_no_factor(self):
        """无亏损交易 → profit_factor 保持 0.0 (避免除零)"""
        result = SimulationResult()
        trades = [
            TradeRecord(direction="SELL", net_pnl=100),
            TradeRecord(direction="SELL", net_pnl=200),
        ]
        equity_curve = [{"date": "d1", "total": 100_000}]
        calc_performance(result, 100_000, 100_000, equity_curve, trades)
        assert result.profit_factor == 0.0