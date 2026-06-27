"""
测试: VNPY-3 第二波 — 集中度风控 + EVENT_RISK_ALERT (ADR-0007 修复 3, D3)

覆盖:
  - 单行业集中度 (sector_concentration_pct=0.40)
  - 单标的集中度 (single_symbol_concentration_pct=0.22)
  - 集中度校验 balance=0 时跳过
  - 未知行业 = 单独一类
  - 拒绝时同步 put EVENT_RISK_ALERT (warn/error 双通道)
  - sector_map 数据字典
  - _last_prices 从 EVENT_TRADE 维护

注: 文件从 test_risk_engine.py 拆出 (>500 行治理, AGENTS.md §4)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.risk.engine import RiskEngine, RiskConfig
from src.risk.event_data import RiskAlert
from src.event import Event, EVENT_TRADE, EVENT_RISK_ALERT

from tests._risk_fixtures import (
    DummyOrder, DummyTrade, freeze_today,
)


# ═════════════════════════════════════════════════════════════
#  单行业集中度 — check_order 步骤 6
# ═════════════════════════════════════════════════════════════
class TestSectorConcentrationLimit:
    """单行业集中度校验 (ADR-0007 修复 3) — 步骤 6"""

    def test_passes_when_sector_under_cap(self):
        """同行业已持仓 + 本笔 < cap → 通过"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(initial_balance=1_000_000, sector_concentration_pct=0.40),
        )
        # 已持 000858.SZ (白酒) 1000 股 @ 100 = 100,000 (10%)
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(
            volume=1000, price=100, vt_symbol="000858.SZ",
        )))
        # 再下一笔 600519.SH (白酒) 1000 股 @ 100 = 100,000 (10%)
        # 同行业总 200,000 / 1,000,000 = 20% < 40% → 通过
        req = DummyOrder(volume=1000, price=100, vt_symbol="600519.SH")
        ok, msg = risk.check_order(req)
        assert ok, msg

    def test_fails_over_sector_cap(self):
        """同行业累计 > cap → 拒绝"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(initial_balance=1_000_000, sector_concentration_pct=0.40),
        )
        # 已持 000858.SZ (白酒) 5000 股 @ 100 = 500,000 (50%)
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(
            volume=5000, price=100, vt_symbol="000858.SZ",
        )))
        # 再下 600519.SH (白酒) 1000 股 @ 100 = 100,000 (10%)
        # 同行业总 600,000 / 1,000,000 = 60% > 40% → 拒
        req = DummyOrder(volume=1000, price=100, vt_symbol="600519.SH")
        ok, msg = risk.check_order(req)
        assert not ok and "行业集中度" in msg and "白酒" in msg


# ═════════════════════════════════════════════════════════════
#  单标的集中度 — check_order 步骤 5
# ═════════════════════════════════════════════════════════════
class TestSingleSymbolConcentration:
    """单标的集中度校验 (ADR-0007 修复 3) — 步骤 5"""

    def test_fails_over_single_cap(self):
        """amount/balance > single_symbol_concentration_pct → 拒绝"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(
                initial_balance=100_000,
                max_order_pct=0.50,            # 放宽 step 4, 让 step 5 触发
                single_symbol_concentration_pct=0.22,
            ),
        )
        # 30,000 / 100,000 = 30% > 22% → 拒 (step 5 触发)
        req = DummyOrder(volume=1000, price=30, vt_symbol="600519.SH")
        ok, msg = risk.check_order(req)
        assert not ok and "单标的集中度" in msg

    def test_passes_under_single_cap(self):
        """amount/balance ≤ cap → 通过"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(
                initial_balance=100_000,
                single_symbol_concentration_pct=0.22,
            ),
        )
        # 20,000 / 100,000 = 20% ≤ 22% → 通过
        req = DummyOrder(volume=200, price=100, vt_symbol="600519.SH")
        ok, _ = risk.check_order(req)
        assert ok


# ═════════════════════════════════════════════════════════════
#  balance=0 时集中度跳过
# ═════════════════════════════════════════════════════════════
class TestConcentrationDisabledWhenNoBalance:
    """集中度校验需余额 — balance=0 时跳过"""

    def test_no_balance_skips_single_concentration(self):
        """balance=0 → 单标的集中度校验跳过, 不报错"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(initial_balance=0, single_symbol_concentration_pct=0.22),
        )
        # 大金额, 但 balance=0, 应跳过集中度校验
        req = DummyOrder(volume=1000, price=1000, vt_symbol="600519.SH")
        ok, msg = risk.check_order(req)
        assert ok, msg

    def test_no_balance_skips_sector_concentration(self):
        """balance=0 → 行业集中度校验跳过"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(initial_balance=0, sector_concentration_pct=0.40),
        )
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(
            volume=100_000, price=10, vt_symbol="600519.SH",
        )))
        # 已持仓极重, 但 balance=0, 不应被拒
        req = DummyOrder(volume=1000, price=10, vt_symbol="000858.SZ")
        ok, msg = risk.check_order(req)
        assert ok, msg


# ═════════════════════════════════════════════════════════════
#  未知行业 = 单独一类
# ═════════════════════════════════════════════════════════════
class TestSectorUnknownHandled:
    """未知行业视为"单独一类" — 与 src.selection.sector_constraint 一致"""

    def test_unknown_sector_does_not_clash_with_known(self):
        """未知行业的持仓不算入已知行业的金额"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(initial_balance=1_000_000, sector_concentration_pct=0.40),
        )
        # 已持 999999.SH (未知) 5000 股 @ 100 = 500,000
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(
            volume=5000, price=100, vt_symbol="999999.SH",
        )))
        # 再下 600519.SH (白酒) 1000 股 @ 100 = 100,000
        # 白酒行业独立, 100,000 / 1,000,000 = 10% → 通过
        req = DummyOrder(volume=1000, price=100, vt_symbol="600519.SH")
        ok, msg = risk.check_order(req)
        assert ok, msg

    def test_unknown_sector_has_own_cap(self):
        """未知行业累计 > cap → 拒"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(initial_balance=1_000_000, sector_concentration_pct=0.40),
        )
        # 已持 999999.SH (未知) 5000 股 @ 100 = 500,000 (50%)
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(
            volume=5000, price=100, vt_symbol="999999.SH",
        )))
        # 再下 888888.SH (未知) 1000 股 @ 100 = 100,000
        # 同为未知, 600,000 / 1,000,000 = 60% > 40% → 拒
        req = DummyOrder(volume=1000, price=100, vt_symbol="888888.SH")
        ok, msg = risk.check_order(req)
        assert not ok and "行业集中度" in msg and "未知" in msg


# ═════════════════════════════════════════════════════════════
#  EVENT_RISK_ALERT 双通道 (ADR-0007 D3)
# ═════════════════════════════════════════════════════════════
class TestRiskAlertEmittedOnReject:
    """拒绝时同步 put EVENT_RISK_ALERT (ADR-0007 D3)"""

    def test_single_reject_emits_alert_warn(self):
        """单笔拒绝 → EVENT_RISK_ALERT (level='warn')"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_order_volume=1000))
        req = DummyOrder(volume=5000, price=50, vt_symbol="600519.SH")
        ok, msg = risk.check_order(req)
        assert not ok

        # 应 put EVENT_RISK_ALERT
        event_engine.put.assert_called()
        put_calls = event_engine.put.call_args_list
        # 找到 EVENT_RISK_ALERT 调用
        risk_alert_calls = [
            c for c in put_calls if c.args and c.args[0] == EVENT_RISK_ALERT
        ]
        assert len(risk_alert_calls) >= 1
        alert = risk_alert_calls[0].args[1]
        assert isinstance(alert, RiskAlert)
        assert alert.level == "warn"
        assert alert.vt_symbol == "600519.SH"
        assert msg in alert.reason or alert.reason in msg

    def test_daily_limit_emits_alert_error(self):
        """日熔断 → EVENT_RISK_ALERT (level='error')"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(max_daily_trades=3))
        freeze_today(risk)
        risk._daily_trades = 5
        ok, _ = risk.check_daily_limit()
        assert not ok

        event_engine.put.assert_called()
        risk_alert_calls = [
            c for c in event_engine.put.call_args_list
            if c.args and c.args[0] == EVENT_RISK_ALERT
        ]
        assert len(risk_alert_calls) >= 1
        alert = risk_alert_calls[0].args[1]
        assert alert.level == "error"
        assert alert.vt_symbol == ""  # 日熔断无特定标的

    def test_pass_order_does_not_emit_alert(self):
        """通过的订单不触发 EVENT_RISK_ALERT"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine, RiskConfig(initial_balance=1_000_000))
        req = DummyOrder(volume=100, price=50, vt_symbol="600519.SH")
        ok, _ = risk.check_order(req)
        assert ok

        # put 不应被调用
        event_engine.put.assert_not_called()

    def test_concentration_reject_emits_alert(self):
        """集中度拒绝 → EVENT_RISK_ALERT (warn)"""
        event_engine = MagicMock()
        risk = RiskEngine(
            event_engine,
            RiskConfig(
                initial_balance=100_000,
                max_order_pct=0.50,           # 放宽 step 4, 让 step 5 触发
                single_symbol_concentration_pct=0.22,
            ),
        )
        req = DummyOrder(volume=1000, price=30, vt_symbol="600519.SH")
        ok, _ = risk.check_order(req)
        assert not ok

        risk_alert_calls = [
            c for c in event_engine.put.call_args_list
            if c.args and c.args[0] == EVENT_RISK_ALERT
        ]
        assert len(risk_alert_calls) >= 1


# ═════════════════════════════════════════════════════════════
#  sector_map 字典
# ═════════════════════════════════════════════════════════════
class TestSectorMap:
    """src.risk.sector_map — A 股 TOP20 行业字典 (v2.2 简化版)"""

    def test_known_symbols(self):
        from src.risk.sector_map import get_sector
        assert get_sector("600519.SH") == "白酒"
        assert get_sector("000858.SZ") == "白酒"
        assert get_sector("600036.SH") == "银行"
        assert get_sector("601318.SH") == "银行"
        assert get_sector("000333.SZ") == "家电"
        assert get_sector("300750.SZ") == "新能源"

    def test_unknown_returns_default(self):
        from src.risk.sector_map import get_sector, UNKNOWN
        assert get_sector("999999.SH") == UNKNOWN
        assert get_sector("") == UNKNOWN
        assert get_sector("INVALID") == UNKNOWN

    def test_case_insensitive(self):
        from src.risk.sector_map import get_sector
        assert get_sector("600519.sh") == "白酒"
        assert get_sector("600519.SH") == get_sector("600519.sh")

    def test_count_at_least_20(self):
        """至少 20 只持仓股 (ADR-0007 修复 3 要求)"""
        from src.risk.sector_map import known_count
        assert known_count() >= 20

    def test_all_sectors_unique(self):
        """覆盖 ≥ 5 个不同行业 (避免单行业字典)"""
        from src.risk.sector_map import all_sectors
        sectors = all_sectors()
        assert len(sectors) >= 5, f"行业过少: {sectors}"


# ═════════════════════════════════════════════════════════════
#  on_trade — _last_prices 跟踪
# ═════════════════════════════════════════════════════════════
class TestOnTradeUpdatesLastPrice:
    """EVENT_TRADE → _last_prices 更新 (集中度计算依赖)"""

    def test_trade_records_last_price(self):
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(
            volume=100, price=50, vt_symbol="000001.SZ",
        )))
        assert risk._last_prices["000001.SZ"] == 50.0

    def test_trade_overwrites_price(self):
        """后续成交价覆盖前价 (最新价优先)"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(
            volume=100, price=50, vt_symbol="000001.SZ",
        )))
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(
            volume=100, price=55, vt_symbol="000001.SZ",
        )))
        assert risk._last_prices["000001.SZ"] == 55.0

    def test_trade_zero_price_ignored(self):
        """price=0 (异常数据) → 不更新 _last_prices"""
        event_engine = MagicMock()
        risk = RiskEngine(event_engine)
        risk.on_trade(Event(EVENT_TRADE, DummyTrade(
            volume=100, price=0, vt_symbol="000001.SZ",
        )))
        assert "000001.SZ" not in risk._last_prices


# ═════════════════════════════════════════════════════════════
#  RiskConfig 集中度字段默认值 (ADR-0007 修复 3)
# ═════════════════════════════════════════════════════════════
class TestRiskConfigConcentration:
    """RiskConfig 集中度字段默认值"""

    def test_default_sector_cap(self):
        """默认 sector_concentration_pct = 0.40 (与 ADR-0007 一致)"""
        c = RiskConfig()
        assert c.sector_concentration_pct == 0.40

    def test_default_single_cap(self):
        """默认 single_symbol_concentration_pct = 0.22 (与 MAX_SINGLE_POSITION_PCT 对齐)"""
        c = RiskConfig()
        assert c.single_symbol_concentration_pct == 0.22

    def test_sector_map_overridable(self):
        """sector_map 字段可注入自定义函数"""
        custom = lambda x: "TEST"  # noqa: E731
        c = RiskConfig(sector_map=custom)
        assert c.sector_map("600519.SH") == "TEST"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])