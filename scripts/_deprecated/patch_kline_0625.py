#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""快速补日K线 2026-06-25 → 追加到 CSV + DB。幂等：已有则跳过 / INSERT OR REPLACE"""
import csv, os, sqlite3, subprocess, sys, time

PKG = "westock-data-clawhub@1.0.4"
TARGET = "2026-06-25"
BATCH, PAUSE = 50, 0.3

ROOT = r"E:\work\work\quant-trading-system"
NPM = r"C:\Program Files\nodejs\npx.cmd"
CSV_PATH = ROOT + r"\market_data\raw\kline_daily\kline_daily_2026.csv"
DB_PATH  = ROOT + r"\database\quant.db"

# ── load codes ──
codes = []
with open(ROOT + r"\market_data\stock_profile.csv", "r", encoding="utf-8-sig") as f:
    for row in csv.reader(f):
        if row and row[0].strip():
            codes.append(row[0].strip())

print(f"股票池: {len(codes)} 只 | BATCH={BATCH} | 目标日期: {TARGET}")

# ── fetch ──
t0, all_rows = time.time(), []
env = os.environ.copy(); env["NODE_OPTIONS"] = "--no-warnings"

for b in range(0, len(codes), BATCH):
    chunk = codes[b:b + BATCH]
    r = subprocess.run(
        [NPM, "-y", PKG, "kline", ",".join(chunk), "--period", "day", "--date", TARGET],
        capture_output=True, encoding="utf-8", errors="replace", timeout=300, env=env)
    if r.stdout:
        for line in r.stdout.splitlines():
            if line.startswith("|") and "---" not in line and f"| {TARGET} |" in line:
                cells = [c.strip() for c in line.split("|")[1:-1]]
                all_rows.append(cells)
    if (b // BATCH + 1) % 20 == 0 or b + BATCH >= len(codes):
        elapsed = max(time.time() - t0, 0.01)
        est = elapsed / (b // BATCH + 1) * (len(codes) // BATCH + 1 - (b // BATCH + 1))
        print(f"  [{b//BATCH+1}/{len(codes)//BATCH+1}] rows={len(all_rows):,}  speed={len(all_rows)/elapsed:.0f}r/s  ETA={est:.0f}s")

print(f"\nfetch done: {len(all_rows):,} rows in {(time.time()-t0):.0f}s")

if not all_rows:
    print("no new data, exiting")
    sys.exit(0)

# ── parse & dedup ──
seen = set()
rows_dedup = []
for c in all_rows:
    key = c[0] + c[1]  # symbol + date
    if key not in seen:
        seen.add(key)
        rows_dedup.append(c)
print(f"去重: {len(all_rows)} → {len(rows_dedup)}")

# ── write CSV ──
import pandas as pd
df_new = pd.DataFrame([{"code": r[0].replace("sh","").replace("sz","").replace("bj",""),
                         "date": r[1], "open": r[2], "close": r[3],
                         "high": r[4], "low": r[5], "volume": r[6], "amount": r[7],
                         "pct_change": r[8]} for r in rows_dedup])

if os.path.exists(CSV_PATH):
    df_old = pd.read_csv(CSV_PATH, encoding="utf-8-sig", dtype={"code": str})
    df_old = df_old[df_old["date"] != TARGET]
    df_out = pd.concat([df_old, df_new], ignore_index=True)
else:
    df_out = df_new

df_out.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
print(f"CSV: 写入 {CSV_PATH} ({len(df_out):,} 行 total)")

# ── write DB ──
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
ins, skip = 0, 0
for r in rows_dedup:
    code = r[0].replace("sh","").replace("sz","").replace("bj","")
    try:
        cur.execute(
            "INSERT OR REPLACE INTO daily_price (code, trade_date, open, close, high, low, volume, amount, pct_change) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (code, r[1],
             float(r[2]) if r[2] and r[2] != "-" else None,
             float(r[3]) if r[3] and r[3] != "-" else None,
             float(r[4]) if r[4] and r[4] != "-" else None,
             float(r[5]) if r[5] and r[5] != "-" else None,
             int(float(r[6])) if r[6] and r[6] != "-" else None,
             float(r[7]) if r[7] and r[7] != "-" else None,
             float(r[8]) if r[8] and r[8] != "-" else None))
        ins += 1
    except Exception:
        skip += 1
        if skip <= 1:
            print(f"  DB err (first): {r[:4]}")

conn.commit(); conn.close()
print(f"DB: {ins} inserted, {skip} skipped | {(time.time()-t0):.0f}s total")
print("DONE")
