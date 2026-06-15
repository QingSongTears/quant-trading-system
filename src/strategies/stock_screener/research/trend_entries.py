"""
趋势中途上车点研究
核心问题：在已经确认的上升趋势中，哪一天上车还能赚钱？
方法：取100只机构票的完整趋势，逐日标记前向收益，找好的上车点特征
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from src.strategies.stock_screener.config import DATA_DIR, OUTPUT_DIR


def analyze_trend_entries():
    print("=" * 70)
    print("🔍 趋势中途上车点分析")
    print("   目标：在已确认的趋势中，什么样的日子适合上车？")
    print("=" * 70)

    # 加载数据
    climbers = pd.read_csv(OUTPUT_DIR / "institutional_climbers_100.csv")
    kline = _load_kline()

    # 对每只标的，逐日提取"上车特征 + 前向收益标签"
    all_entries = []
    day_stats = []  # 按"趋势进度"分组统计

    for i, (_, stock) in enumerate(climbers.iterrows()):
        code = str(stock["code"]).zfill(6)
        stock_k = kline[kline["code"] == code].sort_values("date")
        if len(stock_k) < 140:
            continue

        # 取最后 120 天的趋势
        trend = stock_k.tail(120).reset_index(drop=True)
        closes = trend["close"].values
        dates = trend["date"].values

        if len(closes) < 120:
            continue

        # 从第 30 天开始（给趋势确认留时间），到倒数第 15 天（给前向收益留空间）
        for day in range(30, len(closes) - 20):
            # 确认当前处于上升趋势中（EMA20 > EMA60）
            ema20 = pd.Series(closes[:day+1]).ewm(span=20, adjust=False).mean().iloc[-1]
            ema60 = pd.Series(closes[:day+1]).ewm(span=60, adjust=False).mean().iloc[-1]
            if ema20 <= ema60 or pd.isna(ema60):
                continue

            # 当前位置判断
            current_close = closes[day]
            position = _classify_position(current_close, closes[:day+1])

            # 前向收益（无未来数据泄露！）
            fwd_5d = (closes[min(day+5, len(closes)-1)] / current_close - 1) * 100
            fwd_10d = (closes[min(day+10, len(closes)-1)] / current_close - 1) * 100
            fwd_15d = (closes[min(day+15, len(closes)-1)] / current_close - 1) * 100
            fwd_20d = (closes[min(day+20, len(closes)-1)] / current_close - 1) * 100

            # 回踩深度
            recent_high = np.max(closes[max(0, day-20):day+1])
            pullback_pct = (current_close / recent_high - 1) * 100

            # 趋势进度（起始到当前位置涨了多少）
            trend_progress = (current_close / closes[0] - 1) * 100

            # RSI
            rsi_14 = _calc_rsi(closes[:day+1], 14)
            rsi_6 = _calc_rsi(closes[:day+1], 6)

            # 成交量
            vol = trend["volume"].values
            vol_ratio = vol[day] / (np.mean(vol[max(0,day-20):day]) + 1e-10)

            # 波动率
            rets = np.diff(closes[max(0,day-20):day+1]) / (closes[max(0,day-20):day] + 1e-10)
            volatility = np.std(rets) * 100

            # MACD
            macd_dif = _calc_macd_dif(closes[:day+1])

            # 日内振幅
            daily_range = (trend["high"].iloc[day] - trend["low"].iloc[day]) / current_close * 100

            entry = {
                "code": code,
                "date": str(dates[day])[:10],
                "day_in_trend": day,
                "position": position,
                "pullback_pct": round(pullback_pct, 2),
                "trend_progress": round(trend_progress, 1),
                "rsi_14": round(rsi_14, 1),
                "rsi_6": round(rsi_6, 1),
                "vol_ratio": round(vol_ratio, 2),
                "volatility_20d": round(volatility, 2),
                "macd_dif": round(macd_dif, 3),
                "daily_range_pct": round(daily_range, 2),
                "fwd_5d": round(fwd_5d, 2),
                "fwd_10d": round(fwd_10d, 2),
                "fwd_15d": round(fwd_15d, 2),
                "fwd_20d": round(fwd_20d, 2),
            }
            all_entries.append(entry)

        if i % 20 == 0:
            print(f"  [{i}/100] {code} {stock['name']}: {len(all_entries)} 个上车点已分析")

    # ===== 汇总分析 =====
    df = pd.DataFrame(all_entries)
    print(f"\n📊 共分析 {len(df):,} 个趋势中途上车点")

    # 标签：好上车点 = 10日收益 > 5%
    df["label"] = "neutral"
    df.loc[df["fwd_10d"] > 5, "label"] = "good_entry"
    df.loc[df["fwd_10d"] < -3, "label"] = "bad_entry"

    good = df[df["label"] == "good_entry"]
    bad = df[df["label"] == "bad_entry"]
    print(f"   好上车点 (10日>+5%): {len(good):,} ({len(good)/len(df)*100:.1f}%)")
    print(f"   差上车点 (10日<-3%): {len(bad):,} ({len(bad)/len(df)*100:.1f}%)")

    # ===== 核心分析 =====
    print(f"\n{'='*70}")
    print("📊 按回踩深度分组：前向收益")
    print(f"{'='*70}")

    # 1. 按回踩深度分组
    bins = [-30, -15, -10, -5, -3, -1, 0, 2, 5, 10, 30]
    labels = ["<-15%","-15~-10%","-10~-5%","-5~-3%","-3~-1%","-1~0%","0~2%","2~5%","5~10%",">10%"]
    df["pb_group"] = pd.cut(df["pullback_pct"], bins=bins, labels=labels)

    print(f"\n{'回踩深度':<15s} {'样本数':<8s} {'10日平均收益':<12s} {'胜率(>0)':<10s} {'好入口率(>5%)':<12s}")
    print("-" * 57)
    for g in labels:
        subset = df[df["pb_group"] == g]
        if len(subset) < 20:
            continue
        win = (subset["fwd_10d"] > 0).mean() * 100
        good_rate = (subset["label"] == "good_entry").mean() * 100
        print(f"  {g:<15s} {len(subset):<8,} {subset['fwd_10d'].mean():<+12.1f}% {win:<10.0f}% {good_rate:<12.0f}%")

    # 2. 按趋势进度分组
    print(f"\n{'='*70}")
    print("📊 按趋势进度分组：前向收益（趋势越走越远，还能上车吗？）")
    print(f"{'='*70}")

    prog_bins = [0, 10, 20, 30, 40, 50, 60, 80, 100, 200]
    prog_labels = ["0~10%","10~20%","20~30%","30~40%","40~50%","50~60%","60~80%","80~100%",">100%"]
    df["prog_group"] = pd.cut(df["trend_progress"], bins=prog_bins, labels=prog_labels)

    print(f"\n{'趋势已涨':<15s} {'样本数':<8s} {'10日平均收益':<12s} {'胜率':<8s} {'好入口率':<10s}")
    print("-" * 55)
    for g in prog_labels:
        subset = df[df["prog_group"] == g]
        if len(subset) < 20:
            continue
        win = (subset["fwd_10d"] > 0).mean() * 100
        good_rate = (subset["label"] == "good_entry").mean() * 100
        print(f"  {g:<15s} {len(subset):<8,} {subset['fwd_10d'].mean():<+12.1f}% {win:<8.0f}% {good_rate:<10.0f}%")

    # 3. 回踩深度+RSI组合
    print(f"\n{'='*70}")
    print("📊 回踩深度 × RSI 组合：10日胜率矩阵")
    print(f"{'='*70}")

    rsi_bins = [0, 30, 40, 50, 60, 70, 100]
    rsi_labels = ["<30","30~40","40~50","50~60","60~70",">70"]
    df["rsi_group"] = pd.cut(df["rsi_14"], bins=rsi_bins, labels=rsi_labels)

    print(f"\n{'回踩↓ RSI→':<15s}", end="")
    for rl in rsi_labels:
        print(f"  {rl:<10s}", end="")
    print(f"  {'任意RSI':<10s}")
    print("-" * (15 + 11 * (len(rsi_labels) + 1)))

    for g in labels[:7]:  # 只看回踩区间
        print(f"  {g:<15s}", end="")
        row_total = 0
        row_wins = 0
        for rl in rsi_labels:
            subset = df[(df["pb_group"] == g) & (df["rsi_group"] == rl)]
            if len(subset) >= 10:
                wr = (subset["fwd_10d"] > 0).mean() * 100
                print(f"  {wr:3.0f}%({len(subset):4d})", end="")
            else:
                print(f"  {'-':>9s}", end="")
            row_total += len(subset)
            row_wins += len(subset[subset["fwd_10d"] > 0])
        # 该回踩行的整体胜率
        if row_total > 0:
            overall = row_wins / row_total * 100
            print(f"  {overall:3.0f}%({row_total:4d})", end="")
        print()

    # 4. 最佳组合发现
    print(f"\n{'='*70}")
    print("🎯 最佳上车条件组合")
    print(f"{'='*70}")

    # 回踩-5%~-1% + RSI 40-60
    best_subset = df[
        (df["pullback_pct"].between(-8, -1)) &
        (df["rsi_14"].between(40, 60)) &
        (df["vol_ratio"].between(0.6, 1.5))
    ]
    if len(best_subset) > 50:
        print(f"\n  条件: 回踩-8%~-1% + RSI 40-60 + 量比0.6~1.5")
        print(f"  样本: {len(best_subset)} 次")
        print(f"  10日平均收益: {best_subset['fwd_10d'].mean():.1f}%")
        print(f"  10日胜率: {(best_subset['fwd_10d']>0).mean()*100:.0f}%")
        print(f"  好入口率: {(best_subset['fwd_10d']>5).mean()*100:.0f}%")
        print(f"  15日平均收益: {best_subset['fwd_15d'].mean():.1f}%")
        print(f"  20日平均收益: {best_subset['fwd_20d'].mean():.1f}%")

    # 5. 多维组合扫描：找最优
    print(f"\n  遍历最优组合...")
    best_combo = None
    best_score = -999
    for pb_lo, pb_hi in [(-3,-0.5), (-5,-1), (-8,-1), (-10,-2), (-5,0), (-8,-0.5), (-12,-2)]:
        for rsi_lo, rsi_hi in [(35,55), (40,60), (35,60), (40,55), (45,65), (30,50)]:
            for vol_lo, vol_hi in [(0.6,1.3), (0.7,1.5), (0.5,1.2), (0.6,1.0)]:
                subset = df[
                    (df["pullback_pct"].between(pb_lo, pb_hi)) &
                    (df["rsi_14"].between(rsi_lo, rsi_hi)) &
                    (df["vol_ratio"].between(vol_lo, vol_hi))
                ]
                if len(subset) < 30:
                    continue
                score = subset["fwd_10d"].mean() * min(len(subset)/50, 2.0)
                if score > best_score:
                    best_score = score
                    best_combo = {
                        "pullback": f"{pb_lo}~{pb_hi}%",
                        "rsi": f"{rsi_lo}~{rsi_hi}",
                        "vol_ratio": f"{vol_lo}~{vol_hi}",
                        "n": len(subset),
                        "avg_10d": round(subset["fwd_10d"].mean(), 1),
                        "win_rate": round((subset["fwd_10d"]>0).mean()*100, 0),
                        "avg_15d": round(subset["fwd_15d"].mean(), 1),
                        "avg_20d": round(subset["fwd_20d"].mean(), 1),
                    }

    if best_combo:
        print(f"\n  🏆 最优组合:")
        print(f"     回踩: {best_combo['pullback']}")
        print(f"     RSI:  {best_combo['rsi']}")
        print(f"     量比: {best_combo['vol_ratio']}")
        print(f"     样本: {best_combo['n']} 次")
        print(f"     10日平均收益: {best_combo['avg_10d']}%")
        print(f"     10日胜率: {best_combo['win_rate']}%")
        print(f"     15日收益: {best_combo['avg_15d']}%")
        print(f"     20日收益: {best_combo['avg_20d']}%")

    # 保存
    df.to_csv(OUTPUT_DIR / "trend_entry_analysis.csv", index=False, encoding="utf-8-sig")
    print(f"\n💾 {OUTPUT_DIR}/trend_entry_analysis.csv ({len(df):,} 行)")

    return df


def _classify_position(close, closes):
    ema20 = pd.Series(closes).ewm(span=20, adjust=False).mean().iloc[-1]
    ema60 = pd.Series(closes).ewm(span=60, adjust=False).mean().iloc[-1]
    if close > ema20 > ema60:
        return "强势"
    elif ema20 > close > ema60:
        return "回踩"
    else:
        return "破位"


def _calc_rsi(data, period):
    if len(data) < period + 1:
        return 50
    delta = np.diff(data)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).ewm(alpha=1/period, adjust=False).mean().iloc[-1]
    avg_loss = pd.Series(loss).ewm(alpha=1/period, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100
    return 100 - 100/(1 + avg_gain/avg_loss)


def _calc_macd_dif(data):
    ema12 = pd.Series(data).ewm(span=12, adjust=False).mean()
    ema26 = pd.Series(data).ewm(span=26, adjust=False).mean()
    return float((ema12.iloc[-1] - ema26.iloc[-1]) / data[-1] * 100)


def _load_kline():
    path = DATA_DIR / "kline_daily.csv"
    if not path.exists():
        return pd.DataFrame()
    cn = ["code","market","name","date","open","high","low","close","volume","amount"]
    df = pd.read_csv(path, names=cn, header=None,
                     dtype={"code":str,"market":str,"name":str,"date":str,
                            "open":float,"high":float,"low":float,"close":float,
                            "volume":float,"amount":float})
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"]).sort_values(["code","date"])


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    analyze_trend_entries()
