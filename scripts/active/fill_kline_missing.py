#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
增量补 kline_daily.csv 缺失日期
目标：补 2026-06-17 / 2026-06-18 两天
数据源：WeStock kline
"""

import subprocess
import csv
import time
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent.parent
KLINE_CSV = ROOT / "market_data" / "kline_daily.csv"
STOCK_CSV = ROOT / "market_data" / "stock_profile.csv"
BATCH = 500


# ---- 工具函数 ----

def load_codes():
    codes = []
    with open(STOCK_CSV, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if row and row[0].strip():
                codes.append(row[0].strip())
    return codes


def fetch_kline(symbols: list, limit=1):
    cmd = (
        "NODE_OPTIONS='--no-warnings' "
        "npx -y westock-data-clawhub@1.0.4 "
        "kline {} --period day --limit {}".format(
            ",".join(symbols), limit
        )
    )
    r = subprocess.run(
        cmd, shell=True,
        capture_output=True, text=True, timeout=120
    )
    if r.returncode != 0:
        return []
    return parse_table(r.stdout)


def parse_table(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    headers = None
    rows = []
    in_table = False
    for l in lines:
        if l.startswith("| date ") or l.startswith("| symbol "):
            headers = [h.strip() for h in l.split("|")[1:-1]]
            in_table = True
            continue
        if "---" in l:
            continue
        if in_table and l.startswith("|"):
            vals = [v.strip() for v in l.split("|")[1:-1]]
            if len(vals) == len(headers):
                rows.append(dict(zip(headers, vals)))
    return rows


# ---- main ----

def main():
    print("=" * 60)
    print("增量补 kline_daily")
    print("=" * 60)

    # 1. 读已有数据
    print("\n[1/4] 读已有 kline_daily.csv ...")
    df_old = pd.read_csv(KLINE_CSV, encoding="utf-8-sig")
    old_max = df_old["date"].max()
    print("  已有最新日期:", old_max)
    print("  已有记录数:", len(df_old))

    codes = load_codes()
    print("  股票总数:", len(codes))

    # 2. 需要补的日期
    MISSING = ["2026-06-17", "2026-06-18"]
    need = [d for d in MISSING if d > old_max]
    if not need:
        print("\n✅ 无需补数据")
        return
    print("\n[2/4] 需补日期:", need)

    all_new = []
    for date_str in need:
        print("\n  ▶ 获取 {} ...".format(date_str))
        batches = (len(codes) + BATCH - 1) // BATCH
        date_rows = []
        for b in range(batches):
            chunk = codes[b * BATCH : (b + 1) * BATCH]
            rows = fetch_kline(chunk, limit=1)
            # 只保留 date == date_str 的行
            for r in rows:
                if r.get("date", "") == date_str:
                    date_rows.append(r)
            print("    batch {}/{}: +{}".format(b + 1, batches, len(rows)))
            time.sleep(1)
        print("  ✅ {} 获取: {} 条".format(date_str, len(date_rows)))
        all_new.extend(date_rows)

    print("\n[3/4] 映射字段...")
    mapped = []
    for r in all_new:
        sym = r.get("symbol", r.get("code", ""))
        if not sym:
            continue
        try:
            mapped.append({
                "code":   sym,
                "market": sym[:2],
                "name":   r.get("name", ""),
                "date":    r.get("date", ""),
                "open":    r.get("open", "0"),
                "high":    r.get("high", "0"),
                "low":     r.get("low", "0"),
                "close":   r.get("last", r.get("close", "0")),
                "volume":  r.get("volume", "0"),
                "amount":  r.get("amount", "0"),
            })
        except Exception:
            pass
    print("  映射:", len(mapped), "条")

    if not mapped:
        print("\n❌ 无新数据")
        return

    # 4. 合并保存
    print("\n[4/4] 合并保存...")
    df_new = pd.DataFrame(mapped)
    combined = pd.concat([df_old, df_new], ignore_index=True)
    combined = combined.drop_duplicates(subset=["code", "date"], keep="last")
    combined.to_csv(KLINE_CSV, index=False, encoding="utf-8-sig")
    print("  ✅ 保存完成!")
    print("  最终记录数:", len(combined))
    print("  最新日期:", combined["date"].max())


if __name__ == "__main__":
    main()
