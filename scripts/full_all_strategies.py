"""
全策略全量回测 — 补跑5个策略 × 4900+只 = 24718次
"""
import sys, json, time, urllib.request, sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "http://localhost:8081"
PARAMS = "start=2024-01-01&end=2026-06-01&capital=100000"

STRATEGIES = [
    (1, "score_cross", "七维评分穿越"),
    (2, "ma_cross", "均线金叉"),
    (3, "bollinger", "布林突破"),
    (4, "oversold", "超卖反转"),
    (5, "trend_follow", "趋势跟踪"),
]

PROJECT_ROOT = Path(__file__).parent.parent
db = PROJECT_ROOT / "database" / "quant.db"
conn = sqlite3.connect(str(db))

# 有K线≥120天的股票
all_codes = set()
for r in conn.execute('SELECT DISTINCT code FROM daily_price GROUP BY code HAVING COUNT(*) >= 120'):
    code = str(r[0]).replace('sz','').replace('sh','').strip().zfill(6)
    if len(code) >= 6: all_codes.add(code)

t0 = time.time()
total_tasks = 0
total_done = 0
total_errors = 0

print(f"🚀 启动5策略全量回测")
print(f"   总股票: {len(all_codes)}只")

for sid, sid_str, sname in STRATEGIES:
    # 已跑的
    done = set()
    for r in conn.execute('SELECT stock_code FROM backtest_result WHERE strategy_id=? AND total_return IS NOT NULL', (sid,)):
        done.add(r[0])
    todo = sorted(all_codes - done)
    tasks = len(todo)
    total_tasks += tasks
    batch_results = []
    batch_errors = []
    
    print(f"\n📊 [{sname}] 需补{tasks}条 (已有{len(done)})")
    
    for batch_start in range(0, len(todo), 200):
        batch_end = min(batch_start + 200, len(todo))
        batch = todo[batch_start:batch_end]
        
API_KEY = "ZsvwN_CFO5KlT7Sp4g3j48bZiL-rGbIMUN7xlgheeDk"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

def backtest_one(code):
    url = f"{BASE}/api/stock/{code}/backtest?strategy={sid_str}&{PARAMS}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        if data.get("error"):
            return (code, None, data['error'])
        s = data.get("summary", {})
        return (code, s.get("totalReturn"), None)
    except Exception as e:
        return (code, None, str(e))
        
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(backtest_one, code): code for code in batch}
            for f in as_completed(futures):
                code, ret, err = f.result()
                if err:
                    batch_errors.append((code, err))
                else:
                    batch_results.append((code, ret))
                total_done += 1
        
        elapsed = time.time() - t0
        rate = total_done / max(elapsed, 1)
        remain = (total_tasks - total_done) / max(rate, 0.01) / 60
        print(f"   [{batch_end}/{tasks}] 累计{total_done}/{total_tasks} | {elapsed/60:.0f}min | ETA{remain:.0f}min | 成功率{total_errors/max(total_done,1)*100:.0f}%")
        time.sleep(0.5)
    
    total_errors += len(batch_errors)

elapsed = time.time() - t0
print(f"\n{'='*60}")
print(f"✅ 全策略全量回测完成")
print(f"   总任务: {total_tasks}")
print(f"   耗时: {elapsed/60:.1f}分钟")
print(f"   成功率: {(total_tasks-total_errors)/total_tasks*100:.1f}%")

# 最终统计
print(f"\n📊 DB最终状态:")
conn2 = sqlite3.connect(str(db))
total = conn2.execute('SELECT COUNT(*) FROM backtest_result WHERE total_return IS NOT NULL').fetchone()[0]
codes = conn2.execute('SELECT COUNT(DISTINCT stock_code) FROM backtest_result').fetchone()[0]
print(f"   总记录: {total}条")
print(f"   覆盖股票: {codes}只")
for sid, _, sname in STRATEGIES:
    cnt = conn2.execute('SELECT COUNT(*) FROM backtest_result WHERE strategy_id=? AND total_return IS NOT NULL', (sid,)).fetchone()[0]
    print(f"   {sname}: {cnt}条")
conn2.close()
