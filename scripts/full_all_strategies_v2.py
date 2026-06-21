"""
全策略全量回测 v2 — 修正闭包问题
"""
import sys, json, time, urllib.request, sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial

BASE = "http://localhost:8081"
PARAMS = "start=2024-01-01&end=2026-06-01&capital=100000"

STRATEGIES = [
    (1, "score_cross", "七维评分穿越"),
    (2, "ma_cross", "均线金叉"),
    (3, "bollinger", "布林突破"),
    (4, "oversold", "超卖反转"),
    (5, "trend_follow", "趋势跟踪"),
]

def backtest_one(code, sid_str):
    """对一只股票跑一个策略"""
    url = f"{BASE}/api/stock/{code}/backtest?strategy={sid_str}&{PARAMS}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        if data.get("error"):
            return False
        return True
    except:
        return False

PROJECT_ROOT = Path(__file__).parent.parent
db = PROJECT_ROOT / "database" / "quant.db"
conn = sqlite3.connect(str(db))

all_codes = sorted(set(
    str(r[0]).replace('sz','').replace('sh','').strip().zfill(6)
    for r in conn.execute('SELECT DISTINCT code FROM daily_price GROUP BY code HAVING COUNT(*) >= 120')
))

t0 = time.time()
total_tasks = 0
total_ok = 0

for sid, sid_str, sname in STRATEGIES:
    done = set(r[0] for r in conn.execute(
        'SELECT stock_code FROM backtest_result WHERE strategy_id=? AND total_return IS NOT NULL', (sid,)))
    todo = sorted(set(all_codes) - done)
    if not todo:
        print(f"  [{sname}] 已完成({len(done)}条), 跳过")
        continue
    
    print(f"  [{sname}] 需补{len(todo)}条 (已有{len(done)})")
    conn.close()
    
    for batch_start in range(0, len(todo), 200):
        batch = todo[batch_start:batch_start+200]
        ok = 0
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(partial(backtest_one, code, sid_str)): code for code in batch}
            for f in as_completed(futures):
                if f.result(): ok += 1
        
        total_ok += ok
        total_tasks += len(batch)
        elapsed = time.time() - t0
        rate = total_ok / max(elapsed, 1)
        remain = ((sum(len(set(all_codes) - set()) for _ in STRATEGIES)) - total_tasks) / max(rate, 0.01) / 60
        print(f"    批{batch_start//200+1}: {ok}/{len(batch)} ok | 累计{total_ok} | {elapsed/60:.0f}min | ETA{remain:.0f}min")
        time.sleep(0.3)
    
    conn = sqlite3.connect(str(db))

elapsed = time.time() - t0
print(f"\n✅ 完成! 总成功{total_ok}/{total_tasks} | 耗时{elapsed/60:.1f}min")
conn = sqlite3.connect(str(db))
total = conn.execute('SELECT COUNT(*) FROM backtest_result WHERE total_return IS NOT NULL').fetchone()[0]
print(f"DB总记录: {total}条")
for sid, _, sname in STRATEGIES:
    cnt = conn.execute('SELECT COUNT(*) FROM backtest_result WHERE strategy_id=? AND total_return IS NOT NULL', (sid,)).fetchone()[0]
    print(f"  {sname}: {cnt}条")
conn.close()
