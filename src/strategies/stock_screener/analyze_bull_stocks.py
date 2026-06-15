"""
牛股搜集 + 策略验证
—— 2025-2026年涨幅最好的市值≥100亿的票，验证v2策略能否选出来
"""
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict

DATA_DIR = Path("/workspace/stock-screener/data")
OUTPUT_DIR = Path("/workspace/stock-screener/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 第一部分：加载数据
# ============================================================

def load_kline() -> pd.DataFrame:
    """加载全市场K线"""
    col_names = ["code", "market", "name", "date", "open", "high", "low", "close", "volume", "amount"]
    df = pd.read_csv(
        DATA_DIR / "kline_daily.csv", names=col_names, header=None,
        dtype={"code": str, "market": str, "name": str, "date": str,
               "open": float, "high": float, "low": float, "close": float,
               "volume": float, "amount": float},
    )
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values(["code", "date"])
    return df


def load_market_cap() -> pd.DataFrame:
    """加载市值数据 (当前快照)"""
    df = pd.read_csv(DATA_DIR / "tencent_quotes.csv", dtype={"code": str})
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df[["code", "name", "mcap_yi", "pe_ttm", "pb", "price"]]


# ============================================================
# 第二部分：筛选大市值牛股
# ============================================================

def find_top_bull_stocks(
    kline: pd.DataFrame,
    market_cap: pd.DataFrame,
    min_mcap: float = 100.0,
    top_n: int = 100,
) -> pd.DataFrame:
    """
    找出2025-2026年涨幅最好的市值≥min_mcap亿的股票
    
    从多个维度衡量"涨幅最好"：
    1. 2025-01-01 到 2026-06-12 的总涨幅
    2. 期间最大涨幅（最低点到最高点）
    3. 翻倍速度（涨幅/天数）
    """
    print("=" * 70)
    print("📊 筛选 2025-2026 年大市值牛股")
    print(f"   市值门槛: ≥{min_mcap}亿 | Top {top_n}")
    print("=" * 70)
    
    # 合并市值
    kline_with_mcap = kline.merge(market_cap[["code", "mcap_yi"]], on="code", how="inner")
    
    # 筛选市值≥门槛
    large_cap = kline_with_mcap[kline_with_mcap["mcap_yi"] >= min_mcap]
    large_codes = large_cap["code"].unique()
    print(f"[FILTER] 市值≥{min_mcap}亿: {len(large_codes)} 只股票")
    
    # 只看2025年之后的数据
    k2025 = large_cap[large_cap["date"] >= "2025-01-01"].copy()
    print(f"[DATA] 2025年后K线: {len(k2025):,} 行")
    
    # ========================================
    # 对每只股票计算多种收益指标
    # ========================================
    results = []
    
    for i, (code, group) in enumerate(k2025.groupby("code")):
        if i % 200 == 0:
            print(f"  [SCAN] {i}/{len(large_codes)}...")
        
        group = group.sort_values("date").reset_index(drop=True)
        if len(group) < 20:
            continue
        
        name = group["name"].iloc[0]
        mcap = group["mcap_yi"].iloc[0]
        closes = group["close"].values
        dates = group["date"].values
        
        # 1. 总涨幅: 第一期到最后一期
        total_return = (closes[-1] / closes[0] - 1) * 100
        
        # 2. 期间最大涨幅: 低点→高点 (低必须先于高)
        min_idx = np.argmin(closes)
        max_idx = np.argmax(closes)
        
        if max_idx > min_idx:
            max_runup = (closes[max_idx] / closes[min_idx] - 1) * 100
            runup_days = int(max_idx - min_idx)
            runup_start = pd.Timestamp(dates[min_idx]).strftime("%Y-%m-%d")
            runup_end = pd.Timestamp(dates[max_idx]).strftime("%Y-%m-%d")
        else:
            # 高点在低点之前，说明整体下跌
            # 找最大连续涨幅
            max_runup = total_return
            runup_days = len(closes) - 1
            runup_start = pd.Timestamp(dates[0]).strftime("%Y-%m-%d")
            runup_end = pd.Timestamp(dates[-1]).strftime("%Y-%m-%d")
        
        # 3. 年化波动率
        daily_returns = np.diff(closes) / closes[:-1]
        annual_vol = np.std(daily_returns) * np.sqrt(252) * 100
        
        # 4. 最大回撤
        peak = np.maximum.accumulate(closes)
        drawdown = (closes - peak) / peak * 100
        max_dd = drawdown.min()
        
        # 5. 上涨天数占比
        up_days = np.sum(daily_returns > 0)
        up_ratio = up_days / len(daily_returns) * 100
        
        # 6. 2025年YTD (到2025-12-31)
        yr2025 = group[group["date"] <= "2025-12-31"]
        if len(yr2025) >= 5:
            ytd_2025 = (yr2025["close"].values[-1] / yr2025["close"].values[0] - 1) * 100
        else:
            ytd_2025 = np.nan
        
        # 7. 2026年YTD (到现在)
        yr2026 = group[group["date"] >= "2026-01-01"]
        if len(yr2026) >= 5:
            ytd_2026 = (yr2026["close"].values[-1] / yr2026["close"].values[0] - 1) * 100
        else:
            ytd_2026 = np.nan
        
        # 8. 翻倍速度: 如果没有真正翻倍也记录最有爆发力的时段
        best_speed = 0
        best_sprint_days = 0
        best_sprint_gain = 0
        for w in [20, 40, 60, 90, 120]:
            for s in range(0, len(closes) - w, 5):
                seg = closes[s:s+w]
                min_i = np.argmin(seg)
                max_i = np.argmax(seg)
                if max_i > min_i:
                    gain = (seg[max_i] / seg[min_i] - 1) * 100
                    if gain > 0:
                        speed = gain / (max_i - min_i + 1)  # 每天涨幅%
                        if speed > best_speed:
                            best_speed = speed
                            best_sprint_gain = gain
                            best_sprint_days = max_i - min_i + 1
        
        results.append({
            "code": code,
            "name": name,
            "mcap_yi": round(mcap, 1),
            "total_return_pct": round(total_return, 1),
            "max_runup_pct": round(max_runup, 1),
            "runup_days": runup_days,
            "runup_start": runup_start,
            "runup_end": runup_end,
            "ytd_2025_pct": round(ytd_2025, 1) if not np.isnan(ytd_2025) else np.nan,
            "ytd_2026_pct": round(ytd_2026, 1) if not np.isnan(ytd_2026) else np.nan,
            "max_drawdown_pct": round(max_dd, 1),
            "annual_vol_pct": round(annual_vol, 1),
            "up_day_ratio_pct": round(up_ratio, 1),
            "best_sprint_gain_pct": round(best_sprint_gain, 1),
            "best_sprint_days": best_sprint_days,
            "best_sprint_speed_pct": round(best_speed, 3),
            "data_days": len(closes),
        })
    
    df = pd.DataFrame(results)
    df = df.sort_values("total_return_pct", ascending=False).reset_index(drop=True)
    
    print(f"\n[RESULT] 共 {len(df)} 只大市值股票有2025年后数据")
    print(f"   总涨幅TOP10:")
    for _, row in df.head(10).iterrows():
        print(f"   {row['code']} {row['name']:<8s} 总涨幅{row['total_return_pct']:+.1f}%  "
              f"最大涨幅{row['max_runup_pct']:+.1f}%  "
              f"市值{row['mcap_yi']:.0f}亿")
    
    return df


# ============================================================
# 第三部分：特征分析
# ============================================================

def analyze_bull_features(bulls: pd.DataFrame, kline: pd.DataFrame):
    """
    分析牛股在起涨前的特征：
    - 均线排列
    - RSI
    - 布林带位置
    - 回撤
    - MACD
    等等
    """
    print("\n" + "=" * 70)
    print("🔬 牛股起涨前特征分析")
    print("=" * 70)
    
    feature_list = []
    
    for _, bull in bulls.iterrows():
        code = bull["code"]
        runup_start = bull["runup_start"]
        
        # 获取起涨前数据
        stock_data = kline[(kline["code"] == code) & (kline["date"] <= runup_start)].sort_values("date")
        if len(stock_data) < 120:
            continue
        
        closes = stock_data["close"].values
        highs = stock_data["high"].values
        lows = stock_data["low"].values
        volumes = stock_data["volume"].values
        
        # 均线
        ma20 = np.mean(closes[-20:])
        ma60 = np.mean(closes[-60:])
        ma120 = np.mean(closes[-120:]) if len(closes) >= 120 else ma60
        ema20 = _calc_ema(closes, 20)
        ema60 = _calc_ema(closes, 60)
        
        # 价格相对均线位置
        price = closes[-1]
        price_vs_ma20 = (price / ma20 - 1) * 100
        price_vs_ma60 = (price / ma60 - 1) * 100
        price_vs_ema20 = (price / ema20 - 1) * 100
        price_vs_ema60 = (price / ema60 - 1) * 100
        
        # 均线排列 (多头=1, 空头=-1, 交叉=0)
        ma_alignment = 1 if ema20 > ema60 else (-1 if ema20 < ema60 else 0)
        
        # RSI(14)
        rsi_14 = _calc_rsi(closes, 14)
        rsi_6 = _calc_rsi(closes, 6)
        
        # 布林带位置
        bb_upper, bb_mid, bb_lower = _calc_bollinger(closes, 20, 2)
        bb_position = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
        
        # MACD
        macd, macd_signal, macd_hist = _calc_macd(closes)
        
        # 近期回撤
        peak_60 = np.max(closes[-60:])
        drawdown_60 = (price / peak_60 - 1) * 100
        
        # 成交量特征
        vol_20_avg = np.mean(volumes[-20:])
        vol_ratio = volumes[-1] / vol_20_avg if vol_20_avg > 0 else 1
        
        # 波动率
        seg_closes = closes[-31:] if len(closes) >= 31 else closes
        returns = np.diff(seg_closes) / seg_closes[:-1]
        vol_30 = np.std(returns) * 100
        
        # 趋势强度 (R²)
        x = np.arange(min(60, len(closes)))
        y = closes[-60:] if len(closes) >= 60 else closes
        r_squared = _calc_r_squared(x[-len(y):], y)
        
        # 金叉检测
        golden_cross_20d = 0
        if len(closes) >= 62:
            ema20_hist = np.array([_calc_ema(closes[:i+1], 20) for i in range(len(closes)-20, len(closes))])
            ema60_hist = np.array([_calc_ema(closes[:i+1], 60) for i in range(len(closes)-20, len(closes))])
            crosses = np.diff(np.sign(ema20_hist - ema60_hist))
            if np.any(crosses > 0):
                golden_cross_20d = 1
        
        feature_list.append({
            "code": code,
            "name": bull["name"],
            "mcap_yi": bull["mcap_yi"],
            "total_return_pct": bull["total_return_pct"],
            "max_runup_pct": bull["max_runup_pct"],
            "runup_start": runup_start,
            # 价格位置
            "price_vs_ma20_pct": round(price_vs_ma20, 2),
            "price_vs_ma60_pct": round(price_vs_ma60, 2),
            "price_vs_ema20_pct": round(price_vs_ema20, 2),
            "price_vs_ema60_pct": round(price_vs_ema60, 2),
            "ma_alignment": ma_alignment,
            # 动量
            "rsi_6": round(rsi_6, 1),
            "rsi_14": round(rsi_14, 1),
            "macd_hist": round(macd_hist, 4),
            # 布林带
            "bb_position": round(bb_position, 3),
            # 回撤
            "drawdown_60d_pct": round(drawdown_60, 2),
            # 量价
            "vol_ratio": round(vol_ratio, 2),
            "vol_30d_pct": round(vol_30, 2),
            # 趋势
            "r_squared_60": round(r_squared, 3),
            "golden_cross_20d": golden_cross_20d,
        })
    
    df = pd.DataFrame(feature_list)
    print(f"[FEATURES] 提取了 {len(df)} 只牛股的起涨前特征")
    
    # 统计特征分布
    print("\n📊 起涨前特征分布 (中位数):")
    for col in ["price_vs_ma20_pct", "price_vs_ma60_pct", "price_vs_ema20_pct", 
                "price_vs_ema60_pct", "rsi_6", "rsi_14", "bb_position",
                "drawdown_60d_pct", "vol_ratio", "r_squared_60"]:
        print(f"   {col:<25s}: median={df[col].median():.2f}, "
              f"mean={df[col].mean():.2f}, "
              f"std={df[col].std():.2f}")
    
    print(f"\n   金叉出现比例: {df['golden_cross_20d'].mean()*100:.1f}%")
    print(f"   多头排列比例: {(df['ma_alignment'] == 1).mean()*100:.1f}%")
    
    return df


# ============================================================
# 第四部分：v2策略验证 - 能否选出牛股
# ============================================================

@dataclass
class V2Config:
    """v2策略参数"""
    ema_fast: int = 20
    ema_slow: int = 60
    trend_r2_min: float = 0.65
    trend_gain_60d_min: float = 8.0
    pullback_min: float = -5.0
    pullback_max: float = 0.0
    rsi_min: float = 38
    rsi_max: float = 62
    vol_ratio_min: float = 0.5
    vol_ratio_max: float = 2.0
    mcap_min: float = 100.0


def validate_v2_strategy(
    bulls: pd.DataFrame,
    kline: pd.DataFrame,
    market_cap: pd.DataFrame,
    config: V2Config = V2Config(),
) -> pd.DataFrame:
    """
    核心验证：对每只牛股，在起涨前扫描，看v2策略能否选中
    
    方法：
    - 对每只牛股，取其最大涨幅起点前的60天
    - 对这60天逐日运行v2策略筛选条件
    - 只要有一天选出了这只票，就计为"命中"
    """
    print("\n" + "=" * 70)
    print("🎯 v2策略验证：牛股能否被选出？")
    print(f"   扫描窗口: 起涨前60天 | 检查条件: 趋势确认+回调上车")
    print("=" * 70)
    
    # 预计算所有股票的日频指标（加快速度）
    print("[PREP] 预计算所有大市值股票的指标...")
    mcap_codes = set(market_cap[market_cap["mcap_yi"] >= config.mcap_min]["code"].values)
    bull_codes = set(bulls["code"].values[:200])  # 重点验证TOP200
    target_codes = mcap_codes & bull_codes
    
    # 对每只牛股，在起涨前逐日判断
    results = []
    
    for idx, (_, bull) in enumerate(bulls.iterrows()):
        code = bull["code"]
        if code not in target_codes:
            continue
        
        runup_start = bull["runup_start"]
        runup_start_dt = pd.Timestamp(runup_start)
        
        # 取该股全部历史数据
        stock_all = kline[(kline["code"] == code) & (kline["date"] <= runup_start_dt)].sort_values("date")
        if len(stock_all) < 120:
            continue
        
        # 检查起涨前60天区域
        # 每天用v2策略条件判断
        hit_dates = []
        hit_details = []
        
        for scan_offset in range(60, 10, -1):  # 从远到近扫描，记录最早命中
            if scan_offset >= len(stock_all):
                continue
            
            # 模拟当天能看到的K线
            visible = stock_all.iloc[:len(stock_all) - scan_offset + 1]
            if len(visible) < 60:
                continue
            
            closes = visible["close"].values
            highs = visible["high"].values
            lows = visible["low"].values
            volumes = visible["volume"].values
            scan_date = visible["date"].iloc[-1]
            
            # v2 策略条件
            
            # 1. 趋势确认
            ema20 = _calc_ema(closes, 20)
            ema60 = _calc_ema(closes, 60)
            ema20_above_ema60 = ema20 > ema60
            
            # R²
            x = np.arange(60)
            y = closes[-60:]
            r2 = _calc_r_squared(x, y)
            trend_strong = r2 >= config.trend_r2_min
            
            # 60日涨幅
            gain_60 = (closes[-1] / closes[-60] - 1) * 100 if len(closes) >= 60 else 0
            gain_ok = gain_60 >= config.trend_gain_60d_min
            
            trend_confirmed = ema20_above_ema60 and trend_strong and gain_ok
            
            # 2. 回踩检测
            peak_20 = np.max(highs[-20:])
            pullback = (closes[-1] / peak_20 - 1) * 100
            pullback_ok = config.pullback_min <= pullback <= config.pullback_max
            
            # 3. RSI
            rsi = _calc_rsi(closes, 14)
            rsi_ok = config.rsi_min <= rsi <= config.rsi_max
            
            # 4. 量比
            vol_avg_20 = np.mean(volumes[-21:-1])
            vol_ratio = volumes[-1] / vol_avg_20 if vol_avg_20 > 0 else 0
            vol_ok = config.vol_ratio_min <= vol_ratio <= config.vol_ratio_max
            
            # 综合判断
            if trend_confirmed and pullback_ok and rsi_ok and vol_ok:
                hit_dates.append(pd.Timestamp(scan_date).strftime("%Y-%m-%d"))
                hit_details.append({
                    "scan_date": pd.Timestamp(scan_date).strftime("%Y-%m-%d"),
                    "days_before_runup": scan_offset,
                    "price": round(closes[-1], 2),
                    "ema20_above_ema60": ema20_above_ema60,
                    "r2": round(r2, 3),
                    "gain_60d": round(gain_60, 1),
                    "pullback_pct": round(pullback, 1),
                    "rsi": round(rsi, 1),
                    "vol_ratio": round(vol_ratio, 2),
                })
        
        # 也检查起涨日之后有没有信号（追涨信号）
        stock_after = kline[(kline["code"] == code) & 
                           (kline["date"] > runup_start_dt) & 
                           (kline["date"] <= runup_start_dt + timedelta(days=30))].sort_values("date")
        
        after_hit = False
        after_dates = []
        if len(stock_after) > 0:
            for a_idx in range(len(stock_after)):
                visible = pd.concat([stock_all, stock_after.iloc[:a_idx+1]])
                if len(visible) < 60:
                    continue
                closes = visible["close"].values
                highs = visible["high"].values
                lows = visible["low"].values
                volumes = visible["volume"].values
                
                ema20 = _calc_ema(closes, 20)
                ema60 = _calc_ema(closes, 60)
                ema20_above_ema60 = ema20 > ema60
                
                x = np.arange(60)
                y = closes[-60:]
                r2 = _calc_r_squared(x, y)
                trend_strong = r2 >= config.trend_r2_min
                
                gain_60 = (closes[-1] / closes[-60] - 1) * 100
                gain_ok = gain_60 >= config.trend_gain_60d_min
                
                trend_confirmed = ema20_above_ema60 and trend_strong and gain_ok
                
                peak_20 = np.max(highs[-20:])
                pullback = (closes[-1] / peak_20 - 1) * 100
                pullback_ok = config.pullback_min <= pullback <= config.pullback_max
                
                rsi = _calc_rsi(closes, 14)
                rsi_ok = config.rsi_min <= rsi <= config.rsi_max
                
                vol_avg_20 = np.mean(volumes[-21:-1])
                vol_ratio = volumes[-1] / vol_avg_20 if vol_avg_20 > 0 else 0
                vol_ok = config.vol_ratio_min <= vol_ratio <= config.vol_ratio_max
                
                if trend_confirmed and pullback_ok and rsi_ok and vol_ok:
                    after_hit = True
                    after_dates.append(pd.Timestamp(visible["date"].iloc[-1]).strftime("%Y-%m-%d"))
                    break
        
        hit_before = len(hit_dates) > 0
        earliest_hit = hit_dates[0] if hit_before else None
        latest_hit = hit_dates[-1] if hit_before else None
        
        results.append({
            "code": code,
            "name": bull["name"],
            "mcap_yi": bull["mcap_yi"],
            "total_return_pct": bull["total_return_pct"],
            "max_runup_pct": bull["max_runup_pct"],
            "runup_start": runup_start,
            "runup_end": bull["runup_end"],
            "hit_before_runup": hit_before,
            "num_hits": len(hit_dates),
            "earliest_hit": earliest_hit,
            "latest_hit": latest_hit,
            "hit_after_runup": after_hit,
            "first_hit_detail": hit_details[0] if hit_details else None,
        })
        
        if idx < 10:
            status = "✅ 命中" if hit_before else ("🟡 追涨命中" if after_hit else "❌ 未命中")
            print(f"  [{idx+1}] {code} {bull['name']:<8s} {status} "
                  f"(涨幅{bull['max_runup_pct']:+.1f}%, "
                  f"提前命中{len(hit_dates)}次"
                  f"{', 最早' + earliest_hit if earliest_hit else ''})")
    
    df = pd.DataFrame(results)
    
    # 统计命中率
    print(f"\n{'='*70}")
    print(f"📊 v2策略验证结果:")
    print(f"   验证牛股数: {len(df)}")
    print(f"   起涨前命中: {df['hit_before_runup'].sum()} 只 ({df['hit_before_runup'].mean()*100:.1f}%)")
    print(f"   起涨后命中: {(~df['hit_before_runup'] & df['hit_after_runup']).sum()} 只")
    print(f"   完全未命中: {(~df['hit_before_runup'] & ~df['hit_after_runup']).sum()} 只")
    
    # 按涨幅分组看命中率
    df["return_group"] = pd.cut(df["max_runup_pct"], bins=[0, 50, 100, 150, 200, 500, 5000],
                                 labels=["0-50%", "50-100%", "100-150%", "150-200%", "200-500%", "500%+"])
    print(f"\n   按涨幅分组命中率:")
    for group_name, group_df in df.groupby("return_group", observed=False):
        if len(group_df) >= 3:
            print(f"     {group_name}: {group_df['hit_before_runup'].mean()*100:.0f}% ({group_df['hit_before_runup'].sum()}/{len(group_df)})")
    
    return df


# ============================================================
# 第五部分：未命中原因分析
# ============================================================

def analyze_misses(validation: pd.DataFrame, kline: pd.DataFrame):
    """分析未命中的牛股，找出策略盲区"""
    misses = validation[~validation["hit_before_runup"]].copy()
    
    if len(misses) == 0:
        print("\n🎉 所有牛股都被命中了！")
        return
    
    print("\n" + "=" * 70)
    print(f"🔍 未命中原因分析 ({len(misses)}只)")
    print("=" * 70)
    
    # 对每只未命中的，检查是哪个条件没通过
    analysis = []
    
    for _, miss in misses.iterrows():
        code = miss["code"]
        runup_start = miss["runup_start"]
        
        stock = kline[(kline["code"] == code) & (kline["date"] <= runup_start)].sort_values("date")
        if len(stock) < 60:
            continue
        
        closes = stock["close"].values
        highs = stock["high"].values
        volumes = stock["volume"].values
        
        ema20 = _calc_ema(closes, 20)
        ema60 = _calc_ema(closes, 60)
        ema_ok = ema20 > ema60
        
        x = np.arange(60)
        y = closes[-60:]
        r2 = _calc_r_squared(x, y)
        r2_ok = r2 >= 0.65
        
        gain_60 = (closes[-1] / closes[-60] - 1) * 100
        gain_ok = gain_60 >= 8.0
        
        peak_20 = np.max(highs[-20:])
        pullback = (closes[-1] / peak_20 - 1) * 100
        pullback_ok = -5.0 <= pullback <= 0.0
        
        rsi = _calc_rsi(closes, 14)
        rsi_ok = 38 <= rsi <= 62
        
        vol_avg_20 = np.mean(volumes[-21:-1])
        vol_ratio = volumes[-1] / vol_avg_20 if vol_avg_20 > 0 else 0
        vol_ok = 0.5 <= vol_ratio <= 2.0
        
        # 遍历起涨前60天，看哪天最接近通过
        best_score = 0
        best_detail = {}
        for offset in range(60, 5, -1):
            if offset >= len(stock):
                continue
            visible = stock.iloc[:len(stock) - offset]
            c = visible["close"].values
            h = visible["high"].values
            v = visible["volume"].values
            
            if len(c) < 60:
                continue
            
            e20 = _calc_ema(c, 20)
            e60 = _calc_ema(c, 60)
            r2_ = _calc_r_squared(np.arange(60), c[-60:])
            g60 = (c[-1] / c[-60] - 1) * 100
            pb = (c[-1] / np.max(h[-20:]) - 1) * 100 if len(h) >= 20 else 0
            rs = _calc_rsi(c, 14)
            vr = v[-1] / np.mean(v[-21:-1]) if np.mean(v[-21:-1]) > 0 else 0
            
            score = 0
            passed = []
            if e20 > e60: score += 1; passed.append("EMA✓")
            else: passed.append("EMA✗")
            if r2_ >= 0.65: score += 1; passed.append("R²✓")
            else: passed.append("R²✗")
            if g60 >= 8: score += 1; passed.append("涨幅✓")
            else: passed.append("涨幅✗")
            if -5 <= pb <= 0: score += 1; passed.append("回踩✓")
            else: passed.append("回踩✗")
            if 38 <= rs <= 62: score += 1; passed.append("RSI✓")
            else: passed.append("RSI✗")
            if 0.5 <= vr <= 2: score += 1; passed.append("量比✓")
            else: passed.append("量比✗")
            
            if score > best_score:
                best_score = score
                best_detail = {
                    "best_date": pd.Timestamp(visible["date"].iloc[-1]).strftime("%Y-%m-%d"),
                    "best_score": score,
                    "passed": " | ".join(passed),
                    "ema20": round(e20, 2), "ema60": round(e60, 2),
                    "r2": round(r2_, 3), "gain_60": round(g60, 1),
                    "pullback": round(pb, 1), "rsi": round(rs, 1),
                    "vol_ratio": round(vr, 2),
                }
        
        analysis.append({
            "code": code,
            "name": miss["name"],
            "max_runup_pct": miss["max_runup_pct"],
            "runup_start": runup_start,
            "fail_reason_at_start": (
                f"{'EMA✗' if not ema_ok else 'EMA✓'} "
                f"{'R²✗' if not r2_ok else 'R²✓'} "
                f"{'涨幅✗' if not gain_ok else '涨幅✓'} "
                f"{'回踩✗' if not pullback_ok else '回踩✓'} "
                f"{'RSI✗' if not rsi_ok else 'RSI✓'} "
                f"{'量比✗' if not vol_ok else '量比✓'}"
            ),
            "nearest_pass": best_detail.get("best_score", 0),
            "nearest_detail": str(best_detail),
        })
    
    df = pd.DataFrame(analysis)
    
    # 统计哪个条件最常导致未命中
    fail_reasons = defaultdict(int)
    for reason in df["fail_reason_at_start"]:
        for part in reason.split():
            if "✗" in part:
                cond = part.replace("✗", "")
                fail_reasons[cond] += 1
    
    print("\n条件失败统计 (起涨日当天):")
    for cond, count in sorted(fail_reasons.items(), key=lambda x: -x[1]):
        print(f"   {cond}: {count}只 ({count/len(df)*100:.0f}%)")
    
    print(f"\n最近通过分数分布:")
    print(f"   4/6条件通过: {(df['nearest_pass'] == 4).sum()} 只")
    print(f"   3/6条件通过: {(df['nearest_pass'] == 3).sum()} 只")
    print(f"   2/6条件通过: {(df['nearest_pass'] == 2).sum()} 只")
    print(f"   ≤1条件通过: {(df['nearest_pass'] <= 1).sum()} 只")
    
    # 按失败原因分类展示前15条
    print(f"\n📋 未命中详情 (TOP15):")
    for _, row in df.head(15).iterrows():
        print(f"   {row['code']} {row['name']:<8s} 涨幅{row['max_runup_pct']:+.1f}% "
              f"起涨{row['runup_start']} → {row['fail_reason_at_start']} "
              f"(最近{row['nearest_pass']}/6: {row['nearest_detail'][:100]})")
    
    return df


# ============================================================
# 第六部分：主流程
# ============================================================

def main():
    print("🚀 牛股搜集 + v2策略验证")
    print("=" * 70)
    
    # 加载数据
    print("[LOAD] 加载K线数据...")
    kline = load_kline()
    print(f"       {len(kline):,} 行, {kline['code'].nunique()} 只股票")
    
    print("[LOAD] 加载市值数据...")
    mcap = load_market_cap()
    print(f"       {len(mcap)} 只股票")
    
    # ==========================
    # 第一步：找TOP牛股
    # ==========================
    bulls = find_top_bull_stocks(kline, mcap, min_mcap=100, top_n=100)
    
    # 保存
    bulls_path = OUTPUT_DIR / "bull_stocks_2025_2026.csv"
    bulls.to_csv(bulls_path, index=False, encoding="utf-8-sig")
    print(f"\n💾 牛股清单已保存: {bulls_path}")
    
    # ==========================
    # 第二步：特征分析
    # ==========================
    features = analyze_bull_features(bulls, kline)
    feat_path = OUTPUT_DIR / "bull_stocks_features.csv"
    features.to_csv(feat_path, index=False, encoding="utf-8-sig")
    print(f"\n💾 特征数据已保存: {feat_path}")
    
    # ==========================
    # 第三步：v2策略验证
    # ==========================
    validation = validate_v2_strategy(bulls, kline, mcap)
    val_path = OUTPUT_DIR / "bull_stocks_validation.csv"
    validation.to_csv(val_path, index=False, encoding="utf-8-sig")
    print(f"\n💾 验证结果已保存: {val_path}")
    
    # ==========================
    # 第四步：未命中分析
    # ==========================
    misses_analysis = analyze_misses(validation, kline)
    if misses_analysis is not None and len(misses_analysis) > 0:
        miss_path = OUTPUT_DIR / "bull_stocks_misses.csv"
        misses_analysis.to_csv(miss_path, index=False, encoding="utf-8-sig")
        print(f"\n💾 未命中分析已保存: {miss_path}")
    
    # ==========================
    # 总结
    # ==========================
    print("\n" + "=" * 70)
    print("📊 最终总结")
    print("=" * 70)
    print(f"大市值(≥100亿)牛股: {len(bulls)} 只")
    print(f"v2策略命中率: {validation['hit_before_runup'].mean()*100:.1f}%")
    print(f"  - 起涨前命中: {validation['hit_before_runup'].sum()} 只")
    print(f"  - 起涨后追到: {(~validation['hit_before_runup'] & validation['hit_after_runup']).sum()} 只")
    print(f"  - 完全错过: {(~validation['hit_before_runup'] & ~validation['hit_after_runup']).sum()} 只")
    
    # 检查按命中率有没有涨幅分组差异
    if 'return_group' in validation.columns:
        print("\n📈 按最大涨幅分组:")
        for group_name, group_df in validation.groupby("return_group", observed=False):
            if len(group_df) >= 3:
                hit_rate = group_df['hit_before_runup'].mean() * 100
                print(f"   {group_name}: 命中率 {hit_rate:.0f}% ({group_df['hit_before_runup'].sum()}/{len(group_df)})")
    
    return bulls, validation


# ============================================================
# 技术指标辅助函数
# ============================================================

def _calc_ema(data: np.ndarray, period: int) -> float:
    """计算EMA"""
    if len(data) < period:
        return np.mean(data)
    alpha = 2 / (period + 1)
    ema = np.mean(data[:period])
    for price in data[period:]:
        ema = alpha * price + (1 - alpha) * ema
    return ema


def _calc_rsi(closes: np.ndarray, period: int = 14) -> float:
    """计算RSI"""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-period-1:])
    gains = np.maximum(deltas, 0)
    losses = np.maximum(-deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_bollinger(closes: np.ndarray, period: int = 20, std_dev: float = 2.0):
    """计算布林带"""
    if len(closes) < period:
        mid = np.mean(closes)
        std = np.std(closes)
        return mid + std_dev * std, mid, mid - std_dev * std
    
    mid = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    return mid + std_dev * std, mid, mid - std_dev * std


def _calc_macd(closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """计算MACD - 返回最新的 MACD线, 信号线, 柱状图"""
    if len(closes) < slow + signal:
        return 0, 0, 0
    
    # 计算完整EMA序列
    ema_fast_arr = _calc_ema_array(closes, fast)
    ema_slow_arr = _calc_ema_array(closes, slow)
    
    # MACD线 = 快EMA - 慢EMA
    macd_arr = ema_fast_arr - ema_slow_arr
    
    # 取有效部分（非NaN）
    valid = macd_arr[~np.isnan(macd_arr)]
    if len(valid) < signal:
        return valid[-1] if len(valid) > 0 else 0, 0, 0
    
    # 信号线 = MACD的EMA(signal)
    signal_line = _calc_ema(valid, signal)
    macd_line = valid[-1]
    hist = macd_line - signal_line
    
    return macd_line, signal_line, hist


def _calc_ema_array(data: np.ndarray, period: int) -> np.ndarray:
    """返回整个EMA序列"""
    alpha = 2 / (period + 1)
    ema = np.zeros_like(data)
    ema[:period] = np.nan
    ema[period-1] = np.mean(data[:period])
    for i in range(period, len(data)):
        ema[i] = alpha * data[i] + (1 - alpha) * ema[i-1]
    return ema


def _calc_r_squared(x: np.ndarray, y: np.ndarray) -> float:
    """计算R²"""
    if len(x) < 2 or len(y) < 2:
        return 0.0
    x = np.arange(len(y)) if len(x) != len(y) else x
    correlation = np.corrcoef(x, y)[0, 1]
    if np.isnan(correlation):
        return 0.0
    return correlation ** 2


if __name__ == "__main__":
    bulls, validation = main()
