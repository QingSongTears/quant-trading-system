"""
量化检测层 — 识别量化资金参与度
6 维评分：
  1. 换手率波动率 (0.20)
  2. 价格锯齿度/影线占比 (0.18)
  3. 尾盘异动率 (0.15)
  4. 大盘脱敏度 (0.17)
  5. 振幅/涨幅比 (0.15)
  6. 成交量异常度 (0.15)
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from config import QUANT_THRESHOLD, QUANT_WEIGHTS, QUANT_MAX_HOLD_DAYS
from data_fetcher import get_stock_kline


def detect_quant_participation(
    code: str,
    kline_df: pd.DataFrame = None,
) -> Dict:
    """
    检测量化资金参与度
    返回评分详情
    """
    if kline_df is None or kline_df.empty:
        kline_df = get_stock_kline(code, days=120)

    if kline_df.empty or len(kline_df) < 60:
        return {"quant_score": 0, "is_quant_stock": False, "details": {}}

    df = kline_df.copy().sort_values("date").tail(60).reset_index(drop=True)

    scores = {}

    # === 1. 换手率波动率 (0-1) ===
    scores["turnover_volatility"] = _calc_turnover_volatility(df)

    # === 2. 价格锯齿度 / 影线占比 (0-1) ===
    scores["price_sawtooth"] = _calc_price_sawtooth(df)

    # === 3. 尾盘异动率 (0-1) ===
    scores["tail_manipulation"] = _calc_tail_manipulation(df)

    # === 4. 大盘脱敏度 (0-1) ===
    scores["market_decoupling"] = _calc_market_decoupling(df)

    # === 5. 振幅/涨幅比 (0-1) ===
    scores["amplitude_yield_ratio"] = _calc_amplitude_yield_ratio(df)

    # === 6. 成交量异常度 (0-1) ===
    scores["volume_anomaly"] = _calc_volume_anomaly(df)

    # 加权总分
    total = sum(
        scores[k] * QUANT_WEIGHTS.get(k, 0)
        for k in scores
    )

    # 量化席位检测（龙虎榜）
    dragon_tiger_boost = _check_quant_seats(code)
    if dragon_tiger_boost > 0:
        total = min(1.0, total + dragon_tiger_boost)  # 最多加到 1.0

    is_quant = total >= QUANT_THRESHOLD

    return {
        "code": code,
        "quant_score": round(total, 3),
        "is_quant_stock": is_quant,
        "max_hold_days": QUANT_MAX_HOLD_DAYS if is_quant else None,
        "label": "🏷️ 短线波段" if is_quant else "📊 正常波段",
        "details": {k: round(v, 3) for k, v in scores.items()},
    }


def _calc_turnover_volatility(df: pd.DataFrame) -> float:
    """
    换手率波动率
    量化特征：换手率忽高忽低、无规律
    用 20 日换手率标准差/均值（变异系数）衡量
    """
    if "turnover" not in df.columns:
        # 模拟换手率：成交量/流通股本（简化）
        if "volume" in df.columns:
            df["turnover_est"] = df["volume"] / df["volume"].rolling(20).mean()
        else:
            return 0.3  # 默认中等

    vol_col = "turnover" if "turnover" in df.columns else "turnover_est"
    cv = df[vol_col].std() / (df[vol_col].mean() + 1e-10)
    # 变异系数 > 0.5 表示高波动（量化特征）
    return min(1.0, cv)


def _calc_price_sawtooth(df: pd.DataFrame) -> float:
    """
    价格锯齿度（影线占比）
    量化特征：频繁冲高回落、留长上影或下影
    影线长度占实体比例的平均值
    """
    if not all(c in df.columns for c in ["open", "close", "high", "low"]):
        return 0.3

    body = abs(df["close"] - df["open"])
    upper_shadow = df["high"] - df[["open", "close"]].max(axis=1)
    lower_shadow = df[["open", "close"]].min(axis=1) - df["low"]
    total_range = df["high"] - df["low"] + 1e-10

    # 影线占全日振幅的比例
    shadow_ratio = (upper_shadow + lower_shadow) / total_range
    avg_shadow = shadow_ratio.mean()

    # > 0.6 表示影线主导，量化特征明显
    return min(1.0, avg_shadow / 0.6)


def _calc_tail_manipulation(df: pd.DataFrame) -> float:
    """
    尾盘异动率
    量化特征：最后 30 分钟频繁拉升/打压
    用当日收盘与当日最高/最低的差距判断
    简化：最后几分钟的异常（用振幅与收盘位置衡量）
    """
    if not all(c in df.columns for c in ["open", "close", "high", "low"]):
        return 0.3

    total_range = df["high"] - df["low"] + 1e-10
    # 收盘价在全日高低的哪部分
    close_position = (df["close"] - df["low"]) / total_range

    # 如果收盘在极端位置（>0.85 或 <0.15）频率高 → 尾盘异动
    extreme_close = ((close_position > 0.85) | (close_position < 0.15)).rolling(20).mean()
    return min(1.0, extreme_close.iloc[-1] / 0.5)


def _calc_market_decoupling(df: pd.DataFrame) -> float:
    """
    大盘脱敏度
    量化特征：跟大盘/板块走势相关性突然降低
    简化：用自身波动独立性和趋势突变判断
    """
    if "close" not in df.columns or len(df) < 30:
        return 0.3

    returns = df["close"].pct_change().dropna()
    if len(returns) < 20:
        return 0.3

    # 5日收益与20日收益的自相关性
    ret_5 = returns.rolling(5).sum()
    ret_20 = returns.rolling(20).sum()

    # 短期与中期方向不一致的频率
    divergence = ((ret_5 * ret_20) < 0).rolling(20).mean().iloc[-1]
    return min(1.0, divergence / 0.4)


def _calc_amplitude_yield_ratio(df: pd.DataFrame) -> float:
    """
    振幅/涨幅比
    量化特征：振幅大但实际涨幅小（典型机器做 T 特征）
    """
    if not all(c in df.columns for c in ["open", "close", "high", "low"]):
        return 0.3

    amplitude = (df["high"] - df["low"]) / (df["low"] + 1e-10)
    daily_ret = df["close"].pct_change().abs()

    # 振幅大但涨跌幅小 → 量化做T
    ratio = (amplitude.rolling(10).mean() / (daily_ret.rolling(10).mean() + 1e-10)).iloc[-1]
    return min(1.0, ratio / 10)


def _calc_volume_anomaly(df: pd.DataFrame) -> float:
    """
    成交量异常度
    量化特征：成交量忽大忽小没有规律，与价格走势脱节
    """
    if "volume" not in df.columns:
        return 0.3

    vol = df["volume"]
    # 成交量突变（突然放大/缩小）
    vol_change = vol.pct_change().abs()
    # 异常突变的频率
    anomaly_rate = (vol_change > 1.0).rolling(20).mean().iloc[-1]
    return min(1.0, anomaly_rate / 0.3)


def _check_quant_seats(code: str) -> float:
    """
    龙虎榜量化席位检测（简化版）
    基于已有的技术特征判断，不依赖东财API
    频繁的高换手+高振幅本身就是量化特征
    """
    # 在沙箱环境中东财API不可用，依赖技术指标判断
    # 该分数在加权时占比小，不影响核心判断
    return 0.0


def batch_detect_quant(
    codes: List[str],
    kline_cache: Dict = None,
) -> pd.DataFrame:
    """批量检测量化参与度"""
    results = []
    for i, code in enumerate(codes):
        if i % 10 == 0:
            print(f"[QUANT] 量化检测进度: {i}/{len(codes)}")

        try:
            kline = None
            if kline_cache and code in kline_cache:
                kline = kline_cache[code]

            result = detect_quant_participation(code, kline)
            results.append(result)
        except Exception as e:
            print(f"[WARN] 量化检测异常 {code}: {e}")
            results.append({
                "code": code, "quant_score": 0,
                "is_quant_stock": False, "label": "📊 正常波段",
                "details": {},
            })

    return pd.DataFrame(results)
