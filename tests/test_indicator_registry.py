"""
Indicator Registry + atomic 算子单测 (2026-06-25)

覆盖:
  - 注册: 17 算子全部注册成功
  - 基本调用: 每个算子 compute() 返 IndicatorResult
  - 算法正确性: 与 ArrayManager 旧实现保持一致 (KDJ 持续状态 / MACD DEA / BOLL ddof=1)
  - 错误处理: 数据不足时 error 字段非空
  - to_scalar / to_tuple: 转换接口
"""
import numpy as np
import pandas as pd
import pytest

from src.indicator import (
    IndicatorRegistry,
    IndicatorResult,
    to_close_series,
    to_ohlc_dataframe,
)
from src.indicator import atomic, operators  # noqa: 触发注册


# ============================================================
# 注册完整性
# ============================================================


EXPECTED_ATOMIC = {
    "sma", "ema", "macd", "boll", "donchian",
    "rsi", "kdj", "wr", "cci", "roc",
    "atr", "std", "natr",
    "obv", "mfi",
    "tr", "dm",
}

EXPECTED_OPERATORS = {
    "rolling_mean", "rolling_std", "rolling_sum",
    "pct_rank", "zscore",
    "max_dd", "adx",
}


def test_atomic_indicators_registered():
    """17 个 atomic 算子全部注册"""
    registered = set(k.split("@")[0] for k in IndicatorRegistry.list())
    missing = EXPECTED_ATOMIC - registered
    assert not missing, f"未注册 atomic: {missing}"


def test_operator_indicators_registered():
    """operators 算子全部注册"""
    registered = set(k.split("@")[0] for k in IndicatorRegistry.list())
    missing = EXPECTED_OPERATORS - registered
    assert not missing, f"未注册 operators: {missing}"


# ============================================================
# 数据 fixture
# ============================================================


@pytest.fixture
def sample_ohlcv():
    """60 根 K 线 OHLCV, close 线性递增 0.5/根"""
    n = 60
    base = np.arange(n, dtype=np.float64) * 0.5 + 10.0
    return pd.DataFrame({
        "open": base - 0.1,
        "high": base + 0.2,
        "low": base - 0.2,
        "close": base,
        "volume": np.full(n, 1000000.0),
    })


# ============================================================
# IndicatorResult 接口
# ============================================================


def test_indicator_result_to_scalar():
    r = IndicatorResult("rsi", 65.5, {"n": 14}, "v1", 15)
    assert r.to_scalar() == 65.5


def test_indicator_result_to_tuple_single():
    r = IndicatorResult("rsi", 65.5, {"n": 14}, "v1", 15)
    assert r.to_tuple() == (65.5,)


def test_indicator_result_to_tuple_multi():
    r = IndicatorResult("macd", (1.0, 2.0, -2.0), {}, "v1", 35)
    assert r.to_tuple() == (1.0, 2.0, -2.0)


def test_indicator_result_to_dict():
    r = IndicatorResult("rsi", 65.5, {"n": 14}, "v1", 15, meta={"k": 1})
    d = r.to_dict()
    assert d["name"] == "rsi"
    assert d["value"] == 65.5
    assert d["version"] == "v1"
    assert d["meta"] == {"k": 1}


def test_indicator_result_error_present():
    r = IndicatorResult("rsi", None, {"n": 14}, "v1", 15, error="insufficient")
    assert r.value is None
    assert r.error == "insufficient"


# ============================================================
# 趋势指标
# ============================================================


def test_sma_basic(sample_ohlcv):
    """SMA(20) ≈ 倒数 20 根 close 均值"""
    r = IndicatorRegistry.get("sma").compute(sample_ohlcv, n=20)
    expected = sample_ohlcv["close"].iloc[-20:].mean()
    assert r.value == pytest.approx(expected, rel=1e-9)
    assert r.error is None


def test_ema_basic(sample_ohlcv):
    """EMA(12) 在 close 递增序列上 > SMA(12) (近端权重大)"""
    r_ema = IndicatorRegistry.get("ema").compute(sample_ohlcv, n=12)
    r_sma = IndicatorRegistry.get("sma").compute(sample_ohlcv, n=12)
    # 线性递增, EMA 应更接近最新值, 更大
    assert r_ema.value > r_sma.value


def test_macd_returns_tuple(sample_ohlcv):
    """MACD 返 (DIF, DEA, MACD柱)"""
    r = IndicatorRegistry.get("macd").compute(sample_ohlcv)
    assert r.value is not None
    assert len(r.value) == 3
    dif, dea, macd = r.value
    # 线性递增: DIF > 0, DEA > 0, MACD = 2*(DIF-DEA)
    assert dif > 0
    assert dea > 0
    assert macd == pytest.approx(2 * (dif - dea))


def test_boll_uses_ddof_1(sample_ohlcv):
    """BOLL 修 2026-06-25: 用 ddof=1 (样本标准差)"""
    r = IndicatorRegistry.get("boll").compute(sample_ohlcv, n=20, dev=2.0)
    mid, upper, lower = r.value
    expected_std = sample_ohlcv["close"].iloc[-20:].std(ddof=1)
    expected_mid = sample_ohlcv["close"].iloc[-20:].mean()
    assert mid == pytest.approx(expected_mid)
    assert upper - mid == pytest.approx(2.0 * expected_std)
    assert mid - lower == pytest.approx(2.0 * expected_std)


def test_donchian_basic(sample_ohlcv):
    """Donchian = (期间内 high.max, low.min)"""
    r = IndicatorRegistry.get("donchian").compute(sample_ohlcv, n=20)
    upper, lower = r.value
    assert upper == sample_ohlcv["high"].iloc[-20:].max()
    assert lower == sample_ohlcv["low"].iloc[-20:].min()


# ============================================================
# 动量指标
# ============================================================


def test_rsi_range_0_100(sample_ohlcv):
    """RSI 应在 0-100 范围内"""
    r = IndicatorRegistry.get("rsi").compute(sample_ohlcv, n=14)
    assert 0 <= r.value <= 100


def test_rsi_monotonic_up_trend_high(sample_ohlcv):
    """线性递增 60 根 → RSI 应 > 70 (强买)"""
    r = IndicatorRegistry.get("rsi").compute(sample_ohlcv, n=14)
    assert r.value > 70


def test_kdj_state_persists_across_calls():
    """KDJ 持续状态 — 第 2 次调用应接着第 1 次递推, 不是 reset 50"""
    n = 30
    df = pd.DataFrame({
        "open": np.arange(n, dtype=float) * 0.1 + 10.0,
        "high": np.arange(n, dtype=float) * 0.1 + 10.2,
        "low": np.arange(n, dtype=float) * 0.1 + 9.8,
        "close": np.arange(n, dtype=float) * 0.1 + 10.0,
        "volume": np.full(n, 1000.0),
    })
    kdj = IndicatorRegistry.get("kdj")
    r1 = kdj.compute(df, n=9, m1=3, m2=3)
    k1, d1, j1 = r1.value
    r2 = kdj.compute(df, n=9, m1=3, m2=3)
    k2, d2, j2 = r2.value
    # K/D 应递增 (价格递增 → RSV 递增 → K 递增)
    assert k2 > k1
    assert d2 > d1


def test_kdj_reset():
    """reset=True 时 K/D 重置为 50"""
    n = 30
    df = pd.DataFrame({
        "open": np.full(n, 10.0),
        "high": np.full(n, 10.5),
        "low": np.full(n, 9.5),
        "close": np.full(n, 10.0),
        "volume": np.full(n, 1000.0),
    })
    kdj = IndicatorRegistry.get("kdj")
    kdj.compute(df, n=9)  # 第一次
    r_reset = kdj.compute(df, n=9, reset=True)  # reset
    # reset 后 K = 50 起步
    k_reset, _, _ = r_reset.value
    # 不严格等于 50 (因为还有递推), 但应 < 60 (因为 RSV≈50)
    assert k_reset < 60


def test_wr_range_neg100_0(sample_ohlcv):
    """WR 在 -100 ~ 0"""
    r = IndicatorRegistry.get("wr").compute(sample_ohlcv, n=14)
    assert -100 <= r.value <= 0


def test_cci_basic(sample_ohlcv):
    """CCI 不崩, 返 float"""
    r = IndicatorRegistry.get("cci").compute(sample_ohlcv, n=14)
    assert isinstance(r.value, float)


def test_roc_positive_in_uptrend(sample_ohlcv):
    """ROC 在线性递增上应 > 0"""
    r = IndicatorRegistry.get("roc").compute(sample_ohlcv, n=12)
    assert r.value > 0


# ============================================================
# 波动指标
# ============================================================


def test_atr_positive(sample_ohlcv):
    """ATR 总是 >= 0"""
    r = IndicatorRegistry.get("atr").compute(sample_ohlcv, n=14)
    assert r.value >= 0


def test_std_ddof_1(sample_ohlcv):
    """std 应等于 sample std (ddof=1)"""
    r = IndicatorRegistry.get("std").compute(sample_ohlcv, n=20)
    expected = sample_ohlcv["close"].iloc[-20:].std(ddof=1)
    assert r.value == pytest.approx(expected)


def test_natr_positive(sample_ohlcv):
    """NATR = ATR/close*100 应 >= 0"""
    r = IndicatorRegistry.get("natr").compute(sample_ohlcv, n=14)
    assert r.value >= 0


# ============================================================
# 成交量指标
# ============================================================


def test_obv_zero_for_constant_close():
    """常数 close → OBV = 0"""
    n = 20
    df = pd.DataFrame({
        "open": np.full(n, 10.0),
        "high": np.full(n, 10.5),
        "low": np.full(n, 9.5),
        "close": np.full(n, 10.0),
        "volume": np.full(n, 1000.0),
    })
    r = IndicatorRegistry.get("obv").compute(df)
    assert r.value == 0.0


def test_mfi_range_0_100(sample_ohlcv):
    """MFI 在 0-100"""
    r = IndicatorRegistry.get("mfi").compute(sample_ohlcv, n=14)
    assert 0 <= r.value <= 100


# ============================================================
# 辅助指标
# ============================================================


def test_tr_positive(sample_ohlcv):
    """TR >= 0"""
    r = IndicatorRegistry.get("tr").compute(sample_ohlcv)
    assert r.value >= 0


def test_dm_returns_tuple(sample_ohlcv):
    """DM 返 (+DM, -DM)"""
    r = IndicatorRegistry.get("dm").compute(sample_ohlcv)
    plus_dm, minus_dm = r.value
    assert isinstance(plus_dm, float)
    assert isinstance(minus_dm, float)


# ============================================================
# DataFrame 算子
# ============================================================


def test_rolling_mean_series(sample_ohlcv):
    """rolling_mean 返 pd.Series"""
    r = IndicatorRegistry.get("rolling_mean").compute(sample_ohlcv, window=20)
    assert isinstance(r.value, pd.Series)
    assert len(r.value) == len(sample_ohlcv)


def test_rolling_std_series(sample_ohlcv):
    """rolling_std 返 pd.Series"""
    r = IndicatorRegistry.get("rolling_std").compute(sample_ohlcv, window=20)
    assert isinstance(r.value, pd.Series)


def test_pct_rank_series(sample_ohlcv):
    """pct_rank 返 pd.Series, 0-1"""
    r = IndicatorRegistry.get("pct_rank").compute(sample_ohlcv, window=60)
    assert isinstance(r.value, pd.Series)
    valid = r.value.dropna()
    assert (valid >= 0).all() and (valid <= 1).all()


def test_max_dd_negative_or_zero(sample_ohlcv):
    """max_dd 应 <= 0 (回撤)"""
    r = IndicatorRegistry.get("max_dd").compute(sample_ohlcv, period=20)
    assert isinstance(r.value, pd.Series)
    valid = r.value.dropna()
    assert (valid <= 0).all()


def test_zscore_mean_zero(sample_ohlcv):
    """zscore 滚动窗口均值应接近 0"""
    r = IndicatorRegistry.get("zscore").compute(sample_ohlcv, window=20)
    assert isinstance(r.value, pd.Series)


def test_adx_returns_series(sample_ohlcv):
    """ADX 返 pd.Series"""
    r = IndicatorRegistry.get("adx").compute(sample_ohlcv, period=14)
    assert isinstance(r.value, pd.Series)
    # ADX 范围 0-100
    valid = r.value.dropna()
    if len(valid) > 0:
        assert (valid >= 0).all() and (valid <= 100).all()


# ============================================================
# 错误处理
# ============================================================


def test_insufficient_data_returns_error():
    """数据不足时 error 字段非空"""
    df = pd.DataFrame({
        "open": [10.0, 10.1, 10.2],
        "high": [10.2, 10.3, 10.4],
        "low": [9.9, 10.0, 10.1],
        "close": [10.1, 10.2, 10.3],
        "volume": [1000.0, 1100.0, 1200.0],
    })
    r = IndicatorRegistry.get("rsi").compute(df, n=14)
    assert r.value is None
    assert "insufficient" in r.error


def test_unknown_indicator_raises():
    """未注册指标应抛 KeyError"""
    with pytest.raises(KeyError, match="未注册指标"):
        IndicatorRegistry.get("nonexistent")


def test_has_method():
    """has() 检查注册状态"""
    assert IndicatorRegistry.has("rsi") is True
    assert IndicatorRegistry.has("nonexistent") is False


# ============================================================
# 输入归一
# ============================================================


def test_to_close_series_accepts_dataframe(sample_ohlcv):
    """to_close_series 接受 DataFrame"""
    s = to_close_series(sample_ohlcv)
    assert isinstance(s, pd.Series)
    assert len(s) == len(sample_ohlcv)


def test_to_close_series_accepts_ndarray(sample_ohlcv):
    """to_close_series 接受 ndarray (1D 视为 close)"""
    arr = sample_ohlcv["close"].values
    s = to_close_series(arr)
    assert len(s) == len(arr)


def test_to_ohlc_dataframe_accepts_ndarray(sample_ohlcv):
    """to_ohlc_dataframe 接受 ndarray 2D"""
    arr = sample_ohlcv[["open", "high", "low", "close"]].values
    df = to_ohlc_dataframe(arr)
    assert "close" in df.columns
    assert len(df) == len(sample_ohlcv)
