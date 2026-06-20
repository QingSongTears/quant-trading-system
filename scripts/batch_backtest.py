"""
批量回测样本脚本
从评分数据中选取有代表性的股票（高分/中分/低分），
逐个跑七维评分穿越策略，结果保存到 DB
"""
import sys, json, time, os, sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent  # 项目根目录
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# ── 1. 加载评分数据 ──
scores_path = PROJECT_ROOT / "data" / "all_7d_scores.json"
print(f"📂 加载评分数据: {scores_path}")
with open(scores_path) as f:
    all_scores = json.load(f)
print(f"   共 {len(all_scores)} 条记录")

# ── 2. 取最新评分，按唯一股票聚合 ──
stock_scores = {}
for s in all_scores:
    code = s['code']
    if code not in stock_scores or s.get('as_of_date', '') > stock_scores[code].get('as_of_date', ''):
        stock_scores[code] = s

print(f"   唯一股票: {len(stock_scores)} 只")

dim_cols = ['tech_weighted','fundam_weighted','fund_weighted','institutional_weighted',
            'lh_institutional_weighted','sentiment_weighted','news_event_weighted','chip_weighted']

# 计算每只股票的最新平均评分
stock_list = []
for code, s in stock_scores.items():
    vals = [s.get(d, 0) or 0 for d in dim_cols]
    avg = sum(vals) / len(vals) if vals else 0
    stock_list.append({"code": code, "avg_score": avg, "score": s})

# ── 3. 按评分排序，挑取样板 ──
stock_list.sort(key=lambda x: x['avg_score'], reverse=True)

# 选 50 只：高分15 + 中分20 + 低分15
high = stock_list[:30]       # 评分最高30
mid_start = len(stock_list) // 2 - 15
mid = stock_list[mid_start:mid_start+30]  # 中间30
low = stock_list[-30:]       # 评分最低30

samples = high + mid + low
# 去重（理论上不会有重复）
seen = set()
unique_samples = []
for s in samples:
    if s['code'] not in seen:
        seen.add(s['code'])
        unique_samples.append(s)

print(f"\n📋 选取 {len(unique_samples)} 只股票")
print(f"   高分: {len(high)} → {[s['code'] for s in high[:5]]}...")
print(f"   中分: {len(mid)} → {[s['code'] for s in mid[:5]]}...")
print(f"   低分: {len(low)} → {[s['code'] for s in low[:5]]}...")

# ── 4. 检查 DB 中每只股票是否有足够 K 线数据 ──
db = PROJECT_ROOT / "database" / "quant.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

eligible = []
for s in unique_samples:
    code = s['code']
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM daily_price WHERE code=?",
        (code,)
    ).fetchone()
    cnt = row['cnt'] if row else 0
    if cnt >= 120:
        eligible.append(code)

conn.close()
print(f"\n📊 有足够K线数据(≥120天): {len(eligible)}/{len(unique_samples)} 只")

# 不够的就替换成评分接近的替代
if len(eligible) < 30:
    print("   数据不足30只，从更多股票中补充...")
    # 扩大池子：从全列表中按评分分段补充
    for s in stock_list:
        code = s['code']
        if code in eligible or code in seen:
            continue
        seen.add(code)
        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT COUNT(*) as cnt FROM daily_price WHERE code=?", (code,)).fetchone()
        cnt = row['cnt'] if row else 0
        conn.close()
        if cnt >= 120:
            eligible.append(code)
        if len(eligible) >= 60:
            break

print(f"   最终可用: {len(eligible)} 只")

# ── 5. 逐个跑回测 ──
STRATEGY_ID = "score_cross"  # 七维评分穿越
PARAMS = {
    "start": "2024-01-01",
    "end": "2026-06-01",
    "initial_capital": 100000,
    "stop_loss": -8,
    "take_profit": 15,
    "threshold": 8,
}

BASE_URL = "http://localhost:8081"

import urllib.request
import urllib.parse

results = []
errors = []

for i, code in enumerate(eligible):
    url = f"{BASE_URL}/api/stock/{code}/backtest?strategy={STRATEGY_ID}"
    url += f"&start={PARAMS['start']}&end={PARAMS['end']}&capital={PARAMS['initial_capital']}"
    
    print(f"\n[{i+1}/{len(eligible)}] {code} ... ", end="", flush=True)
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        
        if data.get("error"):
            print(f"❌ {data['error']}")
            errors.append((code, data['error']))
        else:
            summary = data.get("summary", {})
            ret = summary.get("totalReturn", "?")
            sharpe = summary.get("sharpe", "?")
            trades = summary.get("totalTrades", "?")
            print(f"✅ 收益={ret}% 夏普={sharpe} 交易={trades}笔")
            results.append({
                "code": code,
                "total_return": ret,
                "sharpe": sharpe,
                "trades": trades,
                "win_rate": summary.get("winRate"),
                "max_drawdown": summary.get("maxDrawdown"),
                "bh_return": summary.get("bhReturn"),
                "excess_return": summary.get("excessReturn"),
            })
    except Exception as e:
        print(f"❌ {e}")
        errors.append((code, str(e)))
    
    # 间隔一下，避免请求太快
    time.sleep(0.5)

# ── 6. 汇总 ──
print(f"\n{'='*60}")
print(f"📊 批量回测汇总")
print(f"{'='*60}")
print(f"   成功: {len(results)} / {len(eligible)}")
print(f"   失败: {len(errors)}")

if results:
    print(f"\n📈 收益排行 TOP 10:")
    results.sort(key=lambda x: x['total_return'] if x['total_return'] is not None else -999, reverse=True)
    for r in results[:10]:
        ret = r['total_return']
        ret_str = f"+{ret:.2f}%" if ret and ret >= 0 else f"{ret:.2f}%" if ret else "N/A"
        print(f"   {r['code']}: 收益={ret_str} 夏普={r['sharpe']} 胜率={r['win_rate']}% 交易={r['trades']}笔")
    
    # 统计
    returns = [r['total_return'] for r in results if r['total_return'] is not None]
    if returns:
        avg_ret = sum(returns) / len(returns)
        win = sum(1 for r in returns if r > 0)
        print(f"\n📊 统计:")
        print(f"   平均收益: {avg_ret:+.2f}%")
        print(f"   盈利比例: {win}/{len(returns)} ({win/len(returns)*100:.1f}%)")
        print(f"   最大收益: {max(returns):+.2f}%")
        print(f"   最小收益: {min(returns):+.2f}%")

if errors:
    print(f"\n⚠️ 错误列表:")
    for code, err in errors[:10]:
        print(f"   {code}: {err}")

# ── 7. 导出结果到文本 ──
out_path = PROJECT_ROOT / "output" / "batch_backtest_results.txt"
with open(out_path, "w") as f:
    f.write("批量回测结果\n")
    f.write(f"运行时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"策略: 七维评分穿越 (score_cross)\n")
    f.write(f"区间: {PARAMS['start']} ~ {PARAMS['end']}\n\n")
    f.write(f"{'代码':>8} {'收益':>8} {'夏普':>6} {'胜率':>6} {'回撤':>8} {'交易':>4} {'基准':>8} {'超额':>8}\n")
    f.write("-" * 70 + "\n")
    results.sort(key=lambda x: x['total_return'] if x['total_return'] is not None else -999, reverse=True)
    for r in results:
        f.write(f"{r['code']:>8} {r['total_return']:>+7.2f}% {r['sharpe']:>+5.2f} {r['win_rate'] or 0:>5.1f}% {r['max_drawdown'] or 0:>+7.2f}% {r['trades'] or 0:>4} {r['bh_return'] or 0:>+7.2f}% {r['excess_return'] or 0:>+7.2f}%\n")
    f.write(f"\n成功: {len(results)} / 总尝试: {len(eligible)}")

print(f"\n📝 结果已保存到: output/batch_backtest_results.txt")
