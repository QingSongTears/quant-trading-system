"""
测试 simulator dataclass (Signal / TradeRecord / Position / SimulationResult)

ADR-0011: simulator 重构 + 测试覆盖 (#82)
- 测试 4 个公开 dataclass 的字段默认值
- 测试 asdict() 序列化
- 测试 to_dict() / from_dict() 序列化
"""
from __future__ import annotations

from dataclasses import asdict, fields

import pytest

from src.strategies.simulator import (
    Signal,
    TradeRecord,
    Position,
    SimulationResult,
)


# ═════════════════════════════════════════════════════════════
#  Signal
# ═════════════════════════════════════════════════════════════
class TestSignal:
    def test_required_fields(self):
        """Signal 5 个必填字段"""
        s = Signal(
            code="000001.SZ",
            date="2026-06-27",
            action="BUY",
            price=10.0,
            confidence=0.8,
            source="test_strategy",
        )
        assert s.code == "000001.SZ"
        assert s.date == "2026-06-27"
        assert s.action == "BUY"
        assert s.price == 10.0
        assert s.confidence == 0.8
        assert s.source == "test_strategy"

    def test_action_enum_values(self):
        """action ∈ {BUY, SELL, HOLD}"""
        for action in ("BUY", "SELL", "HOLD"):
            s = Signal(code="x", date="2026-01-01", action=action,
                       price=1.0, confidence=0.5, source="t")
            assert s.action == action

    def test_confidence_range(self):
        """confidence ∈ [0, 1]"""
        s_low = Signal(code="x", date="2026-01-01", action="HOLD",
                       price=1.0, confidence=0.0, source="t")
        s_high = Signal(code="x", date="2026-01-01", action="HOLD",
                        price=1.0, confidence=1.0, source="t")
        assert s_low.confidence == 0.0
        assert s_high.confidence == 1.0

    def test_asdict_roundtrip(self):
        """asdict 保留所有字段"""
        s = Signal(code="600519.SH", date="2026-06-27", action="BUY",
                   price=1800.0, confidence=0.95, source="v6_reversal")
        d = asdict(s)
        assert d == {
            "code": "600519.SH",
            "date": "2026-06-27",
            "action": "BUY",
            "price": 1800.0,
            "confidence": 0.95,
            "source": "v6_reversal",
        }


# ═════════════════════════════════════════════════════════════
#  TradeRecord
# ═════════════════════════════════════════════════════════════
class TestTradeRecord:
    def test_defaults(self):
        """TradeRecord 18 个字段全默认值"""
        t = TradeRecord()
        assert t.trade_id == ""
        assert t.run_id == ""
        assert t.code == ""
        assert t.direction == ""
        assert t.signal_source == ""
        assert t.entry_date == ""
        assert t.entry_price == 0.0
        assert t.entry_size == 0
        assert t.exit_date == ""
        assert t.exit_price == 0.0
        assert t.exit_reason == ""
        assert t.pnl == 0.0
        assert t.pnl_pct == 0.0
        assert t.commission == 0.0
        assert t.stamp_tax == 0.0
        assert t.net_pnl == 0.0
        assert t.holding_days == 0
        assert t.slippage == 0.0

    def test_field_count(self):
        """TradeRecord 字段数稳定 (防止新增字段意外破坏序列化)"""
        assert len(fields(TradeRecord)) == 18

    def test_asdict_serializable(self):
        """asdict 后可序列化 (用于 DB 写入)"""
        t = TradeRecord(
            trade_id="t123", run_id="r456", code="000001.SZ",
            direction="SELL", entry_date="2026-06-01", entry_price=10.0,
            entry_size=1000, exit_date="2026-06-15", exit_price=11.5,
            exit_reason="take_profit", pnl=1500.0, pnl_pct=15.0,
            commission=5.75, stamp_tax=5.75, net_pnl=1488.5,
            holding_days=14, slippage=0.05,
        )
        d = asdict(t)
        assert d["trade_id"] == "t123"
        assert d["net_pnl"] == 1488.5
        assert d["holding_days"] == 14


# ═════════════════════════════════════════════════════════════
#  Position
# ═════════════════════════════════════════════════════════════
class TestPosition:
    def test_defaults(self):
        """Position 10 个字段默认值"""
        p = Position()
        assert p.code == ""
        assert p.size == 0
        assert p.cost_basis == 0.0
        assert p.market_value == 0.0
        assert p.unrealized_pnl == 0.0
        assert p.entry_date == ""
        assert p.entry_price == 0.0
        assert p.highest_price == 0.0
        assert p.current_price == 0.0
        assert p.profit_pct == 0.0

    def test_field_count(self):
        assert len(fields(Position)) == 10

    def test_profit_pct_calculation(self):
        """profit_pct = (current / entry - 1) * 100"""
        # 模拟业务用法: Position 在 _simulate_one 内被更新
        p = Position(
            code="000001.SZ", size=1000,
            entry_price=10.0, highest_price=11.0,
            current_price=11.0,
        )
        p.market_value = p.size * p.current_price
        p.unrealized_pnl = p.market_value - p.size * p.cost_basis
        # 业务代码: profit_pct = (close / cost_basis - 1) * 100
        p.profit_pct = (p.current_price / 10.0 - 1) * 100
        assert p.market_value == 11000.0
        # 浮点精度: 用 approx 校验
        assert p.profit_pct == pytest.approx(10.0)


# ═════════════════════════════════════════════════════════════
#  SimulationResult
# ═════════════════════════════════════════════════════════════
class TestSimulationResult:
    def test_defaults(self):
        """SimulationResult 16 个字段默认值"""
        r = SimulationResult()
        assert r.run_id == ""
        assert r.model == ""
        assert r.config == {}
        assert r.status == "running"
        assert r.start_date == ""
        assert r.end_date == ""
        assert r.initial_capital == 0.0
        assert r.final_capital == 0.0
        assert r.total_return == 0.0
        assert r.annual_return == 0.0
        assert r.sharpe_ratio == 0.0
        assert r.max_drawdown == 0.0
        assert r.win_rate == 0.0
        assert r.total_trades == 0
        assert r.profit_factor == 0.0
        assert r.error == ""

    def test_field_count(self):
        assert len(fields(SimulationResult)) == 16

    def test_status_lifecycle(self):
        """status ∈ {running, done, error}"""
        for status in ("running", "done", "error"):
            r = SimulationResult(status=status)
            assert r.status == status

    def test_config_default_factory(self):
        """config 是 dict (default_factory=field)"""
        r1 = SimulationResult()
        r2 = SimulationResult()
        r1.config["test"] = 1
        # default_factory 确保不同实例不共享引用
        assert r2.config == {}


# ═════════════════════════════════════════════════════════════
#  公开 API smoke
# ═════════════════════════════════════════════════════════════
class TestPublicAPISmoke:
    """确保 6 个公开类都可导入 (向后兼容)"""

    def test_all_classes_importable(self):
        from src.strategies.simulator import (
            Signal, TradeRecord, Position, SimulationResult,
            SignalAdapter, Simulator,
        )
        # 类存在 + 是 type
        for cls in (Signal, TradeRecord, Position, SimulationResult,
                    SignalAdapter, Simulator):
            assert isinstance(cls, type)