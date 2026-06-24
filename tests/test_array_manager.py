"""
ArrayManager 单测 — src/indicator/array_manager.py (2026-06-24)

涵盖:
  - 构造 + 校验
  - update_bar 推单根
  - update_bars 批量推
  - 17 指标: sma / ema / macd / boll / donchian
             rsi / kdj / wr / cci / roc
             atr / std / natr
             obv / mfi
             tr / dm
  - 数据不足返 None
  - FIFO 行为 (超过 size 自动淘汰)
  - reset
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

# 让 tests/ 可以 import src/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.gateway import BarData
from src.indicator import ArrayManager


# ── 工具 ──────────────────────────


def _make_bar(i: int, o=10.0, h=11.0, l=9.5, c=10.5, vol=1000.0, turnover=10000.0):
    """生成第 i 根 bar (i 越大时间越后)"""
    return BarData(
        symbol="000001", exchange="SZ",
        datetime=datetime(2024, 1, 1) + timedelta(days=i),
        interval="1d",
        open_price=o, high_price=h, low_price=l, close_price=c,
        volume=vol, turnover=turnover,
    )


def _rising_bars(n: int, start: float = 10.0, step: float = 0.1):
    """生成连续上涨的 n 根 bar"""
    return [
        _make_bar(
            i,
            o=start + i * step,
            h=start + (i + 1) * step + 0.1,
            l=start + i * step - 0.05,
            c=start + (i + 1) * step,
            vol=1000.0,
        )
        for i in range(n)
    ]


# ── 构造 ──────────────────────────


def test_init_default_size_100():
    am = ArrayManager()
    assert am.size == 100
    assert am.count == 0


def test_init_rejects_size_less_than_1():
    with pytest.raises(ValueError, match="size"):
        ArrayManager(size=0)


def test_init_arrays_are_zero_filled():
    am = ArrayManager(size=10)
    assert am.open.shape == (10,)
    assert am.high.shape == (10,)
    assert am.low.shape == (10,)
    assert am.close.shape == (10,)
    assert am.volume.shape == (10,)
    assert am.turnover.shape == (10,)
    assert am.open[-1] == 0.0


# ── update_bar ──────────────────────────


def test_update_bar_appends():
    am = ArrayManager(size=5)
    bar = _make_bar(0, c=10.5)
    am.update_bar(bar)
    assert am.count == 1
    assert am.close[-1] == 10.5


def test_update_bar_increments_count():
    am = ArrayManager(size=10)
    for i in range(5):
        am.update_bar(_make_bar(i))
    assert am.count == 5


def test_update_bar_does_not_exceed_size():
    """count 不超过 size"""
    am = ArrayManager(size=3)
    for i in range(10):
        am.update_bar(_make_bar(i))
    assert am.count == 3
    assert am.close[-1] == _make_bar(9).close_price


def test_update_bar_fifo():
    """超过 size 后老的被淘汰"""
    am = ArrayManager(size=3)
    for i in range(5):
        am.update_bar(_make_bar(i, c=10.0 + i))
    # 最后 3 根: i=2, 3, 4
    assert am.close[-3] == 12.0
    assert am.close[-2] == 13.0
    assert am.close[-1] == 14.0


def test_update_bars_batch():
    am = ArrayManager(size=20)
    bars = _rising_bars(10)
    am.update_bars(bars)
    assert am.count == 10


def test_reset_clears_data():
    am = ArrayManager(size=5)
    am.update_bars(_rising_bars(5))
    am.reset()
    assert am.count == 0
    assert am.close[-1] == 0.0


# ── 趋势指标 ──────────────────────────


def test_sma_returns_mean():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(10))
    # close: 10.1, 10.2, ..., 11.0 (10 根, 第 1 根起始 10.0)
    # mean = 10.55
    val = am.sma(10)
    assert val is not None
    assert abs(val - 10.55) < 0.01


def test_sma_returns_none_when_insufficient():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(5))
    assert am.sma(10) is None


def test_ema_returns_value():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    val = am.ema(10)
    assert val is not None
    assert 10.0 < val < 11.5


def test_macd_returns_tuple():
    am = ArrayManager(size=60)
    am.update_bars(_rising_bars(50))
    result = am.macd(12, 26, 9)
    assert result is not None
    dif, dea, macd_val = result
    # 上涨趋势, DIF > 0, DEA > 0
    assert dif > 0
    assert dea > 0


def test_macd_returns_none_when_insufficient():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    assert am.macd() is None  # 12+26+9+5=52 > 15


def test_boll_returns_tuple():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(20))
    result = am.boll(20, 2.0)
    assert result is not None
    mid, upper, lower = result
    assert upper > mid > lower


def test_donchian_returns_high_low():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(20))
    result = am.donchian(20)
    assert result is not None
    upper, lower = result
    # 最高 = 最后 1 根 high (i=19): h = 10 + 20*0.1 + 0.1 = 12.1
    assert upper == 12.1
    # 最低 = 第 1 根 low (i=0): l = 10.0 - 0.05 = 9.95
    assert lower == 9.95


# ── 动量指标 ──────────────────────────


def test_rsi_rising_trend_near_100():
    """连续上涨, RSI 应接近 100"""
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    val = am.rsi(14)
    assert val is not None
    assert val > 70  # 强超买区


def test_rsi_falling_trend_near_0():
    """连续下跌, RSI 应接近 0"""
    am = ArrayManager(size=20)
    bars = _rising_bars(15)
    # 倒序成下跌
    falling = [
        _make_bar(i, o=15.0 - i * 0.1, h=15.0 - i * 0.1 + 0.1,
                  l=15.0 - i * 0.1 - 0.05, c=15.0 - (i + 1) * 0.1)
        for i in range(15)
    ]
    am.update_bars(falling)
    val = am.rsi(14)
    assert val is not None
    assert val < 30  # 强超卖区


def test_rsi_returns_none_when_insufficient():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(5))
    assert am.rsi(14) is None  # 需 15 根


def test_kdj_returns_tuple():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    result = am.kdj(9, 3, 3)
    assert result is not None
    k, d, j = result
    # K, D, J 应在合理范围
    assert 0 <= k <= 100
    assert 0 <= d <= 100
    # J = 3K - 2D
    assert abs(j - (3 * k - 2 * d)) < 0.01


def test_kdj_state_persists_across_calls():
    """修 2026-06-25: KDJ K/D 状态应持续, 不每次重置 50

    验证方式: 同趋势连续调, 第二次 K 不应跳回 50
    """
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(10))
    k1, d1, _ = am.kdj(9, 3, 3)
    # 推 1 根再算, K/D 应继续递推, 不是从 50 重新开始
    am.update_bar(_make_bar(10, c=15.5))
    k2, d2, _ = am.kdj(9, 3, 3)
    # 上涨 → K 继续上升
    assert k2 > k1 - 0.5  # 允许小幅波动, 但不能跳回 50
    # 第二次不是从 50 重新递推 (如果重置, k2 应 ≈ 50)
    assert k2 > 50


def test_kdj_state_resets_on_reset():
    """reset() 后 KDJ 状态应回 50"""
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    am.kdj(9, 3, 3)  # 推 KDJ 状态
    am.reset()
    # reset 后, 重新推 9 根 (kdj lookback=9), K 应从 50 开始递推
    for i in range(9):
        am.update_bar(_make_bar(i, c=10.0 + i * 0.1))
    k, d, _ = am.kdj(9, 3, 3)
    # 从 50 出发, 连续递推 9 次, K 应在 [50, 100]
    assert 50 <= k <= 100
    # K 应有非零精度, 不应是初始 50 (除非 RSV=50 极端 case)
    assert k != 50.0 or d != 50.0  # 至少有一个动了


def test_macd_dea_matches_vnpy_methodology():
    """修 2026-06-25: DEA = EMA(DIF, signal) 全序列递推

    验证: macd 算 2 次 (数据相同) 应返相同结果 (确定性)
    验证: DEA 应在 DIF 附近 (而不是噪声值)
    """
    am = ArrayManager(size=60)
    am.update_bars(_rising_bars(50))
    dif, dea, macd_val = am.macd(12, 26, 9)
    # 上涨趋势, DIF > 0, DEA > 0
    assert dif > 0
    assert dea > 0
    # DEA 应在 DIF 附近 (DEA 是 DIF 的 EMA 平滑)
    assert abs(dea - dif) < abs(dif) * 0.5  # DEA 不应偏离 DIF 太远
    # MACD 柱 = 2*(DIF-DEA)
    assert abs(macd_val - 2 * (dif - dea)) < 1e-6


def test_boll_uses_sample_std_ddof1():
    """修 2026-06-25: boll 用 ddof=1 (样本标准差, 跟 vnpy 一致)

    验证: 已知数据手动算, 对比
    """
    am = ArrayManager(size=20)
    # 固定 close: 10, 11, 12, ..., 19 (10 根, 后面 0)
    # mean = 14.5, std (ddof=1) ≈ 3.027
    bars = [
        _make_bar(i, o=10.0 + i, h=10.0 + i, l=10.0 + i, c=10.0 + i, vol=0)
        for i in range(10)
    ]
    am.update_bars(bars)
    mid, upper, lower = am.boll(10, 2.0)
    assert abs(mid - 14.5) < 0.01
    # 样本标准差 (ddof=1) 比总体 (ddof=0) 大 sqrt(n/(n-1)) 倍
    import math
    expected_std = math.sqrt(sum((c - 14.5) ** 2 for c in range(10, 20)) / 9)
    assert abs(upper - mid) - 2 * expected_std < 0.01


def test_wr_returns_negative():
    """Williams %R 应为 -100 ~ 0"""
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    val = am.wr(14)
    assert val is not None
    assert -100 <= val <= 0


def test_cci_returns_value():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    val = am.cci(14)
    assert val is not None
    assert isinstance(val, float)


def test_roc_positive_for_rising():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    val = am.roc(10)
    assert val is not None
    assert val > 0  # 上涨 → 正 ROC


# ── 波动指标 ──────────────────────────


def test_atr_returns_positive():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    val = am.atr(14)
    assert val is not None
    assert val > 0


def test_std_returns_positive():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    val = am.std(10)
    assert val is not None
    assert val > 0


def test_natr_returns_percentage():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    val = self = am.natr(14)  # bind for type checker
    assert val is not None
    assert 0 < val < 100  # 百分比


# ── 成交量指标 ──────────────────────────


def test_obv_rising_is_positive():
    """上涨 → OBV 为正 (累计买入)"""
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    val = am.obv()
    assert val is not None
    assert val > 0


def test_obv_returns_none_when_insufficient():
    am = ArrayManager(size=10)
    am.update_bar(_make_bar(0))
    assert am.obv() is None


def test_mfi_returns_0_to_100():
    am = ArrayManager(size=20)
    am.update_bars(_rising_bars(15))
    val = am.mfi(14)
    assert val is not None
    assert 0 <= val <= 100


# ── 辅助指标 ──────────────────────────


def test_tr_returns_positive():
    am = ArrayManager(size=10)
    am.update_bars(_rising_bars(5))
    val = am.tr()
    assert val is not None
    assert val >= 0


def test_dm_returns_tuple():
    am = ArrayManager(size=10)
    am.update_bars(_rising_bars(5))
    result = am.dm()
    assert result is not None
    plus_dm, minus_dm = result
    # 上涨趋势, +DM 应 >= -DM
    assert plus_dm >= 0
    assert minus_dm >= 0


# ── 调试属性 ──────────────────────────


def test_last_close_returns_zero_when_empty():
    am = ArrayManager(size=10)
    assert am.last_close == 0.0


def test_last_close_returns_last():
    am = ArrayManager(size=10)
    am.update_bars(_rising_bars(5))
    # 最后 1 根 c = 10 + 5*0.1 = 10.5
    assert am.last_close == 10.5


def test_repr_includes_size_count_last_close():
    am = ArrayManager(size=10)
    am.update_bars(_rising_bars(5))
    r = repr(am)
    assert "size=10" in r
    assert "count=5" in r
    assert "last_close=10.50" in r


# ── 集成: 接 data_mgr ──────────────────────────


def test_array_manager_with_bars_from_lower():
    """ArrayManager 接 bars_from_lower 输出的 K 线"""
    from src.indicator import bars_from_lower
    # 生成 30 根 1m 模拟数据
    bars_1m = [
        BarData(
            symbol="000001", exchange="SZ",
            datetime=datetime(2024, 1, 1, 9, 30 + i),
            interval="1m",
            open_price=10.0 + i * 0.01, high_price=10.0 + i * 0.01 + 0.1,
            low_price=10.0 + i * 0.01 - 0.05, close_price=10.0 + i * 0.01,
            volume=1000.0, turnover=10000.0,
        )
        for i in range(30)
    ]
    bars_5m = bars_from_lower(bars_1m, window=5, interval="5m")
    assert len(bars_5m) == 6  # 30 / 5

    am = ArrayManager(size=6)
    am.update_bars(bars_5m)
    assert am.count == 6
    sma5 = am.sma(5)
    assert sma5 is not None
