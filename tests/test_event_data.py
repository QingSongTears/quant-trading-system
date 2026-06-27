"""
test_monitoring_event_data.py — ADR-0012 #83 Step 1 单元测试

覆盖:
  - PnlSnapshot: 字段默认值 + repr + serialization 友好
  - PositionSnapshot: 字段默认值 + repr
  - AnomalyEvent: kind/severity Literal 校验 + 默认值
"""
from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from datetime import datetime

import pytest

from src.monitoring.event_data import (
    AnomalyEvent,
    PnlSnapshot,
    PositionSnapshot,
)


class TestPnlSnapshot:
    """PnlSnapshot dataclass 字段 + 默认值"""

    def test_default_values(self):
        p = PnlSnapshot()
        assert p.total_value == 0.0
        assert p.cash == 0.0
        assert p.position_value == 0.0
        assert p.unrealized_pnl == 0.0
        assert p.realized_pnl == 0.0
        assert p.position_count == 0
        assert p.run_id == ""
        assert isinstance(p.timestamp, datetime)

    def test_custom_values(self):
        ts = datetime(2026, 6, 27, 14, 30, 0)
        p = PnlSnapshot(
            timestamp=ts,
            total_value=100_500.0,
            cash=60_000.0,
            position_value=40_500.0,
            unrealized_pnl=500.0,
            realized_pnl=-200.0,
            position_count=3,
            run_id="abc123",
        )
        assert p.timestamp == ts
        assert p.total_value == 100_500.0
        assert p.run_id == "abc123"

    def test_repr_contains_key_metrics(self):
        p = PnlSnapshot(total_value=100_000.0, position_count=2)
        r = repr(p)
        assert "100,000" in r or "100000" in r
        assert "2" in r

    def test_is_dataclass(self):
        assert is_dataclass(PnlSnapshot)
        field_names = {f.name for f in fields(PnlSnapshot)}
        assert "timestamp" in field_names
        assert "total_value" in field_names
        assert "run_id" in field_names

    def test_asdict_roundtrip(self):
        p = PnlSnapshot(total_value=12345.6, position_count=1)
        d = asdict(p)
        restored = PnlSnapshot(**d)
        assert restored.total_value == 12345.6
        assert restored.position_count == 1


class TestPositionSnapshot:
    """PositionSnapshot dataclass 字段 + 默认值"""

    def test_default_values(self):
        p = PositionSnapshot()
        assert p.vt_symbol == ""
        assert p.size == 0
        assert p.cost_basis == 0.0
        assert p.current_price == 0.0
        assert p.market_value == 0.0
        assert p.unrealized_pnl == 0.0
        assert p.profit_pct == 0.0
        assert p.holding_days == 0
        assert isinstance(p.timestamp, datetime)

    def test_long_position(self):
        p = PositionSnapshot(
            vt_symbol="000001.SZ",
            size=1000,
            cost_basis=10.0,
            current_price=11.5,
            market_value=11_500.0,
            unrealized_pnl=1_500.0,
            profit_pct=15.0,
            holding_days=5,
        )
        assert p.vt_symbol == "000001.SZ"
        assert p.size == 1000
        assert p.unrealized_pnl == 1_500.0

    def test_repr_includes_symbol(self):
        p = PositionSnapshot(vt_symbol="600519.SH", size=100)
        r = repr(p)
        assert "600519.SH" in r
        assert "100" in r


class TestAnomalyEvent:
    """AnomalyEvent dataclass + kind Literal"""

    def test_default_kind_data_delay(self):
        e = AnomalyEvent()
        assert e.kind == "data_delay"
        assert e.severity == "warn"
        assert e.detail == ""
        assert e.source == ""

    def test_api_failure_kind(self):
        e = AnomalyEvent(
            kind="api_failure",
            severity="error",
            detail="Tushare 连续失败 5 次",
            source="datafeed.local",
        )
        assert e.kind == "api_failure"
        assert e.severity == "error"
        assert "Tushare" in e.detail

    def test_order_timeout_kind(self):
        e = AnomalyEvent(
            kind="order_timeout",
            severity="warn",
            detail="订单 abc123 已挂 6 分钟未成交",
            source="OmsEngine",
        )
        assert e.kind == "order_timeout"
        assert e.source == "OmsEngine"

    def test_all_three_kinds_valid(self):
        for kind in ("data_delay", "api_failure", "order_timeout"):
            e = AnomalyEvent(kind=kind)
            assert e.kind == kind

    def test_repr_truncates_detail(self):
        long_detail = "x" * 200
        e = AnomalyEvent(detail=long_detail)
        r = repr(e)
        # repr 截断 40 字符
        assert "x" * 40 in r or "x" * 41 in r
        assert "x" * 200 not in r

    def test_severity_levels(self):
        # 3 个级别都是合法的 Literal
        for sev in ("info", "warn", "error"):
            e = AnomalyEvent(severity=sev)
            assert e.severity == sev


if __name__ == "__main__":
    pytest.main([__file__, "-v"])