"""调试 pivot 价格数据"""
import sys
from pathlib import Path
sys.path.insert(0, '.')
import pandas as pd
from src.config import get_config, get_db_url
from sqlalchemy import create_engine

engine = create_engine(get_db_url(get_config()))

q = "SELECT DISTINCT trade_date FROM daily_price WHERE trade_date BETWEEN '2026-05-20' AND '2026-06-20' ORDER BY trade_date"
df = pd.read_sql(q, engine)
print('交易日:')
print(df['trade_date'].tolist())

# 检查调仓选股 000568 的价格
q2 = "SELECT trade_date, close FROM daily_price WHERE code='000568' AND trade_date BETWEEN '2026-05-20' AND '2026-06-20' ORDER BY trade_date"
df2 = pd.read_sql(q2, engine)
print('\n000568 价格:')
print(df2.to_string())

# 检查所有调仓日选股的价格趋势
q3 = """
SELECT code, trade_date, close FROM daily_price
WHERE code IN ('000568','000524','000950','000975','000799')
  AND trade_date BETWEEN '2026-05-22' AND '2026-06-16'
ORDER BY code, trade_date
"""
df3 = pd.read_sql(q3, engine)
piv = df3.pivot(index='trade_date', columns='code', values='close')
print('\npivot:')
print(piv)

# 看 5月22日是否有数据
print('\n5月22日:')
print(piv.loc['2026-05-22'] if '2026-05-22' in piv.index else 'MISSING')