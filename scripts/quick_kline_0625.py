#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""快速拉取 2026-06-25 日K线，追加到 CSV + DB"""
import csv
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"E:\work\work\quant-trading-system")
NPM = r"C:\Program Files\nodejs\npx.cmd"
PKG = "westock-data-clawhub@1.0.4"
BATCH = 500
TARGET_DATE = "2026-06-25"

def load_codes():
    p = ROOT / "market_data" / "stock_profile.csv"
    codes = []
    with open(p, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if row and row[0].strip():
                codes.append(row[0].strip())
    return codes

def run_wes(args, timeout=180):
    cmd = [NPM, "-y", PKG] + args
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--no-warnings"
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env,
                           encoding="utf-8", errors="replace")
        return r.returncode == 0, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return False, None, "timeout"
    except Exception as e:
        return False, None, str(e)

def parse_table(stdout):
    rows, headers = [], None
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("=") or line.startswith("💡"):
            continue
        if line.startswith("|") and "---" not in line:
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if headers is None:
                headers = cells
                continue
            if len(cells) == len(headers):
                rows.append(dict(zip(headers, cells)))
    return rows

def main():
    codes = load_codes()
    print(f"股票池: {len(codes)} 只, 目标: {TARGET_DATE}")

    total_batches = (len(codes) + BATCH - 1) // BATCH
    all_rows = []
    t0 = time.time()

    for b in range(total_batches):
        chunk = codes[b * BATCH: (b + 1) * BATCH]
        ok, out, err = run_wes(["kline", ",".join(chunk), "--period", "day", "--date", TARGET_DATE])
        if not ok:
            if b == 0:
                print(f"  ❌ batch 1: {err[:100]}")
            continue
        if not ok or out is None:
            if b == 0:
                print(f"  ❌ batch {b+1}: {err[:100]}")
            continue
        rows = parse_table(out)
        all_rows.extend(rows)
        if (b + 1) % 10 == 0 or b == total_batches - 1:
            elapsed = time.time() - t0
            speed = (b + 1) / max(elapsed, 0.01)
            eta = (total_batches - b - 1) / max(speed, 0.1)
            print(f"  batch {b+1}/{total_batches}: +{len(all_rows)} 条  {speed:.1f}批/s  ETA {eta:.0f}s")

    if not all_rows:
        print("❌ 无数据")
        return 1

    print(f"\n✅ 获取 {len(all_rows)} 条 K线")

    # 字段映射: westock → kline CSV
    csv_rows = []
    for r in all_rows:
        code_raw = str(r.get("code", ""))
        code6 = code_raw.replace("sh", "").replace("sz", "").replace("bj", "")
        market = code_raw[:2] if len(code_raw) >= 8 else ""
        csv_rows.append({
            "code": code6.zfill(6),
            "name": r.get("name", ""),
            "market": market,
            "date": TARGET_DATE,
            "open": r.get("open", "0"),
            "high": r.get("high", "0"),
            "low": r.get("low", "0"),
            "close": r.get("last", r.get("close", "0")),
            "volume": r.get("volume", "0"),
            "amount": r.get("amount", "0"),
            "turnover": r.get("exchange", r.get("turnover", "0")),
            "pct_change": r.get("pctChange", r.get("pct_change", "0")),
        })

    # 追加到 CSV
    csv_path = ROOT / "market_data" / "raw" / "kline_daily" / "kline_daily_2026.csv"
    existing_df = None
    if csv_path.exists():
        import pandas as pd
        existing_df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"code": str})

    import pandas as pd
    new_df = pd.DataFrame(csv_rows)

    if existing_df is not None:
        # 移除 06-25 旧行, 追加新行
        existing_df = existing_df[existing_df["date"] != TARGET_DATE]
        combined = pd.concat([existing_df, new_df], ignore_index=True)
    else:
        combined = new_df

    combined.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"📄 CSV: {len(combined):,} 行 → {csv_path.name}")

    # 写入 DB
    db_path = ROOT / "database" / "quant.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    inserted = 0
    for r in csv_rows:
        try:
            cur.execute(
                "INSERT OR REPLACE INTO daily_price "
                "(code, trade_date, open, high, low, close, volume, amount, pct_change, turnover) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r["code"], r["date"],
                 float(r["open"]) if r["open"] else None,
                 float(r["high"]) if r["high"] else None,
                 float(r["low"]) if r["low"] else None,
                 float(r["close"]) if r["close"] else None,
                 int(float(r["volume"])) if r["volume"] else None,
                 float(r["amount"]) if r["amount"] else None,
                 float(r["pct_change"]) if r["pct_change"] and r["pct_change"] != "0" else None,
                 float(r["turnover"]) if r["turnover"] and r["turnover"] != "0" else None),
            )
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            if inserted == 0:
                print(f"  DB err (first): {e}")

    conn.commit()
    conn.close()

    print(f"🗄  DB: {inserted}/{len(csv_rows)} 行入库")
    print(f"⏱  耗时: {(time.time()-t0)/60:.1f}min")
    return 0

if __name__ == "__main__":
    sys.exit(main())
