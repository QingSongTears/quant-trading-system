"""
全量回测 — 对4874只未回测股票跑牛股策略
策略: bull_wave(七维共振牛股) + combo(综合多信号)
加速: 并行4线程
"""
import sys, json, time, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = Path(__file__).parent.parent
BASE = "http://localhost:8081"
PARAMS = "start=2024-01-01&end=2026-06-01&capital=100000"
STRATEGIES = [("bull_wave", "七维共振牛股"), ("combo", "综合多信号")]

# 1. 获取未回测但有K线的股票
print("📊 获取未回测股票...")
import sqlite3
db = PROJECT_ROOT / "database" / "quant.db"
conn = sqlite3.connect(str(db))
# 已回测的 (bull_wave + combo)
done = set()
rows = conn.execute("SELECT DISTINCT stock_code FROM backtest_result WHERE strategy_id IN (6,7) AND total_return IS NOT NULL").fetchall()
for r in rows: done.add(r[0])

# 有足够K线数据的
all_codes = []
rows = conn.execute("""
    SELECT code FROM daily_price GROUP BY code HAVING COUNT(*) >= 120 ORDER BY code
""").fetchall()
for r in rows:
    code = r[0].replace('sz','').replace('sh','').strip().zfill(6)
    if len(code) >= 6: all_codes.append(code)
conn.close()

# 未回测的
todo = sorted(set(all_codes) - done)
print(f"   有K线(≥120天): {len(all_codes)} 只")
print(f"   已回测(牛股策略): {len(done)} 只")
print(f"   待回测: {len(todo)} 只")
print(f"   总API调用: {len(todo) * len(STRATEGIES)} 次")

# 截取上限(全部跑完可能要很久, 先跑2000只)
BATCH_LIMIT = 2000
if len(todo) > BATCH_LIMIT:
    print(f"   分批: 先跑前{BATCH_LIMIT}只")
    todo = todo[:BATCH_LIMIT]

# 2. 并发回测
results = []
errors = []
t0 = time.time()

def backtest_one(code):
    """对一只股票跑两个牛股策略"""
    returns = []
    for sid, sname in STRATEGIES:
        url = f"{BASE}/api/stock/{code}/backtest?strategy={sid}&{PARAMS}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            if data.get("error"):
                returns.append((code, sid, None, None, None, None, data['error']))
            else:
                s = data.get("summary", {})
                returns.append((code, sname,
                    s.get("totalReturn"), s.get("sharpe"),
                    s.get("winRate"), s.get("totalTrades"), None))
        except Exception as e:
            returns.append((code, sname, None, None, None, None, str(e)))
    return returns

# 分批并发
batch_size = 200
for batch_start in range(0, len(todo), batch_size):
    batch_end = min(batch_start + batch_size, len(todo))
    batch = todo[batch_start:batch_end]
    
    batch_results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(backtest_one, code): code for code in batch}
        for f in as_completed(futures):
            try:
                batch_results.extend(f.result())
            except Exception as e:
                errors.append((futures[f], "thread", str(e)))
    
    # 统计
    for r in batch_results:
        code, sname, ret, sharpe, wr, trades, err = r
        if err:
            errors.append((code, sname, err))
        else:
            results.append(r)
    
    elapsed = time.time() - t0
    done_cnt = batch_end
    pct = done_cnt / len(todo) * 100
    rate = done_cnt / max(elapsed, 1)
    remain = (len(todo) - done_cnt) / max(rate, 0.01) / 60
    good = len([r for r in results if r[2] is not None])
    print(f"   [{done_cnt}/{len(todo)}] {pct:.0f}% | {elapsed/60:.1f}min | ETA{remain:.0f}min | 成功{good} 失败{len(errors)} | {rate:.1f}只/min")

    # 每批休息一下
    time.sleep(1)

elapsed = time.time() - t0
print(f"\n{'='*70}")
print(f"📊 全量回测完成")
print(f"{'='*70}")
print(f"   耗时: {elapsed/60:.1f}分钟")
print(f"   成功: {len(results)}")
print(f"   失败: {len(errors)}")

# 3. 按策略汇总
print(f"\n📈 按策略汇总:")
for sid, sname in STRATEGIES:
    sr = [r for r in results if r[1] == sname]
    rets = [r[2] for r in sr if r[2] is not None]
    if rets:
        avg = sum(rets)/len(rets)
        win = sum(1 for r in rets if r > 0)
        flat = sum(1 for r in rets if r == 0)
        active = sum(1 for r in rets if r != 0)
        wr = win/max(active,1)*100
        print(f"   {sname}: {len(sr):>4}只 | 平均{avg:>+7.2f}% | 盈利{win}/{active}({wr:.0f}%) | 空仓{flat}")

# 4. 模型精度验证（与XGBoost预测对比）
print(f"\n🎯 模型精度验证 (XGBoost v4 vs 回测):")
verify_results = []
for sid, sname in STRATEGIES:
    sr = [r for r in results if r[1] == sname and r[2] is not None and r[2] != 0]
    for r in sr[:]:  # 限制取样
        code = r[0]
        bt_ret = r[2]
        # 获取XGBoost预测
        try:
            url = f"{BASE}/api/predict/{code}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                d = json.loads(resp.read().decode())
            xgb_p = d.get("pred_proba_up", 0)
            verify_results.append({
                "code": code, "strategy": sname,
                "bt_return": bt_ret, "xgb_proba": xgb_p,
            })
        except:
            pass
        if len(verify_results) >= 500:  # 取500条验证即可
            break
    if len(verify_results) >= 500:
        break

if verify_results:
    print(f"   验证样本: {len(verify_results)} 条")
    bins = {">=0.55": [], "0.4-0.55": [], "<0.4": []}
    for r in verify_results:
        p = r['xgb_proba']
        if p >= 0.55: bins[">=0.55"].append(r['bt_return'])
        elif p >= 0.4: bins["0.4-0.55"].append(r['bt_return'])
        else: bins["<0.4"].append(r['bt_return'])
    
    for label, rets in bins.items():
        if rets:
            avg = sum(rets)/len(rets)
            pos = sum(1 for r in rets if r > 0)
            print(f"   {label:>10}: {len(rets):>4}只 | 平均{avg:>+7.2f}% | 盈利{pos}/{len(rets)}({pos/len(rets)*100:.0f}%)")

# 5. 保存
out_path = PROJECT_ROOT / "output" / "full_bull_backtest.txt"
with open(out_path, 'w') as f:
    f.write(f"全量牛股回测结果\n时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"股票: {len(results)//len(STRATEGIES)}只 | 策略: {len(STRATEGIES)} | 回测: {len(results)}次\n\n")
    for r in sorted(results, key=lambda x: x[2] or -999, reverse=True)[:100]:
        ret = r[2] or 0
        f.write(f"{r[0]:>8} {r[1]:>12} {ret:>+7.2f}%\n")
print(f"\n📝 结果已保存: {out_path}")
