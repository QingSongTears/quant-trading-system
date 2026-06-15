import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

#!/usr/bin/env python3
"""端到端测试脚本"""
import sys, pandas as pd
from datetime import datetime

from .core.data_fetcher import get_all_stocks_with_market_cap, get_stock_kline
from .core.screener import screen_candidates
from .core.signal_detector import batch_detect_signals
from .core.quant_detector import batch_detect_quant
from .core.buy_point import batch_evaluate_buy_points

print("=" * 60)
print(f"A股波段选股策略 v0.1 — {datetime.now().strftime('%Y-%m-%d')}")
print("=" * 60)

spot = get_all_stocks_with_market_cap()  # 全量 5209 只
candidates = screen_candidates(spot)
codes = candidates["code"].tolist()

kline_cache = {}
for i, code in enumerate(codes):
    try:
        k = get_stock_kline(code, days=200)
        if not k.empty:
            kline_cache[code] = k
    except:
        pass

signals = batch_detect_signals(codes, kline_cache)
print(f"\n📊 金叉信号: {len(signals)} 只")
signal_codes = signals["code"].tolist()

quant = batch_detect_quant(signal_codes, kline_cache)
bp = batch_evaluate_buy_points(signals, kline_cache)

# 合并信息
info = spot[["code", "name", "mcap_yi", "pe_ttm", "pb", "turnover_pct"]].set_index("code")
for col in ["name", "mcap_yi", "pe_ttm", "pb", "turnover_pct"]:
    bp[col] = bp["code"].map(info[col])
if not quant.empty:
    bp["quant_score"] = bp["code"].map(quant.set_index("code")["quant_score"])
    bp["quant_label"] = bp["code"].map(quant.set_index("code")["label"])
    bp["is_quant"] = bp["code"].map(quant.set_index("code")["is_quant_stock"])

bp["priority"] = bp["buy_ready"].astype(int) * 100 + bp["signal_score"].fillna(0)
bp = bp.sort_values("priority", ascending=False)

# 输出表格
header = f"{'排名':<4} {'代码':<8} {'名称':<10} {'市值(亿)':<10} {'信号':<5} {'买点':<5} {'量化':<6} {'位置':<10} {'状态':<8} 说明"
print(f"\n{'=' * len(header)}")
print(header)
print(f"{'=' * len(header)}")

for i, (_, r) in enumerate(bp.head(15).iterrows()):
    rank = i + 1
    buy_icon = "🟢" if r["buy_ready"] else "🟡"
    q_icon = "🤖" if r.get("is_quant") else "📊"
    desc = str(r.get("desc", ""))[:25]
    print(f"{rank:<4} {r['code']:<8} {str(r.get('name','')):<10} {r.get('mcap_yi',0):<10.0f} "
          f"{int(r.get('signal_score',0)):<5} {int(r.get('score',0)):<5} {q_icon:<6} "
          f"{r.get('current_position',''):<10} {buy_icon:<8} {desc}")

print(f"\n{'=' * len(header)}")
print("✅ 具备买点 (buy_ready=True):")
ready = bp[bp["buy_ready"]]
if not ready.empty:
    for _, r in ready.iterrows():
        print(f"  {r['code']} {r.get('name','')} | 市值{r.get('mcap_yi',0):.0f}亿 | PE{r.get('pe_ttm',0):.1f} | "
              f"{r.get('quant_label','')} | {r.get('desc','')}")
        stop = round(r["current_close"] * 0.9, 2)
        print(f"    → 止损: {stop} (-10%) | 当前价: {r['current_close']} | EMA20: {r.get('current_ma20','?')}")

print(f"\n📋 等待回调 (buy_ready=False, 信号有效):")
waiting = bp[~bp["buy_ready"]]
if not waiting.empty:
    for _, r in waiting.head(5).iterrows():
        print(f"  {r['code']} {r.get('name','')} | {r.get('current_position','')} | {r.get('desc','')}")

print(f"\n⚠️ 以上为量化策略筛选结果，仅供参考，不构成投资建议")
