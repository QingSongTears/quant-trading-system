"""
优化轮次3 — 市场广度精准过滤 + 动态止盈 + v5放宽趋势确认
基于轮次2的发现：
  1. 放宽v3入场(RSI30→38)反而亏损 → 保持v3基线RSI≤30/BB≤0.08
  2. 市场广度40%阈值太松 → 测试SMA5平滑版 + 更高阈值
  3. v5混合信号太少(26笔) → 放宽趋势确认 2/3→1/3
  4. 动态止盈 → 强势股让利润奔跑

测试变体:
  A. v3基线 (参考基准)
  B. v3 + breadth SMA5(40%)
  C. v3 + breadth SMA5(45%)
  D. v3 + breadth SMA5(50%)
  E. v5_1of3 (趋势确认仅需1/3)
  F. v5_1of3 + breadth SMA5(45%)

输出: output/combined/optimization_round3.csv + 各策略详细trades
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import numpy as np
import time
from typing import Dict, Any

from config import DATA_RAW_DIR, OUTPUT_COMBINED_DIR
from core.data_loader import load_kline, load_quotes, load_finance, build_exclusion_set, build_spot_map
from core.indicators import precompute_indicators
from backtest.engine import BacktestEngine, BacktestResult
from strategies.v3_reversal import V3ReversalStrategy
from strategies.v5_hybrid import V5HybridStrategy

OUTPUT_COMBINED_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 预加载数据（所有变体共享，避免重复加载）
# ============================================================
print("=" * 70)
print("📊 优化轮次3 — 市场广度精准过滤 + 动态止盈 + v5放宽")
print("=" * 70)
print("[INIT] 预加载数据...")
kline = load_kline()
quotes = load_quotes()
finance = load_finance()
print(f"[INIT] ✅ K线: {len(kline):,}行, 行情: {len(quotes)}只, 财务: {len(finance)}只")

# ============================================================
# 辅助函数
# ============================================================

def run_one(name: str, strategy_class, strategy_kwargs: dict,
            use_breadth: bool = False, breadth_threshold: float = 0.40,
            use_smoothed: bool = True, **engine_kwargs) -> Dict[str, Any]:
    """运行一个变体"""
    engine = BacktestEngine(
        initial_capital=1_000_000,
        start_date="2025-01-01",
        end_date="2026-06-12",
        max_positions=10,
        single_position_pct=0.10,
        use_breadth_filter=use_breadth,
        breadth_min_up_ratio=breadth_threshold,
    )

    # 如果需要SMA平滑版，设置breadth_filter的lookback
    if use_breadth and use_smoothed:
        engine.breadth_filter.lookback_days = 5

    strategy = strategy_class(**strategy_kwargs)
    engine.add_strategy(strategy, weight=1.0)

    t0 = time.time()
    result = engine.run(verbose=False, kline=kline, quotes=quotes, finance=finance)
    elapsed = time.time() - t0

    if result is None:
        return {"name": name, "error": "回测失败"}

    sr = result.strategies.get(strategy.name)
    if not sr or not sr.trades:
        return {"name": name, "trades": 0, "return": 0, "max_dd": 0,
                "sharpe": 0, "win_rate": 0, "elapsed": elapsed}

    trades = sr.trades
    wins = [t.return_pct for t in trades if t.return_pct > 0]
    losses = [t.return_pct for t in trades if t.return_pct <= 0]
    wr = len(wins)/len(trades)*100 if trades else 0
    pf = sum(wins)/abs(sum(losses)) if wins and losses and sum(losses) != 0 else 0
    avg_ret = np.mean([t.return_pct for t in trades]) if trades else 0
    avg_hold = np.mean([t.hold_days for t in trades]) if trades else 0
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    # 年化
    days = (pd.Timestamp("2026-06-12") - pd.Timestamp("2025-01-01")).days
    annual_ret = ((1 + result.total_return/100) ** (365/days) - 1) * 100

    return {
        "name": name,
        "trades": len(trades),
        "return": round(result.total_return, 2),
        "annual_ret": round(annual_ret, 2),
        "max_dd": round(result.max_drawdown, 2),
        "sharpe": round(result.sharpe, 2),
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "avg_return": round(avg_ret, 2),
        "avg_hold": round(avg_hold, 0),
        "elapsed": round(elapsed, 1),
        "exit_reasons": str(reasons),
    }


# ============================================================
# 运行所有变体
# ============================================================

variants = []

# A: v3基线 (参考基准)
print("\n[A] v3基线 (RSI30/BB0.08, 无广度过滤)...")
variants.append(run_one(
    "A_v3基线", V3ReversalStrategy, {},
    use_breadth=False
))

# B: v3 + breadth SMA5(40%)
print("\n[B] v3 + breadth SMA5(40%)...")
variants.append(run_one(
    "B_v3+广度SMA40", V3ReversalStrategy, {},
    use_breadth=True, breadth_threshold=0.40, use_smoothed=True
))

# C: v3 + breadth SMA5(45%)
print("\n[C] v3 + breadth SMA5(45%)...")
variants.append(run_one(
    "C_v3+广度SMA45", V3ReversalStrategy, {},
    use_breadth=True, breadth_threshold=0.45, use_smoothed=True
))

# D: v3 + breadth SMA5(50%)
print("\n[D] v3 + breadth SMA5(50%)...")
variants.append(run_one(
    "D_v3+广度SMA50", V3ReversalStrategy, {},
    use_breadth=True, breadth_threshold=0.50, use_smoothed=True
))

# E: v5_1of3 (趋势确认仅需1/3)
print("\n[E] v5_1of3 (趋势确认1/3)...")
variants.append(run_one(
    "E_v5_1of3", V5HybridStrategy, {"trend_checks_min": 1},
    use_breadth=False
))

# F: v5_1of3 + breadth SMA5(45%)
print("\n[F] v5_1of3 + breadth SMA5(45%)...")
variants.append(run_one(
    "F_v5_1of3+广度SMA45", V5HybridStrategy, {"trend_checks_min": 1},
    use_breadth=True, breadth_threshold=0.45, use_smoothed=True
))

# ============================================================
# 汇总输出
# ============================================================

print("\n" + "=" * 70)
print("📊 优化轮次3 汇总结果")
print("=" * 70)

df = pd.DataFrame(variants)
# 排序：按收益降序
df = df.sort_values("return", ascending=False)

# 格式化输出
print(f"\n{'变体':<28s} {'交易':>4s} {'胜率':>6s} {'收益':>7s} {'年化':>7s} {'回撤':>7s} {'夏普':>6s} {'盈亏比':>6s} {'均持':>4s} {'耗时':>5s}")
print("-" * 95)
for _, r in df.iterrows():
    if "error" in r:
        print(f"{r['name']:<28s} ❌ {r['error']}")
        continue
    print(f"{r['name']:<28s} {r['trades']:4.0f} {r['win_rate']:5.1f}% {r['return']:+6.2f}% {r['annual_ret']:+6.2f}% "
          f"{r['max_dd']:+6.1f}% {r['sharpe']:5.2f} {r['profit_factor']:5.2f} {r['avg_hold']:4.0f}d {r['elapsed']:4.1f}s")

# 计算目标达成情况
print(f"\n{'='*70}")
print("🎯 目标达成分析")
print(f"{'='*70}")
print(f"  目标: 年化>15% | 回撤<-25% | 交易≥50笔/年(~75笔) | 夏普>1.0")
print()

targets = {
    "annual_ret": (15.0, ">="),
    "max_dd": (-25.0, ">"),
    "trades": (75, ">="),
    "sharpe": (1.0, ">="),
}

for _, r in df.iterrows():
    if "error" in r:
        continue
    checks = []
    ann = r.get("annual_ret", 0)
    dd = r.get("max_dd", -100)
    tr = r.get("trades", 0)
    sh = r.get("sharpe", 0)
    
    checks.append("✅" if ann >= 15 else "❌")
    checks.append("✅" if dd > -25 else "❌")
    checks.append("✅" if tr >= 75 else "❌")
    checks.append("✅" if sh >= 1.0 else "❌")
    passed = checks.count("✅")
    print(f"  {r['name']:<28s} 年化{ann:+6.1f}% {checks[0]} | "
          f"回撤{dd:+6.1f}% {checks[1]} | "
          f"交易{tr:4.0f} {checks[2]} | "
          f"夏普{sh:5.2f} {checks[3]} | {passed}/4")

# 保存结果
df.to_csv(OUTPUT_COMBINED_DIR / "optimization_round3.csv", index=False, encoding="utf-8-sig")
print(f"\n💾 结果已保存到 {OUTPUT_COMBINED_DIR / 'optimization_round3.csv'}")

# 最佳推荐
print(f"\n{'='*70}")
print("🏆 最佳变体推荐")
print(f"{'='*70}")
best = df[df["return"].notna()].iloc[0] if len(df) > 0 else None
if best is not None:
    print(f"  最高收益: {best['name']} ({best['return']:+.2f}%, 年化{best['annual_ret']:+.2f}%)")
    print(f"    交易{best['trades']:.0f}笔, 胜率{best['win_rate']:.1f}%, 回撤{best['max_dd']:+.1f}%, 夏普{best['sharpe']:.2f}")
    
best_dd = df[df["max_dd"].notna()].sort_values("max_dd", ascending=False).iloc[0]
if best_dd is not None and best_dd["name"] != best["name"]:
    print(f"  最低回撤: {best_dd['name']} (回撤{best_dd['max_dd']:+.1f}%)")
    
best_sharpe = df[df["sharpe"].notna()].sort_values("sharpe", ascending=False).iloc[0]
if best_sharpe is not None and best_sharpe["name"] != best["name"] and best_sharpe["name"] != best_dd["name"]:
    print(f"  最高夏普: {best_sharpe['name']} (夏普{best_sharpe['sharpe']:.2f})")

print(f"\n✅ 优化轮次3完成")
