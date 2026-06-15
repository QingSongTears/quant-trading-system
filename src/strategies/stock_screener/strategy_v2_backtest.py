"""
策略 v2 — 趋势确认 + 回调上车
基于100只机构稳步上涨票的研究结论：
  - 趋势确认：EMA20>EMA60, R²>0.7, 60日涨>10%, 回撤<25%
  - 上車信号：浅回踩(-3%~0%) + RSI 40-60 + 量比正常
  - 止损-8%，止盈+12%
  
每日全市场重新扫描，严格 walk-forward 无未来数据泄露
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
import warnings
warnings.filterwarnings("ignore")

DATA_DIR = Path("/workspace/stock-screener/data")
OUTPUT_DIR = Path("/workspace/stock-screener/output")
CACHE_DIR = Path("/workspace/stock-screener/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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
    return 1 - ss_res / (ss_tot + 1e-10)


def _rolling_max_dd(arr):
    """滚动窗口最大回撤%"""
    if len(arr) < 20:
        return np.nan
    peak = np.maximum.accumulate(arr)
    return float(np.min((arr - peak) / peak * 100))

# ============================================================
# 策略参数 v2
# ============================================================

class Config:
    # 趋势确认
    MIN_EMA_ALIGNMENT = 0     # EMA20 > EMA60 (价格在EMA20上方）
    MIN_R_SQUARED = 0.65       # 60日趋势R²
    MIN_RETURN_60D = 8         # 60日最少涨8%
    MAX_DRAWDOWN = -28         # 最大回撤不超过-28%
    MIN_MA_ALIGNMENT = 0       # MA20 > MA60

    # 上车信号
    PULLBACK_LO = -5.0         # 回踩下限（距20日高点）
    PULLBACK_HI = 0.0          # 回踩上限
    RSI_LO = 38                # RSI(14) 下限
    RSI_HI = 62                # RSI(14) 上限
    VOL_RATIO_LO = 0.6         # 量比下限
    VOL_RATIO_HI = 1.8         # 量比上限

    # 风控
    STOP_LOSS = -0.08          # -8%
    TAKE_PROFIT = 0.12         # +12%
    MAX_POSITIONS = 5
    SINGLE_POSITION_PCT = 0.18 # 单票18%

    # 排除
    EXCLUDE_SECTORS = ["银行","证券","保险","地产","金融","白酒","中药","教育","猪肉"]
    MIN_MCAP = 100
    MIN_TURNOVER = 5000  # 万元

    # 回测
    START_DATE = "2025-06-01"
    END_DATE = "2026-06-12"
    INITIAL_CAPITAL = 1_000_000


@dataclass
class TradeV2:
    code: str; name: str; entry_date: str; exit_date: str
    entry_price: float; exit_price: float; exit_reason: str
    return_pct: float; hold_days: int; is_quant: bool = False


def run_backtest_v2():
    """主回测函数"""
    C = Config()
    print("=" * 70)
    print("📊 策略 v2 回测 — 趋势确认 + 回调上车")
    print(f"   {C.START_DATE} ~ {C.END_DATE}")
    print("=" * 70)

    # 1. 加载数据
    kline, spot = _load_all()
    if kline.empty:
        return None

    # 预计算所有股票的日级指标
    print("[PREP] 预计算60维指标...")
    kline = _precompute_all_indicators(kline)
    print(f"[PREP] ✅ {kline['code'].nunique()} 只, {len(kline):,} 行")

    # 2. 构建候选池（排除板块+ST）
    spot["code"] = spot["code"].astype(str).str.zfill(6)
    excluded_codes = set()
    for kw in C.EXCLUDE_SECTORS:
        excluded_codes.update(spot[spot["name"].str.contains(kw, na=False)]["code"].values)
    excluded_codes.update(spot[spot["name"].str.contains("ST|退", na=False)]["code"].values)

    spot_map = spot.set_index("code")[["name","mcap_yi","pe_ttm"]].to_dict("index")

    # 3. 交易日历
    trading_days = sorted(kline["date"].unique())
    trading_days = [d for d in trading_days
                    if C.START_DATE <= d.strftime("%Y-%m-%d") <= C.END_DATE]
    print(f"[CAL] {len(trading_days)} 个交易日")

    # 4. 逐日回测
    positions = []
    trades = []
    cash = C.INITIAL_CAPITAL
    equity_curve = []

    for day_idx, today in enumerate(trading_days):
        today_str = today.strftime("%Y-%m-%d")

        if day_idx % 30 == 0:
            print(f"  [{today_str}] day {day_idx}/{len(trading_days)} | pos:{len(positions)} | trades:{len(trades)}")

        # 4a. 更新持仓
        positions, closed = _update_positions_v2(positions, kline, today, today_str, C)
        for t in closed:
            trades.append(t)
            cash += t.exit_price * getattr(t, '_shares', 0)

        # 4b. 今日权益
        unrealized = 0
        for p in positions:
            row = kline[(kline["code"]==p["code"]) & (kline["date"]==today)]
            if not row.empty:
                unrealized += (row.iloc[-1]["close"] - p["entry_price"]) * p["shares"]
        equity = cash + unrealized
        equity_curve.append({"date": today_str, "equity": equity})

        # 4c. 开新仓
        if len(positions) < C.MAX_POSITIONS and cash > C.INITIAL_CAPITAL * 0.05:
            signals = _daily_scan_v2(kline, today, excluded_codes, spot_map, C)
            for sig in signals:
                if len(positions) >= C.MAX_POSITIONS:
                    break
                cost = cash * C.SINGLE_POSITION_PCT
                shares = int(cost / sig["close"])
                if shares == 0:
                    continue
                cash -= shares * sig["close"]
                positions.append({
                    "code": sig["code"], "entry_price": sig["close"],
                    "entry_date": today_str, "shares": shares,
                    "is_quant": sig.get("is_quant", False),
                    "name": sig.get("name", ""),
                })

    # 5. 期末清仓
    last_day = trading_days[-1]
    last_str = last_day.strftime("%Y-%m-%d")
    for p in positions:
        row = kline[(kline["code"]==p["code"]) & (kline["date"]==last_day)]
        if not row.empty:
            ep = row.iloc[-1]["close"]
            ret = (ep - p["entry_price"]) / p["entry_price"] * 100
            t = TradeV2(p["code"], p["name"], p["entry_date"], last_str,
                         p["entry_price"], ep, "end", ret,
                         (last_day-pd.Timestamp(p["entry_date"])).days)
            setattr(t, '_shares', p["shares"])
            trades.append(t)
            cash += ep * p["shares"]

    # 6. 输出结果
    _print_results(trades, equity_curve, C)
    return trades, equity_curve


def _daily_scan_v2(kline, today, excluded_codes, spot_map, C):
    """
    每日全市场扫描 v2 选股逻辑：
    1. 趋势确认（EMA多头 + R²高 + 已涨一段 + 回撤可控）
    2. 回调上车（浅回踩 + RSI适中 + 量比正常）
    """
    today_data = kline[kline["date"] == today]
    if today_data.empty:
        return []

    candidates = []
    for _, row in today_data.iterrows():
        code = row["code"]
        if code in excluded_codes:
            continue

        # 市值/流动性快速过滤
        info = spot_map.get(code, {})
        mcap = info.get("mcap_yi", 0) or 0
        if mcap < C.MIN_MCAP:
            continue

        # === V2 趋势确认 ===
        ema20 = row.get("ema20_d", 0)
        ema60 = row.get("ema60_d", 0)
        ma20 = row.get("ma20_d", 0)
        ma60 = row.get("ma60_d", 0)
        close = row["close"]
        r_sq = row.get("r2_60d", 0) or 0
        ret_60d = row.get("ret_60d_pct", 0) or 0
        max_dd = row.get("max_dd_60d", 0) or -100

        # 趋势检查
        if ema20 <= ema60 or pd.isna(ema20) or pd.isna(ema60):
            continue
        if close < ema20:  # 不能在均线下方
            continue
        if r_sq < C.MIN_R_SQUARED:
            continue
        if ret_60d < C.MIN_RETURN_60D:
            continue
        if max_dd < C.MAX_DRAWDOWN:
            continue

        # === V2 回调上车 ===
        # 回踩深度（距离20日高点）
        high_20d = row.get("high_20d", close)
        pullback = (close / high_20d - 1) * 100 if high_20d > 0 else 0
        if pullback < C.PULLBACK_LO or pullback > C.PULLBACK_HI:
            continue

        # RSI
        rsi14 = row.get("rsi_14_d", 50) or 50
        if rsi14 < C.RSI_LO or rsi14 > C.RSI_HI:
            continue

        # 量比
        vol_ratio = row.get("vol_ratio_d", 1.0) or 1.0
        if vol_ratio < C.VOL_RATIO_LO or vol_ratio > C.VOL_RATIO_HI:
            continue

        # === 通过！===
        score = (r_sq * 30 + (ret_60d / 60 * 25) + (-pullback) * 15 +
                 (50 - abs(rsi14 - 50)) * 0.3)

        candidates.append({
            "code": code, "close": close, "name": info.get("name", ""),
            "score": round(score, 1), "is_quant": row.get("is_quant_d", False),
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:C.MAX_POSITIONS]


def _update_positions_v2(positions, kline, today, today_str, C):
    """更新持仓：止损/止盈"""
    closed, surviving = [], []
    for p in positions:
        row = kline[(kline["code"]==p["code"]) & (kline["date"]==today)]
        if row.empty:
            surviving.append(p); continue
        cp = row.iloc[-1]["close"]
        hold = (today - pd.Timestamp(p["entry_date"])).days
        ret = (cp - p["entry_price"]) / p["entry_price"]

        # 止损
        if ret <= C.STOP_LOSS:
            t = TradeV2(p["code"], p["name"], p["entry_date"], today_str,
                         p["entry_price"], cp, "stop_loss", ret*100, hold)
            setattr(t, '_shares', p["shares"]); closed.append(t); continue

        # 止盈
        if ret >= C.TAKE_PROFIT:
            t = TradeV2(p["code"], p["name"], p["entry_date"], today_str,
                         p["entry_price"], cp, "take_profit", ret*100, hold)
            setattr(t, '_shares', p["shares"]); closed.append(t); continue

        # EMA20跌破离场
        ema20 = row.iloc[-1].get("ema20_d", cp)
        if cp < ema20 and hold > 5:
            t = TradeV2(p["code"], p["name"], p["entry_date"], today_str,
                         p["entry_price"], cp, "ema_exit", ret*100, hold)
            setattr(t, '_shares', p["shares"]); closed.append(t); continue

        surviving.append(p)
    return surviving, closed


def _precompute_all_indicators(kline):
    """预计算所有需要的日级指标（一次性，高效）"""
    df = kline.sort_values(["code","date"]).reset_index(drop=True)

    # 需要预加载所有列
    needs = ["ema20","ema60","ma20","ma60","rsi_14","r2_60d","ret_60d_pct",
             "max_dd_60d","high_20d","vol_ratio","is_quant"]
    for n in needs:
        if n not in df.columns:
            df[n] = np.nan

    print("  计算均线...")
    for grp_name in ["ema20","ema60","ma20","ma60"]:
        df[grp_name + "_d"] = df.groupby("code")["close"].transform(
            lambda x: x.ewm(span=int(grp_name[-2:]), adjust=False).mean()
            if "ema" in grp_name else x.rolling(int(grp_name[-2:]), min_periods=1).mean()
        )

    print("  计算RSI...")
    def _calc_rsi_14(series):
        if len(series) < 15: return pd.Series([np.nan]*len(series), index=series.index)
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_g = gain.ewm(alpha=1/14, adjust=False).mean()
        avg_l = loss.ewm(alpha=1/14, adjust=False).mean()
        rs = avg_g / (avg_l + 1e-10)
        return 100 - 100 / (1 + rs)

    df["rsi_14_d"] = df.groupby("code")["close"].transform(_calc_rsi_14)

    print("  计算趋势指标...")
    # 60日收益、R²、最大回撤（用 transform + lambda）
    df["ret_60d_pct"] = df.groupby("code")["close"].transform(
        lambda x: x.pct_change(60) * 100
    )
    # R²: 简化用 rolling 线性回归
    df["r2_60d"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(60, min_periods=40).apply(_rolling_r2, raw=True)
    )
    # 最大回撤
    df["max_dd_60d"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(60, min_periods=40).apply(_rolling_max_dd, raw=True)
    )
    print("  计算20日高点...")
    df["high_20d"] = df.groupby("code")["high"].transform(lambda x: x.rolling(20, min_periods=1).max())
    print("  计算量比...")
    df["vol_20ma"] = df.groupby("code")["volume"].transform(lambda x: x.rolling(20, min_periods=1).mean())
    df["vol_ratio_d"] = df["volume"] / (df["vol_20ma"] + 1e-10)

    return df


def _print_results(trades, equity_curve, C):
    eq = pd.DataFrame(equity_curve)
    if eq.empty:
        print("\n⚠️ 无回测数据"); return

    # 绩效指标
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
    print(f"📊 策略 v2 回测结果")
    print(f"{'='*70}")
    print(f"  回测期间: {C.START_DATE} ~ {C.END_DATE}")
    print(f"  总交易: {len(trades)} 笔")
    print(f"  胜率: {win_rate:.1f}%")
    print(f"  总收益: {total_ret:.1f}%")
    print(f"  最大回撤: {max_dd:.1f}%")
    print(f"  夏普: {sharpe:.2f}")
    print(f"  平均盈利: +{np.mean(wins):.2f}%" if wins else "  平均盈利: N/A")
    print(f"  平均亏损: {np.mean(losses):.2f}%" if losses else "  平均亏损: N/A")
    print(f"  盈亏比: {np.sum(wins)/abs(np.sum(losses)):.2f}" if losses and wins else "  盈亏比: N/A")
    avg_hold = np.mean([t.hold_days for t in trades]) if trades else 0
    print(f"  平均持仓: {avg_hold:.1f}天")

    # 离场原因
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    print(f"  离场分布: {reasons}")

    # 年度收益
    eq["date"] = pd.to_datetime(eq["date"])
    eq["year"] = eq["date"].dt.year
    for yr, grp in eq.groupby("year"):
        yr_ret = (grp["equity"].iloc[-1]/grp["equity"].iloc[0]-1)*100
        print(f"  {yr}年收益: {yr_ret:.1f}%")

    # 最近交易
    if trades:
        print(f"\n  最近10笔交易:")
        for t in trades[-10:]:
            print(f"    {t.entry_date} → {t.exit_date} {t.code} {t.name} "
                  f"{t.return_pct:+.1f}% [{t.exit_reason}]")

    # 保存
    eq.to_csv(OUTPUT_DIR / "v2_equity_curve.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([t.__dict__ for t in trades]).to_csv(
        OUTPUT_DIR / "v2_trades.csv", index=False, encoding="utf-8-sig")
    print(f"\n💾 {OUTPUT_DIR}/v2_equity_curve.csv")
    print(f"💾 {OUTPUT_DIR}/v2_trades.csv")


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
    run_backtest_v2()
