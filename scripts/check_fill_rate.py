import pandas as pd

df = pd.read_csv(r'E:\work\work\quant-trading-system\market_data\finance_summary.csv')
total = len(df)
ts_filled = df['totalShare'].notna().sum()
ls_filled = df['liqaShare'].notna().sum()
print(f'Total rows: {total}')
print(f'totalShare filled: {ts_filled}/{total} ({100*ts_filled/total:.1f}%)')
print(f'liqaShare filled: {ls_filled}/{total} ({100*ls_filled/total:.1f}%)')
print(f'totalShare missing: {total - ts_filled}')
print(f'liqaShare missing: {total - ls_filled}')
print()
print('Sample codes:', df['code'].head(3).tolist())
