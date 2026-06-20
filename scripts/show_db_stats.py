import sqlite3
conn = sqlite3.connect('database/quant.db')
conn.row_factory = sqlite3.Row

cnt = conn.execute('SELECT COUNT(*) FROM backtest_result WHERE total_return IS NOT NULL').fetchone()[0]
print(f'💾 DB回测记录总数: {cnt} 条')
print()

rows = conn.execute('''
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
''').fetchall()

header = f"{'策略':>14} | {'记录':>4} | {'平均收益':>8} | {'盈利率':>6} | {'空仓':>4} | {'最佳':>8} | {'最差':>8} | {'均夏普':>6}"
print(header)
print('-' * 80)
for r in rows:
    name = r['name'] or f'策略{r[0]}'
    active = r['cnt'] - r['flat']
    win_rate = r['win']/active*100 if active else 0
    line = f"{name:>14} | {r['cnt']:>4} | {r['avg_ret']:>+7.2f}% | {win_rate:>5.0f}% | {r['flat']:>4} | {r['best']:>+7.1f}% | {r['worst']:>+7.1f}% | {r['avg_sharpe']:>+5.2f}"
    print(line)

print()
print('🏆 全局 TOP 10:')
rows = conn.execute('''
    SELECT r.stock_code, s.name, r.total_return, r.sharpe_ratio, r.win_rate, r.total_trades
    FROM backtest_result r
    LEFT JOIN strategy_config s ON r.strategy_id = s.id
    WHERE r.total_return IS NOT NULL
    ORDER BY r.total_return DESC LIMIT 10
''').fetchall()
for r in rows:
    print(f"   {r['stock_code']} [{r['name']}]: {r['total_return']:+.2f}% 夏普={r['sharpe_ratio']} 胜率={r['win_rate']}% 交易={r['total_trades']}笔")

conn.close()
