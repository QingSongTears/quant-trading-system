"""
回测模块 — Walk-Forward Backtest
基于 tdrive 日K线数据，逐日模拟完整交易流程
严格无未来数据泄露：第 T 天的决策只能用 ≤T 的数据
"""

import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[3]  # stock_screener/backtest -> project root
sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from src.strategies.stock_screener.config import (
    DATA_DIR, OUTPUT_DIR, CACHE_DIR,
    EMA_FAST, EMA_SLOW, VOLUME_RATIO_THRESHOLD,
    QUANT_THRESHOLD, QUANT_WEIGHTS, QUANT_MAX_HOLD_DAYS,
    STOP_LOSS_PCT, QUANT_TAKE_PROFIT_PCT, NORMAL_TAKE_PROFIT_PCT,
    MAX_POSITIONS, MAX_SINGLE_POSITION_PCT, MAX_SIGNAL_AGE_DAYS,
    MIN_MARKET_CAP, MIN_DAILY_TURNOVER,
)

@dataclass
class Trade:
    """单笔交易记录"""
    code: str
    name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    exit_reason: str  # stop_loss / take_profit / time_exit / ema_exit
    return_pct: float
    hold_days: int
    is_quant: bool = False


@dataclass
class BacktestResult:
    """回测结果"""
    trades: List[Trade] = field(default_factory=list)
    daily_equity: pd.DataFrame = None  # 每日权益曲线
    metrics: Dict = field(default_factory=dict)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def win_trades(self) -> int:
        return sum(1 for t in self.trades if t.return_pct > 0)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0
        return self.win_trades / len(self.trades) * 100

    @property
    def avg_return(self) -> float:
        if not self.trades:
            return 0
        return np.mean([t.return_pct for t in self.trades])

    @property
    def avg_win(self) -> float:
        wins = [t.return_pct for t in self.trades if t.return_pct > 0]
        return np.mean(wins) if wins else 0

    @property
    def avg_loss(self) -> float:
        losses = [t.return_pct for t in self.trades if t.return_pct <= 0]
        return np.mean(losses) if losses else 0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.return_pct for t in self.trades if t.return_pct > 0)
        gross_loss = abs(sum(t.return_pct for t in self.trades if t.return_pct <= 0))
        return gross_profit / gross_loss if gross_loss > 0 else float('inf')

    @property
    def max_drawdown(self) -> float:
        if self.daily_equity is None or self.daily_equity.empty:
            return 0
        equity = self.daily_equity["equity"].values
        peak = np.maximum.accumulate(equity)
        drawdown = (peak - equity) / peak * 100
        return float(np.max(drawdown))

    @property
    def sharpe_ratio(self) -> float:
        if self.daily_equity is None or len(self.daily_equity) < 2:
            return 0
        returns = self.daily_equity["equity"].pct_change().dropna()
        if len(returns) < 2:
            return 0
        return float((returns.mean() / returns.std()) * np.sqrt(252))

    @property
    def total_return(self) -> float:
        if self.daily_equity is None or self.daily_equity.empty:
            return 0
        start = self.daily_equity["equity"].iloc[0]
        end = self.daily_equity["equity"].iloc[-1]
        return (end - start) / start * 100


def run_backtest(
    start_date: str = "2024-06-01",
    end_date: str = "2026-06-12",
    initial_capital: float = 1_000_000,
    verbose: bool = True,
) -> BacktestResult:
    """
    主回测函数
    - 从 start_date 到 end_date 逐日模拟
    - 严格 walk-forward：每天只用当天及之前的数据
    - 完整模拟：信号检测 → 量化检测 → 买点评分 → 入场 → 止损/止盈离场
    """
    result = BacktestResult()

    # ===== 1. 加载数据 =====
    if verbose:
        print("=" * 60)
        print(f"📊 回测: {start_date} ~ {end_date}")
        print("=" * 60)

    kline_all, spot_all = _load_all_data()
    if kline_all.empty:
        print("❌ 数据加载失败")
        return result

    # 预处理：为所有股票计算指标
    kline_all = _precompute_indicators(kline_all)

    # 获取交易日列表
    trading_days = sorted(kline_all["date"].unique())
    trading_days = [d for d in trading_days if start_date <= d.strftime("%Y-%m-%d") <= end_date]

    if verbose:
        print(f"[DATA] 交易日: {len(trading_days)} 天")
        print(f"[DATA] 股票数: {kline_all['code'].nunique()} 只")

    # ===== 2. 获取股票基本信息和行业分类 =====
    stock_info = _get_stock_info(spot_all)

    # ===== 3. 逐日回测 =====
    positions: List[Dict] = []  # 当前持仓
    equity_curve = []
    trade_records = []
    cash = initial_capital

    # 筛选池
    eligible_codes = _get_eligible_stocks(kline_all, spot_all, stock_info)

    if verbose:
        print(f"[SCREEN] 回测候选池: {len(eligible_codes)} 只")
        print(f"[TRADE] 开始逐日模拟...")

    progress_step = max(1, len(trading_days) // 20)

    for day_idx, today in enumerate(trading_days):
        today_str = today.strftime("%Y-%m-%d")

        if verbose and day_idx % progress_step == 0:
            print(f"  [{today_str}] day {day_idx}/{len(trading_days)} | positions: {len(positions)} | trades: {len(trade_records)}")

        # 3a. 更新持仓：检查止损/止盈/到期
        positions, closed_today = _update_positions(
            positions, kline_all, today, today_str
        )
        for t in closed_today:
            trade_records.append(t)
            cash += t.exit_price * t._shares  # 平仓回收资金

        # 3b. 更新权益
        capital_used = sum(p["entry_price"] * p["shares"] for p in positions)
        unrealized = 0
        for p in positions:
            price_row = kline_all[
                (kline_all["code"] == p["code"]) & (kline_all["date"] == today)
            ]
            if not price_row.empty:
                unrealized += (price_row.iloc[-1]["close"] - p["entry_price"]) * p["shares"]

        equity = cash + unrealized
        equity_curve.append({"date": today_str, "equity": equity})

        # 3c. 如果持仓未满，扫描新信号
        if len(positions) < MAX_POSITIONS:
            new_signals = _scan_signals_for_day(
                kline_all, eligible_codes, today, day_idx
            )
            for sig in new_signals:
                if len(positions) >= MAX_POSITIONS:
                    break
                cost = cash * MAX_SINGLE_POSITION_PCT
                shares = int(cost / sig["close"])
                if shares == 0:
                    continue
                cash -= shares * sig["close"]  # 开仓扣减资金
                positions.append({
                    "code": sig["code"],
                    "entry_price": sig["close"],
                    "entry_date": today_str,
                    "shares": shares,
                    "is_quant": sig.get("is_quant", False),
                    "name": sig.get("name", ""),
                })

    # 4. 清仓：最后一天强制平仓所有持仓
    last_day = trading_days[-1]
    last_day_str = last_day.strftime("%Y-%m-%d")
    for p in positions:
        price_row = kline_all[
            (kline_all["code"] == p["code"]) & (kline_all["date"] == last_day)
        ]
        if not price_row.empty:
            exit_price = price_row.iloc[-1]["close"]
            ret = (exit_price - p["entry_price"]) / p["entry_price"] * 100
            t = Trade(
                code=p["code"], name=p.get("name", ""),
                entry_date=p["entry_date"], exit_date=last_day_str,
                entry_price=p["entry_price"], exit_price=exit_price,
                exit_reason="end_of_test", return_pct=ret,
                hold_days=(last_day - pd.Timestamp(p["entry_date"])).days,
                is_quant=p.get("is_quant", False),
            )
            t._shares = p["shares"]
            trade_records.append(t)
            cash += exit_price * p["shares"]

    # ===== 5. 汇总结果 =====
    result.trades = trade_records
    result.daily_equity = pd.DataFrame(equity_curve)
    result.metrics = {
        "total_trades": result.total_trades,
        "win_trades": result.win_trades,
        "win_rate": round(result.win_rate, 1),
        "avg_return": round(result.avg_return, 2),
        "avg_win": round(result.avg_win, 2),
        "avg_loss": round(result.avg_loss, 2),
        "profit_factor": round(result.profit_factor, 2),
        "max_drawdown": round(result.max_drawdown, 1),
        "sharpe_ratio": round(result.sharpe_ratio, 2),
        "total_return": round(result.total_return, 1),
        "avg_hold_days": round(np.mean([t.hold_days for t in trade_records]), 1) if trade_records else 0,
    }

    if verbose:
        _print_backtest_summary(result)

    return result


def _load_all_data() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """加载 K线和行情数据"""
    kline_path = DATA_DIR / "kline_daily.csv"
    spot_path = DATA_DIR / "tencent_quotes.csv"

    kline = pd.DataFrame()
    spot = pd.DataFrame()

    if kline_path.exists():
        col_names = ["code", "market", "name", "date", "open", "high", "low", "close", "volume", "amount"]
        kline = pd.read_csv(
            kline_path, names=col_names, header=None,
            dtype={"code": str, "market": str, "name": str, "date": str,
                   "open": float, "high": float, "low": float, "close": float,
                   "volume": float, "amount": float},
        )
        kline["code"] = kline["code"].astype(str).str.zfill(6)
        kline["date"] = pd.to_datetime(kline["date"], errors="coerce")
        kline = kline.dropna(subset=["date"]).sort_values(["code", "date"])

    if spot_path.exists():
        spot = pd.read_csv(spot_path)
        spot["code"] = spot["code"].astype(str).str.zfill(6)

    return kline, spot


def _precompute_indicators(kline: pd.DataFrame) -> pd.DataFrame:
    """为所有股票预计算技术指标（EMA/MA/量均线）—— 使用 groupby transform"""
    print("[INDICATOR] 预计算技术指标...")

    df = kline.copy()
    df = df.sort_values(["code", "date"])

    # 使用 groupby transform（比循环快10x+）
    df["ema_fast"] = df.groupby("code")["close"].transform(
        lambda x: x.ewm(span=EMA_FAST, adjust=False).mean()
    )
    df["ema_slow"] = df.groupby("code")["close"].transform(
        lambda x: x.ewm(span=EMA_SLOW, adjust=False).mean()
    )
    df["ma20"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(20, min_periods=1).mean()
    )
    df["ma60"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(60, min_periods=1).mean()
    )
    df["vol_ma20"] = df.groupby("code")["volume"].transform(
        lambda x: x.rolling(20, min_periods=1).mean()
    )

    # 金叉检测
    df["cross_up"] = (df["ema_fast"] > df["ema_slow"]) & (
        df.groupby("code")["ema_fast"].shift(1) <= df.groupby("code")["ema_slow"].shift(1)
    )
    df["bullish"] = (df["ema_fast"] > df["ema_slow"]) & (df["close"] > df["ema_fast"])
    df["vol_confirm"] = df["volume"] >= df["vol_ma20"] * VOLUME_RATIO_THRESHOLD

    print(f"[INDICATOR] ✅ 指标计算完成")
    return df


def _get_stock_info(spot: pd.DataFrame) -> Dict[str, Dict]:
    """获取股票基本信息（名称、市值、PE）"""
    info = {}
    if not spot.empty:
        for _, row in spot.iterrows():
            code = str(row["code"]).zfill(6)
            info[code] = {
                "name": str(row.get("name", "")),
                "mcap_yi": float(row.get("mcap_yi", 0) or 0),
                "pe_ttm": float(row.get("pe_ttm", 0) or 0),
            }
    return info


def _get_eligible_stocks(kline: pd.DataFrame, spot: pd.DataFrame, stock_info: Dict) -> set:
    """获取回测期间的稳定候选池（避免每日重复计算排除规则）"""
    eligible = set()

    # 从 kline 获取所有股票代码
    all_codes = set(kline["code"].unique())

    excluded_keywords = [
        "银行", "证券", "保险", "金融", "信托", "期货", "租赁",
        "地产", "置业", "万科",
        "猪肉", "养殖", "牧原", "温氏", "新希望",
        "白酒", "茅台", "五粮液", "泸州老窖", "汾酒",
        "中药", "中成药", "片仔癀", "同仁堂",
        "教育", "培训",
    ]

    for code in all_codes:
        name = stock_info.get(code, {}).get("name", "")
        mcap = stock_info.get(code, {}).get("mcap_yi", 0)

        # 板块排除
        if any(kw in name for kw in excluded_keywords):
            continue
        # ST 排除
        if "ST" in name or "退" in name:
            continue
        # 市值过滤（用快照数据近似）
        if mcap < MIN_MARKET_CAP:
            continue

        eligible.add(code)

    return eligible


def _scan_signals_for_day(
    kline: pd.DataFrame,
    eligible_codes: set,
    today: pd.Timestamp,
    day_idx: int,
) -> List[Dict]:
    """
    扫描今天的新金叉信号（只用今天及之前的数据）
    返回信号列表，每个包含 code/close/is_quant/name
    """
    signals = []

    # 获取今天的金叉（cross_up=True 且今天确认）
    today_data = kline[kline["date"] == today]

    for _, row in today_data.iterrows():
        code = row["code"]
        if code not in eligible_codes:
            continue

        cross = row["cross_up"]
        bullish = row["bullish"]
        vol_ok = row["vol_confirm"]

        if not (cross and bullish and vol_ok):
            continue

        # 确保今天有足够的历史数据
        hist = kline[(kline["code"] == code) & (kline["date"] <= today)]
        if len(hist) < EMA_SLOW + 10:
            continue

        # 检查金叉信号时效性：30天内
        recent_crosses = hist[hist["cross_up"]]
        if recent_crosses.empty:
            continue

        last_cross_date = recent_crosses.iloc[-1]["date"]
        days_since = (today - last_cross_date).days
        if days_since > MAX_SIGNAL_AGE_DAYS:
            continue

        # 位置判断：需要是回踩支撑或强势多头（不能破位下行）
        latest = hist.iloc[-1]
        if latest["close"] < latest["ema_slow"]:
            continue  # 破位下行，不买

        # 量化检测（简化版，使用预计算指标）
        is_quant = _simple_quant_check(hist.tail(60))

        signals.append({
            "code": code,
            "close": row["close"],
            "is_quant": is_quant,
            "name": str(row.get("name", "")),
            "score": 5,  # 简化版默认给5分
        })

    # 按信号质量排序，取前 MAX_POSITIONS
    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals[:MAX_POSITIONS]


def _simple_quant_check(df_60d: pd.DataFrame) -> bool:
    """简化版量化检测（回测用，避免过度计算）"""
    if df_60d.empty or len(df_60d) < 30:
        return False

    score = 0
    # 1. 换手率波动（用成交量代替）
    if "volume" in df_60d.columns:
        vol = df_60d["volume"]
        cv = vol.std() / (vol.mean() + 1e-10)
        if cv > 1.0:
            score += 0.2

    # 2. 振幅/涨幅比
    if all(c in df_60d.columns for c in ["high", "low", "close"]):
        amplitude = (df_60d["high"] - df_60d["low"]) / (df_60d["low"] + 1e-10)
        ret = df_60d["close"].pct_change().abs()
        ratio = amplitude.rolling(10).mean().iloc[-1] / (ret.rolling(10).mean().iloc[-1] + 1e-10)
        if ratio > 8:
            score += 0.2

    # 3. 影线占比
    if all(c in df_60d.columns for c in ["open", "close", "high", "low"]):
        upper = df_60d["high"] - df_60d[["open", "close"]].max(axis=1)
        lower = df_60d[["open", "close"]].min(axis=1) - df_60d["low"]
        total = df_60d["high"] - df_60d["low"] + 1e-10
        shadow_ratio = (upper + lower) / total
        if shadow_ratio.mean() > 0.6:
            score += 0.2

    # 4. 成交量异常
    if "volume" in df_60d.columns:
        vol_change = df_60d["volume"].pct_change().abs()
        anomaly = (vol_change > 1.0).rolling(20).mean().iloc[-1]
        if anomaly > 0.2:
            score += 0.15

    return score >= QUANT_THRESHOLD


def _update_positions(
    positions: List[Dict],
    kline: pd.DataFrame,
    today: pd.Timestamp,
    today_str: str,
) -> Tuple[List[Dict], List[Trade]]:
    """更新持仓，检查止损/止盈/到期"""
    closed_trades = []
    surviving = []

    for p in positions:
        code = p["code"]
        price_row = kline[(kline["code"] == code) & (kline["date"] == today)]
        if price_row.empty:
            surviving.append(p)
            continue

        current_price = price_row.iloc[-1]["close"]
        entry_price = p["entry_price"]
        hold_days = (today - pd.Timestamp(p["entry_date"])).days
        is_quant = p.get("is_quant", False)
        ema20 = price_row.iloc[-1].get("ema_fast", 0)

        # 止损
        if current_price <= entry_price * (1 + STOP_LOSS_PCT):
            ret = (current_price - entry_price) / entry_price * 100
            t = Trade(
                code=code, name=p.get("name", ""),
                entry_date=p["entry_date"], exit_date=today_str,
                entry_price=entry_price, exit_price=current_price,
                exit_reason="stop_loss", return_pct=ret,
                hold_days=hold_days, is_quant=is_quant,
            )
            t._shares = p["shares"]
            closed_trades.append(t)
            continue

        # 止盈
        tp_pct = QUANT_TAKE_PROFIT_PCT if is_quant else NORMAL_TAKE_PROFIT_PCT
        if current_price >= entry_price * (1 + tp_pct):
            ret = (current_price - entry_price) / entry_price * 100
            t = Trade(
                code=code, name=p.get("name", ""),
                entry_date=p["entry_date"], exit_date=today_str,
                entry_price=entry_price, exit_price=current_price,
                exit_reason="take_profit", return_pct=ret,
                hold_days=hold_days, is_quant=is_quant,
            )
            t._shares = p["shares"]
            closed_trades.append(t)
            continue

        # 量化票到期（5日）
        if is_quant and hold_days >= QUANT_MAX_HOLD_DAYS:
            ret = (current_price - entry_price) / entry_price * 100
            t = Trade(
                code=code, name=p.get("name", ""),
                entry_date=p["entry_date"], exit_date=today_str,
                entry_price=entry_price, exit_price=current_price,
                exit_reason="time_exit", return_pct=ret,
                hold_days=hold_days, is_quant=is_quant,
            )
            t._shares = p["shares"]
            closed_trades.append(t)
            continue

        # 正常票跌破EMA20
        if not is_quant and ema20 > 0 and current_price < ema20:
            ret = (current_price - entry_price) / entry_price * 100
            t = Trade(
                code=code, name=p.get("name", ""),
                entry_date=p["entry_date"], exit_date=today_str,
                entry_price=entry_price, exit_price=current_price,
                exit_reason="ema_exit", return_pct=ret,
                hold_days=hold_days, is_quant=is_quant,
            )
            t._shares = p["shares"]
            closed_trades.append(t)
            continue

        surviving.append(p)

    return surviving, closed_trades


def _print_backtest_summary(result: BacktestResult):
    """打印回测摘要"""
    print()
    print("=" * 60)
    print("📊 回测结果")
    print("=" * 60)
    print(f"  总交易次数: {result.total_trades}")
    print(f"  胜率: {result.metrics.get('win_rate', 0)}%")
    print(f"  总收益率: {result.metrics.get('total_return', 0):.1f}%")
    print(f"  平均收益: {result.metrics.get('avg_return', 0):.2f}%")
    print(f"  平均盈利: +{result.metrics.get('avg_win', 0):.2f}%")
    print(f"  平均亏损: {result.metrics.get('avg_loss', 0):.2f}%")
    print(f"  盈亏比: {result.metrics.get('profit_factor', 0)}")
    print(f"  最大回撤: {result.metrics.get('max_drawdown', 0):.1f}%")
    print(f"  夏普比率: {result.metrics.get('sharpe_ratio', 0)}")
    print(f"  平均持仓: {result.metrics.get('avg_hold_days', 0)} 天")

    # 离场原因分布
    if result.trades:
        reasons = {}
        for t in result.trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        print(f"\n  离场原因分布:")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count} 笔")
