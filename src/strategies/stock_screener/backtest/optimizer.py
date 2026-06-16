"""
参数优化器 — 网格搜索最优策略参数

用法:
    from backtest.optimizer import optimize_v3
    best_params, results = optimize_v3(fast=True)  # fast模式只测关键组合
"""

import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import pandas as pd
import numpy as np
import itertools
from typing import Dict, List, Tuple
import warnings
warnings.filterwarnings("ignore")

from config import OUTPUT_DIR
from backtest.engine import BacktestEngine, BacktestResult
from strategies.v3_reversal import V3ReversalStrategy


def _score_result(result: BacktestResult) -> float:
    """综合评分：收益*0.6 + 夏普*0.3 + 胜率*0.1，亏损策略直接 -100"""
    if result is None or result.total_trades < 10:
        return -999
    if result.total_return < -20:
        return -999

    wr = sum(len(sr.trades) for sr in result.strategies.values()
             if any(t.return_pct > 0 for t in sr.trades)) / max(1, result.total_trades) * 100

    score = (result.total_return * 0.6 +
             result.sharpe * 10 * 0.3 +
             wr * 0.1)
    return score


def optimize_v3(fast: bool = True) -> Tuple[Dict, pd.DataFrame]:
    """
    v3 超卖反转 参数网格搜索

    参数:
        fast: True=快速模式(少组合), False=完整模式

    返回:
        (best_params, all_results_df)
    """
    print("=" * 70)
    print("🔧 v3 参数优化 — 网格搜索")
    print("=" * 70)

    if fast:
        grid = {
            "MAX_RSI_14": [25, 28, 30, 32],
            "MAX_RSI_6": [15, 18, 20, 22],
            "MAX_BB_POSITION": [0.05, 0.08, 0.12],
            "MAX_DRAWDOWN_60D": [-12, -15, -18],
            "MIN_PRICE_CHG": [0.5, 1.0, 1.5],
            "MIN_VOL_RATIO": [1.2, 1.3, 1.5],
            "TAKE_PROFIT": [0.12, 0.15, 0.18],
            "STOP_LOSS": [-0.05, -0.07, -0.10],
        }
    else:
        grid = {
            "MAX_RSI_14": [25, 27, 28, 29, 30, 31, 32, 35],
            "MAX_RSI_6": [15, 17, 18, 19, 20, 21, 22, 25],
            "MAX_BB_POSITION": [0.03, 0.05, 0.08, 0.10, 0.12, 0.15],
            "MAX_DRAWDOWN_60D": [-10, -12, -14, -15, -16, -18, -20],
            "MIN_PRICE_CHG": [0.5, 0.8, 1.0, 1.2, 1.5, 2.0],
            "MIN_VOL_RATIO": [1.1, 1.2, 1.3, 1.4, 1.5, 1.8],
            "TAKE_PROFIT": [0.10, 0.12, 0.14, 0.15, 0.17, 0.20],
            "STOP_LOSS": [-0.05, -0.06, -0.07, -0.08, -0.10],
        }

    # 生成参数组合（关键组合）
    keys = list(grid.keys())
    all_combos = []
    for combo in itertools.product(*grid.values()):
        all_combos.append(dict(zip(keys, combo)))

    # 如果组合太多，随机采样
    if len(all_combos) > 200:
        np.random.seed(42)
        idx = np.random.choice(len(all_combos), 200, replace=False)
        all_combos = [all_combos[i] for i in idx]

    print(f"   参数组合数: {len(all_combos)}")

    results = []
    best_score = -float("inf")
    best_params = None

    for i, params in enumerate(all_combos):
        if i % 20 == 0:
            print(f"   进度: {i}/{len(all_combos)} | 当前最优: {best_score:.1f}")

        strategy = V3ReversalStrategy(**params)
        engine = BacktestEngine(
            initial_capital=1_000_000,
            start_date="2025-03-01",
            end_date="2026-06-12",
            max_positions=8,
            single_position_pct=0.10,
        )
        engine.add_strategy(strategy, weight=1.0)
        result = engine.run(verbose=False)

        score = _score_result(result)
        results.append({**params, "score": score,
                        "trades": result.total_trades if result else 0,
                        "return": result.total_return if result else 0,
                        "sharpe": result.sharpe if result else 0,
                        "max_dd": result.max_drawdown if result else 0})

        if score > best_score:
            best_score = score
            best_params = params.copy()
            print(f"   🏆 新最优: score={score:.1f} | ret={result.total_return:.1f}% | "
                  f"trades={result.total_trades} | params={params}")

    # 保存
    df = pd.DataFrame(results).sort_values("score", ascending=False)
    df.to_csv(OUTPUT_DIR / "v3" / "optimization_results.csv", index=False, encoding="utf-8-sig")

    print(f"\n🏆 最优参数: {best_params}")
    print(f"   得分: {best_score:.1f}")
    print(f"💾 {OUTPUT_DIR / 'v3' / 'optimization_results.csv'}")

    return best_params, df


if __name__ == "__main__":
    optimize_v3(fast=True)
