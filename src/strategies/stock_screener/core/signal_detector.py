"""
信号检测层 — EMA 金叉 + 多头趋势确认
核心逻辑：
  1. 检测 EMA20 上穿 EMA60
  2. 确认多头排列（EMA20 > EMA60，价格站上双均线）
  3. 验证金叉放量（金叉日成交量 ≥ 20日均量的 1.2 倍）
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from ..config import (
    EMA_FAST, EMA_SLOW, REQUIRE_PRICE_ABOVE_EMA,
    VOLUME_RATIO_THRESHOLD, GOLDEN_CROSS_CONFIRM_BARS,
    MAX_SIGNAL_AGE_DAYS,
)
from .data_fetcher import get_stock_kline


def detect_golden_cross_signal(
    code: str,
    kline_df: pd.DataFrame = None,
) -> Optional[Dict]:
    """
    检测单只股票的 EMA 金叉信号
    返回信号详情字典，若无信号返回 None
    """
    if kline_df is None or kline_df.empty:
        kline_df = get_stock_kline(code, days=300)

    if kline_df.empty or len(kline_df) < EMA_SLOW + 10:
        return None

    df = kline_df.copy()
    df = df.sort_values("date").reset_index(drop=True)

    # 计算 EMA
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()

    # 检测金叉：EMA20 从下方上穿 EMA60
    df["cross_up"] = (df["ema_fast"] > df["ema_slow"]) & (df["ema_fast"].shift(1) <= df["ema_slow"].shift(1))

    # 多头排列确认
    df["bullish_alignment"] = (df["ema_fast"] > df["ema_slow"]) & (df["close"] > df["ema_fast"])

    # 放量确认
    df["vol_confirm"] = df["volume"] >= df["vol_ma20"] * VOLUME_RATIO_THRESHOLD

    # 找最近的金叉
    cross_days = df[df["cross_up"]]
    if cross_days.empty:
        return None

    # 取最近一次金叉
    last_cross_idx = cross_days.index[-1]

    # 确认金叉后是否继续多头
    confirm_start = last_cross_idx + 1
    confirm_end = min(last_cross_idx + GOLDEN_CROSS_CONFIRM_BARS + 1, len(df))

    # 金叉日后是否持续多头
    post_cross = df.iloc[confirm_start:confirm_end]
    if REQUIRE_PRICE_ABOVE_EMA:
        bullish_after = post_cross["bullish_alignment"].all() if len(post_cross) > 0 else False
    else:
        bullish_after = (post_cross["ema_fast"] > post_cross["ema_slow"]).all() if len(post_cross) > 0 else False

    # 放量确认
    cross_row = df.iloc[last_cross_idx]
    vol_confirmed = cross_row["vol_confirm"]

    # 计算金叉距今多少天
    latest = df.iloc[-1]
    cross_date = cross_row["date"]
    days_since_cross = (latest["date"] - cross_date).days if hasattr(cross_date, 'days') else (
        (pd.Timestamp(latest["date"]) - pd.Timestamp(cross_date)).days
    )

    # 综合判断
    if not bullish_after:
        return None

    # 时效性检查：金叉不能太久远（超过30天）
    if days_since_cross > MAX_SIGNAL_AGE_DAYS:
        return None

    # 构建信号详情
    cross_close = cross_row["close"]
    latest_close = latest["close"]
    gain_since_cross = (latest_close - cross_close) / cross_close * 100

    # 判断当前位置
    current_position = _classify_position(latest, df)

    return {
        "code": code,
        "cross_date": str(cross_date)[:10],
        "cross_close": round(cross_close, 2),
        "days_since_cross": days_since_cross,
        "gain_since_cross": round(gain_since_cross, 2),
        "vol_confirmed": bool(vol_confirmed),
        "latest_close": round(latest_close, 2),
        "ema_fast": round(latest["ema_fast"], 2),
        "ema_slow": round(latest["ema_slow"], 2),
        "current_position": current_position,
        "signal_score": _compute_signal_score(cross_row, latest, vol_confirmed, current_position),
    }


def _classify_position(latest: pd.Series, df: pd.DataFrame) -> str:
    """分类当前价格位置"""
    close = latest["close"]
    ema_f = latest["ema_fast"]
    ema_s = latest["ema_slow"]

    if close > ema_f > ema_s:
        return "强势多头"  # 价格在双均线之上
    elif ema_f > close > ema_s:
        return "回踩支撑"  # 价格回落到 MA20-MA60 之间 ⭐ 买点区域
    elif close < ema_s:
        return "破位下行"  # 已跌破 EMA60
    else:
        return "震荡整理"


def _compute_signal_score(
    cross_row: pd.Series,
    latest: pd.Series,
    vol_confirmed: bool,
    position: str,
) -> int:
    """金叉信号综合打分（满分 10）"""
    score = 0

    # 金叉基础分
    score += 3

    # 放量确认 +2
    if vol_confirmed:
        score += 2

    # 位置分
    pos_scores = {
        "强势多头": 2,
        "回踩支撑": 4,  # 回踩是最佳买点
        "震荡整理": 0,
        "破位下行": -3,
    }
    score += pos_scores.get(position, 0)

    # 金叉后涨幅：涨太多追高风险大
    gain = (latest["close"] - cross_row["close"]) / cross_row["close"] * 100
    if 0 <= gain <= 5:
        score += 1  # 微涨，还能追
    elif gain > 15:
        score -= 2  # 涨多了

    return max(0, min(10, score))


def batch_detect_signals(codes: List[str], kline_cache: Dict = None) -> pd.DataFrame:
    """
    批量检测金叉信号
    返回有信号的股票 DataFrame
    """
    results = []
    total = len(codes)

    for i, code in enumerate(codes):
        if i % 20 == 0:
            print(f"[SIGNAL] 检测进度: {i}/{total}")

        try:
            kline = None
            if kline_cache and code in kline_cache:
                kline = kline_cache[code]

            signal = detect_golden_cross_signal(code, kline)
            if signal:
                results.append(signal)
        except Exception as e:
            print(f"[WARN] 信号检测异常 {code}: {e}")
            continue

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values("signal_score", ascending=False)

    print(f"[SIGNAL] 检测完成: {len(results)}/{total} 有金叉信号")
    return df
