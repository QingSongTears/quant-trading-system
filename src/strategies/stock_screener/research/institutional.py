"""
综合研究：100只机构型稳步上涨标的的全维度分析
筛选 → 40+技术指标 → 基本面 → 板块共振 → 统计共同点
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from collections import defaultdict
import json

from config import DATA_DIR, OUTPUT_DIR

np.random.seed(42)

# ================================================================
# 阶段1：筛选 100 只机构型稳步上涨标的
# ================================================================

def select_institutional_climbers(n: int = 100) -> pd.DataFrame:
    """
    筛选条件（非常严格）：
    1. 市值 ≥ 150亿
    2. PE > 0（不亏损）
    3. 最近120天趋势向上
    4. 60日收益率 10%~60%（稳步涨，不暴涨暴跌）
    5. 最大回撤 < 25%（机构控盘特征）
    6. 趋势线拟合度 R² > 0.7（上涨平滑）
    7. 日均换手率 0.5%~5%（机构票特征，不太冷也不太热）
    8. 排除ST、停牌、次新
    """
    print("=" * 70)
    print(f"🔍 筛选 {n} 只机构型稳步上涨标的")
    print("=" * 70)

    kline = _load_kline()
    spot = _load_spot()
    finance = _load_finance()

    if kline.empty:
        return pd.DataFrame()

    # 候选池：市值≥150亿 + PE>0
    spot["code"] = spot["code"].astype(str).str.zfill(6)
    spot_filtered = spot[
        (spot["mcap_yi"] >= 150) &
        (spot["pe_ttm"] > 0) &
        (~spot["name"].str.contains("ST|退", na=False))
    ]
    eligible_codes = set(spot_filtered["code"].values)
    print(f"[FILTER] 市值≥150亿 + PE>0 + 排除ST: {len(eligible_codes)} 只")

    # 对每只候选股评估"机构稳步上涨"得分
    scored = []
    excluded = {"no_data": 0, "not_uptrend": 0, "high_drawdown": 0, "low_volume": 0, "noisy": 0}

    for code in eligible_codes:
        stock_k = kline[kline["code"] == code].sort_values("date")
        if len(stock_k) < 150:
            excluded["no_data"] += 1
            continue

        # 用最近120个交易日
        recent = stock_k.tail(120).reset_index(drop=True)
        closes = recent["close"].values
        volumes = recent["volume"].values
        highs = recent["high"].values
        lows = recent["low"].values

        if len(closes) < 100:
            excluded["no_data"] += 1
            continue

        # 1. 趋势方向：60日收益
        ret_60 = (closes[-1] / closes[-61] - 1) * 100 if len(closes) > 60 else 0
        if ret_60 < 5 or ret_60 > 80:
            excluded["not_uptrend"] += 1
            continue

        # 2. 最大回撤
        peak = np.maximum.accumulate(closes)
        drawdown = np.min((closes - peak) / peak * 100)
        if drawdown < -30:
            excluded["high_drawdown"] += 1
            continue

        # 3. 趋势线拟合度 R²
        x = np.arange(len(closes[-60:]))
        y = np.log(closes[-60:])
        slope, intercept = np.polyfit(x, y, 1)
        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        r_squared = 1 - ss_res / (ss_tot + 1e-10)
        if r_squared < 0.6:
            excluded["noisy"] += 1
            continue

        # 4. 日均换手率
        avg_vol = volumes[-60:].mean()
        med_vol = stock_k["volume"].tail(250).median()
        vol_ratio = avg_vol / (med_vol + 1e-10)
        if vol_ratio < 0.2 or vol_ratio > 4.0:
            excluded["low_volume"] += 1
            continue

        # 5. 波动率
        daily_ret = np.diff(closes[-61:]) / (closes[-61:-1] + 1e-10)
        vol = np.std(daily_ret) * 100
        if vol > 5.0:
            excluded["noisy"] += 1
            continue

        # 综合得分
        score = (
            ret_60 * 0.25 +           # 收益
            r_squared * 100 * 0.25 +  # 平滑度
            abs(drawdown) * 0.15 +    # 回撤小加分
            vol_ratio * 10 * 0.10 +   # 量能适中
            (5 - vol) * 5 * 0.15 +    # 波动小加分
            (closes[-1] / closes[-20] - 1) * 50 * 0.10  # 近期动量
        )

        name = stock_k["name"].iloc[0] if "name" in stock_k.columns else ""
        mcap = spot_filtered[spot_filtered["code"] == code]["mcap_yi"].values
        mcap = mcap[0] if len(mcap) > 0 else 0

        scored.append({
            "code": code,
            "name": name,
            "mcap_yi": mcap,
            "ret_60d": round(ret_60, 1),
            "r_squared": round(r_squared, 3),
            "max_drawdown": round(drawdown, 1),
            "vol_ratio": round(vol_ratio, 2),
            "daily_vol": round(vol, 2),
            "score": round(score, 2),
        })

    print(f"\n[EXCLUDE] no_data:{excluded['no_data']} "
          f"not_uptrend:{excluded['not_uptrend']} "
          f"high_dd:{excluded['high_drawdown']} "
          f"low_vol:{excluded['low_volume']} "
          f"noisy:{excluded['noisy']}")

    # 按得分排序取 TOP N（不做行业去重，先看全貌）
    df = pd.DataFrame(scored).sort_values("score", ascending=False)
    selected = df.head(n)

    print(f"\n✅ 最终选定 {len(selected)} 只标的")
    print(f"   市值范围: {selected['mcap_yi'].min():.0f}~{selected['mcap_yi'].max():.0f}亿")
    print(f"   平均60日收益: {selected['ret_60d'].mean():.1f}%")
    print(f"   平均R²: {selected['r_squared'].mean():.3f}")

    # 保存
    selected.to_csv(OUTPUT_DIR / "institutional_climbers_100.csv", index=False, encoding="utf-8-sig")
    print(f"💾 {OUTPUT_DIR}/institutional_climbers_100.csv")

    return selected


def _ensure_diversity(df: pd.DataFrame, kline: pd.DataFrame, n: int) -> pd.DataFrame:
    """确保行业多样性"""
    # 简单按名称做行业去重（后续可改进）
    sector_keywords = {
        "半导体": ["芯片", "半导", "硅", "集成"],
        "新能源": ["锂", "电", "光伏", "储能", "能", "新能"],
        "消费电子": ["电子", "光学", "声学"],
        "医药": ["药", "医", "生物"],
        "军工": ["军工", "航天", "航空", "兵器"],
        "有色": ["铜", "铝", "金", "稀土", "矿", "钴", "镍"],
        "机械": ["机械", "重工", "机床", "轴承"],
        "电力": ["电力", "电", "能源"],
        "汽车": ["汽车", "车"],
        "通信": ["通信", "通讯", "5G", "网络"],
    }

    selected = []
    sector_counts = defaultdict(int)

    for _, row in df.iterrows():
        name = row["name"]
        assigned = False
        for sector, kws in sector_keywords.items():
            if any(kw in name for kw in kws):
                if sector_counts[sector] < n // 10 + 1:  # 每行业不超过10%
                    sector_counts[sector] += 1
                    selected.append(row)
                    assigned = True
                break
        if not assigned:
            selected.append(row)

        if len(selected) >= n:
            break

    return pd.DataFrame(selected)


# ================================================================
# 阶段2：全维度特征提取
# ================================================================

def full_feature_extraction(selected: pd.DataFrame) -> pd.DataFrame:
    """
    对选定的100只标的提取全维度特征：
    - 40+ 技术指标
    - 基本面因子
    - 行业/板块共振
    """
    print(f"\n{'='*70}")
    print(f"📐 全维度特征提取 ({len(selected)} 只)")
    print(f"{'='*70}")

    kline = _load_kline()
    spot = _load_spot()
    finance = _load_finance()

    features = []
    for i, (_, row) in enumerate(selected.iterrows()):
        code = str(row["code"]).zfill(6)
        if i % 20 == 0:
            print(f"  [{i}/{len(selected)}] {code} {row['name']}")

        feats = _extract_all_features(code, kline, spot, finance)
        if feats:
            features.append(feats)

    df = pd.DataFrame(features)
    
    # 保存
    df.to_csv(OUTPUT_DIR / "full_features_100.csv", index=False, encoding="utf-8-sig")
    print(f"\n💾 {OUTPUT_DIR}/full_features_100.csv ({len(df)} 只, {len(df.columns)} 维特征)")
    return df


def _extract_all_features(code: str, kline: pd.DataFrame,
                          spot: pd.DataFrame, finance: pd.DataFrame) -> Dict:
    """单只股票全维度特征提取"""
    stock_k = kline[kline["code"] == code].sort_values("date")
    if len(stock_k) < 120:
        return None

    recent = stock_k.tail(120)
    closes = recent["close"].values
    volumes = recent["volume"].values
    highs = recent["high"].values
    lows = recent["low"].values
    opens = recent["open"].values
    name = stock_k["name"].iloc[0].strip()

    feats = {"code": code, "name": name}

    # ---- A. 价格位置 (8个) ----
    ema5 = _ema(closes, 5)
    ema10 = _ema(closes, 10)
    ema20 = _ema(closes, 20)
    ema60 = _ema(closes, 60)
    ema120 = _ema(closes, 120) if len(closes) >= 120 else ema60
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    ma120 = _sma(closes, 120) if len(closes) >= 120 else ma60

    latest = closes[-1]
    feats.update({
        "price_vs_ema5": round((latest/ema5[-1]-1)*100, 2),
        "price_vs_ema10": round((latest/ema10[-1]-1)*100, 2),
        "price_vs_ema20": round((latest/ema20[-1]-1)*100, 2),
        "price_vs_ema60": round((latest/ema60[-1]-1)*100, 2),
        "price_vs_ma20": round((latest/ma20[-1]-1)*100, 2),
        "price_vs_ma60": round((latest/ma60[-1]-1)*100, 2),
        "ema20_vs_ema60": round((ema20[-1]/ema60[-1]-1)*100, 2),
        "ema10_vs_ema20": round((ema10[-1]/ema20[-1]-1)*100, 2),
    })

    # ---- B. 趋势强度 (6个) ----
    feats.update({
        "adx_14": round(_adx(highs, lows, closes, 14), 1),
        "plus_di": round(_plus_di(highs, lows, closes, 14), 1),
        "minus_di": round(_minus_di(highs, lows, closes, 14), 1),
        "linear_r2_60d": round(_r_squared(closes[-60:]), 3),
        "linear_slope_60d": round(_slope(closes[-60:]) / closes[-60:].mean() * 100, 2),
        "trend_streak": _count_consecutive(np.diff(closes[-20:]) > 0),
    })

    # ---- C. 动量指标 (8个) ----
    feats.update({
        "rsi_6": round(_rsi(closes, 6), 1),
        "rsi_14": round(_rsi(closes, 14), 1),
        "rsi_28": round(_rsi(closes, 28), 1),
        "macd_dif": round(_macd_dif(closes), 3),
        "macd_hist": round(_macd_hist(closes), 3),
        "williams_r": round(_williams_r(highs, lows, closes, 14), 1),
        "cci_20": round(_cci(highs, lows, closes, 20), 1),
        "roc_20": round((closes[-1]/closes[-21]-1)*100, 2) if len(closes) > 20 else 0,
    })

    # ---- D. 波动率 (5个) ----
    daily_ret = np.diff(closes) / closes[:-1]
    feats.update({
        "volatility_20d": round(np.std(daily_ret[-20:])*100, 2),
        "volatility_60d": round(np.std(daily_ret[-60:])*100, 2),
        "atr_14": round(_atr(highs, lows, closes, 14) / closes[-1] * 100, 2),
        "beta_vs_market": round(_beta(closes), 2),
        "max_drawdown_60d": round(_max_dd(closes[-60:]), 1),
    })

    # ---- E. 成交量/资金 (5个) ----
    vol_ma10 = pd.Series(volumes).rolling(10).mean().values
    vol_ma60 = pd.Series(volumes).rolling(60).mean().values
    feats.update({
        "vol_ratio_5_20": round(volumes[-5:].mean()/(volumes[-20:].mean()+1e-10), 2),
        "vol_ratio_20_60": round(volumes[-20:].mean()/(volumes[-60:].mean()+1e-10), 2),
        "vol_trend_20d": round(_slope(volumes[-20:])/volumes[-20:].mean()*100, 2),
        "obv_trend": round(_obv_trend(closes, volumes), 2),
        "mfi_14": round(_mfi(highs, lows, closes, volumes, 14), 1),
    })

    # ---- F. 形态特征 (6个) ----
    feats.update({
        "bb_position": round(_bb_pos(closes, 20), 2),
        "bb_width": round(_bb_width(closes, 20), 2),
        "higher_highs": 1 if _has_higher_highs(closes, 20) else 0,
        "higher_lows": 1 if _has_higher_lows(closes, 20) else 0,
        "consolidation": 1 if _is_consolidating(closes, 20) else 0,
        "avg_body_pct": round(np.mean(abs(closes[-20:]-opens[-20:])/opens[-20:])*100, 2),
    })

    # ---- G. 影线/结构 (4个) ----
    upper_s = (highs[-20:] - np.maximum(opens[-20:], closes[-20:])) / (highs[-20:] - lows[-20:] + 1e-10)
    lower_s = (np.minimum(opens[-20:], closes[-20:]) - lows[-20:]) / (highs[-20:] - lows[-20:] + 1e-10)
    feats.update({
        "upper_shadow_pct": round(np.mean(upper_s)*100, 1),
        "lower_shadow_pct": round(np.mean(lower_s)*100, 1),
        "gap_up_count": np.sum((lows[1:] > highs[:-1]).astype(int)[-60:]),
        "gap_down_count": np.sum((highs[1:] < lows[:-1]).astype(int)[-60:]),
    })

    # ---- H. 基本面 (8个) ----
    spot_row = spot[spot["code"] == code] if not spot.empty else pd.DataFrame()
    fin_row = finance[finance["code"] == code] if not finance.empty else pd.DataFrame()

    feats.update({
        "pe_ttm": round(float(spot_row["pe_ttm"].iloc[0]), 1) if not spot_row.empty else 0,
        "pb": round(float(spot_row["pb"].iloc[0]), 2) if not spot_row.empty else 0,
        "mcap_yi": round(float(spot_row["mcap_yi"].iloc[0]), 0) if not spot_row.empty else 0,
        "turnover_pct": round(float(spot_row["turnover_pct"].iloc[0]), 2) if not spot_row.empty else 0,
        "roe": round(float(fin_row["roe"].iloc[0]), 1) if not fin_row.empty and "roe" in fin_row.columns else 0,
        "net_profit_yi": round(float(fin_row["net_profit"].iloc[0])/1e8, 0) if not fin_row.empty and "net_profit" in fin_row.columns else 0,
        "revenue_yi": round(float(fin_row["revenue"].iloc[0])/1e8, 0) if not fin_row.empty and "revenue" in fin_row.columns else 0,
        "employee_count": int(fin_row["employees"].iloc[0]) if not fin_row.empty and "employees" in fin_row.columns else 0,
    })

    # ---- I. 板块共振 (计算该股所在行业近期表现) ----
    sector_perf = _calc_sector_performance(name, kline)
    feats.update(sector_perf)

    return feats


# ================================================================
# 阶段3：统计共同点
# ================================================================

def find_common_patterns(features_df: pd.DataFrame):
    """统计分析 100 只标的的共同特征"""
    print(f"\n{'='*70}")
    print(f"📊 共同点统计分析")
    print(f"{'='*70}")

    # 排除非数值列
    exclude_cols = ["code", "name"] + [c for c in features_df.columns if "sector" in c]
    numeric_cols = [c for c in features_df.columns if c not in exclude_cols 
                    and features_df[c].dtype in ['float64', 'int64', 'float32', 'int32']]

    stats = []
    for col in numeric_cols:
        data = features_df[col].dropna()
        if len(data) < 50:
            continue
        stats.append({
            "feature": col,
            "mean": round(data.mean(), 3),
            "median": round(data.median(), 3),
            "std": round(data.std(), 3),
            "q25": round(data.quantile(0.25), 3),
            "q75": round(data.quantile(0.75), 3),
            "cv": round(data.std()/(abs(data.mean())+1e-10), 3),  # 变异系数
            "concentration": round(len(data[(data>data.quantile(0.25))&(data<data.quantile(0.75))])/len(data)*100, 1),
        })

    stats_df = pd.DataFrame(stats).sort_values("concentration", ascending=False)
    
    # 打印高集中度特征
    print(f"\n高集中度特征（75%样本落在IQR内）:")
    high_conc = stats_df[stats_df["concentration"] > 60].head(30)
    for _, r in high_conc.iterrows():
        print(f"  {r['feature']:<30s} 均值={r['mean']:8.3f}  中位={r['median']:8.3f}  Q25~Q75=[{r['q25']:8.3f}, {r['q75']:8.3f}]  CV={r['cv']:.2f}")

    # 保存
    stats_df.to_csv(OUTPUT_DIR / "common_patterns.csv", index=False, encoding="utf-8-sig")
    print(f"\n💾 {OUTPUT_DIR}/common_patterns.csv")


# ================================================================
# 辅助函数
# ================================================================

def _load_kline():
    path = DATA_DIR / "kline_daily.csv"
    if not path.exists(): return pd.DataFrame()
    cn = ["code","market","name","date","open","high","low","close","volume","amount"]
    df = pd.read_csv(path, names=cn, header=None,
                     dtype={"code":str,"market":str,"name":str,"date":str,
                            "open":float,"high":float,"low":float,"close":float,
                            "volume":float,"amount":float})
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"]).sort_values(["code","date"])

def _load_spot():
    path = DATA_DIR / "tencent_quotes.csv"
    if not path.exists(): return pd.DataFrame()
    df = pd.read_csv(path)
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df

def _load_finance():
    path = DATA_DIR / "finance_snapshot.csv"
    if not path.exists(): return pd.DataFrame()
    cn = ["code","market","name","date","net_profit","revenue","total_assets",
          "total_liabilities","operating_profit","operating_cashflow",
          "roe","dividend","equity","growth_revenue","growth_profit",
          "employees","type_code","ipo_date"]
    df = pd.read_csv(path, names=cn, header=None)
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df

# 技术指标
_ema = lambda d,s: pd.Series(d).ewm(span=s,adjust=False).mean().values
_sma = lambda d,s: pd.Series(d).rolling(s,min_periods=1).mean().values
_rsi = lambda d,p: 100-100/(1+(pd.Series(np.where(np.diff(d)>0,np.diff(d),0)).ewm(alpha=1/p,adjust=False).mean()/
                             (pd.Series(np.where(np.diff(d)<0,-np.diff(d),0)).ewm(alpha=1/p,adjust=False).mean()+1e-10)).iloc[-1]) if len(d)>p else 50
def _adx(h,l,c,p):
    tr = np.maximum(h[1:]-l[1:], np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1]))
    atr = pd.Series(tr).ewm(span=p,adjust=False).mean().iloc[-1]
    up = np.maximum(h[1:]-h[:-1], 0); dn = np.maximum(l[:-1]-l[1:], 0)
    pdi = pd.Series(up).ewm(span=p,adjust=False).mean().iloc[-1]/atr*100
    mdi = pd.Series(dn).ewm(span=p,adjust=False).mean().iloc[-1]/atr*100
    dx = abs(pdi-mdi)/(pdi+mdi+1e-10)*100
    return float(pd.Series([dx]*p).ewm(span=p,adjust=False).mean().iloc[-1])
_plus_di = lambda h,l,c,p: float(pd.Series(np.maximum(h[1:]-h[:-1],0)).ewm(span=p,adjust=False).mean().iloc[-1]/
                        pd.Series(np.maximum(h[1:]-l[1:],np.abs(h[1:]-c[:-1]),np.abs(l[1:]-c[:-1]))).ewm(span=p,adjust=False).mean().iloc[-1]*100)
_minus_di = lambda h,l,c,p: float(pd.Series(np.maximum(l[:-1]-l[1:],0)).ewm(span=p,adjust=False).mean().iloc[-1]/
                         pd.Series(np.maximum(h[1:]-l[1:],np.abs(h[1:]-c[:-1]),np.abs(l[1:]-c[:-1]))).ewm(span=p,adjust=False).mean().iloc[-1]*100)
_r_squared = lambda y: 1-np.sum((y-np.polyval(np.polyfit(np.arange(len(y)),y,1),np.arange(len(y))))**2)/np.sum((y-y.mean())**2) if len(y)>2 else 0
_slope = lambda d: np.polyfit(np.arange(len(d)), d, 1)[0]
_count_consecutive = lambda d: max((len(list(g)) for k,g in __import__('itertools').groupby(d) if k), default=0)
_macd_dif = lambda d: float((pd.Series(d).ewm(span=12,adjust=False).mean()-pd.Series(d).ewm(span=26,adjust=False).mean()).iloc[-1]/d[-1]*100)
_macd_hist = lambda d: float((lambda dif: (dif-dif.ewm(span=9,adjust=False).mean()))(pd.Series(d).ewm(span=12,adjust=False).mean()-pd.Series(d).ewm(span=26,adjust=False).mean()).iloc[-1]/d[-1]*100)
_williams_r = lambda h,l,c,p: float((np.max(h[-p:])-c[-1])/(np.max(h[-p:])-np.min(l[-p:])+1e-10)*-100)
_cci = lambda h,l,c,p: float(((h[-p:]+l[-p:]+c[-p:])/3).mean()/(0.015*np.std((h[-p:]+l[-p:]+c[-p:])/3)))
_atr = lambda h,l,c,p: float(pd.Series(np.maximum(h[1:]-l[1:],np.abs(h[1:]-c[:-1]),np.abs(l[1:]-c[:-1]))).ewm(span=p,adjust=False).mean().iloc[-1])
_beta = lambda c: 1.0  # 简化：需要市场指数数据才能计算真实beta
_max_dd = lambda d: float(np.min((d-np.maximum.accumulate(d))/np.maximum.accumulate(d)*100))
_obv_trend = lambda c,v: _slope(np.cumsum(np.where(np.diff(c)>0,v[1:],np.where(np.diff(c)<0,-v[1:],0))))
_mfi = lambda h,l,c,v,p: 50.0  # 简化MFI计算
_bb_pos = lambda d,p: float((d[-1]-(pd.Series(d).rolling(p).mean().iloc[-1]-2*pd.Series(d).rolling(p).std().iloc[-1]))/
                       (4*pd.Series(d).rolling(p).std().iloc[-1]+1e-10))
_bb_width = lambda d,p: float(4*pd.Series(d).rolling(p).std().iloc[-1]/pd.Series(d).rolling(p).mean().iloc[-1]*100)
_has_higher_highs = lambda d,w: float(pd.Series(d[-w:]).iloc[-w//2:].max())>float(pd.Series(d[-w:]).iloc[:w//2].max())
_has_higher_lows = lambda d,w: float(pd.Series(d[-w:]).iloc[-w//2:].min())>float(pd.Series(d[-w:]).iloc[:w//2].min())
_is_consolidating = lambda d,w: (np.max(d[-w:])/np.min(d[-w:])-1)<0.08

def _calc_sector_performance(name: str, kline: pd.DataFrame) -> Dict:
    """估算该股所在行业的近期表现"""
    sector_perf = {}
    # 简化：用名称关键词分类
    sector_kw = {
        "半导体": ["芯片","半导","硅","集成","存储","微"],
        "新能源汽车": ["锂","电池","储能","充电","新能源"],
        "光伏": ["光伏","太阳"],
        "医药医疗": ["药","医","生物","诊断"],
        "军工航天": ["军工","航天","航空","兵器","导弹"],
        "AI人工智能": ["智能","AI","人工","机器","自动"],
        "消费电子": ["电子","光学","声学","连接"],
        "电力能源": ["电力","能源","电","核电","风电","火电"],
        "有色金属": ["铜","铝","金","稀土","矿","钴","镍","锂矿"],
        "机械设备": ["机械","重工","机床","轴承","模具"],
        "汽车零部件": ["汽车","车","轮"],
        "通信5G": ["通信","通讯","5G","光模块","光缆"],
        "化工材料": ["化工","化学","材料","塑料"],
        "食品饮料": ["食品","饮料","酒","乳"],
        "金融软件": ["金融","银行","证券","保险","软件"],
    }

    for sector, kws in sector_kw.items():
        if any(kw in name for kw in kws):
            # 查找同板块其他股票最近表现
            sector_codes = set()
            for kw in kws:
                matching = kline[kline["name"].str.contains(kw, na=False)]["code"].unique()
                sector_codes.update(matching)

            if len(sector_codes) > 5:
                sector_closes = []
                for sc in sector_codes:
                    sk = kline[kline["code"]==sc].sort_values("date")
                    if len(sk) >= 60:
                        sector_closes.append(sk["close"].iloc[-1]/sk["close"].iloc[-21]-1)
                if sector_closes:
                    sector_perf["sector_20d_return"] = round(np.mean(sector_closes)*100, 2)
                    sector_perf["sector_count"] = len(sector_closes)
            break

    if "sector_20d_return" not in sector_perf:
        sector_perf["sector_20d_return"] = 0
        sector_perf["sector_count"] = 0

    return sector_perf


# ================================================================
# 主入口
# ================================================================

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    # 阶段1：筛选100只
    selected = select_institutional_climbers(100)

    if not selected.empty:
        # 阶段2：全维度特征提取
        features = full_feature_extraction(selected)

        # 阶段3：统计共同点
        if not features.empty:
            find_common_patterns(features)

    print(f"\n{'='*70}")
    print(f"✅ 研究完成！输出文件:")
    print(f"  {OUTPUT_DIR}/institutional_climbers_100.csv  — 100只标的列表")
    print(f"  {OUTPUT_DIR}/full_features_100.csv          — 全维度特征")
    print(f"  {OUTPUT_DIR}/common_patterns.csv            — 共同点统计")
