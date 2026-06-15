"""
买点评分层 — 回踩权重评分
逻辑：
  1. 确认金叉后出现回调，价格进入 MA20~MA60 区间
  2. 获取该股历史 N 次金叉后的回踩深度分布
  3. 根据当前回踩在历史分布中的位置打分
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple
from ..config import (
    PULLBACK_MA_FAST, PULLBACK_MA_SLOW,
    HISTORY_LOOKBACK_TIMES, PULLBACK_SCORE_CONFIG,
)
from .data_fetcher import get_stock_kline


def evaluate_pullback(code: str, signal: Dict, kline_df: pd.DataFrame = None) -> Dict:
    """
    评估回踩买点质量
    返回买点评分和详细信息
    """
    if kline_df is None or kline_df.empty:
        kline_df = get_stock_kline(code, days=300)

    if kline_df.empty or len(kline_df) < 120:
        return _no_buy_point(code)

    # 前置检查：信号位置必须有效
    position = signal.get("current_position", "")
    if position == "破位下行":
        return {
            "code": code,
            "buy_ready": False,
            "score": 0,
            "status": "broken",
            "desc": "已破位下行 — 跌破EMA60，不满足买点条件",
            "current_close": 0, "current_ma20": 0, "current_ma60": 0,
            "pullback_depth_pct": 0, "volume_shrinking": False, "price_stabilizing": False,
            "hist_avg_pullback_pct": None, "hist_max_pullback_pct": None,
        }

    df = kline_df.copy().sort_values("date").reset_index(drop=True)

    # 计算均线
    df["ma20"] = df["close"].rolling(PULLBACK_MA_FAST).mean()
    df["ma60"] = df["close"].rolling(PULLBACK_MA_SLOW).mean()

    # EMA 用于金叉检测
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema60"] = df["close"].ewm(span=60, adjust=False).mean()

    latest = df.iloc[-1]
    close = latest["close"]
    ma20 = latest["ma20"]
    ma60 = latest["ma60"]

    # 判断是否在回踩区间
    if pd.isna(ma20) or pd.isna(ma60):
        return _no_buy_point(code)

    in_pullback_zone = ma60 <= close <= ma20

    if not in_pullback_zone:
        # 不在回踩区间
        if close > ma20:
            return {
                "code": code,
                "buy_ready": False,
                "score": PULLBACK_SCORE_CONFIG["no_pullback"]["score"],
                "status": "waiting",
                "desc": "未出现回踩 — 价格在均线上方，等待回调",
                "current_ma20": round(ma20, 2),
                "current_ma60": round(ma60, 2),
                "current_close": round(close, 2),
                "distance_to_ma20_pct": round((close - ma20) / ma20 * 100, 2),
            }

    # 在回踩区间内，评估质量
    # 1. 计算回踩深度（距离 MA20 的距离）
    pullback_depth = (ma20 - close) / ma20 * 100

    # 2. 获取历史金叉后回踩行为
    historical_pullbacks = _get_historical_pullbacks(df)
    hist_avg, hist_std, hist_max = _calc_pullback_stats(historical_pullbacks)

    # 3. 判断回踩质量
    # 检查最近成交量是否在缩量企稳
    vol_recent = df["volume"].tail(5)
    vol_prev = df["volume"].tail(10).head(5)
    volume_shrinking = vol_recent.mean() < vol_prev.mean() * 0.9  # 缩量

    # 反弹确认（最近 2-3 天价格是否企稳）
    recent_lows = df["low"].tail(3)
    price_stabilizing = recent_lows.min() >= recent_lows.iloc[0] * 0.98

    # 分类
    if historic_avg_depth := hist_avg:
        if pullback_depth <= historic_avg_depth * 0.7:
            category = "shallow_pullback_decay"  # 浅回踩
        elif pullback_depth <= historic_avg_depth * 1.3:
            category = "mid_pullback_recovery"  # 中等回踩
        else:
            category = "deep_pullback_no_recovery"  # 深回踩
    else:
        # 无历史数据，用默认判断
        if pullback_depth < 3:
            category = "shallow_pullback_decay"
        elif pullback_depth < 8:
            category = "mid_pullback_recovery"
        else:
            category = "deep_pullback_no_recovery"

    base_score = PULLBACK_SCORE_CONFIG[category]["score"]

    # 加分项
    bonus = 0
    if volume_shrinking:
        bonus += 1  # 缩量回调加分
    if price_stabilizing:
        bonus += 1  # 企稳加分

    final_score = min(5, base_score + bonus)

    return {
        "code": code,
        "buy_ready": final_score >= 2,
        "score": final_score,
        "status": "pullback" if final_score >= 2 else "weak",
        "desc": f"{PULLBACK_SCORE_CONFIG[category]['desc']}" + (
            " + 缩量" if volume_shrinking else ""
        ) + (
            " + 企稳" if price_stabilizing else ""
        ),
        "pullback_depth_pct": round(pullback_depth, 2),
        "current_close": round(close, 2),
        "current_ma20": round(ma20, 2),
        "current_ma60": round(ma60, 2),
        "hist_avg_pullback_pct": round(hist_avg, 2) if hist_avg else None,
        "hist_max_pullback_pct": round(hist_max, 2) if hist_max else None,
        "volume_shrinking": volume_shrinking,
        "price_stabilizing": price_stabilizing,
    }


def _get_historical_pullbacks(df: pd.DataFrame) -> pd.DataFrame:
    """
    获取历史金叉后的回踩数据
    每次金叉后，记录价格从峰值回落到 MA20-MA60 的深度
    """
    if "ema20" not in df.columns or "ema60" not in df.columns:
        return pd.DataFrame()

    # 找所有金叉点
    cross_up = (df["ema20"] > df["ema60"]) & (df["ema20"].shift(1) <= df["ema60"].shift(1))
    cross_indices = df[cross_up].index.tolist()

    if len(cross_indices) <= 1:
        return pd.DataFrame()

    pullbacks = []
    for idx in cross_indices[:-1]:  # 不包括最近一次（可能正在进行中）
        # 找金叉后到下次金叉之间的数据
        next_idx = cross_indices[cross_indices.index(idx) + 1] if idx < cross_indices[-1] else len(df)
        segment = df.iloc[idx:next_idx]

        if len(segment) < 10:
            continue

        # 找峰值（金叉后的最高点）
        peak_idx = segment["close"].idxmax()
        peak = segment.loc[peak_idx]

        if peak_idx >= len(df) - 1:
            continue

        # 峰值后的回调
        after_peak = df.iloc[peak_idx:next_idx]
        if len(after_peak) < 5:
            continue

        # 找最低点
        trough_idx = after_peak["close"].idxmin()
        trough = df.loc[trough_idx]

        if pd.notna(trough.get("ma20")):
            ma20_at_trough = trough["ma20"]
            pullback_pct = (peak["close"] - trough["close"]) / peak["close"] * 100
            pullbacks.append({
                "cross_date": df.loc[idx, "date"],
                "peak_date": peak["date"],
                "trough_date": trough["date"],
                "pullback_pct": pullback_pct,
                "days_peak_to_trough": (trough["date"] - peak["date"]).days if hasattr(trough["date"], 'days') else 5,
            })

    return pd.DataFrame(pullbacks)


def _calc_pullback_stats(hist_df: pd.DataFrame) -> Tuple[float, float, float]:
    """计算历史回踩统计"""
    if hist_df.empty or "pullback_pct" not in hist_df.columns:
        return 0, 0, 0

    pullbacks = hist_df["pullback_pct"].dropna()
    if len(pullbacks) == 0:
        return 0, 0, 0

    return (
        round(pullbacks.mean(), 2),
        round(pullbacks.std(), 2) if len(pullbacks) > 1 else 0,
        round(pullbacks.max(), 2),
    )


def _no_buy_point(code: str) -> Dict:
    return {
        "code": code,
        "buy_ready": False,
        "score": 0,
        "status": "no_data",
        "desc": "数据不足或不在回踩区间",
        "pullback_depth_pct": 0,
        "current_close": 0,
        "current_ma20": 0,
        "current_ma60": 0,
    }


def batch_evaluate_buy_points(
    signal_df: pd.DataFrame,
    kline_cache: Dict = None,
) -> pd.DataFrame:
    """批量评估买点"""
    results = []
    for _, row in signal_df.iterrows():
        code = row["code"]
        signal = row.to_dict()

        try:
            kline = None
            if kline_cache and code in kline_cache:
                kline = kline_cache[code]

            bp = evaluate_pullback(code, signal, kline)
            bp.update({k: v for k, v in signal.items() if k != "code"})
            results.append(bp)
        except Exception as e:
            print(f"[WARN] 买点评估异常 {code}: {e}")

    return pd.DataFrame(results)
