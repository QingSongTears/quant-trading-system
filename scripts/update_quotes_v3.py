#!/usr/bin/env python3.11
"""
更新实时行情快照 tencent_quotes.csv
策略（简单粗暴）：
1. 用 WeStock kline 获取全市场最新一天数据
2. 按行号顺序直接覆盖 tencent_quotes.csv 的行情相关列
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

# 需要更新的列（WeStock 能提供）
UPDATE_COLS = [
    'price', 'last_close', 'open', 'high', 'low',
    'change_amt', 'change_pct', 'turn_over_pct',
    'amount_wan', 'update_time'
]


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
    if not QUOTES_CSV.exists():
        print("  ❌ 文件不存在，无法更新")
        return

    df_old = pd.read_csv(QUOTES_CSV, encoding='utf-8-sig')
    print(f"  已有: {len(df_old)} 条")

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

    # 3. 按行号映射：all_records 的顺序与 stock_codes 一致
    #     即 all_records[i] 对应 stock_codes[i]
    #     而 df_old 的行号 i 也对应 stock_codes[i]
    print(f"\n[3/4] 按行号覆盖更新...")

    # 构建 code -> 行情数据 的 dict
    quote_map = {}
    for r in all_records:
        sym = r.get('symbol') or r.get('code', '')
        if sym:
            quote_map[sym] = r

    # 读取 stock_profile 的顺序（即 df_old 的行顺序）
    profile_codes = []
    with open(STOCK_PROFILE, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if row and row[0].strip():
                profile_codes.append(row[0].strip())

    update_count = 0
    for idx, code in enumerate(profile_codes):
        if code in quote_map:
            r = quote_map[code]
            try:
                open_p = float(r.get('open', 0))
                last_p = float(r.get('last', r.get('close', 0)))
                high_p = float(r.get('high', 0))
                low_p = float(r.get('low', 0))
                amount = float(r.get('amount', 0))
                exchange = float(r.get('exchange', 0))
                change_amt = round(last_p - open_p, 2)
                change_pct = round(change_amt / open_p * 100, 2) if open_p > 0 else 0

                # 按行号 idx 更新 df_old
                df_old.at[idx, 'price'] = str(last_p)
                df_old.at[idx, 'last_close'] = str(open_p)
                df_old.at[idx, 'open'] = str(open_p)
                df_old.at[idx, 'high'] = str(high_p)
                df_old.at[idx, 'low'] = str(low_p)
                df_old.at[idx, 'change_amt'] = str(change_amt)
                df_old.at[idx, 'change_pct'] = str(change_pct)
                df_old.at[idx, 'turn_over_pct'] = str(exchange)
                df_old.at[idx, 'amount_wan'] = str(round(amount / 10000, 2))
                df_old.at[idx, 'update_time'] = f"{DATE}T18:00:00.000000"
                update_count += 1
            except Exception as e:
                pass  # 跳过异常行

    print(f"  已更新: {update_count} 行")

    # 4. 保存
    print(f"\n[4/4] 保存...")
    df_old.to_csv(QUOTES_CSV, index=False, encoding='utf-8-sig')
    print(f"  ✅ 已保存 {len(df_old)} 条到 {QUOTES_CSV}")

    print("\n" + "=" * 60)
    print("✅ 实时行情快照更新完成！")
    print("=" * 60)
    print("⚠️  注意: pe_ttm/pb/mcap_yi 等字段未更新（WeStock 不提供）")


if __name__ == "__main__":
    main()
