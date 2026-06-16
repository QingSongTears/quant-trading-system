"""
优化轮次4 — 广度紧急清仓降回撤
基于轮次3发现：广度入口过滤对v3无效(信号自然避开差市)，但-79%回撤
是因为持股硬扛暴跌。本轮加广度紧急清仓：SMA5上涨占比跌破阈值时强制平仓。

测试变体（基础策略: v3基线）:
  A. v3基线 (参考)
  B. v3 + 广度清仓 SMA5<35%
  C. v3 + 广度清仓 SMA5<40%
  D. v3 + 广度清仓 SMA5<45%
  E. v3 + 广度清仓 SMA5<45% + 入口过滤 SMA5<45%
  F. v3_dynamic + 广度清仓 SMA5<40%
  G. v5_1of3 + 广度清仓 SMA5<40%
  H. v5_1of3 + 广度清仓 SMA5<40% + 入口过滤 SMA5<45%

目标: 降低回撤到-25%以内，同时不杀死太多信号
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import numpy as np
import time
from typing import Dict, Any

from config import OUTPUT_COMBINED_DIR
from core.data_loader import load_kline, load_quotes, load_finance
from backtest.engine import BacktestEngine
from strategies.v3_reversal import V3ReversalStrategy
from strategies.v3_reversal_dynamic import V3ReversalDynamicStrategy
from strategies.v5_hybrid import V5HybridStrategy

OUTPUT_COMBINED_DIR.mkdir(parents=True, exist_ok=True)

print("=" * 70)
print("📊 优化轮次4 — 广度紧急清仓降回撤")
print("=" * 70)
print("[INIT] 预加载数据...")
kline = load_kline()
quotes = load_quotes()
finance = load_finance()
print(f"[INIT] ✅ 就绪: K线{len(kline):,}行, 行情{len(quotes)}只")

def run_one(name, strategy_class, strategy_kwargs,
            use_exit=False, exit_thresh=0.40,
            use_filter=False, filter_thresh=0.45):
    engine = BacktestEngine(
        initial_capital=1_000_000,
        start_date="2025-01-01",
        end_date="2026-06-12",
        max_positions=10,
        single_position_pct=0.10,
        use_breadth_filter=use_filter,
        breadth_min_up_ratio=filter_thresh,
        use_breadth_exit=use_exit,
        breadth_exit_threshold=exit_thresh,
        breadth_exit_smoothed=True,
    )
    strategy = strategy_class(**strategy_kwargs)
    engine.add_strategy(strategy, weight=1.0)
    
    t0 = time.time()
    result = engine.run(verbose=False, kline=kline, quotes=quotes, finance=finance)
    elapsed = time.time() - t0
    
    if result is None:
        return {"name": name, "error": "回测失败"}
    sr = result.strategies.get(strategy.name)
    if not sr or not sr.trades:
        return {"name": name, "trades": 0, "return": 0, "max_dd": 0, "sharpe": 0, "win_rate": 0}

    trades = sr.trades
    wins = [t.return_pct for t in trades if t.return_pct > 0]
    losses = [t.return_pct for t in trades if t.return_pct <= 0]
    wr = len(wins)/len(trades)*100 if trades else 0
    pf = sum(wins)/abs(sum(losses)) if wins and losses and sum(losses)!=0 else 0
    avg_hold = np.mean([t.hold_days for t in trades]) if trades else 0
    
    # 按离场原因统计
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    
    days = (pd.Timestamp("2026-06-12") - pd.Timestamp("2025-01-01")).days
    annual_ret = ((1 + result.total_return/100) ** (365/days) - 1) * 100
    
    return {
        "name": name, "trades": len(trades),
        "return": round(result.total_return, 2),
        "annual_ret": round(annual_ret, 2),
        "max_dd": round(result.max_drawdown, 2),
        "sharpe": round(result.sharpe, 2),
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "avg_hold": round(avg_hold, 0),
        "elapsed": round(elapsed, 1),
        "exit_reasons": str(reasons),
        "breadth_exits": reasons.get("breadth_exit", 0),
    }

variants = []

# A: v3基线
print("\n[A] v3基线 (参考)...")
variants.append(run_one("A_v3基线", V3ReversalStrategy, {}))

# B: v3 + 广度清仓 SMA5<35%
print("\n[B] v3 + breadthExit SMA35...")
variants.append(run_one("B_v3+清仓SMA35", V3ReversalStrategy, {},
    use_exit=True, exit_thresh=0.35))

# C: v3 + 广度清仓 SMA5<40%
print("\n[C] v3 + breadthExit SMA40...")
variants.append(run_one("C_v3+清仓SMA40", V3ReversalStrategy, {},
    use_exit=True, exit_thresh=0.40))

# D: v3 + 广度清仓 SMA5<45%
print("\n[D] v3 + breadthExit SMA45...")
variants.append(run_one("D_v3+清仓SMA45", V3ReversalStrategy, {},
    use_exit=True, exit_thresh=0.45))

# E: v3 + 清仓SMA45 + 入口SMA45 (双管齐下)
print("\n[E] v3 + 清仓+入口 SMA45...")
variants.append(run_one("E_v3+清仓入口SMA45", V3ReversalStrategy, {},
    use_exit=True, exit_thresh=0.45, use_filter=True, filter_thresh=0.45))

# F: v3_dynamic + 广度清仓 SMA40
print("\n[F] v3动态止盈 + breadthExit SMA40...")
variants.append(run_one("F_v3动态+清仓SMA40", V3ReversalDynamicStrategy, {},
    use_exit=True, exit_thresh=0.40))

# G: v5_1of3 + 广度清仓 SMA40
print("\n[G] v5_1of3 + breadthExit SMA40...")
variants.append(run_one("G_v5_1of3+清仓SMA40", V5HybridStrategy, {"trend_checks_min": 1},
    use_exit=True, exit_thresh=0.40))

# H: v5_1of3 + 清仓SMA40 + 入口SMA45
print("\n[H] v5_1of3 + 清仓SMA40 + 入口SMA45...")
variants.append(run_one("H_v5_1of3+清仓入口", V5HybridStrategy, {"trend_checks_min": 1},
    use_exit=True, exit_thresh=0.40, use_filter=True, filter_thresh=0.45))

# ===== 汇总 =====
print("\n" + "=" * 90)
print("📊 优化轮次4 汇总结果")
print("=" * 90)
df = pd.DataFrame(variants)
df = df.sort_values("return", ascending=False)

print(f"\n{'变体':<30s} {'交易':>4s} {'强平':>4s} {'胜率':>6s} {'收益':>7s} {'年化':>7s} {'回撤':>8s} {'夏普':>5s} {'盈亏比':>6s} {'均持':>4s}")
print("-" * 95)
for _, r in df.iterrows():
    if "error" in r:
        print(f"{r['name']:<30s} ❌ {r['error']}")
        continue
    be = r.get("breadth_exits", 0)
    print(f"{r['name']:<30s} {r['trades']:4.0f} {be:4.0f} {r['win_rate']:5.1f}% {r['return']:+6.2f}% "
          f"{r['annual_ret']:+6.2f}% {r['max_dd']:+7.1f}% {r['sharpe']:4.2f} {r['profit_factor']:5.2f} {r['avg_hold']:4.0f}d")

# 目标达成
print(f"\n{'='*90}")
print("🎯 目标达成: 年化>15% | 回撤<-25% | 交易≥75笔 | 夏普>1.0")
print(f"{'='*90}")
for _, r in df.iterrows():
    if "error" in r: continue
    ann, dd, tr, sh = r.get("annual_ret", 0), r.get("max_dd", -100), r.get("trades", 0), r.get("sharpe", 0)
    c = [("✅" if ann>=15 else "❌"), ("✅" if dd>-25 else "❌"), ("✅" if tr>=75 else "❌"), ("✅" if sh>=1.0 else "❌")]
    print(f"  {r['name']:<30s} {c[0]}{c[1]}{c[2]}{c[3]}  {c.count('✅')}/4 | 年化{ann:+.1f}% 回撤{dd:+.1f}% 交易{tr:.0f} 夏普{sh:.2f}")

df.to_csv(OUTPUT_COMBINED_DIR / "optimization_round4.csv", index=False, encoding="utf-8-sig")
print(f"\n💾 已保存: {OUTPUT_COMBINED_DIR / 'optimization_round4.csv'}")

# 对比轮次3（无清仓）→ 轮次4（有清仓）的回撤改善
print(f"\n{'='*90}")
print("📈 回撤改善对比 (vs 轮次3 v3基线: -79.2%)")
print(f"{'='*90}")
for _, r in df.iterrows():
    if "error" in r: continue
    improvement = abs(r['max_dd']) - 79.2
    print(f"  {r['name']:<30s} 回撤{r['max_dd']:+6.1f}% → 改善{improvement:+.1f}%")

print("\n✅ 优化轮次4完成")
