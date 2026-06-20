"""
大规模批量回测 v2
200只股票 × 3策略 = 600次回测
策略: score_cross / ma_cross / trend_follow
"""
import sys, json, time, urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# ── 1. 加载评分数据 ──
scores_path = PROJECT_ROOT / "data" / "all_7d_scores.json"
print(f"📂 加载评分数据...")
with open(scores_path) as f:
    all_scores = json.load(f)
print(f"   共 {len(all_scores)} 条记录")

# ── 2. 取最新评分，按唯一股票聚合 ──
stock_scores = {}
for s in all_scores:
    code = s['code']
    if code not in stock_scores or s.get('as_of_date', '') > stock_scores[code].get('as_of_date', ''):
        stock_scores[code] = s

dim_cols = ['tech_weighted','fundam_weighted','fund_weighted','institutional_weighted',
            'lh_institutional_weighted','sentiment_weighted','news_event_weighted','chip_weighted']

stock_list = []
for code, s in stock_scores.items():
    vals = [s.get(d, 0) or 0 for d in dim_cols]
    avg = sum(vals) / len(vals) if vals else 0
    stock_list.append({"code": code, "avg_score": avg})

stock_list.sort(key=lambda x: x['avg_score'], reverse=True)
print(f"   唯一股票: {len(stock_list)} 只")

# ── 3. 均匀采样200只 ──
import sqlite3
db = PROJECT_ROOT / "database" / "quant.db"

# 先筛出有足够K线的
print("🔍 筛选有K线数据的股票...")
eligible_codes = []
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
step = max(1, len(stock_list) // 300)  # 从全列表均匀取样
for i in range(0, len(stock_list), step):
    code = stock_list[i]['code']
    row = conn.execute("SELECT COUNT(*) as cnt FROM daily_price WHERE code=?", (code,)).fetchone()
    if row and row['cnt'] >= 120:
        eligible_codes.append(code)
    if len(eligible_codes) >= 250:
        break
conn.close()

# 取200只
samples = eligible_codes[:200]
print(f"   最终采样: {len(samples)} 只")

# ── 4. 策略列表 ──
strategies = [
    ("score_cross", "七维评分穿越"),
    ("ma_cross", "均线金叉"),
    ("trend_follow", "趋势跟踪"),
]

PARAMS = {
    "start": "2024-01-01",
    "end": "2026-06-01",
    "initial_capital": 100000,
}

BASE_URL = "http://localhost:8081"
results = []
errors = []
total_tasks = len(samples) * len(strategies)
done = 0

print(f"\n🚀 开始批量回测: {len(samples)}只 × {len(strategies)}策略 = {total_tasks}次")
print(f"   区间: {PARAMS['start']} ~ {PARAMS['end']}")
print(f"   预计耗时: ~{total_tasks * 1.5 / 60:.0f} 分钟")
print()

t0 = time.time()

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
                summary = data.get("summary", {})
                results.append({
                    "code": code,
                    "strategy": strat_id,
                    "strategy_name": strat_name,
                    "total_return": summary.get("totalReturn"),
                    "sharpe": summary.get("sharpe"),
                    "max_drawdown": summary.get("maxDrawdown"),
                    "win_rate": summary.get("winRate"),
                    "total_trades": summary.get("totalTrades"),
                    "bh_return": summary.get("bhReturn"),
                    "excess_return": summary.get("excessReturn"),
                })
        except Exception as e:
            errors.append((code, strat_id, str(e)))

        # 进度
        if done % 20 == 0 or done == total_tasks:
            elapsed = time.time() - t0
            pct = done / total_tasks * 100
            eta = (elapsed / done) * (total_tasks - done) / 60 if done > 0 else 0
            print(f"   [{done}/{total_tasks}] {pct:.0f}% | 已用{elapsed/60:.1f}min | 剩余{eta:.1f}min | 成功{len(results)} 失败{len(errors)}")

        time.sleep(0.3)

# ── 5. 汇总 ──
elapsed = time.time() - t0
print(f"\n{'='*70}")
print(f"📊 批量回测 v2 汇总")
print(f"{'='*70}")
print(f"   总任务: {total_tasks}")
print(f"   成功: {len(results)}")
print(f"   失败: {len(errors)}")
print(f"   耗时: {elapsed/60:.1f} 分钟")

# 按策略分组统计
print(f"\n📈 按策略分组:")
for strat_id, strat_name in strategies:
    strat_results = [r for r in results if r['strategy'] == strat_id]
    rets = [r['total_return'] for r in strat_results if r['total_return'] is not None]
    if rets:
        avg = sum(rets) / len(rets)
        win = sum(1 for r in rets if r > 0)
        active = sum(1 for r in rets if r != 0)
        print(f"   {strat_name:12s}: {len(strat_results):3d}只 | 平均{avg:+.2f}% | 盈利{win}/{active} ({win/max(active,1)*100:.0f}%) | 最佳{max(rets):+.1f}% | 最差{min(rets):+.1f}%")

# Top 10 总排行
print(f"\n🏆 TOP 10 收益排行:")
results.sort(key=lambda x: x['total_return'] if x['total_return'] is not None else -999, reverse=True)
for r in results[:10]:
    ret = r['total_return']
    print(f"   {r['code']} [{r['strategy_name']}]: {ret:+.2f}% 夏普={r['sharpe']} 胜率={r['win_rate']}% 交易={r['total_trades']}笔")

# ── 6. 保存结果 ──
out_path = PROJECT_ROOT / "output" / "batch_backtest_v2_results.txt"
with open(out_path, "w") as f:
    f.write("批量回测 v2 结果\n")
    f.write(f"运行时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"股票数: {len(samples)} | 策略数: {len(strategies)} | 总回测: {total_tasks}\n")
    f.write(f"区间: {PARAMS['start']} ~ {PARAMS['end']}\n\n")
    f.write(f"{'代码':>8} {'策略':>12} {'收益':>8} {'夏普':>6} {'胜率':>6} {'回撤':>8} {'交易':>4} {'基准':>8} {'超额':>8}\n")
    f.write("-" * 80 + "\n")
    for r in results:
        ret = r['total_return'] or 0
        f.write(f"{r['code']:>8} {r['strategy_name']:>12} {ret:>+7.2f}% {r['sharpe'] or 0:>+5.2f} {r['win_rate'] or 0:>5.1f}% {r['max_drawdown'] or 0:>+7.2f}% {r['total_trades'] or 0:>4} {r['bh_return'] or 0:>+7.2f}% {r['excess_return'] or 0:>+7.2f}%\n")

print(f"\n📝 结果已保存到: output/batch_backtest_v2_results.txt")
print(f"💾 DB中已有回测记录，可在 /backtest-lab 查看对比")
