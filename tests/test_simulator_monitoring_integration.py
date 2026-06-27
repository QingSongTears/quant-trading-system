"""
test_simulator_monitoring_integration.py — ADR-0012 #83 Step 6 测试

覆盖:
  - SimulatorEngine 接受可选 event_engine 参数 (向后兼容)
  - simulate_one 在 EventEngine 注入时推送 EVENT_PNL_UPDATE / EVENT_POSITION_UPDATE
  - 未注入 EventEngine 时静默 (向后兼容)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.event import Event, EventEngine
from src.event import (
    EVENT_PNL_UPDATE,
    EVENT_POSITION_UPDATE,
)
from src.monitoring.collector import PnlCollector, PositionCollector
from src.monitoring.event_data import PnlSnapshot, PositionSnapshot
from src.strategies.simulator.event_loop import simulate_one
from src.strategies.simulator.engine import SimulatorEngine
from src.strategies.simulator.portfolio_types import Position


# ── SimulatorEngine 接受 event_engine 参数 ──────────────────────────────────────
class TestSimulatorEngineEventEngineParam:
    def test_default_no_event_engine(self):
        s = SimulatorEngine()
        assert s.event_engine is None

    def test_with_event_engine(self):
        engine = EventEngine(interval=1, raise_on_inactive=False)
        s = SimulatorEngine(event_engine=engine)
        assert s.event_engine is engine

    def test_backward_compat_no_event_engine(self):
        """#82 已调用方: Simulator(config, risk_engine) 不传 event_engine 仍可用"""
        s = SimulatorEngine(config=None, risk_engine=None)
        assert s.event_engine is None


# ── simulate_one 推 PNL/POSITION 事件 ──────────────────────────────────────
class TestSimulateOneEmitsEvents:
    def _setup_engine_with_data(self) -> SimulatorEngine:
        """mock data_mgr 返回 30 行 close 递增 → 模拟买入 + 持仓"""
        s = SimulatorEngine()
        rows = []
        for i in range(30):
            rows.append({
                "trade_date": f"2026-06-{i+1:02d}",
                "open": 10.0 + i * 0.1,
                "high": 10.5 + i * 0.1,
                "low": 9.5 + i * 0.1,
                "close": 10.0 + i * 0.1,
                "pct_change": 1.0,
            })
        return s, rows

    def test_emit_pnl_update_without_event_engine_silent(self):
        """未注入 EventEngine → 静默 (不抛)"""
        s = SimulatorEngine()
        # mock data_mgr
        rows = [{
            "trade_date": "2026-06-01",
            "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0,
            "pct_change": 1.0,
        } for _ in range(30)]
        with patch("src.data.data_mgr") as mock_dm:
            mock_dm.query.return_value = rows
            # 不传 event_engine → simulate_one 不应抛
            simulate_one(s, "000001", "2026-06-01", "2026-06-30", 6)

    def test_emit_pnl_update_with_event_engine(self):
        """注入 EventEngine + PnlCollector → 收到 PnlSnapshot"""
        engine = EventEngine(interval=1, raise_on_inactive=False)
        engine.start()
        s = SimulatorEngine(event_engine=engine)
        rows = [{
            "trade_date": f"2026-06-{i+1:02d}",
            "open": 10.0 + i * 0.1,
            "high": 10.5 + i * 0.1,
            "low": 9.5 + i * 0.1,
            "close": 10.0 + i * 0.1,
            "pct_change": 1.0,
        } for i in range(30)]
        collector = PnlCollector()
        collector.attach(engine)

        with patch("src.data.data_mgr") as mock_dm:
            mock_dm.query.return_value = rows
            simulate_one(s, "000001", "2026-06-01", "2026-06-30", 6)

        # 30 天 → 至少 1 条 PnlSnapshot (有可能更多, 取决于实际触发)
        assert len(collector) >= 1
        engine.stop()

    def test_emit_position_update_with_event_engine(self):
        """持仓建立 → 至少 1 条 PositionSnapshot"""
        engine = EventEngine(interval=1, raise_on_inactive=False)
        engine.start()
        s = SimulatorEngine(event_engine=engine)
        rows = [{
            "trade_date": f"2026-06-{i+1:02d}",
            "open": 10.0 + i * 0.1,
            "high": 10.5 + i * 0.1,
            "low": 9.5 + i * 0.1,
            "close": 10.0 + i * 0.1,
            "pct_change": 1.0,
        } for i in range(30)]
        collector = PositionCollector()
        collector.attach(engine)

        with patch("src.data.data_mgr") as mock_dm:
            mock_dm.query.return_value = rows
            simulate_one(s, "000001", "2026-06-01", "2026-06-30", 6)

        assert len(collector) >= 1
        engine.stop()


# ── emit 函数直接测试 ──────────────────────────────────────
class TestEmitFunctionsDirect:
    """直接测 _emit_pnl_snapshot / _emit_position_snapshot"""

    def test_emit_pnl_silent_when_no_event_engine(self):
        s = SimulatorEngine()
        from src.strategies.simulator.event_loop import _emit_pnl_snapshot
        # 不抛
        _emit_pnl_snapshot(s, "000001", "2026-06-01", 100_000.0)

    def test_emit_position_silent_when_no_event_engine(self):
        s = SimulatorEngine()
        from src.strategies.simulator.event_loop import _emit_position_snapshot
        pos = Position(code="000001", size=100, cost_basis=10.0)
        # 不抛
        _emit_position_snapshot(s, pos, "2026-06-01")

    def test_emit_pnl_snapshot_payload(self):
        engine = EventEngine(interval=1, raise_on_inactive=False)
        engine.start()
        s = SimulatorEngine(event_engine=engine)
        s.run_id = "test_run_1"
        from src.strategies.simulator.event_loop import _emit_pnl_snapshot

        _emit_pnl_snapshot(s, "000001", "2026-06-01", 100_500.0)

        assert engine.event_count == 1
        engine.stop()

    def test_emit_position_snapshot_payload(self):
        engine = EventEngine(interval=1, raise_on_inactive=False)
        engine.start()
        s = SimulatorEngine(event_engine=engine)
        from src.strategies.simulator.event_loop import _emit_position_snapshot

        pos = Position(
            code="000001", size=1000, cost_basis=10.0,
            current_price=11.0, market_value=11_000.0,
            unrealized_pnl=1_000.0, profit_pct=10.0,
        )
        _emit_position_snapshot(s, pos, "2026-06-05")

        assert engine.event_count == 1
        engine.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])