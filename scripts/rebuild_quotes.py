#!/usr/bin/env python3.11
"""
重建 tencent_quotes.csv（快照）
数据来源：
1. kline_daily.csv 最新一天（price/open/high/low/amount）
2. stock_profile.csv 提供完整股票列表和顺序
3. PE/PB/市值等字段留空（WeStock 不提供）
"""

import csv
from pathlib import Path
import pandas as pd

REPO_ROOT = Path(__file__).parent.parent
STOCK_PROFILE = REPO_ROOT / "market_data" / "stock_profile.csv"
KLINE_CSV = REPO_ROOT / "market_data" / "kline_daily.csv"
QUOTES_CSV = REPO_ROOT / "market_data" / "tencent_quotes.csv"
DATE = "2026-06-18"


def main():
    print("=" * 60)
    print(f"重建实时行情快照: {DATE}")
    print("=" * 60)

    # 1. 读取 stock_profile 获取完整股票列表（按顺序）
    print(f"\n[1/4] 读取 stock_profile...")
    profile_codes = []
    with open(STOCK_PROFILE, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if row and row[0].strip():
                profile_codes.append(row[0].strip())
    print(f"  ✅ {len(profile_codes)} 只股票")

    # 2. 读取 kline_daily 最新一天
    print(f"\n[2/4] 读取 kline_daily 最新一天...")
    df_kline = pd.read_csv(KLINE_CSV, encoding='utf-8-sig')
    latest_date = df_kline['date'].max()
    df_latest = df_kline[df_kline['date'] == latest_date].copy()
    print(f"  最新日期: {latest_date}")
    print(f"  有数据: {len(df_latest)} 只")

    # 建立 code -> 行情数据 的 dict
    quote_dict = {}
    for _, row in df_latest.iterrows():
        code = row['code']
        quote_dict[code] = {
            'open': row['open'],
            'high': row['high'],
            'low': row['low'],
            'close': row['close'],
            'amount': row['amount'],
        }

    # 3. 按 profile 顺序重建 tencent_quotes
    print(f"\n[3/4] 重建快照数据...")
    records = []
    for idx, code in enumerate(profile_codes):
        q = quote_dict.get(code, {})
        close = float(q.get('close', 0))
        open_p = float(q.get('open', close))
        high_p = float(q.get('high', close))
        low_p = float(q.get('low', close))
        amount = float(q.get('amount', 0))

        change_amt = round(close - open_p, 2)
        change_pct = round(change_amt / open_p * 100, 2) if open_p > 0 else 0

        # market 判断
        if code.startswith('sh'):
            market = 'sh'
        elif code.startswith('sz'):
            market = 'sz'
        elif code.startswith('bj'):
            market = 'bj'
        else:
            market = ''

        records.append({
            'code': code,
            'market': market,
            'name': '',  # 暂缺
            'price': str(close),
            'last_close': str(open_p),
            'open': str(open_p),
            'high': str(high_p),
            'low': str(low_p),
            'change_amt': str(change_amt),
            'change_pct': str(change_pct),
            'turn_over_pct': '',   # 暂无
            'pe_ttm': '',
            'pb': '',
            'mcap_yi': '',
            'float_mcap_yi': '',
            'limit_up': '',
            'limit_down': '',
            'vol_ratio': '',
            'pe_static': '',
            'amplitude_pct': '',
            'amount_wan': str(round(amount / 10000, 2)),
            'update_time': f"{DATE}T18:00:00.000000",
        })

    df_result = pd.DataFrame(records)
    print(f"  ✅ 重建 {len(df_result)} 条")

    # 4. 保存
    print(f"\n[4/4] 保存到 {QUOTES_CSV}...")
    df_result.to_csv(QUOTES_CSV, index=False, encoding='utf-8-sig')
    print(f"  ✅ 保存完成！")

    print("\n" + "=" * 60)
    print("✅ 实时行情快照重建完成！")
    print("=" * 60)
    print(f"  有行情数据: {len(df_latest)} 只")
    print(f"  总记录数: {len(df_result)} 条")
    print("⚠️  注意: PE/PB/市值等字段为空（需用其他数据源补充）")


if __name__ == "__main__":
    main()
