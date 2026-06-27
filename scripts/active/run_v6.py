#!/usr/bin/env python3
"""
V6 策略统一回测 CLI
====================

合并原 6 个 run_v6_*.py 脚本为 1 个参数化 CLI。

用法:
    python scripts/run_v6.py --list-modes
    python scripts/run_v6.py --mode bridge
    python scripts/run_v6.py --mode comparison --start 2024-06-03 --end 2025-06-30
    python scripts/run_v6.py --mode fund_flow --capital 500000
    python scripts/run_v6.py --mode threshold --output-dir ./results

可用 mode (对应原脚本):
    bridge       V6 + 3 baselines (4 策略)        原 run_v6_bridge_backtest.py
    comparison   V6 + 2 baselines (3 策略)        原 run_v6_comparison.py
    fund_flow    V6 + 资金面融合 (4 策略)         原 run_v6_fund_flow_test.py
    fundamental  V6 + 基本面融合 (2 策略)         原 run_v6_fundamental_backtest.py
    hybrid       V6 + 技术面融合 (3 策略)         原 run_v6_hybrid_backtest.py
    threshold    V6 阈值放宽 (5 档)              原 run_v6_threshold_test.py

共享逻辑:
- 预计算缓存复用 (各 mode 内的策略共享)
- 统一输出格式 (CSV + 表格)
- 统一错误处理
"""
import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.backtest.portfolio_engine import PortfolioBacktestEngine
from src.strategies.reversal import ReversalStrategy
from src.strategies.small_cap import SmallCapStrategy
from src.strategies.v6_pipeline_hybrid import V6PipelineHybridStrategy
from src.strategies.v6_reversal_selection import V6ReversalSelectionStrategy

# ============================================================
# 策略工厂 — 避免重复 import / 简化延迟加载
# ============================================================


def _make_v6_basic():
    return V6ReversalSelectionStrategy(
        n_stocks=8, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
    )


def _make_v6_two_up():
    return V6ReversalSelectionStrategy(
        n_stocks=8, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=2, MIN_PRICE_CHG=0.5,
    )


def _make_v6_daily():
    """v6 日频调仓版本(fundamental 模式使用)"""
    return V6ReversalSelectionStrategy(
        n_stocks=8, rebalance_days=1, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
    )


def _make_v6_fund_daily():
    """v6 + 基本面 6:4 日频版本"""
    from scripts.run_v6_fundamental_backtest import V6FundamentalHybrid
    return V6FundamentalHybrid(
        n_stocks=8, rebalance_days=1, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        v6_weight=0.6, fundam_weight=0.4,
    )


def _make_hybrid_fund_64():
    return V6PipelineHybridStrategy(
        n_stocks=5, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        scoring_dims=["fund_flow"],
        v6_weight=0.6, pipeline_weight=0.4,
    )


def _make_hybrid_fund_55():
    return V6PipelineHybridStrategy(
        n_stocks=5, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        scoring_dims=["fund_flow"],
        v6_weight=0.5, pipeline_weight=0.5,
    )


def _make_hybrid_tech_64():
    return V6PipelineHybridStrategy(
        n_stocks=5, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        scoring_dims=["technical"],
        v6_weight=0.6, pipeline_weight=0.4,
    )


def _make_hybrid_tech_55():
    return V6PipelineHybridStrategy(
        n_stocks=5, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        scoring_dims=["technical"],
        v6_weight=0.5, pipeline_weight=0.5,
    )


def _make_hybrid_all_433():
    return V6PipelineHybridStrategy(
        n_stocks=5, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        scoring_dims=["technical", "fund_flow"],
        v6_weight=0.4, pipeline_weight=0.6,
    )


# threshold 模式 — 5 档参数
def _make_threshold_variant(rsi14, rsi6, bb, dd60):
    return V6ReversalSelectionStrategy(
        n_stocks=8, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
        MAX_RSI_14=rsi14, MAX_RSI_6=rsi6,
        MAX_BB_POSITION=bb, MAX_DRAWDOWN_60D=dd60,
    )


# ============================================================
# 模式配置 — 6 个 mode 的策略集
# ============================================================


@dataclass
class StrategySpec:
    """单个策略的规格"""
    name: str                    # 短名 (CSV 用)
    label: str                   # 显示标签
    factory: Callable[[], Any]   # 工厂函数,返回策略实例
    share_cache_from: Optional[str] = None  # 共享预计算缓存的来源策略名


@dataclass
class ModeConfig:
    """一个 mode 的完整配置"""
    key: str                                # CLI 用 key
    description: str                        # 描述
    default_start: date = date(2024, 6, 3)
    default_end: date = date(2025, 6, 30)
    default_capital: float = 1_000_000.0
    strategies: List[StrategySpec] = field(default_factory=list)
    output_basename: str = ""               # CSV 文件名前缀
    note: str = ""                          # mode 特定的打印说明


def _v6_cache_setup(strategies: List[StrategySpec], start: date, end: date) -> None:
    """预计算一次 v6 指标,所有依赖 v6 缓存的策略共享

    寻找第一个 factory 指向 V6ReversalSelectionStrategy 的策略,
    调用 precompute_all,然后把它生成的 _indicator_cache 共享给其他
    标记了 share_cache_from 的策略。
    """
    v6_strategy = None
    for s in strategies:
        # 通过 type() 名字识别 v6 (避免 import 循环)
        sample = s.factory()
        if sample.__class__.__name__ == "V6ReversalSelectionStrategy":
            v6_strategy = sample
            break

    if v6_strategy is None:
        return

    print(f"\n[预计算] v6 指标 (全市场, 一次性)...")
    t0 = time.time()
    v6_strategy.precompute_all(start, end)
    elapsed = time.time() - t0
    print(f"  预计算完成: {len(v6_strategy._indicator_cache)} 个日期, 耗时{elapsed:.0f}s")

    # 共享缓存
    for s in strategies:
        if s.share_cache_from:
            # 找到被引用的策略,直接用它的缓存
            for other in strategies:
                if other.name == s.share_cache_from:
                    # 重新构造该策略以避免污染,但共享 cache
                    fresh = s.factory()
                    fresh._indicator_cache = v6_strategy._indicator_cache
                    # 用 fresh 替换 strategies 列表里的实例
                    # 注:factory 是无参函数,我们不能直接 mutate spec
                    # 改为通过 name 索引共享
                    setattr(s, "_shared_cache", v6_strategy._indicator_cache)
                    break


def _apply_shared_cache(strategies: List[StrategySpec], instances: List[Any]) -> None:
    """把预计算缓存附加到对应策略实例上"""
    for s, inst in zip(strategies, instances):
        cache = getattr(s, "_shared_cache", None)
        if cache is not None:
            inst._indicator_cache = cache


MODES: Dict[str, ModeConfig] = {
    "bridge": ModeConfig(
        key="bridge",
        description="V6 + 3 baselines (新旧架构对比)",
        strategies=[
            StrategySpec("v6", "v6_reversal (新引擎)", _make_v6_basic),
            StrategySpec("v6_2up", "v6_reversal+阳线 (新引擎)", _make_v6_two_up),
            StrategySpec("small_cap", "小市值 (基线)", lambda: SmallCapStrategy()),
            StrategySpec("reversal", "短期反转 (基线)", lambda: ReversalStrategy()),
        ],
        output_basename="v6_bridge_comparison",
        note="对比旧架构 v6 的回测结果(仅参考,引擎不同不可直接对比)",
    ),
    "comparison": ModeConfig(
        key="comparison",
        description="V6 + 2 baselines (高效版,单次预计算)",
        strategies=[
            StrategySpec("v6", "v6_reversal", _make_v6_basic),
            StrategySpec("small_cap", "small_cap", lambda: SmallCapStrategy()),
            StrategySpec("reversal", "reversal", lambda: ReversalStrategy()),
        ],
        output_basename="v6_comparison",
        note="共享 v6 预计算缓存",
    ),
    "fund_flow": ModeConfig(
        key="fund_flow",
        description="V6 + 资金面评分融合对比 (4 策略)",
        strategies=[
            StrategySpec("v6", "v6 (纯信号)", _make_v6_basic),
            StrategySpec("v6_fund_64", "v6+资金面(6:4)", _make_hybrid_fund_64, share_cache_from="v6"),
            StrategySpec("v6_fund_55", "v6+资金面(5:5)", _make_hybrid_fund_55, share_cache_from="v6"),
            StrategySpec("v6_all_433", "v6+技术+资金(4:3:3)", _make_hybrid_all_433, share_cache_from="v6"),
        ],
        output_basename="v6_fund_flow_comparison",
        note="资金面与技术面天然正交",
    ),
    "fundamental": ModeConfig(
        key="fundamental",
        description="V6 + 基本面融合 (日频调仓,2 策略)",
        default_start=date(2024, 1, 2),
        strategies=[
            StrategySpec("v6", "Pure V6", _make_v6_daily),
            StrategySpec("v6_fund", "V6+Fundamental", _make_v6_fund_daily, share_cache_from="v6"),
        ],
        output_basename="v6_fundamental_comparison",
        note="日频调仓,2 策略对比",
    ),
    "hybrid": ModeConfig(
        key="hybrid",
        description="V6 + 技术面融合 (3 策略)",
        strategies=[
            StrategySpec("v6", "v6 (纯信号)", _make_v6_basic),
            StrategySpec("v6_tech_64", "v6+技术面(6:4)", _make_hybrid_tech_64, share_cache_from="v6"),
            StrategySpec("v6_tech_55", "v6+技术面(5:5)", _make_hybrid_tech_55, share_cache_from="v6"),
        ],
        output_basename="v6_hybrid_comparison",
    ),
    "threshold": ModeConfig(
        key="threshold",
        description="T1.1 v6 阈值放宽 — 5 档参数对比",
        strategies=[
            StrategySpec("baseline", "基线 (当前)",
                         lambda: _make_threshold_variant(30.0, 20.0, 0.08, -15.0)),
            StrategySpec("rsi_wide", "档A (RSI宽)",
                         lambda: _make_threshold_variant(40.0, 25.0, 0.08, -15.0)),
            StrategySpec("rsi_mid", "档B (RSI折中)",
                         lambda: _make_threshold_variant(35.0, 20.0, 0.08, -15.0)),
            StrategySpec("dd_relax", "档C (回撤放宽)",
                         lambda: _make_threshold_variant(30.0, 20.0, 0.08, -10.0)),
            StrategySpec("all_relax", "档D (全面放宽)",
                         lambda: _make_threshold_variant(35.0, 20.0, 0.08, -10.0)),
        ],
        output_basename="v6_threshold_test",
    ),
}


# ============================================================
# 回测执行
# ============================================================


def _run_one(strategy, engine, start, end, capital, label):
    """运行单个策略,带计时和错误处理"""
    print(f"\n  [{label}] 回测中...", end=" ", flush=True)
    t0 = time.time()
    try:
        # 缓存复用
        if hasattr(strategy, "_indicator_cache") and strategy._indicator_cache:
            pass  # 缓存已设置,无需重算
        elif hasattr(strategy, "precompute_all"):
            strategy.precompute_all(start, end)

        report = engine.run(
            strategy=strategy, start_date=start, end_date=end,
            initial_capital=capital,
        )
        elapsed = time.time() - t0
        print(
            f"年化={report.annual_return:+.2f}% 夏普={report.sharpe_ratio:+.2f} "
            f"回撤={report.max_drawdown:+.2f}% 交易={report.total_trades} "
            f"胜率={report.win_rate:.1f}% ({elapsed:.0f}s)"
        )
        return report, elapsed
    except Exception as e:
        elapsed = time.time() - t0
        print(f"❌ 失败 ({elapsed:.0f}s): {e}")
        import traceback
        traceback.print_exc()
        return None, elapsed


def _print_summary(results: List[tuple], mode: ModeConfig, start: date, end: date, capital: float):
    """统一打印结果表"""
    print(f"\n{'='*100}")
    print(f"  {mode.description} | {start} ~ {end} | 初始资金: {capital:,.0f}")
    print(f"{'='*100}")
    headers = ["策略", "总收益%", "年化%", "夏普", "回撤%", "胜率%", "交易", "卡玛", "耗时(s)"]
    print(f"{headers[0]:<24} {headers[1]:>8} {headers[2]:>8} {headers[3]:>7} "
          f"{headers[4]:>8} {headers[5]:>7} {headers[6]:>5} {headers[7]:>6} {headers[8]:>8}")
    print("-" * 100)

    for label, report, elapsed in results:
        if report is None:
            print(f"{label:<24} {'N/A':>8} {'N/A':>8} {'N/A':>7} "
                  f"{'N/A':>8} {'N/A':>7} {'N/A':>5} {'N/A':>6} {elapsed:>8.0f}")
        else:
            print(
                f"{label:<24} {report.total_return:>+8.2f} {report.annual_return:>+8.2f} "
                f"{report.sharpe_ratio:>+7.2f} {report.max_drawdown:>+8.2f} "
                f"{report.win_rate:>7.1f} {report.total_trades:>5} "
                f"{report.calmar_ratio:>+6.2f} {elapsed:>8.0f}"
            )

    # 最优策略
    valid = [(l, r, e) for l, r, e in results if r is not None]
    if valid:
        best_sharpe = max(valid, key=lambda x: x[1].sharpe_ratio)
        best_return = max(valid, key=lambda x: x[1].total_return)
        print(f"\n  🏆 夏普最优: {best_sharpe[0]} (夏普={best_sharpe[1].sharpe_ratio:.2f}, "
              f"收益={best_sharpe[1].total_return:+.2f}%)")
        if best_return[0] != best_sharpe[0]:
            print(f"  💰 收益最优: {best_return[0]} (收益={best_return[1].total_return:+.2f}%, "
                  f"夏普={best_return[1].sharpe_ratio:.2f})")

    if mode.note:
        print(f"\n  📋 {mode.note}")


def _save_csv(results: List[tuple], mode: ModeConfig, output_dir: Path) -> Optional[Path]:
    """保存结果到 CSV"""
    rows = []
    for label, report, elapsed in results:
        if report is None:
            continue
        rows.append({
            "strategy": label,
            "total_return": round(report.total_return, 2),
            "annual_return": round(report.annual_return, 2),
            "sharpe": round(report.sharpe_ratio, 2),
            "max_drawdown": round(report.max_drawdown, 2),
            "win_rate": round(report.win_rate, 1),
            "total_trades": report.total_trades,
            "calmar": round(report.calmar_ratio, 2),
            "elapsed_sec": round(elapsed, 1),
        })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{mode.output_basename}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path


# ============================================================
# CLI 入口
# ============================================================


def list_modes():
    print("可用 mode:\n")
    for k, m in MODES.items():
        n_strat = len(m.strategies)
        print(f"  {k:12s}  {m.description}")
        print(f"  {'':12s}  策略数: {n_strat}, 默认区间: {m.default_start} ~ {m.default_end}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="V6 策略统一回测 CLI (合并原 6 个 run_v6_*.py)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --list-modes
  %(prog)s --mode bridge
  %(prog)s --mode comparison --start 2024-01-01 --end 2025-06-30
  %(prog)s --mode fund_flow --capital 500000
  %(prog)s --mode threshold --output-dir ./results
        """,
    )
    parser.add_argument(
        "--mode",
        choices=list(MODES.keys()),
        help="回测模式 (见 --list-modes)",
    )
    parser.add_argument(
        "--list-modes",
        action="store_true",
        help="列出所有可用 mode 并退出",
    )
    parser.add_argument(
        "--start", type=date.fromisoformat,
        help="回测开始日期 (YYYY-MM-DD),默认 mode 特定值",
    )
    parser.add_argument(
        "--end", type=date.fromisoformat,
        help="回测结束日期 (YYYY-MM-DD),默认 mode 特定值",
    )
    parser.add_argument(
        "--capital", type=float,
        help="初始资金,默认 mode 特定值",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=PROJECT_ROOT / "output",
        help="CSV 输出目录 (默认: output/)",
    )

    args = parser.parse_args()

    if args.list_modes:
        list_modes()
        return 0

    if not args.mode:
        parser.error("--mode 必填(或用 --list-modes 查看)")

    mode = MODES[args.mode]
    start = args.start or mode.default_start
    end = args.end or mode.default_end
    capital = args.capital or mode.default_capital

    print("=" * 80)
    print(f"  {mode.description}")
    print(f"  mode={args.mode} | 区间 {start} ~ {end} | 资金 {capital:,.0f}")
    print("=" * 80)
    if mode.note:
        print(f"  📋 {mode.note}")
    print()

    engine = PortfolioBacktestEngine()

    # 第一步: 共享预计算(若需要)
    _v6_cache_setup(mode.strategies, start, end)

    # 第二步: 实例化所有策略
    instances = [s.factory() for s in mode.strategies]
    _apply_shared_cache(mode.strategies, instances)

    # 第三步: 跑回测
    results = []
    for spec, inst in zip(mode.strategies, instances):
        report, elapsed = _run_one(inst, engine, start, end, capital, spec.label)
        results.append((spec.label, report, elapsed))

    # 第四步: 汇总
    _print_summary(results, mode, start, end, capital)

    # 第五步: 保存 CSV
    csv_path = _save_csv(results, mode, args.output_dir)
    if csv_path:
        print(f"\n💾 结果已保存: {csv_path}")

    print(f"\n✅ mode={args.mode} 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
