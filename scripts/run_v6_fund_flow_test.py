#!/usr/bin/env python3
"""
V6 + 资金面评分融合对比回测
============================
对比:
  1. v6_reversal (纯信号, 基线)
  2. v6 + 资金面 (6:4)
  3. v6 + 资金面 (5:5)
  4. v6 + 技术面+资金面 (4:3:3)
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
import pandas as pd

from src.backtest.portfolio_engine import PortfolioBacktestEngine
from src.strategies.v6_reversal_selection import V6ReversalSelectionStrategy
from src.strategies.v6_pipeline_hybrid import V6PipelineHybridStrategy

START = date(2024, 6, 3)
END = date(2025, 6, 30)
CAPITAL = 1_000_000


def run_one(strategy, engine, start, end, capital, label):
    print(f"  [{label}] 回测中...", end=" ", flush=True)
    t0 = time.time()
    if hasattr(strategy, 'precompute_all') and not strategy._indicator_cache:
        strategy.precompute_all(start, end)
    report = engine.run(strategy, start_date=start, end_date=end, initial_capital=capital)
    elapsed = time.time() - t0
    print(f"年化={report.annual_return:.2f}% 夏普={report.sharpe_ratio:.2f} 回撤={report.max_drawdown:.2f}% 交易={report.total_trades} ({elapsed:.0f}s)")
    return report


def main():
    print("=" * 65)
    print(f"  V6 + 资金面评分融合对比 | {START} ~ {END}")
    print("=" * 65)

    engine = PortfolioBacktestEngine()

    # Strategy 1: v6 纯信号 (基线)
    v6 = V6ReversalSelectionStrategy(
        n_stocks=8, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5
    )

    # Strategy 2: v6 + 资金面 (6:4)
    hybrid_fund = V6PipelineHybridStrategy(
        n_stocks=5, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        scoring_dims=["fund_flow"],
        v6_weight=0.6, pipeline_weight=0.4
    )

    # Strategy 3: v6 + 资金面 (5:5)
    hybrid_fund_eq = V6PipelineHybridStrategy(
        n_stocks=5, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        scoring_dims=["fund_flow"],
        v6_weight=0.5, pipeline_weight=0.5
    )

    # Strategy 4: v6 + 技术面 + 资金面 (4:3:3)
    hybrid_all = V6PipelineHybridStrategy(
        n_stocks=5, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        scoring_dims=["technical", "fund_flow"],
        v6_weight=0.4, pipeline_weight=0.6
    )

    # 预计算 v6 指标 (所有策略共享)
    print("\n[预计算] v6 指标...")
    v6.precompute_all(START, END)

    # 复用缓存
    for s in [hybrid_fund, hybrid_fund_eq, hybrid_all]:
        s._indicator_cache = v6._indicator_cache

    # 运行回测
    reports = {}
    reports["v6 (纯信号)"] = run_one(v6, engine, START, END, CAPITAL, "v6基线")
    reports["v6+资金面(6:4)"] = run_one(hybrid_fund, engine, START, END, CAPITAL, "v6+资金6:4")
    reports["v6+资金面(5:5)"] = run_one(hybrid_fund_eq, engine, START, END, CAPITAL, "v6+资金5:5")
    reports["v6+技术+资金(4:3:3)"] = run_one(hybrid_all, engine, START, END, CAPITAL, "v6+技+资4:3:3")

    # 对比表
    print(f"\n{'='*85}")
    print(f"  融合对比结果")
    print(f"{'='*85}")
    best_sharpe = -999
    best_name = ""
    for name, r in reports.items():
        sharpe = r.sharpe_ratio
        print(f"  {name:<22s} 年化{r.annual_return:>+7.2f}% 夏普{sharpe:>+6.2f} 回撤{r.max_drawdown:>7.2f}% 胜率{r.win_rate:>5.1f}% 交易{r.total_trades:>4} 卡玛{r.calmar_ratio:>+6.2f}")
        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_name = name

    print(f"\n  🏆 最优: {best_name} (夏普={best_sharpe:.2f})")

    # 保存
    os.makedirs("output", exist_ok=True)
    rows = []
    for name, r in reports.items():
        rows.append({
            "strategy": name,
            "total_return": round(r.total_return, 2),
            "annual_return": round(r.annual_return, 2),
            "sharpe": round(r.sharpe_ratio, 2),
            "max_drawdown": round(r.max_drawdown, 2),
            "win_rate": round(r.win_rate, 1),
            "total_trades": r.total_trades,
            "calmar": round(r.calmar_ratio, 2),
        })
    df = pd.DataFrame(rows)
    csv_path = "output/v6_fund_flow_comparison.csv"
    df.to_csv(csv_path, index=False)
    print(f"\n结果已保存: {csv_path}")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
