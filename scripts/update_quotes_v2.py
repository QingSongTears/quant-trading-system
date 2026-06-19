#!/usr/bin/env python3.11
"""
更新实时行情快照 tencent_quotes.csv
策略：
1. 用 WeStock kline 获取最新一天数据
2. 读取已有 CSV，按 code 做 merge 更新
3. 保留原有 PE/PB/市值等 WeStock 不提供的字段
"""

import subprocess
import csv
import time
from pathlib import Path
import pandas as pd

REPO_ROOT = Path(__file__).parent.parent
STOCK_PROFILE = REPO_ROOT / "A股全市场数据" / "stock_profile.csv"
QUOTES_CSV = REPO_ROOT / "A股全市场数据" / "tencent_quotes.csv"
DATE = "2026-06-18"
BATCH_SIZE = 500


def get_stock_codes():
    codes = []
    with open(STOCK_PROFILE, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if row and row[0].strip():
                codes.append(row[0].strip())
    return codes


def fetch_kline_latest(stock_codes):
    """批量获取最新一天K线"""
    codes_str = ",".join(stock_codes)
    cmd = (
        f"NODE_OPTIONS='--no-warnings' "
        f"npx -y westock-data-clawhub@1.0.4 "
        f"kline {codes_str} --period day --limit 1"
    )
    print(f"  获取最新行情: {len(stock_codes)}只...")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, shell=True
        )
        if result.returncode != 0:
            print(f"  ❌ 失败: {result.stderr[:200]}")
            return []
        lines = result.stdout.split('\n')
        records = []
        headers = None
        data_start = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if (line.startswith('| date |') or line.startswith('| symbol |')
                    or line.startswith('| code |')):
                headers = [h.strip() for h in line.split('|')[1:-1]]
                data_start = True
                continue
            if '---' in line:
                continue
            if data_start and line.startswith('|'):
                values = [v.strip() for v in line.split('|')[1:-1]]
                if len(values) == len(headers):
                    records.append(dict(zip(headers, values)))
        print(f"  ✅ 解析到 {len(records)} 条")
        return records
    except Exception as e:
        print(f"  ❌ 异常: {e}")
        return []


def main():
    print("=" * 60)
    print(f"更新实时行情快照: {DATE}")
    print("=" * 60)

    # 1. 读取已有快照
    print(f"\n[1/4] 读取已有快照...")
    if QUOTES_CSV.exists():
        df_old = pd.read_csv(QUOTES_CSV, encoding='utf-8-sig')
        print(f"  已有: {len(df_old)} 条")
    else:
        df_old = pd.DataFrame()

    # 2. 批量获取最新行情
    print(f"\n[2/4] 批量获取最新行情（分批）...")
    stock_codes = get_stock_codes()
    print(f"  股票总数: {len(stock_codes)}")

    all_records = []
    total_batches = (len(stock_codes) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(stock_codes), BATCH_SIZE):
        batch_num = i // BATCH_SIZE + 1
        batch = stock_codes[i:i + BATCH_SIZE]
        print(f"\n  批次 {batch_num}/{total_batches}")
        records = fetch_kline_latest(batch)
        all_records.extend(records)
        if batch_num < total_batches:
            time.sleep(2)

    print(f"\n✅ 总共获取 {len(all_records)} 条行情")

    if not all_records:
        print("❌ 未获取到数据")
        return

    # 3. 映射字段
    print(f"\n[3/4] 映射字段...")
    mapped = []
    for r in all_records:
        # WeStock 返回字段: symbol,date,open,last,high,low,volume,amount,exchange
        code = r.get('symbol') or r.get('code', '')
        if not code:
            continue
        try:
            open_p = float(r.get('open', 0))
            last_p = float(r.get('last', r.get('close', 0)))
            high_p = float(r.get('high', 0))
            low_p = float(r.get('low', 0))
            amount = float(r.get('amount', 0))
            exchange = float(r.get('exchange', 0))
        except Exception:
            continue

        change_amt = round(last_p - open_p, 2)
        change_pct = round(change_amt / open_p * 100, 2) if open_p > 0 else 0

        mapped.append({
            'code': code,
            'price': str(last_p),
            'last_close': str(open_p),
            'open': str(open_p),
            'high': str(high_p),
            'low': str(low_p),
            'change_amt': str(change_amt),
            'change_pct': str(change_pct),
            'turn_over_pct': str(exchange),
            'amount_wan': str(round(amount / 10000, 2)),
            'update_time': f"{DATE}T18:00:00.000000",
        })

    df_new = pd.DataFrame(mapped).set_index('code')
    print(f"  映射完成: {len(df_new)} 条")

    # 4. 用 merge 方式更新已有数据
    print(f"\n[4/4] 合并数据...")
    if not df_old.empty:
        df_old = df_old.set_index('code')
        common = df_old.index.intersection(df_new.index)
        print(f"  共同 code: {len(common)} 个")

        update_cols = [
            'price', 'last_close', 'open', 'high', 'low',
            'change_amt', 'change_pct', 'turn_over_pct',
            'amount_wan', 'update_time'
        ]
        for col in update_cols:
            if col in df_new.columns:
                df_old.loc[common, col] = df_new.loc[common, col].astype(str).values

        df_result = df_old.reset_index()
        print(f"  ✅ 已更新 {len(common)} 条")
    else:
        df_result = df_new.reset_index()

    # 保存
    df_result.to_csv(QUOTES_CSV, index=False, encoding='utf-8-sig')
    print(f"\n✅ 已保存 {len(df_result)} 条到 {QUOTES_CSV}")
    print(f"\n⚠️  注意: pe_ttm/pb/mcap_yi 等字段未更新（WeStock 不提供）")


if __name__ == "__main__":
    main()
