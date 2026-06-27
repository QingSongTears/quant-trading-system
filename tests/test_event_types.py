"""
test_monitoring_event_types.py — ADR-0012 #83 Step 1 事件常量测试

覆盖:
  - 4 个监控事件常量已添加到 src.event
  - 监控包 re-export 4 个常量 (从 src.monitoring.event_types)
  - 字符串值符合 vnpy 风格 (eXxx)
  - EVENT_RISK_ALERT 仍然可用 (没破坏 #77)
"""
from __future__ import annotations

import pytest

from src.event import (
    EVENT_ALERT,
    EVENT_ANOMALY,
    EVENT_PNL_UPDATE,
    EVENT_POSITION_UPDATE,
    EVENT_RISK_ALERT,
)
from src.monitoring import event_types as mt
from src.monitoring.event_types import (
    EVENT_ALERT as MT_EVENT_ALERT,
    EVENT_ANOMALY as MT_EVENT_ANOMALY,
    EVENT_PNL_UPDATE as MT_EVENT_PNL_UPDATE,
    EVENT_POSITION_UPDATE as MT_EVENT_POSITION_UPDATE,
)


class TestEventConstantsDefined:
    """4 个监控事件常量已定义 + 字符串值 vnpy 风格"""

    def test_event_pnl_update_exists(self):
        assert EVENT_PNL_UPDATE == "ePnlUpdate"

    def test_event_position_update_exists(self):
        assert EVENT_POSITION_UPDATE == "ePositionUpdate"

    def test_event_anomaly_exists(self):
        assert EVENT_ANOMALY == "eAnomaly"

    def test_event_alert_exists(self):
        assert EVENT_ALERT == "eAlert"

    def test_event_risk_alert_still_exists(self):
        """#77 EVENT_RISK_ALERT 不被破坏"""
        assert EVENT_RISK_ALERT == "eRiskAlert"


class TestEventTypesModuleReexport:
    """src.monitoring.event_types re-export 4 个常量"""

    def test_monitoring_event_types_match_src_event(self):
        assert MT_EVENT_PNL_UPDATE == EVENT_PNL_UPDATE
        assert MT_EVENT_POSITION_UPDATE == EVENT_POSITION_UPDATE
        assert MT_EVENT_ANOMALY == EVENT_ANOMALY
        assert MT_EVENT_ALERT == EVENT_ALERT

    def test_monitoring_event_types_all_present(self):
        names = mt.__all__
        for name in (
            "EVENT_PNL_UPDATE",
            "EVENT_POSITION_UPDATE",
            "EVENT_ANOMALY",
            "EVENT_ALERT",
        ):
            assert name in names, f"{name} 应在 src.monitoring.event_types.__all__"

    def test_no_unexpected_constants(self):
        """不应引入新常量; 当前 4 个 + __all__ 是约定"""
        module_attrs = {n for n in dir(mt) if n.startswith("EVENT_")}
        assert module_attrs == {
            "EVENT_PNL_UPDATE",
            "EVENT_POSITION_UPDATE",
            "EVENT_ANOMALY",
            "EVENT_ALERT",
        }


class TestVNPYSuffixConvention:
    """事件字符串符合 vnpy 风格 (eXxx)"""

    @pytest.mark.parametrize("event", [
        EVENT_PNL_UPDATE,
        EVENT_POSITION_UPDATE,
        EVENT_ANOMALY,
        EVENT_ALERT,
    ])
    def test_starts_with_e_lowercase(self, event):
        assert event.startswith("e"), f"{event!r} 应以 'e' 开头 (vnpy 风格)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])