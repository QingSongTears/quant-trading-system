"""DB统计速览 — 展示回测策略汇总与TOP10"""
from __future__ import annotations
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.engine import get_engine
from src.db.sql_utils import read_sql

engine = get_engine()

cnt = read_sql('SELECT COUNT(*) as cnt FROM backtest_result WHERE total_return IS NOT NULL', engine)
print(f'💾 DB回测记录总数: {cnt.iloc[0, 0]} 条')
print()

df = read_sql('''
    SELECT s.name, COUNT(*) as cnt,
           AVG(r.total_return) as avg_ret,
           SUM(CASE WHEN r.total_return > 0 THEN 1 ELSE 0 END) as win,
           SUM(CASE WHEN r.total_return = 0 THEN 1 ELSE 0 END) as flat,
           MAX(r.total_return) as best,
           MIN(r.total_return) as worst,
           AVG(r.sharpe_ratio) as avg_sharpe
    FROM backtest_result r
    LEFT JOIN strategy_config s ON r.strategy_id = s.id
    WHERE r.total_return IS NOT NULL
    GROUP BY r.strategy_id
    ORDER BY avg_ret DESC
''', engine)

header = f"{'策略':>14} | {'记录':>4} | {'平均收益':>8} | {'盈利率':>6} | {'空仓':>4} | {'最佳':>8} | {'最差':>8} | {'均夏普':>6}"
print(header)
print('-' * 80)

for _, r in df.iterrows():
    name = r['name'] or '—'
    active = r['cnt'] - r['flat']
    win_rate = r['win']/active*100 if active else 0
    line = f"{name:>14} | {int(r['cnt']):>4} | {r['avg_ret']:>+7.2f}% | {win_rate:>5.0f}% | {int(r['flat']):>4} | {r['best']:>+7.1f}% | {r['worst']:>+7.1f}% | {r['avg_sharpe']:>+5.2f}"
    print(line)

print()
print('🏆 全局 TOP 10:')
top10 = read_sql('''
    SELECT r.stock_code, s.name, r.total_return, r.sharpe_ratio, r.win_rate, r.total_trades
    FROM backtest_result r
    LEFT JOIN strategy_config s ON r.strategy_id = s.id
    WHERE r.total_return IS NOT NULL
    ORDER BY r.total_return DESC LIMIT 10
''', engine)
for _, r in top10.iterrows():
    print(f"   {r['stock_code']} [{r['name']}]: {r['total_return']:+.2f}% 夏普={r['sharpe_ratio']} 胜率={r['win_rate']}% 交易={int(r['total_trades'])}笔")
