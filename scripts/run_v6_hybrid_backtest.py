#!/usr/bin/env python3
"""
V6 + 多维评分融合对比回测
==========================
对比:
  1. v6_reversal (纯信号)
  2. v6_pipeline_hybrid (v6 + 技术面评分)
  
评估融合是否能提升夏普比率。
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta
import pandas as pd
import numpy as np

from src.backtest.portfolio_engine import PortfolioBacktestEngine
from src.strategies.v6_reversal_selection import V6ReversalSelectionStrategy
from src.strategies.v6_pipeline_hybrid import V6PipelineHybridStrategy

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
          f"年化={report.annual_return:.2f}% 回撤={report.max_drawdown:.2f}% "
          f"夏普={report.sharpe_ratio:.2f} 交易={report.total_trades}")
    return report


def main():
    print("=" * 60)
    print(f"V6 + 多维评分融合对比 | {START} ~ {END}")
    print("=" * 60)

    engine = PortfolioBacktestEngine()

    # ── Strategy 1: v6 纯信号 ──
    v6 = V6ReversalSelectionStrategy(
        n_stocks=8, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5
    )

    # ── Strategy 2: v6 + 技术面 ──
    hybrid_tech = V6PipelineHybridStrategy(
        n_stocks=5, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        scoring_dims=["technical"],
        v6_weight=0.6, pipeline_weight=0.4
    )

    # ── Strategy 3: v6 + 技术面 (等权) ──
    hybrid_equal = V6PipelineHybridStrategy(
        n_stocks=5, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        scoring_dims=["technical"],
        v6_weight=0.5, pipeline_weight=0.5
    )

    # 预计算 v6 指标 (策略1)
    print("\n[预计算] v6 指标 (所有策略共享缓存)...")
    v6.precompute_all(START, END)

    # 让 hybrid 策略复用 v6 的缓存
    hybrid_tech._indicator_cache = v6._indicator_cache
    hybrid_equal._indicator_cache = v6._indicator_cache

    # 运行回测
    reports = {}
    reports["v6 (纯信号)"] = run_one(v6, engine, START, END, CAPITAL, "v6纯信号")
    reports["v6+技术面(6:4)"] = run_one(hybrid_tech, engine, START, END, CAPITAL, "v6+技术6:4")
    reports["v6+技术面(5:5)"] = run_one(hybrid_equal, engine, START, END, CAPITAL, "v6+技术5:5")

    # ── 对比表 ──
    print(f"\n{'='*80}")
    print(f"  融合对比结果 ({START} ~ {END})")
    print(f"{'='*80}")
    headers = ["策略", "年化%", "回撤%", "夏普", "胜率%", "交易", "卡玛"]
    print(f"{'策略':<20} {'年化%':>7} {'回撤%':>7} {'夏普':>6} {'胜率%':>7} {'交易':>5} {'卡玛':>6}")
    print("-" * 80)

    best_sharpe = -999
    best_name = ""
    for name, r in reports.items():
        print(f"{name:<20} {r.annual_return:>7.2f} {r.max_drawdown:>7.2f} "
              f"{r.sharpe_ratio:>6.2f} {r.win_rate:>7.2f} {r.total_trades:>5} {r.calmar_ratio:>6.2f}")
        if r.sharpe_ratio > best_sharpe:
            best_sharpe = r.sharpe_ratio
            best_name = name

    print(f"\n  🏆 最优: {best_name} (夏普={best_sharpe:.2f})")

    # 保存
    os.makedirs("/workspace/quant-trading-system/output", exist_ok=True)
    rows = []
    for name, r in reports.items():
        rows.append({
            "strategy": name, "annual_return": r.annual_return,
            "max_drawdown": r.max_drawdown, "sharpe": r.sharpe_ratio,
            "win_rate": r.win_rate, "total_trades": r.total_trades,
            "calmar": r.calmar_ratio, "total_return": r.total_return,
        })
    pd.DataFrame(rows).to_csv("/workspace/quant-trading-system/output/v6_hybrid_comparison.csv", index=False)
    print(f"\n结果已保存: output/v6_hybrid_comparison.csv")


if __name__ == "__main__":
    main()
