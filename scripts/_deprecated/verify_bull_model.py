"""
牛股模型验证 — XGBoost预测 vs 实际回测匹配度
从DB中取出牛股共振策略回测最佳的100只股票，
用XGBoost预测它们，对比预测信号与实际回测收益
"""
import sys, json, time, urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.engine import get_engine
from src.db.sql_utils import read_sql

BASE = "http://localhost:8081"

# 1. 从DB取牛股共振策略回测表现最佳的100只股票
print("📊 从DB加载牛股共振策略回测结果...")

# 取所有牛股共振策略的回测记录
df = read_sql("""
    SELECT r.stock_code, r.total_return, r.sharpe_ratio, r.win_rate,
           r.max_drawdown, r.total_trades, r.benchmark_return, r.excess_return,
           r.start_date, r.end_date
    FROM backtest_result r
    LEFT JOIN strategy_config s ON r.strategy_id = s.id
    WHERE s.name = '七维共振牛股' AND r.total_return IS NOT NULL
    ORDER BY r.total_return DESC
""", get_engine())

rows = df.to_dict('records')

print(f"   牛股共振策略: {len(rows)} 条回测记录")

# 取前100只(最佳表现)
top_stocks = rows[:100]
print(f"   取TOP100: 收益范围 {top_stocks[-1]['total_return']:.1f}% ~ {top_stocks[0]['total_return']:.1f}%")

# 2. 对每只股票调用XGBoost预测 + 综合信号
results = []
errors = []
t0 = time.time()

print(f"\n🚀 开始批量验证 {len(top_stocks)} 只牛股...")

for i, row in enumerate(top_stocks):
    code = row['stock_code']
    
    # 调用综合信号API (含XGBoost预测)
    try:
        url = f"{BASE}/api/strategy/signal/{code}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            signal_data = json.loads(resp.read().decode())
        
        if signal_data.get('error'):
            errors.append((code, signal_data['error']))
            continue
        
        xgb_proba = signal_data.get('xgb_prediction', {}).get('proba', 0)
        xgb_signal = signal_data.get('xgb_prediction', {}).get('signal', '')
        composite = signal_data.get('composite_score', 0)
        comp_signal = signal_data.get('signal', '')
        position = signal_data.get('position', '')
        vote_for = signal_data.get('strategy_vote', {}).get('for', 0)
        vote_total = signal_data.get('strategy_vote', {}).get('total', 0)
        
        results.append({
            'code': code,
            'bt_return': row['total_return'],
            'bt_sharpe': row['sharpe_ratio'],
            'bt_win_rate': row['win_rate'],
            'bt_drawdown': row['max_drawdown'],
            'bt_trades': row['total_trades'],
            'xgb_proba': xgb_proba,
            'xgb_signal': xgb_signal,
            'composite': composite,
            'comp_signal': comp_signal,
            'position': position,
            'vote_for': vote_for,
            'vote_total': vote_total,
        })
        
    except Exception as e:
        errors.append((code, str(e)))
    
    if (i+1) % 20 == 0 or i == len(top_stocks)-1:
        elapsed = time.time() - t0
        print(f"   [{i+1}/{len(top_stocks)}] {elapsed:.0f}s | 成功{len(results)} 失败{len(errors)}")
    
    time.sleep(0.2)

# 3. 分析匹配度
print(f"\n{'='*70}")
print(f"📊 牛股模型验证结果")
print(f"{'='*70}")
print(f"   总股票: {len(top_stocks)}")
print(f"   成功: {len(results)}")
print(f"   失败: {len(errors)}")

if not results:
    print("❌ 无有效结果")
    sys.exit(1)

# 4. XGBoost预测分布
xgb_buys = [r for r in results if r['xgb_signal'] == '买入']
xgb_neutrals = [r for r in results if r['xgb_signal'] == '中性']
xgb_avoids = [r for r in results if r['xgb_signal'] == '回避']

print(f"\n📈 XGBoost预测分布:")
print(f"   买入(≥55%): {len(xgb_buys)}只 ({len(xgb_buys)/len(results)*100:.1f}%)")
print(f"   中性(40-55%): {len(xgb_neutrals)}只 ({len(xgb_neutrals)/len(results)*100:.1f}%)")
print(f"   回避(<40%): {len(xgb_avoids)}只 ({len(xgb_avoids)/len(results)*100:.1f}%)")

# 5. 关键指标：XGBoost买入的股票 vs 非买入的 实际回测收益对比
if xgb_buys:
    avg_buy_return = sum(r['bt_return'] for r in xgb_buys) / len(xgb_buys)
    print(f"\n   XGBoost买入组: 平均回测收益 {avg_buy_return:+.2f}%")
if xgb_avoids:
    avg_avoid_return = sum(r['bt_return'] for r in xgb_avoids) / len(xgb_avoids)
    print(f"   XGBoost回避组: 平均回测收益 {avg_avoid_return:+.2f}%")
if xgb_neutrals:
    avg_neutral_return = sum(r['bt_return'] for r in xgb_neutrals) / len(xgb_neutrals)
    print(f"   XGBoost中性组: 平均回测收益 {avg_neutral_return:+.2f}%")

# 6. 综合信号分布
comp_buys = [r for r in results if r['comp_signal'] == '买入']
comp_avoids = [r for r in results if r['comp_signal'] == '回避']
print(f"\n📊 综合信号分布:")
print(f"   买入: {len(comp_buys)}只 ({len(comp_buys)/len(results)*100:.1f}%)")
print(f"   回避: {len(comp_avoids)}只 ({len(comp_avoids)/len(results)*100:.1f}%)")

if comp_buys:
    avg_comp_buy = sum(r['bt_return'] for r in comp_buys) / len(comp_buys)
    print(f"   综合买入组: 平均回测收益 {avg_comp_buy:+.2f}%")
if comp_avoids:
    avg_comp_avoid = sum(r['bt_return'] for r in comp_avoids) / len(comp_avoids)
    print(f"   综合回避组: 平均回测收益 {avg_comp_avoid:+.2f}%")

# 7. 匹配度统计
print(f"\n🎯 匹配度分析:")

# XGBoost预测买入 → 实际回测正收益 = 命中
xgb_hits = sum(1 for r in xgb_buys if r['bt_return'] > 0)
xgb_hit_rate = xgb_hits / len(xgb_buys) * 100 if xgb_buys else 0
print(f"   XGBoost买入→实际正收益: {xgb_hits}/{len(xgb_buys)} ({xgb_hit_rate:.1f}%)")

# XGBoost回避 → 实际回测负收益 = 命中
xgb_avoid_hits = sum(1 for r in xgb_avoids if r['bt_return'] <= 0)
xgb_avoid_rate = xgb_avoid_hits / len(xgb_avoids) * 100 if xgb_avoids else 0
print(f"   XGBoost回避→实际非正收益: {xgb_avoid_hits}/{len(xgb_avoids)} ({xgb_avoid_rate:.1f}%)")

# 8. 概率分组 vs 实际收益
print(f"\n📊 XGBoost概率分组 vs 实际回测收益:")
bins = {'<0.3': [], '0.3-0.45': [], '0.45-0.55': [], '0.55-0.7': [], '>=0.7': []}
for r in results:
    p = r['xgb_proba']
    if p < 0.3: bins['<0.3'].append(r['bt_return'])
    elif p < 0.45: bins['0.3-0.45'].append(r['bt_return'])
    elif p < 0.55: bins['0.45-0.55'].append(r['bt_return'])
    elif p < 0.7: bins['0.55-0.7'].append(r['bt_return'])
    else: bins['>=0.7'].append(r['bt_return'])

for label, rets in bins.items():
    if rets:
        avg = sum(rets) / len(rets)
        pos = sum(1 for r in rets if r > 0)
        print(f"   {label:>10}: {len(rets):>3}只 | 平均{avg:>+7.2f}% | 正收益{pos}/{len(rets)} ({pos/len(rets)*100:.0f}%)")

# 9. TOP 20 详细
print(f"\n🏆 TOP 20 牛股验证明细:")
print(f"{'代码':>8} | {'回测收益':>8} | {'XGBoost':>8} | {'XGB信号':>6} | {'综合':>6} | {'仓位':>8} | {'投票':>6}")
print("-" * 70)
for r in results[:20]:
    print(f"{r['code']:>8} | {r['bt_return']:>+7.2f}% | {r['xgb_proba']*100:>6.1f}% | {r['xgb_signal']:>6} | {r['comp_signal']:>4} | {r['position']:>6} | {r['vote_for']}/{r['vote_total']}")

# 10. 保存
out_path = PROJECT_ROOT / "output" / "bull_model_verify.txt"
with open(out_path, 'w') as f:
    f.write(f"牛股模型验证报告\n")
    f.write(f"运行时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"样本: {len(results)}只 (牛股共振策略TOP100)\n\n")
    f.write(f"{'代码':>8} {'回测收益':>8} {'XGBoost':>8} {'XGB信号':>6} {'综合':>6} {'仓位':>8}\n")
    f.write("-" * 60 + "\n")
    for r in results:
        f.write(f"{r['code']:>8} {r['bt_return']:>+7.2f}% {r['xgb_proba']*100:>6.1f}% {r['xgb_signal']:>6} {r['comp_signal']:>4} {r['position']:>6}\n")
print(f"\n📝 结果已保存: output/bull_model_verify.txt")
