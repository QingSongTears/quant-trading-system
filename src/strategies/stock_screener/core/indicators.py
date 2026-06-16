"""
统一指标计算模块 — 所有技术指标的单次预计算
从 backtest_v2/v3 中提取，统一接口，避免重复计算

用法:
    from core.indicators import precompute_indicators
    kline = precompute_indicators(kline)
    # 此后 kline 包含所有 _d 后缀的指标列
"""

import pandas as pd
import numpy as np
from typing import Optional


# ============================================================
# 原子计算函数（无状态，可单独测试）
# ============================================================

def rolling_r2(arr: np.ndarray) -> float:
    """滚动窗口R² — 对数价格线性拟合趋势强度"""
    if len(arr) < 30:
        return np.nan
    y = np.log(arr + 1e-10)
    x = np.arange(len(arr))
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = np.sum((y - pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    return max(0.0, min(1.0, float(1 - ss_res / (ss_tot + 1e-10))))


def rolling_max_dd(arr: np.ndarray) -> float:
    """滚动窗口最大回撤 %"""
    if len(arr) < 20:
        return np.nan
    peak = np.maximum.accumulate(arr)
    return float(np.min((arr - peak) / peak * 100))


def rolling_bb_position(arr: np.ndarray, period: int = 20, std_dev: float = 2.0) -> float:
    """滚动窗口布林带位置 (0=下轨, 0.5=中轨, 1=上轨)"""
    if len(arr) < period:
        return np.nan
    mid = np.mean(arr[-period:])
    std = np.std(arr[-period:])
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    if upper == lower:
        return 0.5
    return float((arr[-1] - lower) / (upper - lower))


def calc_rsi_series(series: pd.Series, period: int) -> pd.Series:
    """计算 RSI 序列（支持 groupby transform）"""
    if len(series) < period + 1:
        return pd.Series([np.nan] * len(series), index=series.index)
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_g = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_l = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_g / (avg_l + 1e-10)
    return 100.0 - 100.0 / (1.0 + rs)


def calc_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """计算 MACD，返回 (macd_line, signal_line, histogram)"""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ============================================================
# 统一预计算入口
# ============================================================

def precompute_indicators(
    kline: pd.DataFrame,
    with_v2: bool = True,
    with_v3: bool = True,
) -> pd.DataFrame:
    """
    一次性预计算所有技术指标并附加到 DataFrame。

    参数:
        kline: 日K线 DataFrame，需含 code/date/open/high/low/close/volume
        with_v2: 是否计算 v2 趋势策略指标
        with_v3: 是否计算 v3 超卖反转指标

    返回:
        带 _d 后缀指标列的 DataFrame（原地排序 + 重置索引）
    """
    df = kline.sort_values(["code", "date"]).reset_index(drop=True)

    print("  [指标] 计算均线 (EMA20/EMA60/MA20/MA60)...")
    # EMA
    df["ema20_d"] = df.groupby("code")["close"].transform(
        lambda x: x.ewm(span=20, adjust=False).mean())
    df["ema60_d"] = df.groupby("code")["close"].transform(
        lambda x: x.ewm(span=60, adjust=False).mean())
    # MA
    df["ma20_d"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(20, min_periods=1).mean())
    df["ma60_d"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(60, min_periods=1).mean())

    print("  [指标] 计算RSI (14/6)...")
    df["rsi_14_d"] = df.groupby("code")["close"].transform(lambda x: calc_rsi_series(x, 14))
    df["rsi_6_d"] = df.groupby("code")["close"].transform(lambda x: calc_rsi_series(x, 6))
    df["prev_rsi6_d"] = df.groupby("code")["rsi_6_d"].shift(1)

    print("  [指标] 计算布林带位置 (20,2)...")
    df["bb_pos_d"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(20, min_periods=10).apply(rolling_bb_position, raw=True))

    print("  [指标] 计算趋势指标 (R²/60日涨跌/回撤)...")
    df["r2_60d"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(60, min_periods=40).apply(rolling_r2, raw=True))
    df["ret_60d_pct"] = df.groupby("code")["close"].transform(
        lambda x: x.pct_change(60) * 100)
    df["max_dd_60d"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(60, min_periods=40).apply(rolling_max_dd, raw=True))

    print("  [指标] 计算量比/20日高点/前日收盘...")
    df["vol_20ma"] = df.groupby("code")["volume"].transform(
        lambda x: x.rolling(20, min_periods=1).mean())
    df["vol_ratio_d"] = df["volume"] / (df["vol_20ma"] + 1e-10)

    df["high_20d"] = df.groupby("code")["high"].transform(
        lambda x: x.rolling(20, min_periods=1).max())
    df["prev_close_d"] = df.groupby("code")["close"].shift(1)

    # v2 额外需要的指标（已在上方包含，这里保留兼容列名）
    # v3 额外需要的指标也已包含

    return df


# ============================================================
# 便捷单值计算（用于实时/增量场景）
# ============================================================

def calc_single_rsi(closes: pd.Series, period: int = 14) -> float:
    """从收盘价序列计算最新 RSI 值"""
    if len(closes) < period + 1:
        return np.nan
    series = calc_rsi_series(closes, period)
    return float(series.iloc[-1])


def calc_single_bb_position(closes: pd.Series, period: int = 20, std_dev: float = 2.0) -> float:
    """从收盘价序列计算最新布林带位置"""
    return rolling_bb_position(closes.values[-period:], period, std_dev)


def calc_single_max_dd(closes: pd.Series, window: int = 60) -> float:
    """从收盘价序列计算最新最大回撤"""
    return rolling_max_dd(closes.values[-window:])


def calc_single_r2(closes: pd.Series, window: int = 60) -> float:
    """从收盘价序列计算最新R²"""
    return rolling_r2(closes.values[-window:])
