#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 K线 2026 CSV 提取所有股票代码，调 westock profile 批量拉基础信息
覆盖 market_data/stock_profile.csv
"""
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
WESTOCK_SCRIPT = "C:/Users/aini7/.workbuddy/plugins/marketplaces/experts/plugins/stock-partner-team/skills/westock-data/scripts/index.js"
NODE_PATH = "C:/Users/aini7/.workbuddy/binaries/node/versions/22.22.2/node.exe"
BATCH = 100  # profile 一次 100 只

def load_codes_from_kline():
    """从所有 kline_daily_*.csv 提取 code 集合"""
    codes = set()
    for p in ROOT.glob("market_data/raw/kline_daily/kline_daily_*.csv"):
        with open(p, 'r', encoding='utf-8-sig') as f:
            r = csv.reader(f)
            h = next(r)
            ci = h.index('code') if 'code' in h else 0
            for row in r:
                if len(row) > ci and row[ci].strip():
                    codes.add(row[ci].strip())
    return sorted(codes)

def run_wes(args, timeout=120):
    cmd = [NODE_PATH, WESTOCK_SCRIPT] + args
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--no-warnings"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode == 0, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except Exception as e:
        return False, "", str(e)

def parse_profile(stdout):
    """解析 westock profile markdown 输出"""
    rows = []
    headers = None
    in_table = False
    for line in stdout.splitlines():
        line = line.rstrip()
        if not line or line.startswith("=") or line.startswith("[") or line.startswith("💡"):
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if "---" in line:
                in_table = True
                continue
            if headers is None:
                headers = cells
                continue
            if in_table and len(cells) == len(headers):
                rows.append(dict(zip(headers, cells)))
    return rows, headers

def main():
    print("■ 加载全市场代码...")
    codes = load_codes_from_kline()
    print(f"  全市场 {len(codes)} 只股票")

    dst = ROOT / "market_data" / "stock_profile.csv"
    # 读旧header
    with open(dst, 'r', encoding='utf-8-sig') as f:
        old_header = next(csv.reader(f))
    print(f"  旧字段: {old_header}")

    all_rows = []
    failed = []
    total = (len(codes) + BATCH - 1) // BATCH
    for i in range(0, len(codes), BATCH):
        chunk = codes[i:i+BATCH]
        b = i // BATCH + 1
        ok, out, err = run_wes(["profile", ",".join(chunk)], timeout=180)
        if not ok:
            print(f"  ❌ batch {b}/{total}: {err[:80]}")
            failed.extend(chunk)
            time.sleep(2)
            continue
        rows, hdr = parse_profile(out)
        all_rows.extend(rows)
        print(f"  batch {b}/{total}: +{len(rows)} 条")
        time.sleep(0.3)

    print(f"\n■ 共获取 {len(all_rows)} 条 (失败 {len(failed)} 只)")
    if not all_rows:
        print("❌ 没有任何数据，退出")
        return 1

    # 按代码去重
    seen = set()
    uniq = []
    for r in all_rows:
        if r['code'] not in seen:
            seen.add(r['code'])
            uniq.append(r)
    print(f"  去重后 {len(uniq)} 条")

    # 写回
    fieldnames = old_header
    with open(dst, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        w.writerows(uniq)
    print(f"✅ 已覆盖 {dst}  ({len(uniq)} 行)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
