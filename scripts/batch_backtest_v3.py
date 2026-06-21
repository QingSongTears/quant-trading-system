"""
批量回测 v3
200只股票 × 4策略 = 800次
策略: bollinger / oversold / bull_wave / combo
"""
from __future__ import annotations
import sys, json, time, urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.engine import get_engine
from src.db.sql_utils import read_sql

BASE_URL = "http://localhost:8081"
PARAMS = {"start": "2024-01-01", "end": "2026-06-01", "initial_capital": 100000}

strategies = [
    ("bollinger", "布林突破"),
    ("oversold", "超卖反转"),
    ("bull_wave", "牛股共振"),
    ("combo", "综合多信号"),
]

# 从DB取已有K线数据的股票
df = read_sql("""
    SELECT DISTINCT r.stock_code FROM backtest_result r
    JOIN daily_price d ON r.stock_code = d.code
    WHERE r.total_return IS NOT NULL
    GROUP BY r.stock_code HAVING COUNT(d.trade_date) >= 120
    ORDER BY r.total_return DESC
""", get_engine())
codes = df["stock_code"].tolist()

samples = codes[:200]
total_tasks = len(samples) * len(strategies)

print(f"🚀 v3: {len(samples)}只 × {len(strategies)}策略 = {total_tasks}次")
t0 = time.time()
results, errors = [], []
done = 0

for si, code in enumerate(samples):
    for strat_id, strat_name in strategies:
        done += 1
        url = f"{BASE_URL}/api/stock/{code}/backtest?strategy={strat_id}"
        url += f"&start={PARAMS['start']}&end={PARAMS['end']}&capital={PARAMS['initial_capital']}"

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            if data.get("error"):
                errors.append((code, strat_id, data['error']))
            else:
                s = data.get("summary", {})
                results.append({
                    "code": code, "strategy": strat_id, "strategy_name": strat_name,
                    "total_return": s.get("totalReturn"),
                    "sharpe": s.get("sharpe"),
                    "max_drawdown": s.get("maxDrawdown"),
                    "win_rate": s.get("winRate"),
                    "total_trades": s.get("totalTrades"),
                    "bh_return": s.get("bhReturn"), "excess_return": s.get("excessReturn"),
                })
        except Exception as e:
            errors.append((code, strat_id, str(e)))

        if done % 30 == 0 or done == total_tasks:
            elapsed = time.time() - t0
            pct = done / total_tasks * 100
            eta = (elapsed / done) * (total_tasks - done) / 60 if done > 0 else 0
            print(f"   [{done}/{total_tasks}] {pct:.0f}% | {elapsed/60:.1f}min | ETA{eta:.1f}min | {len(results)}成功 {len(errors)}失败")
        time.sleep(0.3)

elapsed = time.time() - t0
print(f"\n{'='*70}")
print(f"📊 v3 完成: {len(results)}成功 / {total_tasks}总 | 耗时{elapsed/60:.1f}min")

for sid, sn in strategies:
    sr = [r for r in results if r['strategy'] == sid]
    rets = [r['total_return'] for r in sr if r['total_return'] is not None]
    if rets:
        avg = sum(rets)/len(rets)
        win = sum(1 for r in rets if r > 0)
        active = sum(1 for r in rets if r != 0)
        print(f"   {sn:12s}: {len(sr):3d} | 平均{avg:+.2f}% | 盈利{win}/{active}({win/max(active,1)*100:.0f}%) | 最佳{max(rets):+.1f}% | 最差{min(rets):+.1f}%")

out = PROJECT_ROOT / "output" / "batch_backtest_v3_results.txt"
with open(out, "w") as f:
    f.write(f"v3 运行: {time.strftime('%Y-%m-%d %H:%M:%S')}\n{len(samples)}只×{len(strategies)}={total_tasks}次\n\n")
    for r in sorted(results, key=lambda x: x['total_return'] or -999, reverse=True):
        ret = r['total_return'] or 0
        f.write(f"{r['code']:>8} {r['strategy_name']:>12} {ret:>+7.2f}% {r['sharpe'] or 0:>+5.2f} {r['win_rate'] or 0:>5.1f}% {r['total_trades'] or 0:>3}笔\n")
print(f"📝 output/batch_backtest_v3_results.txt")
