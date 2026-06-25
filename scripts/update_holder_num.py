#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
update_holder_num.py — 股东户数增量更新 CSV + DB

数据源: westock shareholder (返回最近几期股东户数)
当前 DB holder_num 表停在 2026-06-18，需补到 06-24
CSV (holder_num.csv) 不存在，从 DB 导出

用法:
  python scripts/update_holder_num.py                            # 自动补最近7天
  python scripts/update_holder_num.py --start 2026-06-19 --end 2026-06-24
  python scripts/update_holder_num.py --csv-only                 # 仅导出 CSV
"""
import argparse
import csv
import os
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "database" / "quant.db"
CSV_PATH = ROOT / "market_data" / "holder_num.csv"
NPM_PATH = "C:/Users/aini7/.workbuddy/binaries/node/versions/22.22.2/npx.cmd"
WESTOCK_PKG = "westock-data-clawhub@1.0.4"
BATCH = 200  # shareholder 小批量
CREATE_NO_WINDOW = 0x08000000


def load_codes() -> list[str]:
    from pathlib import Path as P
    p = ROOT / "market_data" / "stock_profile.csv"
    codes = []
    with open(p, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if row and row[0].strip():
                codes.append(row[0].strip())
    return codes


def run_wes(argv: list[str], timeout: int = 120) -> tuple[bool, str, str]:
    cmd = [NPM_PATH, "-y", WESTOCK_PKG] + argv
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--no-warnings"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=env,
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except Exception as e:
        return False, "", str(e)


def parse_table(stdout: str) -> list[dict]:
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


def pull_shareholder(codes: list[str]) -> int:
    """从 westock shareholder 拉全市场股东户数"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 确保表存在
    cur.execute("""
        CREATE TABLE IF NOT EXISTS holder_num (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            end_date DATE NOT NULL,
            holder_num INTEGER,
            pre_holder_num INTEGER,
            holder_change_pct REAL,
            avg_holding REAL,
            source TEXT DEFAULT 'westock',
            UNIQUE(code, end_date)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_hn_code_date ON holder_num(code, end_date)")
    conn.commit()

    total_batches = (len(codes) + BATCH - 1) // BATCH
    all_rows = []
    t0 = time.time()

    for b in range(total_batches):
        chunk = codes[b * BATCH: (b + 1) * BATCH]
        ok, out, err = run_wes(["shareholder", ",".join(chunk)], timeout=180)
        if not ok:
            if b == 0:
                print(f"  SKIP batch {b+1}: {err[:80]}")
            time.sleep(1)
            continue
        rows = parse_table(out)
        for r in rows:
            code_raw = str(r.get("code", ""))
            code6 = code_raw.replace("sh", "").replace("sz", "").replace("bj", "")
            end_date = r.get("EndDate", r.get("endDate", r.get("date", "")))
            holder_num_val = r.get("HolderNum", r.get("holderNum", ""))
            pre_holder = r.get("PreviousHolderNum", r.get("previousHolderNum", ""))
            change_pct = r.get("ChangeRatio", r.get("changeRatio", ""))
            avg_hold = r.get("AvgHolding", r.get("avgHolding", ""))

            if not end_date or not code6:
                continue

            try:
                holder_int = int(float(holder_num_val)) if holder_num_val else None
            except (ValueError, TypeError):
                holder_int = None

            all_rows.append({
                "code": code6.zfill(6),
                "end_date": end_date[:10] if len(str(end_date)) >= 10 else end_date,
                "holder_num": holder_int,
                "pre_holder_num": int(float(pre_holder)) if pre_holder else None,
                "holder_change_pct": float(change_pct) if change_pct else None,
                "avg_holding": float(avg_hold) if avg_hold else None,
            })

        if (b + 1) % 10 == 0 or b == total_batches - 1:
            elapsed = time.time() - t0
            speed = (b + 1) / max(elapsed, 0.01)
            eta = (total_batches - b - 1) / max(speed, 0.1)
            print(f"  batch {b+1}/{total_batches}: +{len(all_rows)} 条  {speed:.1f}批/s  ETA {eta:.0f}s")

    # 入库
    if all_rows:
        cur.execute("SELECT code, end_date FROM holder_num")
        existing = {(r[0], r[1]) for r in cur.fetchall()}

        inserted = 0
        for r in all_rows:
            if (r["code"], r["end_date"]) in existing:
                continue
            try:
                cur.execute(
                    "INSERT OR IGNORE INTO holder_num "
                    "(code, end_date, holder_num, pre_holder_num, holder_change_pct, avg_holding) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (r["code"], r["end_date"], r["holder_num"],
                     r["pre_holder_num"], r["holder_change_pct"], r["avg_holding"]),
                )
                if cur.rowcount > 0:
                    inserted += 1
            except Exception:
                pass
        conn.commit()
        print(f"  ✅ 入库 {inserted} 条 (总数 {len(all_rows)})")
    else:
        print("  ⚠️ 无数据可入库")
        inserted = 0

    conn.close()
    return inserted


def export_csv_from_db():
    """从 DB 导出股东户数 CSV"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM holder_num")
    total = cur.fetchone()[0]
    if total == 0:
        print("  DB 无股东户数数据")
        conn.close()
        return

    df = pd.read_sql_query(
        "SELECT code, end_date, holder_num, pre_holder_num, holder_change_pct, avg_holding, source "
        "FROM holder_num ORDER BY end_date DESC, code",
        conn,
    )
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    dates = pd.to_datetime(df["end_date"]).sort_values()
    print(f"  ✅ CSV 导出: {len(df)} 行, {dates.min().date()} ~ {dates.max().date()} → {CSV_PATH.name}")
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-only", action="store_true", help="仅导出 CSV")
    parser.add_argument("--pull", action="store_true", help="从 westock 拉取最新股东户数")
    args = parser.parse_args()

    if args.csv_only:
        export_csv_from_db()
        return 0

    t0 = time.time()

    if args.pull:
        codes = load_codes()
        print(f"股票池: {len(codes)} 只")
        print(f"分批: {BATCH} 只/批, 共 {(len(codes) + BATCH - 1) // BATCH} 批")
        n = pull_shareholder(codes)
        print(f"\n✅ 拉取完成: {n} 条新增 (耗时 {(time.time()-t0)/60:.1f}min)")

    export_csv_from_db()
    return 0


if __name__ == "__main__":
    sys.exit(main())
