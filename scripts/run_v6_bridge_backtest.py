#!/usr/bin/env python3
"""
V6 策略桥接回测 — 新旧架构对比
================================
使用新架构 PortfolioBacktestEngine + quant.db 运行:
  1. V6ReversalSelectionStrategy (v6信号, 新引擎)
  2. SmallCapStrategy (小市值基线, 新引擎)
  3. ReversalStrategy (短期反转基线, 新引擎)

对比旧架构 v6 的回测结果（仅为参考，引擎不同不可直接对比）。

用法:
  python scripts/run_v6_bridge_backtest.py
"""
import sys
import os
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

from src.backtest.portfolio_engine import PortfolioBacktestEngine
from src.strategies.v6_reversal_selection import V6ReversalSelectionStrategy
from src.strategies.small_cap import SmallCapStrategy
from src.strategies.reversal import ReversalStrategy

# ============================================================
# 回测参数
# ============================================================
START_DATE = date(2024, 6, 3)
END_DATE = date(2025, 6, 30)   # 1年回测
INITIAL_CAPITAL = 1_000_000     # 100万

def run_strategy(engine, strategy, name, start, end, capital):
    """运行单个策略回测并返回报告"""
    print(f"\n{'='*60}")
    print(f"  运行: {name}")
    print(f"{'='*60}")
    t0 = time.time()

    # 如果策略有预计算方法，先预计算
    if hasattr(strategy, 'precompute_all'):
        strategy.precompute_all(start, end)

    try:
        report = engine.run(
            strategy=strategy,
            start_date=start,
            end_date=end,
            initial_capital=capital,
        )
        elapsed = time.time() - t0
        print(f"  耗时: {elapsed:.1f}s")
        return report
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ❌ 失败 ({elapsed:.1f}s): {e}")
        import traceback
        traceback.print_exc()
        return None


def print_comparison(reports: dict):
    """打印对比表格"""
    print(f"\n{'='*80}")
    print(f"  策略回测结果对比")
    print(f"  回测区间: {START_DATE} ~ {END_DATE} | 初始资金: {INITIAL_CAPITAL:,}")
    print(f"{'='*80}")

    headers = [
        "策略", "总收益%", "年化%", "夏普", "最大回撤%",
        "胜率%", "交易次数", "卡玛", "超额%"
    ]
    rows = []
    for name, r in reports.items():
        if r is None:
            rows.append([name] + ["N/A"] * (len(headers) - 1))
        else:
            rows.append([
                name,
                f"{r.total_return:.2f}",
                f"{r.annual_return:.2f}",
                f"{r.sharpe_ratio:.2f}",
                f"{r.max_drawdown:.2f}",
                f"{r.win_rate:.2f}",
                str(r.total_trades),
                f"{r.calmar_ratio:.2f}",
                f"{r.excess_return:.2f}",
            ])

    # 格式化输出
    col_widths = [max(len(str(r[i])) for r in [headers] + rows) + 2
                  for i in range(len(headers))]

    def fmt_row(row):
        return "│ " + " │ ".join(
            str(c).ljust(col_widths[i]) for i, c in enumerate(row)
        ) + " │"

    sep = "├" + "┼".join("─" * w for w in col_widths) + "┤"
    top = "┌" + "┬".join("─" * w for w in col_widths) + "┐"
    bot = "└" + "┴".join("─" * w for w in col_widths) + "┘"

    print(top)
    print(fmt_row(headers))
    print(sep)
    for row in rows:
        print(fmt_row(row))
    print(bot)

    # 旧架构参考
    print(f"\n  📋 旧架构v6参考 (不同引擎, 不可直接对比):")
    print(f"     v6_improved: 年化+16.12%, 夏普1.28, 回撤-43.1%, 57笔交易")
    print(f"     v3_baseline: 年化+16.12%, 夏普1.42, 回撤-79.2%, 45笔交易")
    print(f"\n  ⚠️ 注意: 新引擎无止损/止盈/移动止损 — 组合回测偏信号质量评估")


def main():
    print("=" * 60)
    print("V6 策略桥接回测 — 新旧架构对比")
    print(f"回测区间: {START_DATE} ~ {END_DATE}")
    print(f"初始资金: {INITIAL_CAPITAL:,}")
    print("=" * 60)

    # 初始化回测引擎
    engine = PortfolioBacktestEngine()

    # 策略配置
    strategies = [
        (
            V6ReversalSelectionStrategy(
                n_stocks=8,
                rebalance_days=5,
                lookback_days=60,
                CONSECUTIVE_UP=1,
                MIN_PRICE_CHG=0.5,
            ),
            "v6_reversal (新引擎)",
        ),
        (
            V6ReversalSelectionStrategy(
                n_stocks=8,
                rebalance_days=5,
                lookback_days=60,
                CONSECUTIVE_UP=2,
                MIN_PRICE_CHG=0.5,
            ),
            "v6_reversal+阳线 (新引擎)",
        ),
        (
            SmallCapStrategy(),
            "小市值 (基线)",
        ),
        (
            ReversalStrategy(),
            "短期反转 (基线)",
        ),
    ]

    reports = {}
    for strat, name in strategies:
        report = run_strategy(engine, strat, name, START_DATE, END_DATE, INITIAL_CAPITAL)
        reports[name] = report

    # 打印对比
    print_comparison(reports)

    # 保存结果
    output_dir = "/workspace/quant-trading-system/output"
    os.makedirs(output_dir, exist_ok=True)

    result_rows = []
    for name, r in reports.items():
        if r:
            result_rows.append({
                "strategy": name,
                "total_return": r.total_return,
                "annual_return": r.annual_return,
                "sharpe": r.sharpe_ratio,
                "max_drawdown": r.max_drawdown,
                "win_rate": r.win_rate,
                "total_trades": r.total_trades,
                "calmar": r.calmar_ratio,
                "excess_return": r.excess_return,
            })

    if result_rows:
        df = pd.DataFrame(result_rows)
        csv_path = os.path.join(output_dir, "v6_bridge_comparison.csv")
        df.to_csv(csv_path, index=False)
        print(f"\n结果已保存: {csv_path}")

    print("\n✅ 桥接回测完成！")


if __name__ == "__main__":
    main()
