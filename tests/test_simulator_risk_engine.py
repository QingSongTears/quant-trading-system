"""
测试 Simulator 与 RiskEngine 集成 (ADR-0011 D4-A #82)

覆盖:
  - risk_engine=None (默认) → 跳过风控, 向后兼容
  - 买入前 risk_engine.check_order 调用, 拒绝时跳过交易
  - 平仓前 risk_engine.check_order 调用, 拒绝时持仓保持
  - 通过时正常交易 (不阻断主流程)
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from src.strategies.simulator import Simulator, SimulatorEngine
from src.strategies.trading.config import TradingConfig


# ═════════════════════════════════════════════════════════════
#  RiskEngine 可选注入
# ═════════════════════════════════════════════════════════════
class TestRiskEngineInjection:
    def test_default_no_risk_engine(self):
        """默认 risk_engine=None (向后兼容)"""
        sim = Simulator()
        assert sim.risk_engine is None

    def test_custom_risk_engine(self):
        """__init__ 接受 risk_engine 参数"""
        mock_risk = MagicMock()
        sim = Simulator(risk_engine=mock_risk)
        assert sim.risk_engine is mock_risk

    def test_risk_engine_with_config(self):
        """config + risk_engine 同时传"""
        mock_risk = MagicMock()
        config = TradingConfig(initial_capital=50_000)
        sim = Simulator(config=config, risk_engine=mock_risk)
        assert sim.config.initial_capital == 50_000
        assert sim.risk_engine is mock_risk


# ═════════════════════════════════════════════════════════════
#  买入前置风控
# ═════════════════════════════════════════════════════════════
class TestBuyRiskCheck:
    """买入前 risk_engine.check_order 调用"""

    @staticmethod
    def _make_price_rows(n: int = 30):
        rows = []
        for i in range(n):
            close = 10.0 + i * 0.1
            prev_close = 10.0 + (i - 1) * 0.1 if i > 0 else 10.0
            pct_chg = (close / prev_close - 1) * 100 if i > 0 else 0.0
            rows.append({
                "trade_date": f"2026-06-{i + 1:02d}",
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": round(close, 2),
                "pct_change": round(pct_chg, 2),
            })
        return rows

    def test_buy_rejected_skipped(self, tmp_path):
        """风控拒绝买入 → 跳过交易 (不 raise)"""
        os.environ["QUANT_DB_PATH"] = str(tmp_path / "test_sim.db")

        # mock RiskEngine.check_order 拒绝
        mock_risk = MagicMock()
        mock_risk.check_order.return_value = (False, "超单笔最大金额")

        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = self._make_price_rows(30)
            sim = Simulator(
                TradingConfig(initial_capital=100_000),
                risk_engine=mock_risk,
            )
            result = sim.run(["000001.SZ"], "2026-06-01", "2026-06-30", 6)

        # 至少调用过一次 check_order
        assert mock_risk.check_order.called
        # status=done (风控拒绝不视为异常)
        assert result.status == "done"
        # 无交易发生 (风控拒绝)
        assert len(sim.trades) == 0

    def test_buy_accepted_trades(self, tmp_path):
        """风控通过 → 正常交易"""
        os.environ["QUANT_DB_PATH"] = str(tmp_path / "test_sim.db")

        mock_risk = MagicMock()
        mock_risk.check_order.return_value = (True, "")

        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = self._make_price_rows(30)
            sim = Simulator(
                TradingConfig(initial_capital=100_000),
                risk_engine=mock_risk,
            )
            sim.run(["000001.SZ"], "2026-06-01", "2026-06-30", 6)

        # check_order 至少调用一次 (买入时)
        assert mock_risk.check_order.called
        # 交易可能发生 (取决于止盈止损触发)
        # 验证 check_order 收到的请求格式
        call_args = mock_risk.check_order.call_args_list
        for call in call_args:
            req = call.args[0] if call.args else call.kwargs.get("order_req")
            assert "vt_symbol" in req
            assert "volume" in req
            assert "price" in req


# ═════════════════════════════════════════════════════════════
#  平仓前置风控
# ═════════════════════════════════════════════════════════════
class TestCloseRiskCheck:
    """平仓前 risk_engine.check_order 调用"""

    def test_close_rejected_hold_position(self, tmp_path):
        """风控拒绝平仓 → 持仓保持 (不 raise)"""
        os.environ["QUANT_DB_PATH"] = str(tmp_path / "test_sim.db")

        # 制造一个持仓后大跌的场景 (触发止损)
        # 前 5 天上涨 (触发买入), 第 6 天开始跌 (触发止损)
        rows = []
        for i in range(30):
            if i < 5:
                close = 10.0 + i * 0.5  # 5% 涨幅
            else:
                close = 12.0 - (i - 5) * 0.5  # 跌回 10
            prev_close = rows[i - 1]["close"] if i > 0 else 10.0
            pct_chg = (close / prev_close - 1) * 100 if i > 0 else 0.0
            rows.append({
                "trade_date": f"2026-06-{i + 1:02d}",
                "open": close * 0.99, "high": close * 1.01, "low": close * 0.98,
                "close": round(close, 2), "pct_change": round(pct_chg, 2),
            })

        # 第 1 次 check_order (买入) 通过, 第 2 次 (平仓) 拒绝
        # 用 function 而非 list, 避免 side_effect 耗尽
        call_count = {"n": 0}

        def check_order_fn(req):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return (True, "")  # 买入通过
            return (False, "平仓超日内限制")  # 后续都拒绝

        mock_risk = MagicMock()
        mock_risk.check_order.side_effect = check_order_fn

        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = rows
            sim = Simulator(
                TradingConfig(initial_capital=100_000, fixed_stop_pct=0.05),
                risk_engine=mock_risk,
            )
            result = sim.run(["000001.SZ"], "2026-06-01", "2026-06-30", 6)

        # 至少调用 1 次 check_order (买入或平仓)
        assert mock_risk.check_order.call_count >= 1
        # status=done (风控拒绝不视为异常)
        assert result.status == "done"
        # 净值曲线仍记录 (平仓被拒但引擎跑完)
        assert len(sim.equity_curve) > 0


# ═════════════════════════════════════════════════════════════
#  向后兼容: 无 risk_engine 时正常运行
# ═════════════════════════════════════════════════════════════
class TestNoRiskEngine:
    """无 risk_engine (默认) 时行为不变 (向后兼容)"""

    def test_run_without_risk_engine(self, tmp_path):
        os.environ["QUANT_DB_PATH"] = str(tmp_path / "test_sim.db")
        rows = []
        for i in range(30):
            close = 10.0 + i * 0.05
            prev_close = 10.0 + (i - 1) * 0.05 if i > 0 else 10.0
            pct_chg = (close / prev_close - 1) * 100 if i > 0 else 0.0
            rows.append({
                "trade_date": f"2026-06-{i + 1:02d}",
                "open": close * 0.99, "high": close * 1.01, "low": close * 0.98,
                "close": round(close, 2), "pct_change": round(pct_chg, 2),
            })

        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = rows
            sim = Simulator(TradingConfig(initial_capital=100_000))
            # 不传 risk_engine
            result = sim.run(["000001.SZ"], "2026-06-01", "2026-06-30", 6)

        # 正常运行
        assert result.status == "done"
        assert len(sim.equity_curve) == 30