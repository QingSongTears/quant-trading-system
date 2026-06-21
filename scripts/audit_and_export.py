"""数据库验证 + 分类导出 CSV"""
import sys
sys.path.insert(0, 'D:/gitHub/qunat/quant-trading-system')

import os, json
from datetime import datetime
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine
from src.config import get_config, get_db_url
from src.db.sql_utils import read_sql

OUT = Path('D:/gitHub/qunat/quant-trading-system/output/audit_20260621')
OUT.mkdir(parents=True, exist_ok=True)

config = get_config()
engine = create_engine(get_db_url(config))

def check(name, df, rule, msg):
    ok = rule(df)
    print(f"  [{ 'OK' if ok else 'FAIL' }] {name}: {msg}")
    return ok

print('=' * 60)
print('DATABASE AUDIT & CSV EXPORT -', datetime.now().strftime('%Y-%m-%d %H:%M'))
print('=' * 60)

# ====== 1. TABLE HEALTH ======
print('\n[1] TABLE HEALTH')
checks = {}
for t in ['stock_basic','daily_price','backtest_result','technical_indicators',
          'fund_flow_data','dragon_tiger_data','margin_trading',
          'strategy_config','benchmark_data']:
    try:
        cnt = read_sql(f'SELECT COUNT(*) as n FROM {t}', engine).iloc[0,0]
        checks[t] = cnt
        print(f'  OK   {t:28s} {cnt:>10,} rows')
    except Exception as e:
        print(f'  FAIL {t:28s} {str(e)[:60]}')

# ====== 2. DAILY PRICE ======
print('\n[2] DAILY_PRICE QUALITY')
df_dp_stats = read_sql('''
SELECT MIN(trade_date) as start_date, MAX(trade_date) as end_date,
       COUNT(DISTINCT code) as stocks, COUNT(*) as total_rows
FROM daily_price
''', engine)
check('Date range', df_dp_stats, lambda d: d.iloc[0,0] is not None,
      f'{df_dp_stats.iloc[0,0]} to {df_dp_stats.iloc[0,1]}')
check('Stock count', df_dp_stats, lambda d: d.iloc[0,2] > 5000,
      f'{df_dp_stats.iloc[0,2]:,}')
check('Total rows', df_dp_stats, lambda d: d.iloc[0,3] > 1000000,
      f'{df_dp_stats.iloc[0,3]:,}')

nulls = read_sql('''
SELECT SUM(CASE WHEN open IS NULL THEN 1 ELSE 0 END) as o,
       SUM(CASE WHEN close IS NULL THEN 1 ELSE 0 END) as c,
       SUM(CASE WHEN volume IS NULL THEN 1 ELSE 0 END) as v
FROM daily_price
''', engine)
check('No NULL open/close/volume', nulls, lambda d: d.iloc[0,0]==0 and d.iloc[0,1]==0 and d.iloc[0,2]==0,
      f'open={nulls.iloc[0,0]}, close={nulls.iloc[0,1]}, volume={nulls.iloc[0,2]}')

# 数据完整性：检查最近30天每只股票的天数分布
dp_days = read_sql('''
SELECT code, COUNT(*) as days
FROM daily_price
WHERE trade_date >= '2026-01-01'
GROUP BY code
''', engine)
check('Recent stocks data', dp_days, lambda d: len(d) > 0,
      f'{len(dp_days)} stocks in 2026, avg {dp_days.days.mean():.0f} days')

# ====== 3. BACKTEST RESULT ======
print('\n[3] BACKTEST_RESULT QUALITY')
br_total = read_sql('SELECT COUNT(*) as n FROM backtest_result', engine)
print(f'  OK   Total backtests: {br_total.iloc[0,0]}')

br_dup = read_sql('''
SELECT COUNT(*) as n FROM (
  SELECT stock_code, strategy_id, COUNT(*) as cnt
  FROM backtest_result GROUP BY stock_code, strategy_id HAVING cnt > 1
) sub
''', engine)
check('No duplicates', br_dup, lambda d: d.iloc[0,0] == 0,
      f'{br_dup.iloc[0,0]} duplicate pairs')

br_null_ret = read_sql('''
SELECT COUNT(*) as n FROM backtest_result WHERE total_return IS NULL
''', engine)
check('All have return', br_null_ret, lambda d: d.iloc[0,0] == 0,
      f'{br_null_ret.iloc[0,0]} NULL returns')

# ====== 4. STOCK_BASIC ======
print('\n[4] STOCK_BASIC QUALITY')
sb = read_sql('''
SELECT COUNT(*) as total,
       SUM(CASE WHEN name IS NULL OR name='' THEN 1 ELSE 0 END) as no_name,
       COUNT(DISTINCT industry) as industries
FROM stock_basic
''', engine)
check('Stock count', sb, lambda d: d.iloc[0,0] > 5000, f'{sb.iloc[0,0]}')
check('All have names', sb, lambda d: d.iloc[0,1] == 0, f'{sb.iloc[0,1]} missing')
check('Industries', sb, lambda d: d.iloc[0,2] > 50, f'{sb.iloc[0,2]}')

# 检查 daily_price 中孤立的 code
orphan = read_sql('''
SELECT COUNT(DISTINCT d.code) as n
FROM daily_price d
LEFT JOIN stock_basic s ON d.code = s.code
WHERE s.code IS NULL
''', engine)
check('No orphan prices', orphan, lambda d: d.iloc[0,0] == 0,
      f'{orphan.iloc[0,0]} orphan codes in daily_price')

# ====== 5. STRATEGY CONFIG ======
print('\n[5] STRATEGY_CONFIG')
sc = read_sql('SELECT id, name FROM strategy_config ORDER BY id', engine)
print(f'  OK   {len(sc)} strategies registered')

# ====== EXPORT CSV ======
print('\n' + '=' * 60)
print('[6] EXPORTING CSV FILES')
print('=' * 60)

# 6.1 回测结果明细
br_detail = read_sql('''
SELECT br.id, br.stock_code, br.stock_name, sc.name as strategy, br.strategy_id,
       br.start_date, br.end_date, br.initial_capital, br.final_equity,
       ROUND(br.total_return, 2) as total_return_pct,
       ROUND(br.annual_return, 2) as annual_return_pct,
       ROUND(br.sharpe_ratio, 2) as sharpe,
       ROUND(br.max_drawdown, 2) as max_drawdown_pct,
       ROUND(br.win_rate, 1) as win_rate_pct,
       br.total_trades,
       ROUND(br.annual_volatility, 2) as annual_vol,
       ROUND(br.calmar_ratio, 2) as calmar,
       ROUND(br.benchmark_return, 2) as benchmark_return_pct,
       ROUND(br.excess_return, 2) as excess_return_pct,
       br.created_at
FROM backtest_result br
JOIN strategy_config sc ON br.strategy_id = sc.id
ORDER BY br.total_return DESC
''', engine)
f = OUT / 'backtest_results_detail.csv'
br_detail.to_csv(f, index=False, encoding='utf-8-sig')
print(f'  OK   {f.name} ({len(br_detail)} rows)')

# 6.2 策略汇总
br_summary = read_sql('''
SELECT sc.name as strategy,
       COUNT(*) as backtest_count,
       COUNT(DISTINCT br.stock_code) as stocks_tested,
       ROUND(AVG(br.total_return), 2) as avg_return_pct,
       ROUND(AVG(br.sharpe_ratio), 2) as avg_sharpe,
       ROUND(AVG(br.win_rate), 1) as avg_win_rate_pct,
       ROUND(AVG(br.max_drawdown), 2) as avg_max_dd_pct,
       ROUND(MAX(br.total_return), 2) as best_return_pct,
       ROUND(MIN(br.total_return), 2) as worst_return_pct,
       ROUND(AVG(br.total_trades), 0) as avg_trades,
       ROUND(SUM(CASE WHEN br.total_return > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as profit_pct
FROM backtest_result br
JOIN strategy_config sc ON br.strategy_id = sc.id
GROUP BY sc.name
ORDER BY AVG(br.total_return) DESC
''', engine)
f = OUT / 'backtest_strategy_summary.csv'
br_summary.to_csv(f, index=False, encoding='utf-8-sig')
print(f'  OK   {f.name} ({len(br_summary)} rows)')

# 6.3 个股最佳表现
br_stock_best = read_sql('''
SELECT br.stock_code, br.stock_name,
       COUNT(*) as strategies_tested,
       ROUND(AVG(br.total_return), 2) as avg_return_pct,
       ROUND(MAX(br.total_return), 2) as best_return_pct,
       ROUND(AVG(br.sharpe_ratio), 2) as avg_sharpe
FROM backtest_result br
GROUP BY br.stock_code
HAVING COUNT(*) >= 2
ORDER BY AVG(br.sharpe_ratio) DESC
LIMIT 30
''', engine)
f = OUT / 'top_stocks_by_sharpe.csv'
br_stock_best.to_csv(f, index=False, encoding='utf-8-sig')
print(f'  OK   {f.name} ({len(br_stock_best)} rows)')

# 6.4 数据覆盖率
coverage = read_sql('''
SELECT trade_date, COUNT(DISTINCT code) as stock_count, COUNT(*) as rows
FROM daily_price
GROUP BY trade_date
ORDER BY trade_date
''', engine)
f = OUT / 'daily_coverage.csv'
coverage.to_csv(f, index=False, encoding='utf-8-sig')
print(f'  OK   {f.name} ({len(coverage)} rows)')

# 6.5 行业分类
industries = read_sql('''
SELECT industry, COUNT(*) as stock_count
FROM stock_basic
WHERE industry IS NOT NULL AND industry != ''
GROUP BY industry
ORDER BY stock_count DESC
''', engine)
f = OUT / 'industry_distribution.csv'
industries.to_csv(f, index=False, encoding='utf-8-sig')
print(f'  OK   {f.name} ({len(industries)} rows)')

# 6.6 回测结果 JSON (Top 50)
br_json = br_detail.head(50)[['stock_code','stock_name','strategy','total_return_pct','sharpe','max_drawdown_pct','win_rate_pct','total_trades']].to_dict(orient='records')
f = OUT / 'backtest_top50.json'
with open(f, 'w', encoding='utf-8') as fh:
    json.dump(br_json, fh, ensure_ascii=False, indent=2, default=str)
print(f'  OK   {f.name}')

# ====== SUMMARY ======
print('\n' + '=' * 60)
print('AUDIT COMPLETE')
print(f'  Export dir: {OUT}')
print(f'  Files: {len(list(OUT.glob("*")))}')
print('=' * 60)

engine.dispose()
