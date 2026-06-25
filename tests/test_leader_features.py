"""
LeaderFeatureBuilder 单测 (2026-06-25)

覆盖:
  - build() 单股: 5 维归一化
  - 范围检查: rsi14/kdj/boll_pos 都在 [0, 1]
  - 数据不足: 返默认值
  - build_batch() 多股
  - KDJ 状态: 切换股票时 reset
"""
import numpy as np
import pandas as pd
import pytest

from src.research.features import LeaderFeatureBuilder, TECH_COLS, TECH_DEFAULTS


@pytest.fixture
def sample_bars():
    """60 根递增 K 线"""
    n = 60
    base = np.arange(n, dtype=np.float64) * 0.5 + 10.0
    df = pd.DataFrame({
        "open": base - 0.1,
        "high": base + 0.2,
        "low": base - 0.2,
        "close": base,
        "volume": np.full(n, 1000000.0),
    }, index=pd.date_range("2024-01-01", periods=n, freq="D"))
    return df


def test_tech_cols_constant():
    """5 维列名常量"""
    assert TECH_COLS == ["macd_hist", "rsi14", "kdj_k", "kdj_j", "boll_pos"]
    assert len(TECH_DEFAULTS) == 5


def test_build_returns_5_dims(sample_bars):
    """build() 返 5 维 dict"""
    builder = LeaderFeatureBuilder()
    r = builder.build(sample_bars)
    assert set(r.keys()) == set(TECH_COLS)


def test_build_in_uptrend_high_rsi(sample_bars):
    """线性递增 60 根 → rsi 应 > 0.7 (强买, 归一后)"""
    builder = LeaderFeatureBuilder()
    r = builder.build(sample_bars)
    assert r["rsi14"] > 0.7


def test_build_normalized_to_0_1(sample_bars):
    """rsi14 / kdj_k / kdj_j / boll_pos 都在 [0, 1]"""
    builder = LeaderFeatureBuilder()
    r = builder.build(sample_bars)
    for k in ["rsi14", "kdj_k", "kdj_j", "boll_pos"]:
        assert 0 <= r[k] <= 1, f"{k}={r[k]} 不在 [0,1]"


def test_build_insufficient_data():
    """数据不足 20 根 → 返默认值"""
    df = pd.DataFrame({
        "open": [10.0] * 5, "high": [10.5] * 5, "low": [9.5] * 5,
        "close": [10.0] * 5, "volume": [1000.0] * 5,
    })
    builder = LeaderFeatureBuilder()
    r = builder.build(df)
    assert r == TECH_DEFAULTS


def test_build_empty_returns_defaults():
    """空 DataFrame 返默认值"""
    df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    builder = LeaderFeatureBuilder()
    r = builder.build(df)
    assert r == TECH_DEFAULTS


def test_build_batch_multiple_codes(sample_bars):
    """build_batch 多股"""
    builder = LeaderFeatureBuilder()
    r = builder.build_batch({
        "000001.SZ": sample_bars,
        "000002.SZ": sample_bars.copy(),
    })
    assert "000001.SZ" in r
    assert "000002.SZ" in r
    for code in r:
        assert set(r[code].keys()) == set(TECH_COLS)


def test_kdj_resets_on_stock_change(sample_bars):
    """KDJ 状态: 切换股票应 reset (reset_kdj=True)"""
    builder = LeaderFeatureBuilder()
    r1 = builder.build(sample_bars, reset_kdj=True)  # 第一次
    r2 = builder.build(sample_bars, reset_kdj=True)  # 第二次 (同数据, 应得相同结果)
    # 因为 KDJ 是累积状态, reset 后应回到一致起点
    assert abs(r1["kdj_k"] - r2["kdj_k"]) < 0.01


def test_macd_hist_normalized_by_close(sample_bars):
    """macd_hist 应是 close 的相对值, 数量级合理"""
    builder = LeaderFeatureBuilder()
    r = builder.build(sample_bars)
    # macd_hist / close 应在 ±0.1 范围内 (一般情况)
    assert abs(r["macd_hist"]) < 0.1


def test_boll_pos_in_range(sample_bars):
    """boll_pos 在 [0, 1]"""
    builder = LeaderFeatureBuilder()
    r = builder.build(sample_bars)
    assert 0 <= r["boll_pos"] <= 1
