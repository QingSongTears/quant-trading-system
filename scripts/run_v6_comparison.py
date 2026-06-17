#!/usr/bin/env python3
"""
V6 桥接对比回测 — 高效版（单次预计算）
========================================
运行 v6 策略 + 基线策略，通过新架构 PortfolioBacktestEngine 对比。
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta
import pandas as pd
import numpy as np

from src.backtest.portfolio_engine import PortfolioBacktestEngine
from src.strategies.v6_reversal_selection import V6ReversalSelectionStrategy
from src.strategies.small_cap import SmallCapStrategy
from src.strategies.reversal import ReversalStrategy

START = date(2024, 6, 3)
END = date(2025, 6, 30)
CAPITAL = 1_000_000


def run_one(strategy, engine, start, end, capital, label):
    print(f"\n  [{label}] 回测中...")
    t0 = time.time()

    if hasattr(strategy, 'precompute_all') and not strategy._indicator_cache:
        strategy.precompute_all(start, end)

    report = engine.run(strategy, start_date=start, end_date=end, initial_capital=capital)
    elapsed = time.time() - t0
    print(f"  [{label}] 完成 ({elapsed:.0f}s): "
          f"收益={report.total_return:.2f}% 回撤={report.max_drawdown:.2f}% "
          f"交易={report.total_trades} 夏普={report.sharpe_ratio:.2f}")
    return report


def main():
    print("=" * 60)
    print(f"V6 桥接对比回测 | {START} ~ {END}")
    print("=" * 60)

    engine = PortfolioBacktestEngine()

    # 只运行关键对比
    v6 = V6ReversalSelectionStrategy(
        n_stocks=8, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5
    )
    small_cap = SmallCapStrategy()
    reversal = ReversalStrategy()

    # 预计算 v6 指标
    print("\n[预计算] v6 指标 (仅一次)...")
    v6.precompute_all(START, END)

    reports = {}
    reports["v6_reversal"] = run_one(v6, engine, START, END, CAPITAL, "v6_reversal")
    reports["small_cap"] = run_one(small_cap, engine, START, END, CAPITAL, "small_cap")
    reports["reversal"] = run_one(reversal, engine, START, END, CAPITAL, "reversal")

    # 打印对比表
    print(f"\n{'='*80}")
    print(f"  策略结果对比 ({START} ~ {END})")
    print(f"{'='*80}")
    print(f"{'策略':<20} {'总收益%':>8} {'年化%':>8} {'夏普':>6} {'回撤%':>8} {'胜率%':>7} {'交易':>5} {'卡玛':>6}")
    print("-" * 80)

    # 旧架构参考
    old_refs = {
        "v6 (旧引擎)": (16.12, 1.28, -43.1, 57),
        "v3 (旧引擎)": (16.12, 1.42, -79.2, 45),
    }

    for name, r in reports.items():
        print(f"{name:<20} {r.total_return:>8.2f} {r.annual_return:>8.2f} "
              f"{r.sharpe_ratio:>6.2f} {r.max_drawdown:>8.2f} "
              f"{r.win_rate:>7.2f} {r.total_trades:>5} {r.calmar_ratio:>6.2f}")

    print(f"\n  旧架构参考 (不同引擎, 不可直接对比):")
    for name, (ret, sharpe, dd, trades) in old_refs.items():
        print(f"    {name}: 年化{ret:+.2f}% 夏普{sharpe:.2f} 回撤{dd:.1f}% {trades}笔")

    print(f"\n  ⚠️ 新引擎: 等权重调仓, 无个股止损/止盈/移动止损 → 信号质量评估而非完整策略回测")

    # 保存
    os.makedirs("/workspace/quant-trading-system/output", exist_ok=True)
    rows = []
    for name, r in reports.items():
        rows.append({
            "strategy": name, "total_return": r.total_return,
            "annual_return": r.annual_return, "sharpe": r.sharpe_ratio,
            "max_drawdown": r.max_drawdown, "win_rate": r.win_rate,
            "total_trades": r.total_trades, "calmar": r.calmar_ratio,
        })
    pd.DataFrame(rows).to_csv("/workspace/quant-trading-system/output/v6_comparison.csv", index=False)
    print(f"\n结果已保存: output/v6_comparison.csv")


if __name__ == "__main__":
    main()
