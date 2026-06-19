#!/usr/bin/env python3
"""
T1.1 v6阈值放宽测试 — 多档参数对比 (高效版)
=========================================
共享预计算缓存，避免重复计算4次。
"""
import sys, os, time, json
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

from src.backtest.portfolio_engine import PortfolioBacktestEngine
from src.strategies.v6_reversal_selection import V6ReversalSelectionStrategy

START_DATE = date(2024, 6, 3)
END_DATE = date(2025, 6, 30)
INITIAL_CAPITAL = 1_000_000

def count_signals_from_cache(indicators_cache, strategy, all_dates):
    """从已有缓存统计信号量"""
    total = 0
    days = 0
    for dt in all_dates:
        date_str = str(dt.date())[:10]
        ind = indicators_cache.get(date_str)
        if ind is None or ind.empty:
            continue
        candidates = strategy._detect_signals(ind.reset_index(), pd.DataFrame())
        total += len(candidates)
        days += 1
    avg = total / days if days > 0 else 0
    return avg, total, days

def run_strategy(engine, strategy, name, start, end, capital):
    print(f"\n  ┌─ {name}")
    t0 = time.time()
    try:
        report = engine.run(
            strategy=strategy,
            start_date=start,
            end_date=end,
            initial_capital=capital,
        )
        elapsed = time.time() - t0
        print(f"  └─ 回测: 收益{report.total_return:+.2f}% | 夏普{report.sharpe_ratio:.2f} | 回撤{report.max_drawdown:.1f}% | 交易{report.total_trades}笔 | 胜率{report.win_rate:.1f}% (耗时{elapsed:.0f}s)")
        return report
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  └─ ❌ 失败 ({elapsed:.0f}s): {e}")
        import traceback; traceback.print_exc()
        return None


def main():
    print("=" * 70)
    print("  T1.1 v6阈值放宽 — 多档参数对比 (高效版)")
    print(f"  回测区间: {START_DATE} ~ {END_DATE}")
    print("=" * 70)
    
    configs = [
        ("基线 (当前)", {
            "MAX_RSI_14": 30.0, "MAX_RSI_6": 20.0,
            "MAX_BB_POSITION": 0.08, "MAX_DRAWDOWN_60D": -15.0,
        }),
        ("档A (RSI宽)", {
            "MAX_RSI_14": 40.0, "MAX_RSI_6": 25.0,
            "MAX_BB_POSITION": 0.08, "MAX_DRAWDOWN_60D": -15.0,
        }),
        ("档B (RSI折中)", {
            "MAX_RSI_14": 35.0, "MAX_RSI_6": 20.0,
            "MAX_BB_POSITION": 0.08, "MAX_DRAWDOWN_60D": -15.0,
        }),
        ("档C (回撤放宽)", {
            "MAX_RSI_14": 30.0, "MAX_RSI_6": 20.0,
            "MAX_BB_POSITION": 0.08, "MAX_DRAWDOWN_60D": -10.0,
        }),
        ("档D (全面放宽)", {
            "MAX_RSI_14": 35.0, "MAX_RSI_6": 20.0,
            "MAX_BB_POSITION": 0.08, "MAX_DRAWDOWN_60D": -10.0,
        }),
    ]
    
    # 第一步：用基线策略预计算一次(全市场~10分钟)
    print("\n[步骤1] 基线预计算 (全市场指标缓存)...")
    t0 = time.time()
    base_strategy = V6ReversalSelectionStrategy(
        n_stocks=8, rebalance_days=5, lookback_days=60,
        CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
    )
    V6ReversalSelectionStrategy.clear_cache()
    base_strategy.precompute_all(START_DATE, END_DATE)
    print(f"  预计算完成: {len(base_strategy._indicator_cache)} 个日期, 耗时{time.time()-t0:.0f}s")
    
    # 获取所有交易日列表 (用于信号统计)
    engine = PortfolioBacktestEngine()
    all_data = engine._load_all_data(START_DATE, END_DATE)
    all_dates = sorted(all_data['trade_date'].unique())
    print(f"  交易日: {len(all_dates)} 天")
    
    # 第二步：为每组配置创建策略，复用缓存，统计信号+跑回测
    print("\n[步骤2] 各档信号统计 + 回测...")
    results = []
    
    for name, params in configs:
        # 创建新策略实例
        strategy = V6ReversalSelectionStrategy(
            n_stocks=8, rebalance_days=5, lookback_days=60,
            CONSECUTIVE_UP=1, MIN_PRICE_CHG=0.5,
            **params,
        )
        # 复用预计算缓存
        strategy._indicator_cache = base_strategy._indicator_cache
        
        # 先统计信号量
        avg_sig, total_sig, days = count_signals_from_cache(
            strategy._indicator_cache, strategy, all_dates
        )
        print(f"\n  ┌─ {name}")
        print(f"  ├─ 信号量: 日均{avg_sig:.1f}只, 共{total_sig}次/{days}天")
        
        # 跑回测
        report = run_strategy(engine, strategy, name, START_DATE, END_DATE, INITIAL_CAPITAL)
        
        if report:
            results.append({
                "config": name,
                "params": str({k: int(v) if v == int(v) else v for k, v in params.items()}),
                "avg_signal": round(avg_sig, 1),
                "total_return": round(report.total_return, 2),
                "annual_return": round(report.annual_return, 2),
                "sharpe": round(report.sharpe_ratio, 2),
                "max_drawdown": round(report.max_drawdown, 2),
                "win_rate": round(report.win_rate, 1),
                "total_trades": report.total_trades,
                "calmar": round(report.calmar_ratio, 2),
            })
    
    # 打印结果
    print("\n" + "=" * 100)
    print("  结果对比")
    print("=" * 100)
    df = pd.DataFrame(results)
    print(df.to_string(index=False))
    
    # 保存
    out_dir = "/workspace/quant-trading-system/output"
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "v6_threshold_test.csv")
    df.to_csv(csv_path, index=False)
    print(f"\n结果保存: {csv_path}")
    
    if results:
        best_sharpe = max(results, key=lambda r: r["sharpe"])
        best_return = max(results, key=lambda r: r["total_return"])
        print(f"\n📊 夏普最优: {best_sharpe['config']} (夏普={best_sharpe['sharpe']}, 收益={best_sharpe['total_return']:+.2f}%)")
        print(f"📊 收益最优: {best_return['config']} (收益={best_return['total_return']:+.2f}%, 夏普={best_return['sharpe']})")

if __name__ == "__main__":
    main()
