"""
策略 v3 — 超卖反转
基于2025-2026大市值牛股研究结论：
  - 最大涨幅来自超卖反转，而非趋势延续
  - 牛股起涨特征：RSI(14)~24, BB跌破下轨, 60日跌-20%, 仅38%多头排列
  - 策略逻辑：超卖检测 + 反转确认 = 买入
  
v2 致命缺陷 (命中率仅22.7%)：
  - 要求趋势确认（EMA多头/R²高/60日涨>8%）
  - 但77%的大牛股起涨时处于空头/下跌状态
"""
import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass
import warnings
from config import DATA_DIR, OUTPUT_DIR
warnings.filterwarnings("ignore")

# ============================================================
# 滚动窗口辅助函数
# ============================================================

def _rolling_r2(arr):
    """滚动窗口R²（对数价格线性拟合）"""
    if len(arr) < 30:
        return np.nan
    y = np.log(arr + 1e-10)
    x = np.arange(len(arr))
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = np.sum((y - pred)**2)
    ss_tot = np.sum((y - y.mean())**2)
    return max(0, min(1, 1 - ss_res / (ss_tot + 1e-10)))


def _rolling_max_dd(arr):
    """滚动窗口最大回撤%"""
    if len(arr) < 20:
        return np.nan
    peak = np.maximum.accumulate(arr)
    return float(np.min((arr - peak) / peak * 100))


def _rolling_bb_position(arr, period=20, std_dev=2.0):
    """滚动窗口布林带位置"""
    if len(arr) < period:
        return np.nan
    mid = np.mean(arr[-period:])
    std = np.std(arr[-period:])
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    if upper == lower:
        return 0.5
    return float((arr[-1] - lower) / (upper - lower))


# ============================================================
# 策略参数 v3 — 超卖反转
# ============================================================

class Config:
    """v3 超卖反转策略参数 — 平衡版"""
    
    # === 超卖检测 ===
    MAX_RSI_14 = 30            # RSI(14) 上限
    MAX_RSI_6 = 20             # RSI(6) 上限
    MAX_BB_POSITION = 0.08     # 布林带位置上限
    MAX_DRAWDOWN_60D = -15     # 60日回撤上限%
    
    # === 反转确认 ===
    MIN_PRICE_CHG = 1.0        # 今日至少涨1%（有意义反弹）
    MIN_VOL_RATIO = 1.3        # 量比下限
    RSI6_MIN_DELTA = 2.0       # RSI(6)至少回升2个点
    
    # === 趋势位置 ===
    PRICE_BELOW_MA20 = True    # 价格 < MA20（不能追高）
    PRICE_BELOW_MA60 = True    # 价格 < MA60
    
    # === 质量过滤 ===
    MIN_MCAP = 100             # 市值≥100亿
    
    # === 板块排除 ===
    EXCLUDE_SECTORS = ["银行","证券","保险","地产","金融","白酒","中药","教育","猪肉"]
    
    # === 风控 ===
    STOP_LOSS = -0.07          # 止损-7%（收紧）
    TAKE_PROFIT = 0.15         # 止盈+15%
    TRAILING_STOP = 0.12       # 移动止损触发: 从最高涨>12%后，回撤3%
    TRAILING_DD = -0.03        # 
    TIME_STOP_DAYS = 20        # 时间止损缩短
    TIME_STOP_RETURN = 0.02    # 时间止损阈值 2%
    MAX_POSITIONS = 8          # 增加持仓分散风险
    SINGLE_POSITION_PCT = 0.10 # 单票仓位降到10%
    
    # === 回测 ===
    START_DATE = "2025-01-01"
    END_DATE = "2026-06-12"
    INITIAL_CAPITAL = 1_000_000


@dataclass
class TradeV3:
    code: str; name: str
    entry_date: str; exit_date: str
    entry_price: float; exit_price: float
    exit_reason: str
    return_pct: float; hold_days: int


# ============================================================
# 主回测入口
# ============================================================

def run_backtest_v3():
    """v3 超卖反转策略回测"""
    C = Config()
    print("=" * 70)
    print("📊 策略 v3 回测 — 超卖反转")
    print(f"   {C.START_DATE} ~ {C.END_DATE}")
    print(f"   超卖: RSI(14)≤{C.MAX_RSI_14} | BB≤{C.MAX_BB_POSITION} | 回撤≤{C.MAX_DRAWDOWN_60D}%")
    print(f"   反转: 收阳 | 量比≥{C.MIN_VOL_RATIO} | RSI6回升")
    print("=" * 70)
    
    # 1. 加载数据
    kline, spot = _load_all()
    if kline.empty:
        return None, None
    
    # 2. 预计算指标
    print("[PREP] 预计算指标...")
    kline = _precompute_indicators_v3(kline)
    print(f"[PREP] ✅ {kline['code'].nunique()} 只, {len(kline):,} 行")
    
    # 3. 构建排除集
    spot["code"] = spot["code"].astype(str).str.zfill(6)
    excluded_codes = set()
    for kw in C.EXCLUDE_SECTORS:
        excluded_codes.update(spot[spot["name"].str.contains(kw, na=False)]["code"].values)
    excluded_codes.update(spot[spot["name"].str.contains("ST|退", na=False)]["code"].values)
    spot_map = spot.set_index("code")[["name","mcap_yi","pe_ttm","turnover_pct"]].to_dict("index")
    
    # 4. 交易日历
    trading_days = sorted(kline["date"].unique())
    trading_days = [d for d in trading_days
                    if C.START_DATE <= d.strftime("%Y-%m-%d") <= C.END_DATE]
    print(f"[CAL] {len(trading_days)} 个交易日")
    
    # 5. 逐日回测
    positions = []
    trades = []
    cash = C.INITIAL_CAPITAL
    equity_curve = []
    
    for day_idx, today in enumerate(trading_days):
        today_str = today.strftime("%Y-%m-%d")
        
        if day_idx % 30 == 0:
            n_trades = len(trades)
            wins = sum(1 for t in trades if t.return_pct > 0)
            wr = f"{wins/n_trades*100:.0f}%" if n_trades > 0 else "-"
            print(f"  [{today_str}] d{day_idx:3d}/{len(trading_days)} | "
                  f"pos:{len(positions)} | trades:{n_trades} wr:{wr}")
        
        # 5a. 更新持仓
        positions, closed = _update_positions_v3(positions, kline, today, today_str, C)
        for t in closed:
            trades.append(t)
            cash += t.exit_price * getattr(t, '_shares', 0)
        
        # 5b. 计算权益
        unrealized = 0
        for p in positions:
            row = kline[(kline["code"]==p["code"]) & (kline["date"]==today)]
            if not row.empty:
                unrealized += (row.iloc[-1]["close"] - p["entry_price"]) * p["shares"]
        equity = cash + unrealized
        equity_curve.append({"date": today_str, "equity": equity})
        
        # 5c. 开新仓
        if len(positions) < C.MAX_POSITIONS and cash > C.INITIAL_CAPITAL * 0.05:
            held_codes = set(p["code"] for p in positions)
            signals = _daily_scan_v3(kline, today, excluded_codes, spot_map, held_codes, C)
            for sig in signals:
                if len(positions) >= C.MAX_POSITIONS:
                    break
                cost = min(cash * C.SINGLE_POSITION_PCT, cash * 0.95 / (C.MAX_POSITIONS - len(positions)))
                shares = int(cost / sig["close"])
                if shares == 0:
                    continue
                cash -= shares * sig["close"]
                positions.append({
                    "code": sig["code"], "entry_price": sig["close"],
                    "entry_date": today_str, "shares": shares,
                    "name": sig.get("name", ""),
                })
    
    # 6. 期末清仓
    last_day = trading_days[-1]
    last_str = last_day.strftime("%Y-%m-%d")
    for p in positions:
        row = kline[(kline["code"]==p["code"]) & (kline["date"]==last_day)]
        if not row.empty:
            ep = row.iloc[-1]["close"]
            ret = (ep - p["entry_price"]) / p["entry_price"] * 100
            t = TradeV3(p["code"], p["name"], p["entry_date"], last_str,
                         p["entry_price"], ep, "end", ret,
                         (last_day-pd.Timestamp(p["entry_date"])).days)
            setattr(t, '_shares', p["shares"])
            trades.append(t)
            cash += ep * p["shares"]
    
    # 7. 输出
    _print_results(trades, equity_curve, C)
    return trades, equity_curve


# ============================================================
# 每日扫描 v3 — 超卖反转
# ============================================================

def _daily_scan_v3(kline, today, excluded_codes, spot_map, held_codes, C):
    """
    v3 选股逻辑：
    1. 超卖检测：RSI低 + BB下轨 + 大幅回撤
    2. 反转确认：收阳 + 放量 + RSI6回升
    """
    today_data = kline[kline["date"] == today]
    if today_data.empty:
        return []
    
    candidates = []
    for _, row in today_data.iterrows():
        code = row["code"]
        
        # 快速过滤
        if code in excluded_codes:
            continue
        if code in held_codes:
            continue
        
        info = spot_map.get(code, {})
        mcap = info.get("mcap_yi", 0) or 0
        if mcap < C.MIN_MCAP:
            continue
        turnover = info.get("turnover_pct", 0) or 0
        if turnover < 0.3:  # 流动性过滤
            continue
        
        close = row["close"]
        open_p = row.get("open", close)
        
        # === 超卖检测（收紧） ===
        rsi14 = row.get("rsi_14_d", 50) or 50
        rsi6 = row.get("rsi_6_d", 50) or 50
        bb_pos = row.get("bb_pos_d", 0.5)
        max_dd_60 = row.get("max_dd_60d", 0) or -100
        
        if pd.isna(rsi14) or pd.isna(rsi6) or pd.isna(bb_pos) or pd.isna(max_dd_60):
            continue
        
        # 超卖四条件
        if rsi14 > C.MAX_RSI_14:
            continue
        if rsi6 > C.MAX_RSI_6:
            continue
        if bb_pos > C.MAX_BB_POSITION:
            continue
        if max_dd_60 > C.MAX_DRAWDOWN_60D:
            continue
        
        # === 趋势位置（必须在均线下方，不能追已涨起来的） ===
        ma20 = row.get("ma20_d", close)
        ma60 = row.get("ma60_d", close)
        if C.PRICE_BELOW_MA20 and close > ma20:
            continue
        if C.PRICE_BELOW_MA60 and close > ma60:
            continue
        
        # === 反转确认 ===
        prev_close = row.get("prev_close_d", close)
        price_chg_pct = (close / prev_close - 1) * 100 if prev_close > 0 else 0
        if price_chg_pct < C.MIN_PRICE_CHG:
            continue
        
        # 实体阳线（close > open）比单纯收阳更有意义
        if close <= open_p:
            continue
        
        # 量比
        vol_ratio = row.get("vol_ratio_d", 1.0) or 1.0
        if vol_ratio < C.MIN_VOL_RATIO:
            continue
        
        # RSI6 回升幅度 >= MIN_DELTA
        prev_rsi6 = row.get("prev_rsi6_d", 50) or 50
        rsi6_delta = rsi6 - prev_rsi6
        if rsi6_delta < C.RSI6_MIN_DELTA:
            continue
        
        # === 评分 ===
        # 超卖深度分 (40%)
        rsi_score = max(0, (C.MAX_RSI_14 - rsi14) / C.MAX_RSI_14) * 25
        bb_score = max(0, (C.MAX_BB_POSITION - bb_pos) / 0.5) * 10  # 越深越好
        dd_score = max(0, (-max_dd_60 + C.MAX_DRAWDOWN_60D) / 30) * 5  # 回撤深度
        
        # 反转力度分 (35%)
        reversal_score = min(price_chg_pct * 3, 15)  # 涨幅越大越好，上限15
        rsi_delta = rsi6 - prev_rsi6
        rsi_turn_score = max(0, rsi_delta) * 2  # RSI回升幅度
        
        # 量能确认分 (15%)
        vol_score = min(vol_ratio - 1, 1.5) * 10
        
        # 质量分 (10%)
        quality_score = min(mcap / 1000, 1.0) * 10
        
        score = rsi_score + bb_score + dd_score + reversal_score + rsi_turn_score + vol_score + quality_score
        
        candidates.append({
            "code": code, "close": close,
            "name": info.get("name", ""),
            "score": round(score, 1),
            "rsi14": round(rsi14, 1),
            "bb_pos": round(bb_pos, 3),
            "max_dd_60": round(max_dd_60, 1),
            "price_chg": round(price_chg_pct, 2),
            "vol_ratio": round(vol_ratio, 2),
            "mcap": mcap,
        })
    
    candidates.sort(key=lambda x: x["score"], reverse=True)
    
    # 去重（同一股票只选一次） + 取TOP
    seen = set()
    unique = []
    for c in candidates:
        if c["code"] not in seen:
            seen.add(c["code"])
            unique.append(c)
        if len(unique) >= C.MAX_POSITIONS * 2:
            break
    
    return unique[:C.MAX_POSITIONS]


# ============================================================
# 持仓更新 v3
# ============================================================

def _update_positions_v3(positions, kline, today, today_str, C):
    """更新持仓：止损/止盈/移动止损/时间止损"""
    closed, surviving = [], []
    for p in positions:
        row = kline[(kline["code"]==p["code"]) & (kline["date"]==today)]
        if row.empty:
            surviving.append(p)
            continue
        
        cp = row.iloc[-1]["close"]
        hold = (today - pd.Timestamp(p["entry_date"])).days
        ret = (cp - p["entry_price"]) / p["entry_price"]
        
        # 更新最高价
        high_water = p.get("high_water", p["entry_price"])
        if cp > high_water:
            p["high_water"] = cp
            high_water = cp
        highest_ret = (high_water - p["entry_price"]) / p["entry_price"]
        
        # 1. 止损
        if ret <= C.STOP_LOSS:
            t = TradeV3(p["code"], p["name"], p["entry_date"], today_str,
                         p["entry_price"], cp, "stop_loss", ret*100, hold)
            setattr(t, '_shares', p["shares"]); closed.append(t); continue
        
        # 2. 止盈
        if ret >= C.TAKE_PROFIT:
            t = TradeV3(p["code"], p["name"], p["entry_date"], today_str,
                         p["entry_price"], cp, "take_profit", ret*100, hold)
            setattr(t, '_shares', p["shares"]); closed.append(t); continue
        
        # 3. 移动止损：从最高点回撤超过阈值
        if highest_ret > C.TRAILING_STOP:
            trail_dd = (cp - high_water) / high_water
            if trail_dd <= C.TRAILING_DD:
                t = TradeV3(p["code"], p["name"], p["entry_date"], today_str,
                             p["entry_price"], cp, "trailing_stop", ret*100, hold)
                setattr(t, '_shares', p["shares"]); closed.append(t); continue
        
        # 4. 时间止损
        if hold > C.TIME_STOP_DAYS and ret < C.TIME_STOP_RETURN:
            t = TradeV3(p["code"], p["name"], p["entry_date"], today_str,
                         p["entry_price"], cp, "time_stop", ret*100, hold)
            setattr(t, '_shares', p["shares"]); closed.append(t); continue
        
        surviving.append(p)
    return surviving, closed


# ============================================================
# 指标预计算 v3
# ============================================================

def _precompute_indicators_v3(kline):
    """预计算v3所需指标"""
    df = kline.sort_values(["code","date"]).reset_index(drop=True)
    
    print("  计算均线(EMA20/EMA60/MA20/MA60)...")
    df["ema20_d"] = df.groupby("code")["close"].transform(
        lambda x: x.ewm(span=20, adjust=False).mean())
    df["ema60_d"] = df.groupby("code")["close"].transform(
        lambda x: x.ewm(span=60, adjust=False).mean())
    df["ma20_d"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(20, min_periods=1).mean())
    df["ma60_d"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(60, min_periods=1).mean())
    
    print("  计算RSI(6/14)...")
    def _calc_rsi(series, period):
        if len(series) < period + 1:
            return pd.Series([np.nan]*len(series), index=series.index)
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_g = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_l = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_g / (avg_l + 1e-10)
        return 100 - 100 / (1 + rs)
    
    df["rsi_14_d"] = df.groupby("code")["close"].transform(lambda x: _calc_rsi(x, 14))
    df["rsi_6_d"] = df.groupby("code")["close"].transform(lambda x: _calc_rsi(x, 6))
    
    # RSI(6) 前一日值
    df["prev_rsi6_d"] = df.groupby("code")["rsi_6_d"].shift(1)
    
    print("  计算布林带位置...")
    df["bb_pos_d"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(20, min_periods=10).apply(_rolling_bb_position, raw=True))
    
    print("  计算趋势指标(R²/涨幅/回撤)...")
    df["r2_60d"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(60, min_periods=40).apply(_rolling_r2, raw=True))
    df["ret_60d_pct"] = df.groupby("code")["close"].transform(
        lambda x: x.pct_change(60) * 100)
    df["max_dd_60d"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(60, min_periods=40).apply(_rolling_max_dd, raw=True))
    
    print("  计算量比和前日收盘...")
    # 量比
    df["vol_20ma"] = df.groupby("code")["volume"].transform(
        lambda x: x.rolling(20, min_periods=1).mean())
    df["vol_ratio_d"] = df["volume"] / (df["vol_20ma"] + 1e-10)
    
    # 前日收盘
    df["prev_close_d"] = df.groupby("code")["close"].shift(1)
    
    # 20日高点（用于回踩计算，保留兼容性）
    df["high_20d"] = df.groupby("code")["high"].transform(
        lambda x: x.rolling(20, min_periods=1).max())
    
    return df


# ============================================================
# 结果输出
# ============================================================

def _print_results(trades, equity_curve, C):
    eq = pd.DataFrame(equity_curve)
    if eq.empty:
        print("\n⚠️ 无回测数据"); return
    
    eq["equity"] = pd.to_numeric(eq["equity"], errors="coerce")
    start_eq = eq["equity"].iloc[0]
    end_eq = eq["equity"].iloc[-1]
    total_ret = (end_eq / start_eq - 1) * 100
    peak = np.maximum.accumulate(eq["equity"].values)
    max_dd = np.min((eq["equity"].values - peak) / peak * 100)
    daily_r = eq["equity"].pct_change().dropna()
    sharpe = float((daily_r.mean()/daily_r.std())*np.sqrt(252)) if len(daily_r)>10 else 0
    
    wins = [t.return_pct for t in trades if t.return_pct > 0]
    losses = [t.return_pct for t in trades if t.return_pct <= 0]
    win_rate = len(wins)/len(trades)*100 if trades else 0
    
    print(f"\n{'='*70}")
    print(f"📊 策略 v3 (超卖反转) 回测结果")
    print(f"{'='*70}")
    print(f"  回测期间: {C.START_DATE} ~ {C.END_DATE}")
    print(f"  总交易: {len(trades)} 笔")
    print(f"  胜率: {win_rate:.1f}%")
    print(f"  总收益: {total_ret:+.1f}%")
    print(f"  最大回撤: {max_dd:.1f}%")
    print(f"  夏普: {sharpe:.2f}")
    if wins:
        print(f"  平均盈利: +{np.mean(wins):.2f}%")
        print(f"  最大盈利: +{np.max(wins):.2f}%")
    if losses:
        print(f"  平均亏损: {np.mean(losses):.2f}%")
        print(f"  最大亏损: {np.min(losses):.2f}%")
    if wins and losses:
        profit_factor = np.sum(wins) / abs(np.sum(losses))
        print(f"  盈亏比: {profit_factor:.2f}")
    avg_hold = np.mean([t.hold_days for t in trades]) if trades else 0
    print(f"  平均持仓: {avg_hold:.1f}天")
    
    # 离场原因
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    print(f"  离场分布: {reasons}")
    print(f"  止盈笔数: {reasons.get('take_profit', 0)}")
    print(f"  止损笔数: {reasons.get('stop_loss', 0)}")
    print(f"  时间止损: {reasons.get('time_stop', 0)}")
    
    # 年度收益
    eq["date"] = pd.to_datetime(eq["date"])
    eq["year"] = eq["date"].dt.year
    for yr, grp in eq.groupby("year"):
        if len(grp) > 1:
            yr_ret = (grp["equity"].iloc[-1]/grp["equity"].iloc[0]-1)*100
            print(f"  {yr}年收益: {yr_ret:+.1f}%")
    
    # 最近交易
    if trades:
        print(f"\n  最近10笔交易:")
        for t in trades[-10:]:
            print(f"    {t.entry_date} → {t.exit_date} {t.code} {t.name} "
                  f"{t.return_pct:+.1f}% [{t.exit_reason}]")
    
    # 保存
    eq.to_csv(OUTPUT_DIR / "v3_equity_curve.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([t.__dict__ for t in trades]).to_csv(
        OUTPUT_DIR / "v3_trades.csv", index=False, encoding="utf-8-sig")
    print(f"\n💾 {OUTPUT_DIR}/v3_equity_curve.csv")
    print(f"💾 {OUTPUT_DIR}/v3_trades.csv")


# ============================================================
# 数据加载
# ============================================================

def _load_all():
    """加载K线和行情"""
    kp = DATA_DIR / "kline_daily.csv"
    sp = DATA_DIR / "tencent_quotes.csv"
    kline = spot = pd.DataFrame()
    
    if kp.exists():
        cn = ["code","market","name","date","open","high","low","close","volume","amount"]
        kline = pd.read_csv(kp, names=cn, header=None,
                            dtype={"code":str,"market":str,"name":str,"date":str,
                                   "open":float,"high":float,"low":float,"close":float,
                                   "volume":float,"amount":float})
        kline["code"] = kline["code"].astype(str).str.zfill(6)
        kline["date"] = pd.to_datetime(kline["date"], errors="coerce")
        kline = kline.dropna(subset=["date"]).sort_values(["code","date"])
    
    if sp.exists():
        spot = pd.read_csv(sp)
        spot["code"] = spot["code"].astype(str).str.zfill(6)
    
    return kline, spot


if __name__ == "__main__":
    run_backtest_v3()
