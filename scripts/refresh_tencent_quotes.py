#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 westock quote 拉全市场实时行情 → tencent_quotes.csv
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
BATCH = 200  # quote 支持更多

def load_codes():
    p = ROOT / "market_data" / "stock_profile.csv"
    with open(p, 'r', encoding='utf-8-sig') as f:
        r = csv.reader(f); next(r)
        return [row[0] for row in r if row and row[0].strip()]

def run_wes(args, timeout=180):
    cmd = [NODE_PATH, WESTOCK_SCRIPT] + args
    env = os.environ.copy(); env["NODE_OPTIONS"] = "--no-warnings"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode == 0, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return False, "", "timeout"

def parse_quote(stdout):
    rows, headers = [], None
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
    print("■ 加载股票代码...")
    codes = load_codes()
    print(f"  {len(codes)} 只")

    dst = ROOT / "market_data" / "raw" / "reference" / "tencent_quotes.csv"
    with open(dst, 'r', encoding='utf-8-sig') as f:
        old_header = next(csv.reader(f))

    all_rows = []
    total = (len(codes) + BATCH - 1) // BATCH
    for i in range(0, len(codes), BATCH):
        chunk = codes[i:i+BATCH]
        b = i // BATCH + 1
        ok, out, err = run_wes(["quote", ",".join(chunk)], timeout=180)
        if not ok:
            print(f"  ❌ batch {b}/{total}: {err[:80]}")
            time.sleep(2)
            continue
        rows, _ = parse_quote(out)
        all_rows.extend(rows)
        if b % 5 == 0 or b == total:
            print(f"  batch {b}/{total}: +{len(rows)} (累计 {len(all_rows)})")
        time.sleep(0.2)

    # 去重
    seen = set(); uniq = []
    for r in all_rows:
        if r.get('code') and r['code'] not in seen:
            seen.add(r['code']); uniq.append(r)
    print(f"\n■ 总计 {len(uniq)} 条 (原始 {len(all_rows)})")

    with open(dst, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=old_header, extrasaction='ignore')
        w.writeheader()
        w.writerows(uniq)
    print(f"✅ {dst}  ({len(uniq)} 行)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
