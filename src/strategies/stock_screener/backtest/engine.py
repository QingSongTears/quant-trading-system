"""
统一回测引擎 — 支持单策略 / 多策略并行回测

核心设计:
  - 严格 Walk-Forward，每日重新扫描全市场
  - 多策略共享资金池，按权重分配
  - 策略通过 BaseStrategy 接口接入
  - 输出: equity_curve + trades (按策略分别记录)

用法:
    from backtest.engine import BacktestEngine
    from strategies.v3_reversal import V3ReversalStrategy

    engine = BacktestEngine(initial_capital=1_000_000, start_date="2025-01-01", end_date="2026-06-12")
    engine.add_strategy(V3ReversalStrategy(), weight=1.0)
    result = engine.run()
"""

import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field
import warnings
warnings.filterwarnings("ignore")

from config import DATA_RAW_DIR, OUTPUT_DIR, OUTPUT_COMBINED_DIR
from core.data_loader import load_kline, load_quotes, load_finance, build_exclusion_set, build_spot_map
from core.indicators import precompute_indicators


# ============================================================
# 数据结构
# ============================================================

@dataclass
class Signal:
    """统一信号结构"""
    code: str
    close: float
    name: str = ""
    score: float = 0.0
    strategy: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Trade:
    """统一交易记录"""
    code: str
    name: str
    strategy: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    exit_reason: str
    return_pct: float
    hold_days: int
    _shares: int = 0


@dataclass
class StrategyResult:
    """单策略回测结果"""
    name: str
    trades: List[Trade]
    equity_curve: pd.DataFrame


@dataclass
class BacktestResult:
    """多策略回测汇总"""
    strategies: Dict[str, StrategyResult]
    combined_equity: pd.DataFrame
    total_trades: int
    total_return: float
    max_drawdown: float
    sharpe: float


# ============================================================
# 策略基类
# ============================================================

class BaseStrategy:
    """策略基类 — 所有策略必须继承"""

    name: str = "base"

    def scan(self, today_data: pd.DataFrame, today, context: dict) -> List[Signal]:
        """
        每日扫描，返回买入信号列表。

        参数:
            today_data: 当日全市场K线（已含预计算指标列）
            today: pd.Timestamp 当日日期
            context: 上下文字典，包含:
                - excluded_codes: set, 排除的股票代码
                - spot_map: dict, code→{name,mcap_yi,...}
                - held_codes: set, 已持仓代码
                - positions: list, 当前持仓列表
        """
        raise NotImplementedError

    def update_positions(
        self, positions: List[dict], row: pd.Series, today, today_str: str
    ) -> Tuple[List[dict], List[Trade]]:
        """
        更新持仓，返回 (surviving, closed_trades)。

        参数:
            positions: 该策略的持仓列表
            row: 当日该股K线行
            today: pd.Timestamp
            today_str: str 日期格式
        """
        raise NotImplementedError


# ============================================================
# 回测引擎
# ============================================================

class BacktestEngine:
    """统一回测引擎"""

    def __init__(
        self,
        initial_capital: float = 1_000_000,
        start_date: str = "2025-01-01",
        end_date: str = "2026-06-12",
        max_positions: int = 10,
        single_position_pct: float = 0.10,
    ):
        self.initial_capital = initial_capital
        self.start_date = start_date
        self.end_date = end_date
        self.max_positions = max_positions
        self.single_position_pct = single_position_pct

        self._strategies: List[Tuple[BaseStrategy, float]] = []  # (strategy, weight)

    def add_strategy(self, strategy: BaseStrategy, weight: float = 1.0):
        """添加策略及其资金权重"""
        self._strategies.append((strategy, weight))

    def run(self, verbose: bool = True,
            kline: "pd.DataFrame" = None, quotes: "pd.DataFrame" = None,
            finance: "pd.DataFrame" = None) -> BacktestResult:
        """
        执行回测。

        参数:
            verbose: 是否打印进度
            kline: 预加载的K线数据（可选，自动加载全部）
            quotes: 预加载的行情数据
            finance: 预加载的财务数据
        """
        if verbose:
            print("=" * 70)
            print("📊 统一回测引擎")
            str_names = [s.name for s, _ in self._strategies]
            print(f"   策略: {', '.join(str_names)}")
            print(f"   期间: {self.start_date} ~ {self.end_date}")
            print("=" * 70)

        # 1. 加载数据（支持外部预加载）
        if kline is None:
            kline = load_kline()
        if quotes is None:
            quotes = load_quotes()
        if finance is None:
            finance = load_finance()
        if kline.empty or quotes.empty:
            return None

        # 过滤到回测窗口 + 60天前置（用于指标计算）
        lookback = pd.Timestamp(self.start_date) - pd.Timedelta(days=90)
        kline = kline[(kline["date"] >= lookback) & (kline["date"] <= pd.Timestamp(self.end_date))]
        if verbose:
            print(f"[ENGINE] 数据窗口: {kline['date'].min().strftime('%Y-%m-%d')} ~ "
                  f"{kline['date'].max().strftime('%Y-%m-%d')}, "
                  f"{kline['code'].nunique()} 只, {len(kline):,} 行")

        # 2. 预计算指标
        if verbose:
            print("[ENGINE] 预计算统一指标...")
        kline = precompute_indicators(kline)
        if verbose:
            print(f"[ENGINE] ✅ 指标就绪")

        # 3. 构建上下文
        context = self._build_context(quotes, finance)
        if verbose:
            print(f"[ENGINE] 排除 {len(context['excluded_codes'])} 只")

        # 4. 交易日历
        trading_days = self._get_trading_days(kline)
        if verbose:
            print(f"[ENGINE] {len(trading_days)} 个交易日")

        # 5. 逐日回测
        positions_by_strategy = {s.name: [] for s, _ in self._strategies}
        trades_by_strategy = {s.name: [] for s, _ in self._strategies}
        cash = self.initial_capital
        equity_curve = []

        for day_idx, today in enumerate(trading_days):
            today_str = today.strftime("%Y-%m-%d")

            if verbose and day_idx % 30 == 0:
                total_pos = sum(len(p) for p in positions_by_strategy.values())
                total_tr = sum(len(t) for t in trades_by_strategy.values())
                wins = sum(1 for ts in trades_by_strategy.values() for t in ts if t.return_pct > 0)
                all_tr = sum(len(t) for t in trades_by_strategy.values())
                wr = f"{wins/all_tr*100:.0f}%" if all_tr > 0 else "-"
                print(f"  [{today_str}] d{day_idx:3d}/{len(trading_days)} | "
                      f"pos:{total_pos} | trades:{total_tr} wr:{wr}")

            # 5a. 更新持仓（各策略独立管理）
            for strategy, _ in self._strategies:
                pos_list = positions_by_strategy[strategy.name]
                positions_by_strategy[strategy.name], closed = self._update_strategy_positions(
                    strategy, pos_list, kline, today, today_str
                )
                for t in closed:
                    trades_by_strategy[strategy.name].append(t)
                    cash += t.exit_price * t._shares

            # 5b. 计算权益
            equity = self._calc_equity(positions_by_strategy, kline, today, cash)
            equity_curve.append({"date": today_str, "equity": equity})

            # 5c. 开新仓
            total_positions = sum(len(p) for p in positions_by_strategy.values())
            if total_positions < self.max_positions and cash > self.initial_capital * 0.05:
                context["positions"] = positions_by_strategy
                context["held_codes"] = set(
                    p["code"] for plist in positions_by_strategy.values() for p in plist
                )

                for strategy, weight in self._strategies:
                    strategy_cash = cash * weight / sum(w for _, w in self._strategies)
                    pos_list = positions_by_strategy[strategy.name]
                    max_for_strategy = max(2, self.max_positions // len(self._strategies))

                    if len(pos_list) >= max_for_strategy:
                        continue
                    if strategy_cash < self.initial_capital * 0.02:
                        continue

                    today_data = kline[kline["date"] == today]
                    signals = strategy.scan(today_data, today, context)

                    for sig in signals:
                        if len(pos_list) >= max_for_strategy:
                            break
                        cost = min(strategy_cash * self.single_position_pct,
                                   strategy_cash * 0.9 / max(1, max_for_strategy - len(pos_list)))
                        shares = int(cost / sig.close)
                        if shares == 0:
                            continue
                        cash -= shares * sig.close
                        pos_list.append({
                            "code": sig.code,
                            "entry_price": sig.close,
                            "entry_date": today_str,
                            "shares": shares,
                            "name": sig.name,
                            "strategy": strategy.name,
                        })

        # 6. 期末清仓
        last_day = trading_days[-1]
        last_str = last_day.strftime("%Y-%m-%d")
        for strategy, _ in self._strategies:
            pos_list = positions_by_strategy[strategy.name]
            for p in pos_list:
                row = kline[(kline["code"] == p["code"]) & (kline["date"] == last_day)]
                if not row.empty:
                    ep = row.iloc[-1]["close"]
                    ret = (ep - p["entry_price"]) / p["entry_price"] * 100
                    t = Trade(p["code"], p["name"], p["strategy"], p["entry_date"],
                              last_str, p["entry_price"], ep, "end", ret,
                              (last_day - pd.Timestamp(p["entry_date"])).days, p["shares"])
                    trades_by_strategy[strategy.name].append(t)
                    cash += ep * p["shares"]
            positions_by_strategy[strategy.name] = []

        # 7. 构建结果
        result = self._build_result(trades_by_strategy, equity_curve)
        if verbose:
            self._print_summary(result)
        return result

    # ---------- 内部方法 ----------

    def _load_data(self):
        kline = load_kline()
        quotes = load_quotes()
        finance = load_finance()
        return kline, quotes, finance

    def _build_context(self, quotes, finance) -> dict:
        from config import EXCLUDE_SECTORS
        excluded = build_exclusion_set(quotes, EXCLUDE_SECTORS)
        spot_map = build_spot_map(quotes)
        return {
            "excluded_codes": excluded,
            "spot_map": spot_map,
            "finance": finance,
            "held_codes": set(),
            "positions": {},
        }

    def _get_trading_days(self, kline):
        days = sorted(kline["date"].unique())
        return [d for d in days if self.start_date <= d.strftime("%Y-%m-%d") <= self.end_date]

    def _update_strategy_positions(self, strategy, positions, kline, today, today_str):
        surviving, closed_all = [], []
        for p in positions:
            row = kline[(kline["code"] == p["code"]) & (kline["date"] == today)]
            if row.empty:
                surviving.append(p)
                continue
            s, c = strategy.update_positions([p], row.iloc[-1], today, today_str)
            surviving.extend(s)
            closed_all.extend(c)
        return surviving, closed_all

    def _calc_equity(self, positions_by_strategy, kline, today, cash):
        unrealized = 0
        for plist in positions_by_strategy.values():
            for p in plist:
                row = kline[(kline["code"] == p["code"]) & (kline["date"] == today)]
                if not row.empty:
                    unrealized += (row.iloc[-1]["close"] - p["entry_price"]) * p["shares"]
        return cash + unrealized

    def _build_result(self, trades_by_strategy, equity_curve) -> BacktestResult:
        strategies = {}
        all_trades = []
        for sname, trades in trades_by_strategy.items():
            eq = pd.DataFrame(equity_curve)
            strategies[sname] = StrategyResult(name=sname, trades=trades, equity_curve=eq)
            all_trades.extend(trades)

        eq = pd.DataFrame(equity_curve)
        total_ret = self._calc_total_return(eq)
        max_dd = self._calc_max_dd(eq)
        sharpe = self._calc_sharpe(eq)

        return BacktestResult(
            strategies=strategies,
            combined_equity=eq,
            total_trades=len(all_trades),
            total_return=total_ret,
            max_drawdown=max_dd,
            sharpe=sharpe,
        )

    def _calc_total_return(self, eq):
        if eq.empty: return 0
        return (eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1) * 100

    def _calc_max_dd(self, eq):
        if eq.empty: return 0
        peak = np.maximum.accumulate(eq["equity"].values)
        return float(np.min((eq["equity"].values - peak) / peak * 100))

    def _calc_sharpe(self, eq):
        if len(eq) < 10: return 0
        daily_r = eq["equity"].pct_change().dropna()
        if daily_r.std() == 0: return 0
        return float((daily_r.mean() / daily_r.std()) * np.sqrt(252))

    def _print_summary(self, result: BacktestResult):
        print(f"\n{'='*70}")
        print(f"📊 回测汇总")
        print(f"{'='*70}")
        print(f"  总交易: {result.total_trades} 笔")
        print(f"  总收益: {result.total_return:+.1f}%")
        print(f"  最大回撤: {result.max_drawdown:.1f}%")
        print(f"  夏普: {result.sharpe:.2f}")
        print()

        for sname, sr in result.strategies.items():
            trades = sr.trades
            if not trades: continue
            wins = [t.return_pct for t in trades if t.return_pct > 0]
            losses = [t.return_pct for t in trades if t.return_pct <= 0]
            wr = len(wins) / len(trades) * 100 if trades else 0
            print(f"  [{sname}] {len(trades)}笔, 胜率{wr:.1f}%", end="")
            if wins and losses:
                pf = sum(wins) / abs(sum(losses))
                print(f", 盈亏比{pf:.2f}", end="")
            avg_h = np.mean([t.hold_days for t in trades]) if trades else 0
            print(f", 均持{avg_h:.0f}天")

            reasons = {}
            for t in trades:
                reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
            print(f"    离场: {reasons}")

        # 保存结果
        eq = result.combined_equity
        eq.to_csv(OUTPUT_COMBINED_DIR / "equity_curve.csv", index=False, encoding="utf-8-sig")
        for sname, sr in result.strategies.items():
            if sr.trades:
                pd.DataFrame([t.__dict__ for t in sr.trades]).to_csv(
                    OUTPUT_COMBINED_DIR / f"{sname}_trades.csv", index=False, encoding="utf-8-sig")
        print(f"\n💾 结果已保存到 {OUTPUT_COMBINED_DIR}")
