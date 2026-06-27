"""
测试 simulator SignalAdapter (ADR-0011 #82)

覆盖:
  - SignalAdapter.__init__
  - SignalAdapter.get_daily_signal: 返回 HOLD 中立信号 (简化实现)
  - SignalAdapter.get_signals: 从 DB 解析 equity_curve → Signal 列表
  - 边界: 空 equity_curve / 缺 trade 字段 / 异常 JSON
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.strategies.simulator import Signal, SignalAdapter


# ═════════════════════════════════════════════════════════════
#  SignalAdapter.__init__
# ═════════════════════════════════════════════════════════════
class TestSignalAdapterInit:
    def test_default_source(self):
        sa = SignalAdapter()
        assert sa.source == "backtest"

    def test_custom_source(self):
        sa = SignalAdapter(source="live")
        assert sa.source == "live"


# ═════════════════════════════════════════════════════════════
#  SignalAdapter.get_daily_signal
# ═════════════════════════════════════════════════════════════
class TestGetDailySignal:
    def test_returns_hold(self):
        """默认实现: 返回中立 HOLD 信号 (简化实现)"""
        sa = SignalAdapter()
        sig = sa.get_daily_signal("000001.SZ", "2026-06-27")
        assert isinstance(sig, Signal)
        assert sig.action == "HOLD"
        assert sig.code == "000001.SZ"
        assert sig.date == "2026-06-27"

    def test_source_includes_strategy_id(self):
        sa = SignalAdapter()
        sig = sa.get_daily_signal("000001.SZ", "2026-06-27", strategy_id=7)
        assert sig.source == "strategy_7"

    def test_confidence_zero(self):
        """HOLD 信号的 confidence = 0 (中立的语义)"""
        sa = SignalAdapter()
        sig = sa.get_daily_signal("600519.SH", "2026-06-27")
        assert sig.confidence == 0
        assert sig.price == 0


# ═════════════════════════════════════════════════════════════
#  SignalAdapter.get_signals (mock data_mgr)
# ═════════════════════════════════════════════════════════════
class TestGetSignals:
    def test_empty_result(self):
        """无 backtest_result 行 → 返回 []"""
        sa = SignalAdapter()
        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = []
            signals = sa.get_signals("000001.SZ", "2026-01-01", "2026-06-27")
            assert signals == []

    def test_parses_equity_curve(self):
        """从 equity_curve 解析 trade 事件 → Signal 列表"""
        sa = SignalAdapter()
        # 模拟 1 行 backtest_result, equity_curve 含 2 个 trade 点
        equity_curve = json.dumps([
            {"date": "2026-01-05", "trade": {"type": "BUY", "price": 10.0}},
            {"date": "2026-01-15", "trade": {"type": "SELL", "price": 11.5}},
        ])
        mock_row = {
            "stock_code": "000001.SZ",
            "start_date": "2026-01-01",
            "end_date": "2026-06-27",
            "total_return": 15.0,
            "equity_curve": equity_curve,
        }
        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = [mock_row]
            signals = sa.get_signals("000001.SZ", "2026-01-01", "2026-06-27")

        assert len(signals) == 2
        assert signals[0].action == "BUY"
        assert signals[0].price == 10.0
        assert signals[1].action == "SELL"
        assert signals[1].price == 11.5
        # 所有信号都打 source = strategy_1 (默认 strategy_id)
        for s in signals:
            assert s.source == "strategy_1"
            assert s.confidence == 0.5

    def test_skips_non_trade_points(self):
        """equity_curve 中无 'trade' 字段的点被跳过"""
        sa = SignalAdapter()
        equity_curve = json.dumps([
            {"date": "2026-01-05", "trade": {"type": "BUY", "price": 10.0}},
            {"date": "2026-01-06"},  # 无 trade
            {"date": "2026-01-07", "trade": {"type": "SELL", "price": 11.0}},
        ])
        mock_row = {"equity_curve": equity_curve}
        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = [mock_row]
            signals = sa.get_signals("000001.SZ", "2026-01-01", "2026-06-27")
        assert len(signals) == 2

    def test_invalid_json_skipped(self):
        """无效 JSON 不抛异常, 静默跳过"""
        sa = SignalAdapter()
        mock_row = {"equity_curve": "{invalid json"}
        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = [mock_row]
            signals = sa.get_signals("000001.SZ", "2026-01-01", "2026-06-27")
        assert signals == []

    def test_none_equity_curve(self):
        """equity_curve 为 None → 空列表"""
        sa = SignalAdapter()
        mock_row = {"equity_curve": None}
        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = [mock_row]
            signals = sa.get_signals("000001.SZ", "2026-01-01", "2026-06-27")
        assert signals == []

    def test_strategy_id_passed_through(self):
        """strategy_id 参数正确传递到 SQL"""
        sa = SignalAdapter()
        with patch("src.data.data_mgr") as mock_mgr:
            mock_mgr.query.return_value = []
            sa.get_signals("000001.SZ", "2026-01-01", "2026-06-27", strategy_id=3)
            # 检查 query 调用参数
            call_args = mock_mgr.query.call_args
            assert call_args is not None
            # 第 2 个位置参数是 params dict
            params = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs
            # 验证 sid=3 在 params 中
            assert params["sid"] == 3