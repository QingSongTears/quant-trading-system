"""
EquityStrategy 单测 — src/strategy/equity_strategy.py (2026-06-24)

涵盖:
  - 默认参数 (top_k / rebalance_days / cash_ratio / min_volume 等)
  - 抽象方法 generate_signals
  - setting 覆盖默认参数
  - filter_universe 默认实现
  - get_cash_per_stock
  - on_bars 再平衡触发逻辑
    - 未到 rebalance_days → 跳过
    - universe < top_k → 跳过
    - generate_signals 返回空 → 跳过
    - generate_signals 缺必需列 → 抛 ValueError
    - 正常路径: 调仓 + 写日志
  - on_trade 日志
  - holding_days 维护
"""
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

# 让 tests/ 可以 import src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.gateway import BarData, TradeData
from src.strategy import AlphaStrategy, EquityStrategy


# ── Mock Engine (满足 StrategyEngine Protocol) ──────────────────────────


class _MockEngine:
    def __init__(self):
        self._cash = 100000.0
        self._holding = 0.0

    def send_order(self, *args, **kwargs):
        return [f"MOCK.{self._next_id()}"]

    def _next_id(self):
        if not hasattr(self, "_id"):
            self._id = 0
        self._id += 1
        return self._id

    def cancel_order(self, *args, **kwargs):
        pass

    def write_log(self, msg, strategy):
        if not hasattr(self, "logs"):
            self.logs = []
        self.logs.append(msg)

    def get_cash_available(self):
        return self._cash

    def get_holding_value(self):
        return self._holding

    def get_signal(self):
        return None


# ── 测试用 EquityStrategy 子类 ──────────────────────────


class _TestEquityStrat(EquityStrategy):
    """满足 ABC 的最小实现 + 可控 generate_signals"""
    def __init__(self, engine, name="test", vt_symbols=None, setting=None,
                 signals_factory=None):
        super().__init__(engine, name, vt_symbols or [], setting)
        self._signals_factory = signals_factory or (lambda: pd.DataFrame())

    def on_init(self):
        pass

    def generate_signals(self):
        return self._signals_factory()


def _make_engine_and_strat(name="test", vt_symbols=None, setting=None,
                            signals_factory=None):
    engine = _MockEngine()
    strat = _TestEquityStrat(engine, name, vt_symbols, setting, signals_factory)
    return engine, strat


def _bar(code, exchange, close=10.0, vt_symbol=None):
    if vt_symbol is None:
        vt_symbol = f"{code}.{exchange}"
    return BarData(
        symbol=code, exchange=exchange,
        datetime=datetime(2024, 1, 1),
        close_price=close,
    )


# ── 默认参数 ──────────────────────────


def test_default_top_k_is_30():
    _, strat = _make_engine_and_strat()
    assert strat.top_k == 30


def test_default_rebalance_days_is_20():
    _, strat = _make_engine_and_strat()
    assert strat.rebalance_days == 20


def test_default_min_days_is_3():
    _, strat = _make_engine_and_strat()
    assert strat.min_days == 3


def test_default_cash_ratio_is_0_95():
    _, strat = _make_engine_and_strat()
    assert strat.cash_ratio == 0.95


def test_default_min_volume_is_100():
    _, strat = _make_engine_and_strat()
    assert strat.min_volume == 100


# ── setting 覆盖 ──────────────────────────


def test_setting_overrides_default_params():
    engine = _MockEngine()
    strat = _TestEquityStrat(
        engine, "x", [],
        setting={"top_k": 10, "rebalance_days": 5},
    )
    assert strat.top_k == 10
    assert strat.rebalance_days == 5


# ── 抽象方法 ──────────────────────────


def test_equity_strategy_cannot_be_instantiated_directly():
    """generate_signals 是 ABC, 不能直接实例化"""
    engine = _MockEngine()
    with pytest.raises(TypeError, match="abstract"):
        EquityStrategy(engine, "x", [])


def test_subclass_must_implement_generate_signals():
    class Bad(EquityStrategy):
        # 缺 generate_signals
        pass

    engine = _MockEngine()
    with pytest.raises(TypeError, match="abstract"):
        Bad(engine, "x", [])


# ── filter_universe ──────────────────────────


def test_filter_universe_keeps_only_symbols_with_bars():
    _, strat = _make_engine_and_strat(vt_symbols=["000001.SZ", "000002.SZ"])
    bars = {"000001.SZ": _bar("000001", "SZ")}
    result = strat.filter_universe(["000001.SZ", "000002.SZ", "000003.SZ"], bars)
    assert result == ["000001.SZ"]


def test_filter_universe_empty_bars_returns_empty():
    _, strat = _make_engine_and_strat()
    result = strat.filter_universe(["000001.SZ", "000002.SZ"], {})
    assert result == []


# ── get_cash_per_stock ──────────────────────────


def test_get_cash_per_stock_uses_cash_ratio_and_top_k():
    _, strat = _make_engine_and_strat()
    # 100000 * 0.95 / 30 = 3166.67
    val = strat.get_cash_per_stock(100000.0)
    assert abs(val - 3166.67) < 0.1


def test_get_cash_per_stock_top_k_zero_uses_max_1():
    """top_k=0 时用 max(0, 1)=1 防除零"""
    engine = _MockEngine()
    strat = _TestEquityStrat(engine, "x", [], setting={"top_k": 0})
    val = strat.get_cash_per_stock(100000.0)
    # 100000 * 0.95 / max(0, 1) = 95000
    assert abs(val - 95000) < 0.1


# ── on_bars 再平衡逻辑 ──────────────────────────


def test_on_bars_skips_until_rebalance_days():
    """未到 rebalance_days 跳过, 不调 generate_signals"""
    engine = _MockEngine()
    called = [0]
    def factory():
        called[0] += 1
        return pd.DataFrame()
    strat = _TestEquityStrat(engine, "x", [], signals_factory=factory)
    # 默认 rebalance_days=20, 推 19 次 bar 不应调 generate_signals
    for _ in range(19):
        strat.on_bars({})
    assert called[0] == 0


def test_on_bars_triggers_at_rebalance_days():
    """第 20 次 bar 触发再平衡 (universe >= top_k 才算触发)"""
    engine = _MockEngine()
    called = [0]
    def factory():
        called[0] += 1
        return pd.DataFrame()
    # 给足 universe + bars, 让流程能走到 generate_signals
    symbols = [f"00000{i}.SZ" for i in range(30)]
    bars = {s: _bar(s[:6], "SZ") for s in symbols}
    strat = _TestEquityStrat(
        engine, "x", symbols,
        setting={"top_k": 30, "rebalance_days": 20},
        signals_factory=factory,
    )
    for _ in range(20):
        strat.on_bars(bars)
    assert called[0] == 1


def test_on_bars_skips_when_universe_smaller_than_top_k():
    """可用股票 < top_k 跳过, 加 2026-06-25: 验证 write_log + 无 target"""
    engine = _MockEngine()
    called = [0]
    def factory():
        called[0] += 1
        return pd.DataFrame({"vt_symbol": ["000001.SZ"], "signal": [1.0]})
    strat = _TestEquityStrat(engine, "x", ["000001.SZ"], signals_factory=factory)
    # 默认 top_k=30, universe=1 < 30 → 跳过, 不调 generate_signals
    for _ in range(20):
        strat.on_bars({"000001.SZ": _bar("000001", "SZ")})
    # 加固 (2026-06-25): generate_signals 不应被调, 写日志应含"跳过"
    assert called[0] == 0
    assert any("跳过" in log for log in engine.logs)
    # target_data 应保持空
    assert len(strat.target_data) == 0


def test_on_bars_skips_when_signals_empty():
    engine = _MockEngine()
    called = [0]
    def factory():
        called[0] += 1
        return pd.DataFrame()  # 空
    strat = _TestEquityStrat(
        engine, "x",
        [f"00000{i}.SZ" for i in range(30)],
        setting={"top_k": 30},
        signals_factory=factory,
    )
    for _ in range(20):
        bars = {f"00000{i}.SZ": _bar(f"00000{i}", "SZ") for i in range(30)}
        strat.on_bars(bars)
    # 加固 (2026-06-25): generate_signals 应被调 (走到第 4 步), 但因空返 None 跳过
    assert called[0] == 1
    # 触发过再平衡, 但因 signals 空, 不应调 execute_trading (target 为空)
    assert len(strat.target_data) == 0


def test_on_bars_skips_when_signals_none():
    engine = _MockEngine()
    called = [0]
    def factory():
        called[0] += 1
        return None
    strat = _TestEquityStrat(
        engine, "x",
        [f"00000{i}.SZ" for i in range(30)],
        setting={"top_k": 30},
        signals_factory=factory,
    )
    for _ in range(20):
        bars = {f"00000{i}.SZ": _bar(f"00000{i}", "SZ") for i in range(30)}
        strat.on_bars(bars)
    # 加固 (2026-06-25): generate_signals 应被调, 但因 None 跳过
    assert called[0] == 1
    assert len(strat.target_data) == 0


def test_on_bars_raises_when_signals_missing_required_columns():
    """generate_signals 返回的 DataFrame 缺 vt_symbol/signal 列 → 抛 ValueError"""
    engine = _MockEngine()
    def factory():
        return pd.DataFrame({"foo": [1]})  # 缺必需列
    strat = _TestEquityStrat(
        engine, "x",
        [f"00000{i}.SZ" for i in range(30)],
        setting={"top_k": 30},
        signals_factory=factory,
    )
    bars = {f"00000{i}.SZ": _bar(f"00000{i}", "SZ") for i in range(30)}
    # 累积到 rebalance_days 再触发
    with pytest.raises(ValueError, match="缺少必需列"):
        for _ in range(20):
            strat.on_bars(bars)


def test_on_bars_full_rebalance_flow():
    """完整再平衡流程: 触发 generate_signals + 调 execute_trading + 写日志"""
    engine = _MockEngine()
    symbols = [f"00000{i}.SZ" for i in range(30)]
    bars = {s: _bar(s[:6], "SZ", close=10.0) for s in symbols}

    def factory():
        # 30 只都有信号
        return pd.DataFrame({
            "vt_symbol": symbols,
            "signal": [float(i) for i in range(30)],
        })

    strat = _TestEquityStrat(
        engine, "x", symbols,
        setting={"top_k": 10},  # 只取前 10
        signals_factory=factory,
    )
    for _ in range(20):
        strat.on_bars(bars)

    # 触发后: target 应有 10 个 (top_k=10)
    nonzero_targets = {k: v for k, v in strat.target_data.items() if v > 0}
    assert len(nonzero_targets) == 10
    # 日志应包含 "再平衡"
    assert any("再平衡" in log for log in engine.logs)


def test_on_bars_picks_top_k_by_signal_descending():
    """按 signal 降序取 Top-K, 不按 vt_symbol 字典序"""
    engine = _MockEngine()
    symbols = ["000001.SZ", "000002.SZ", "000003.SZ"]
    bars = {s: _bar(s[:6], "SZ", close=10.0) for s in symbols}

    # signal: 000001=0.5, 000002=2.0, 000003=1.0
    # top_k=2 → 应选 000002 + 000003 (signal 最高两个)
    def factory():
        return pd.DataFrame({
            "vt_symbol": symbols,
            "signal": [0.5, 2.0, 1.0],
        })

    strat = _TestEquityStrat(
        engine, "x", symbols,
        setting={"top_k": 2, "rebalance_days": 1},
        signals_factory=factory,
    )
    strat.on_bars(bars)

    nonzero_targets = {k: v for k, v in strat.target_data.items() if v > 0}
    assert "000002.SZ" in nonzero_targets
    assert "000003.SZ" in nonzero_targets
    assert "000001.SZ" not in nonzero_targets


# ── holding_days 维护 ──────────────────────────


def test_holding_days_increments_for_long_position():
    _, strat = _make_engine_and_strat()
    strat.pos_data["000001.SZ"] = 100
    strat.on_bars({})  # pos > 0 时, holding_days 累加
    assert strat.holding_days["000001.SZ"] == 1


def test_holding_days_resets_when_position_closes():
    _, strat = _make_engine_and_strat()
    strat.pos_data["000001.SZ"] = 100
    strat.on_bars({})
    assert strat.holding_days["000001.SZ"] == 1
    strat.pos_data["000001.SZ"] = 0
    strat.on_bars({})
    assert "000001.SZ" not in strat.holding_days


# ── on_trade 日志 ──────────────────────────


def test_on_trade_writes_log():
    from src.gateway import Direction
    engine = _MockEngine()
    strat = _TestEquityStrat(engine, "x", [])
    trade = TradeData(
        gateway_name="MOCK", symbol="000001", exchange="SZ",
        orderid="o1", tradeid="t1",
        direction=Direction.LONG,
        price=10.0, volume=100,
    )
    strat.on_trade(trade)
    assert any("000001" in log for log in engine.logs)
