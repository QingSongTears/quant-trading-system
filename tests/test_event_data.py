"""测试: src/risk/event_data.py — RiskAlert 数据类 (ADR-0007 D3)

EVENT_RISK_ALERT 的载荷, RiskEngine.check_order / check_daily_limit
拒绝时同步推送, 让 UI / 监控可消费。
"""
from datetime import datetime

import pytest

from src.risk.event_data import RiskAlert, RiskAlertLevel


class TestRiskAlert:
    def test_defaults(self):
        a = RiskAlert(reason="test", level="warn")
        assert a.reason == "test"
        assert a.level == "warn"
        assert a.vt_symbol == ""
        assert isinstance(a.timestamp, datetime)

    def test_all_levels(self):
        for lv in ("info", "warn", "error"):
            a = RiskAlert(reason="x", level=lv, vt_symbol="000001.SZ")
            assert a.level == lv
            assert a.vt_symbol == "000001.SZ"

    def test_repr_includes_reason(self):
        a = RiskAlert(reason="超单笔最大股数: 5000 > 1000", level="warn")
        r = repr(a)
        assert "超单笔最大股数" in r
        assert "warn" in r

    def test_timestamp_defaults_to_now(self):
        """默认 timestamp ≈ 调用时间"""
        before = datetime.now()
        a = RiskAlert(reason="x", level="info")
        after = datetime.now()
        assert before <= a.timestamp <= after

    def test_vt_symbol_optional(self):
        """vt_symbol 可空 (日熔断无具体标的)"""
        a = RiskAlert(reason="日内亏损达熔断线", level="error")
        assert a.vt_symbol == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])