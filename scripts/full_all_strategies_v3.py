"""
全策略全量回测 v3 — 修正策略ID映射，支持全部7策略
"""
import sys, json, time, urllib.request, sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial

BASE = "http://localhost:8081"
PARAMS = "start=2024-01-01&end=2026-06-01&capital=100000"

PROJECT_ROOT = Path(__file__).parent.parent
db = PROJECT_ROOT / "database" / "quant.db"

# 从DB读取正确的策略映射
conn = sqlite3.connect(str(db))
rows = conn.execute("SELECT id, class_path, name FROM strategy_config ORDER BY id").fetchall()
conn.close()

# 生成策略列表: (db_id, func_name, display_name)
ALL_STRATEGIES = [(r[0], r[1].split(".")[-1], r[2]) for r in rows]
# 跳过已经100%完成的（七维评分穿越 5166, 均线金叉 5161, 七维共振牛股 5161, 综合多信号 5161）
# 但脚本会自动跳过已完成的，所以全部加入
STRATEGIES = ALL_STRATEGIES

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

conn = sqlite3.connect(str(db))
all_codes = sorted(set(
    str(r[0]).replace('sz','').replace('sh','').strip().zfill(6)
    for r in conn.execute('SELECT DISTINCT code FROM daily_price GROUP BY code HAVING COUNT(*) >= 120')
))
conn.close()

print(f"\n{'='*60}")
print(f"全策略全量回测 v3")
print(f"总股票数: {len(all_codes)}")
print(f"策略数: {len(STRATEGIES)}")
for sid, fn, name in STRATEGIES:
    print(f"  ID={sid}: {name} ({fn})")
print(f"{'='*60}\n")

t0 = time.time()
total_tasks = 0
total_ok = 0

for sid, sid_str, sname in STRATEGIES:
    conn = sqlite3.connect(str(db))
    done = set(r[0] for r in conn.execute(
        'SELECT stock_code FROM backtest_result WHERE strategy_id=? AND total_return IS NOT NULL', (sid,)))
    todo = sorted(set(all_codes) - done)
    conn.close()
    
    if not todo:
        print(f"  [{sname}] 已完成({len(done)}条), 跳过")
        continue
    
    print(f"  [{sname}] 需补{len(todo)}条 (已有{len(done)}条)")
    
    for batch_start in range(0, len(todo), 200):
        batch = todo[batch_start:batch_start+200]
        ok = 0
        with ThreadPoolExecutor(max_workers=12) as ex:
            futures = {ex.submit(partial(backtest_one, code, sid_str)): code for code in batch}
            for f in as_completed(futures):
                if f.result(): ok += 1
        
        total_ok += ok
        total_tasks += len(batch)
        elapsed = time.time() - t0
        rate = total_ok / max(elapsed, 1)
        remain = ((sum(len(set(all_codes)) for _ in STRATEGIES)) - total_tasks) / max(rate, 0.01) / 60
        batch_no = batch_start // 200 + 1
        print(f"    [{sname}] 批{batch_no}: {ok}/{len(batch)} ok | 累计{total_ok} | {elapsed/60:.0f}min | ETA约{remain:.0f}min")
        time.sleep(0.3)

elapsed = time.time() - t0
print(f"\n{'='*60}")
print(f"✅ 完成! 总成功{total_ok}/{total_tasks} | 耗时{elapsed/60:.1f}min")

conn = sqlite3.connect(str(db))
total = conn.execute('SELECT COUNT(*) FROM backtest_result WHERE total_return IS NOT NULL').fetchone()[0]
print(f"DB总记录: {total}条")
for sid, fn, sname in STRATEGIES:
    cnt = conn.execute('SELECT COUNT(*) FROM backtest_result WHERE strategy_id=? AND total_return IS NOT NULL', (sid,)).fetchone()[0]
    print(f"  {sname}: {cnt}条")
conn.close()
