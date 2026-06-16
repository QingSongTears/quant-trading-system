"""
回测报告生成器 — 从 BacktestResult 生成格式化报告

用法:
    from backtest.reporter import generate_report
    generate_report(result, output_dir)
"""

import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import pandas as pd
import numpy as np
from typing import Optional
from backtest.engine import BacktestResult, StrategyResult


def generate_report(result: BacktestResult, output_dir: Path = None) -> str:
    """
    生成多策略对比报告

    返回:
        格式化的 Markdown 报告字符串
    """
    if result is None:
        return "⚠️ 无回测数据"

    lines = []
    lines.append("# 选股策略回测报告")
    lines.append("")
    lines.append(f"- 总交易: {result.total_trades} 笔")
    lines.append(f"- 总收益: {result.total_return:+.1f}%")
    lines.append(f"- 最大回撤: {result.max_drawdown:.1f}%")
    lines.append(f"- 夏普比率: {result.sharpe:.2f}")
    lines.append("")

    # 策略对比表
    lines.append("## 策略对比")
    lines.append("")
    lines.append("| 策略 | 交易数 | 胜率 | 总收益 | 盈亏比 | 均持(天) | 夏普 |")
    lines.append("|------|--------|------|--------|--------|----------|------|")

    for sname, sr in result.strategies.items():
        trades = sr.trades
        if not trades:
            lines.append(f"| {sname} | 0 | - | - | - | - | - |")
            continue

        wins = [t.return_pct for t in trades if t.return_pct > 0]
        losses = [t.return_pct for t in trades if t.return_pct <= 0]
        wr = len(wins) / len(trades) * 100
        pf = sum(wins) / abs(sum(losses)) if losses and wins else 0
        avg_h = np.mean([t.hold_days for t in trades])

        # 策略独立夏普
        eq = sr.equity_curve
        if len(eq) > 10:
            dr = eq["equity"].pct_change().dropna()
            s = float((dr.mean() / dr.std()) * np.sqrt(252)) if dr.std() > 0 else 0
        else:
            s = 0

        # 计算策略独立收益
        if not eq.empty:
            sr_ret = (eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1) * 100
        else:
            sr_ret = 0

        lines.append(f"| {sname} | {len(trades)} | {wr:.1f}% | {sr_ret:+.1f}% | {pf:.2f} | {avg_h:.0f} | {s:.2f} |")

    lines.append("")

    # 离场原因分布
    lines.append("## 离场原因分布")
    lines.append("")
    for sname, sr in result.strategies.items():
        reasons = {}
        for t in sr.trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        lines.append(f"- **{sname}**: {reasons}")

    lines.append("")

    # 年度收益
    lines.append("## 年度收益")
    lines.append("")
    eq = result.combined_equity.copy()
    if not eq.empty:
        eq["date"] = pd.to_datetime(eq["date"])
        eq["year"] = eq["date"].dt.year
        for yr, grp in eq.groupby("year"):
            yr_ret = (grp["equity"].iloc[-1] / grp["equity"].iloc[0] - 1) * 100
            lines.append(f"- **{yr}年**: {yr_ret:+.1f}%")

    lines.append("")

    # Top/Bottom trades
    lines.append("## 最佳/最差交易 (Top 5)")
    lines.append("")
    for sname, sr in result.strategies.items():
        if not sr.trades:
            continue
        sorted_trades = sorted(sr.trades, key=lambda t: t.return_pct, reverse=True)
        lines.append(f"### {sname} — 最佳")
        for t in sorted_trades[:5]:
            lines.append(f"- {t.entry_date}→{t.exit_date} {t.code} {t.name}: "
                         f"{t.return_pct:+.1f}% ({t.exit_reason})")
        lines.append(f"### {sname} — 最差")
        for t in sorted_trades[-5:]:
            lines.append(f"- {t.entry_date}→{t.exit_date} {t.code} {t.name}: "
                         f"{t.return_pct:+.1f}% ({t.exit_reason})")

    report = "\n".join(lines)

    # 保存
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "report.md").write_text(report, encoding="utf-8")
        print(f"💾 报告已保存: {output_dir / 'report.md'}")

    return report


def print_quick_stats(result: BacktestResult):
    """打印快速统计"""
    if result is None:
        print("⚠️ 无数据")
        return

    print(f"\n{'='*60}")
    print(f"📊 快速统计")
    print(f"{'='*60}")
    print(f"  总交易: {result.total_trades} | 总收益: {result.total_return:+.1f}% | "
          f"回撤: {result.max_drawdown:.1f}% | 夏普: {result.sharpe:.2f}")

    for sname, sr in result.strategies.items():
        trades = sr.trades
        if not trades:
            continue
        wins = [t.return_pct for t in trades if t.return_pct > 0]
        wr = len(wins) / len(trades) * 100
        reasons = {}
        for t in trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        print(f"  [{sname}] {len(trades)}笔, 胜率{wr:.1f}%, 离场: {reasons}")
