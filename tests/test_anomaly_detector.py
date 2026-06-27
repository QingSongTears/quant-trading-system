"""
test_anomaly_detector.py — ADR-0012 #83 Step 4 测试

覆盖:
  - AnomalyDetector: 数据延迟 / API 失败 / 订单超时 3 类检测
  - 阈值默认 + 可配置
  - 失败计数复位逻辑
  - 订单超时扫描
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.event import Event, EventEngine
from src.event import EVENT_ANOMALY
from src.monitoring.anomaly_detector import (
    DEFAULT_API_FAILURE_RESET_SECONDS,
    DEFAULT_API_FAILURE_THRESHOLD,
    DEFAULT_DATA_DELAY_SECONDS,
    DEFAULT_ORDER_TIMEOUT_SECONDS,
    AnomalyDetector,
)
from src.monitoring.event_data import AnomalyEvent


@pytest.fixture
def engine() -> EventEngine:
    return EventEngine(interval=1, raise_on_inactive=False)


# ── 数据延迟 ──────────────────────────────────────
class TestDataDelay:
    def test_no_data_no_alert(self):
        d = AnomalyDetector()
        assert d.check_data_delay() is False

    def test_recent_data_no_alert(self):
        d = AnomalyDetector()
        d.record_data(datetime.now())
        assert d.check_data_delay() is False

    def test_old_data_triggers_alert(self, engine: EventEngine):
        engine.start()
        d = AnomalyDetector(
            event_engine=engine,
            data_delay_seconds=10,
        )
        # 注入 20 秒前的数据
        old = datetime.now() - timedelta(seconds=20)
        d.record_data(old)
        assert d.check_data_delay() is True
        assert d.anomaly_count == 1
        engine.stop()

    def test_alert_emits_event_anomaly(self, engine: EventEngine):
        engine.start()
        d = AnomalyDetector(event_engine=engine, data_delay_seconds=1)
        old = datetime.now() - timedelta(seconds=5)
        d.record_data(old)
        d.check_data_delay()
        # 验证 EVENT_ANOMALY 已 put
        assert engine.event_count >= 1
        engine.stop()


# ── API 失败 ──────────────────────────────────────
class TestApiFailure:
    def test_below_threshold_no_alert(self):
        d = AnomalyDetector(api_failure_threshold=5)
        for _ in range(4):
            d.record_api_failure(source="datafeed")
        assert d.anomaly_count == 0

    def test_reaching_threshold_triggers_alert(self, engine: EventEngine):
        engine.start()
        d = AnomalyDetector(
            event_engine=engine,
            api_failure_threshold=3,
        )
        for _ in range(3):
            d.record_api_failure(source="datafeed", detail="Tushare 限流")
        assert d.anomaly_count == 1
        engine.stop()

    def test_success_resets_failure_count(self):
        d = AnomalyDetector(api_failure_threshold=5)
        for _ in range(4):
            d.record_api_failure()
        d.record_api_success()
        # 复位后, 再失败 4 次仍不应触发
        for _ in range(4):
            d.record_api_failure()
        assert d.anomaly_count == 0

    def test_reset_window_clears_old_failures(self):
        """超过 reset 窗口无新失败 → 计数清零"""
        d = AnomalyDetector(
            api_failure_threshold=5,
            api_failure_reset_seconds=0,  # 立即重置
        )
        for _ in range(4):
            d.record_api_failure()
        # 等 0 秒后, 下一调用应触发 reset
        d.record_api_failure()
        # 触发 → anomaly_count == 1 (前 4 被 reset, 这 1 次不触发, 总 5 次全累计未超)
        # 实际上 reset 后 5 次都重置, anomaly_count 应为 0
        # 但 record_api_failure 时已 +1, 然后判断 >=5 是 False (1<5)
        assert d.anomaly_count == 0


# ── 订单超时 ──────────────────────────────────────
class TestOrderTimeout:
    def test_no_pending_no_timeout(self):
        d = AnomalyDetector()
        assert d.check_order_timeout() == 0

    def test_pending_order_within_window(self):
        d = AnomalyDetector(order_timeout_seconds=300)
        d.record_order_sent("order_1")
        assert d.pending_order_count == 1
        assert d.check_order_timeout() == 0  # 立即检查不超时

    def test_old_order_triggers_timeout(self, engine: EventEngine):
        engine.start()
        d = AnomalyDetector(
            event_engine=engine,
            order_timeout_seconds=1,
        )
        d.record_order_sent("order_1")
        # 注入"老订单": 用 backdoor 改时间
        # 由于 record_order_sent 用 now, 这里用 sleep 模拟
        import time
        time.sleep(1.1)
        n = d.check_order_timeout()
        assert n == 1
        assert d.anomaly_count == 1
        engine.stop()

    def test_order_filled_removes_from_pending(self):
        d = AnomalyDetector()
        d.record_order_sent("order_1")
        assert d.pending_order_count == 1
        d.record_order_filled("order_1")
        assert d.pending_order_count == 0

    def test_order_rejected_removes_from_pending(self):
        d = AnomalyDetector()
        d.record_order_sent("order_2")
        d.record_order_rejected("order_2")
        assert d.pending_order_count == 0


# ── 默认阈值 ──────────────────────────────────────
class TestDefaults:
    def test_default_data_delay_30min(self):
        assert DEFAULT_DATA_DELAY_SECONDS == 1800

    def test_default_api_threshold_5(self):
        assert DEFAULT_API_FAILURE_THRESHOLD == 5

    def test_default_reset_60s(self):
        assert DEFAULT_API_FAILURE_RESET_SECONDS == 60

    def test_default_order_timeout_5min(self):
        assert DEFAULT_ORDER_TIMEOUT_SECONDS == 300


# ── tick / reset / repr ──────────────────────────────────────
class TestMisc:
    def test_tick_runs_data_check(self):
        d = AnomalyDetector(data_delay_seconds=1)
        d.record_data(datetime.now() - timedelta(seconds=10))
        d.tick()
        assert d.anomaly_count == 1

    def test_reset_clears_state(self):
        d = AnomalyDetector(api_failure_threshold=2)
        d.record_api_failure()
        d.record_order_sent("order_1")
        d.reset()
        assert d.anomaly_count == 0
        assert d.pending_order_count == 0

    def test_repr_includes_counts(self):
        d = AnomalyDetector()
        r = repr(d)
        assert "anomalies" in r
        assert "pending_orders" in r


if __name__ == "__main__":
    pytest.main([__file__, "-v"])