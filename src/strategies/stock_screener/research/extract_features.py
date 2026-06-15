"""
翻倍股研究 — 阶段2：提取翻倍前的多维特征向量
对每个翻倍案例，提取起涨点前 60 天的技术指标
并与非翻倍股的随机采样做对比
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Tuple

from config import DATA_DIR, OUTPUT_DIR

# 特征提取参数
PRE_WINDOW_DAYS = 60  # 翻倍前回溯天数


def extract_features_for_all_doublers() -> pd.DataFrame:
    """
    对每个翻倍案例，提取起涨点前 60 天的多维特征
    """
    print("=" * 60)
    print("📐 阶段2：提取翻倍前特征向量")
    print("=" * 60)

    # 加载翻倍案例
    cases_path = OUTPUT_DIR / "doubler_cases.csv"
    if not cases_path.exists():
        print("[ERROR] 请先运行 research_find_doublers.py")
        return pd.DataFrame()

    doublers = pd.read_csv(cases_path)
    print(f"[INPUT] {len(doublers)} 例翻倍案例")

    # 加载K线
    kline = _load_kline()
    if kline.empty:
        return pd.DataFrame()

    # 采样翻倍案例（太多太慢，采样5000例）
    if len(doublers) > 5000:
        doublers = doublers.sample(5000, random_state=42)
        print(f"[SAMPLE] 采样 {len(doublers)} 例")

    # 提取特征
    print(f"[EXTRACT] 提取特征...")
    features = []
    total = len(doublers)

    for i, (_, row) in enumerate(doublers.iterrows()):
        if i % 500 == 0:
            print(f"  {i}/{total}")

        code = str(row["code"]).zfill(6)
        start_date = row["start_date"]

        # 获取该股的所有K线
        stock_kline = kline[kline["code"] == code].sort_values("date")

        # 找到起涨点的位置
        start_date_ts = pd.Timestamp(start_date)
        pre_data = stock_kline[stock_kline["date"] < start_date_ts]

        if len(pre_data) < PRE_WINDOW_DAYS + 20:
            continue

        # 取最近 PRE_WINDOW_DAYS 天
        pre_data = pre_data.tail(PRE_WINDOW_DAYS + 20)

        feat = _extract_single_case_features(pre_data, row)
        if feat:
            features.append(feat)

    df = pd.DataFrame(features)
    print(f"[RESULT] 成功提取 {len(df)} 例特征")

    # 保存
    output_path = OUTPUT_DIR / "doubler_features.csv"
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"💾 已保存: {output_path}")

    return df


def _load_kline() -> pd.DataFrame:
    path = DATA_DIR / "kline_daily.csv"
    if not path.exists():
        return pd.DataFrame()
    col_names = ["code", "market", "name", "date", "open", "high", "low", "close", "volume", "amount"]
    df = pd.read_csv(path, names=col_names, header=None,
                     dtype={"code": str, "market": str, "name": str, "date": str,
                            "open": float, "high": float, "low": float, "close": float,
                            "volume": float, "amount": float})
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"]).sort_values(["code", "date"])


def _extract_single_case_features(pre_data: pd.DataFrame, case: pd.Series) -> Dict:
    """
    对单个翻倍案例，从起涨前数据中提取特征
    pre_data: 起涨日之前的K线数据（至少有60天）
    """
    df = pre_data.copy()
    close = df["close"].values
    volume = df["volume"].values
    high = df["high"].values
    low = df["low"].values
    open_ = df["open"].values

    if len(close) < 50:
        return None

    # ===== 1. 价格位置特征 =====
    ema20 = _ema(close, 20)
    ema60 = _ema(close, 60)
    ma20 = _sma(close, 20)
    ma60 = _sma(close, 60)

    latest_close = close[-1]
    latest_ema20 = ema20[-1]
    latest_ema60 = ema60[-1]
    latest_ma20 = ma20[-1]
    latest_ma60 = ma60[-1]

    feat = {
        "code": case["code"],
        "name": case["name"],
        "start_date": case["start_date"],
        "actual_gain_pct": case["gain_pct"],  # 这是标签，不是特征
        "actual_days": case["days"],

        # 1. 价格 vs 均线
        "price_vs_ema20_pct": round((latest_close / latest_ema20 - 1) * 100, 2),
        "price_vs_ema60_pct": round((latest_close / latest_ema60 - 1) * 100, 2),
        "price_vs_ma20_pct": round((latest_close / latest_ma20 - 1) * 100, 2),
        "price_vs_ma60_pct": round((latest_close / latest_ma60 - 1) * 100, 2),

        # 2. 均线关系
        "ema20_vs_ema60_pct": round((latest_ema20 / latest_ema60 - 1) * 100, 2),
        "ma20_vs_ma60_pct": round((latest_ma20 / latest_ma60 - 1) * 100, 2),

        # 3. EMA金叉
        "ema_golden_cross_days": _days_since_cross(ema20, ema60),
        "ema_golden_cross_recent": 1 if _crossed_recently(ema20, ema60, 20) else 0,

        # 4. 近期回踩深度
        "max_pullback_20d_pct": round(_max_pullback(close, 20), 2),
        "max_pullback_60d_pct": round(_max_pullback(close, 60), 2),
        "current_drawdown_pct": round((close[-1] / np.max(close[-20:]) - 1) * 100, 2),

        # 5. 成交量特征
        "vol_ratio_5d": round(volume[-5:].mean() / (volume[-20:].mean() + 1e-10), 2),
        "vol_ratio_20d": round(volume[-20:].mean() / (volume[-60:].mean() + 1e-10), 2),
        "vol_trend_20d": round(_linear_slope(volume[-20:]) / (volume[-20:].mean() + 1e-10) * 100, 2),

        # 6. 价格动量
        "return_5d_pct": round((close[-1] / close[-6] - 1) * 100, 2) if len(close) > 5 else 0,
        "return_20d_pct": round((close[-1] / close[-21] - 1) * 100, 2) if len(close) > 20 else 0,
        "return_60d_pct": round((close[-1] / close[-61] - 1) * 100, 2) if len(close) > 60 else 0,

        # 7. 波动率
        "volatility_20d": round(np.std(close[-20:] / _sma(close, 20)[-20:] - 1) * 100, 2),
        "volatility_60d": round(np.std(close[-60:] / _sma(close, 60)[-60:] - 1) * 100, 2),
        "amplitude_avg_20d": round(np.mean((high[-20:] - low[-20:]) / low[-20:]) * 100, 2),

        # 8. RSI
        "rsi_14": round(_calc_rsi(close, 14), 1),
        "rsi_6": round(_calc_rsi(close, 6), 1),

        # 9. MACD
        "macd_diff": round(_calc_macd_diff(close), 3),
        "macd_hist": round(_calc_macd_hist(close), 3),

        # 10. 布林带位置
        "bb_position": round(_bb_position(close, 20), 2),

        # 11. 价格结构
        "higher_highs_20d": 1 if _has_higher_highs(close, 20) else 0,
        "higher_lows_20d": 1 if _has_higher_lows(close, 20) else 0,
        "consolidation_20d": 1 if _is_consolidating(close, 20) else 0,

        # 12. 影线特征
        "upper_shadow_ratio": round(_avg_upper_shadow(df.tail(20)), 2),
        "lower_shadow_ratio": round(_avg_lower_shadow(df.tail(20)), 2),
    }

    return feat


# ===== 技术指标辅助函数 =====

def _ema(data, span):
    return pd.Series(data).ewm(span=span, adjust=False).mean().values

def _sma(data, span):
    return pd.Series(data).rolling(span, min_periods=1).mean().values

def _days_since_cross(fast, slow):
    """距最近一次金叉的天数, 负数=未金叉"""
    for i in range(len(fast)-1, -1, -1):
        if fast[i] > slow[i] and i > 0 and fast[i-1] <= slow[i-1]:
            return len(fast) - 1 - i
    return -1

def _crossed_recently(fast, slow, days):
    """最近 days 天内是否有金叉"""
    ds = _days_since_cross(fast, slow)
    return 0 <= ds <= days

def _max_pullback(data, window):
    """最近 window 天内的最大回撤 %"""
    segment = data[-window:]
    peak = np.maximum.accumulate(segment)
    dd = (segment - peak) / peak * 100
    return float(np.min(dd))

def _linear_slope(data):
    """线性趋势斜率"""
    x = np.arange(len(data))
    return float(np.polyfit(x, data, 1)[0])

def _calc_rsi(data, period):
    """RSI"""
    delta = np.diff(data)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).ewm(alpha=1/period, adjust=False).mean().iloc[-1]
    avg_loss = pd.Series(loss).ewm(alpha=1/period, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - 100/(1+rs), 1)

def _calc_macd_diff(data):
    """MACD DIF 值"""
    ema12 = pd.Series(data).ewm(span=12, adjust=False).mean()
    ema26 = pd.Series(data).ewm(span=26, adjust=False).mean()
    return round(float(ema12.iloc[-1] - ema26.iloc[-1]) / float(data[-1]) * 100, 3)

def _calc_macd_hist(data):
    """MACD 柱"""
    ema12 = pd.Series(data).ewm(span=12, adjust=False).mean()
    ema26 = pd.Series(data).ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    return round(float((dif.iloc[-1] - dea.iloc[-1]) / data[-1] * 100), 3)

def _bb_position(data, period):
    """布林带位置: 0=中轨, 1=上轨, -1=下轨"""
    ma = pd.Series(data).rolling(period).mean()
    std = pd.Series(data).rolling(period).std()
    upper = ma + 2*std
    lower = ma - 2*std
    if upper.iloc[-1] == lower.iloc[-1]:
        return 0
    return round(float((data[-1] - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1])), 2)

def _has_higher_highs(data, window):
    """是否有更高的高点"""
    segment = pd.Series(data[-window:])
    half = window // 2
    return float(segment.iloc[-half:].max()) > float(segment.iloc[:half].max())

def _has_higher_lows(data, window):
    """是否有更高的低点"""
    segment = pd.Series(data[-window:])
    half = window // 2
    return float(segment.iloc[-half:].min()) > float(segment.iloc[:half].min())

def _is_consolidating(data, window):
    """是否在横盘整理（振幅<10%）"""
    segment = data[-window:]
    return (np.max(segment) / np.min(segment) - 1) < 0.10

def _avg_upper_shadow(df_tail):
    """平均上影线占比"""
    if df_tail.empty: return 0
    body_high = df_tail[["open", "close"]].max(axis=1)
    total_range = df_tail["high"] - df_tail["low"] + 1e-10
    return float((df_tail["high"] - body_high).div(total_range).mean())

def _avg_lower_shadow(df_tail):
    """平均下影线占比"""
    if df_tail.empty: return 0
    body_low = df_tail[["open", "close"]].min(axis=1)
    total_range = df_tail["high"] - df_tail["low"] + 1e-10
    return float((body_low - df_tail["low"]).div(total_range).mean())


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    extract_features_for_all_doublers()
