#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
update_hot_reason.py — 同花顺热点/题材归因增量更新 CSV + DB

数据源: westock hot (同花顺热点板块 + 个股归因)
当前 CSV (ths_hot_reason.csv) 停在 06-16，DB 也停在 06-16

用法:
  python scripts/update_hot_reason.py                             # 自动补最近7天
  python scripts/update_hot_reason.py --pull                      # 拉最新
  python scripts/update_hot_reason.py --export                    # 从 DB 导出 CSV
"""
import argparse
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
CSV_PATH = ROOT / "market_data" / "raw" / "reference" / "ths_hot_reason.csv"
NPM_PATH = "C:/Users/aini7/.workbuddy/binaries/node/versions/22.22.2/npx.cmd"
WESTOCK_PKG = "westock-data-clawhub@1.0.4"
CREATE_NO_WINDOW = 0x08000000


def run_wes(argv: list[str], timeout: int = 60) -> tuple[bool, str, str]:
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


def pull_hot_reason(d: str) -> int:
    """拉某天的同花顺热点归因 → DB"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 确保表存在
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ths_hot_reason (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code VARCHAR(10) NOT NULL,
            date DATE NOT NULL,
            name VARCHAR(50),
            close REAL,
            change_pct REAL,
            hot_reason TEXT,
            hot_rank INTEGER,
            UNIQUE(code, date)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_thr_date ON ths_hot_reason(date)")
    conn.commit()

    ok, out, err = run_wes(["hot", "--date", d], timeout=120)
    if not ok:
        print(f"  ❌ fail: {err[:80]}")
        conn.close()
        return 0

    rows = parse_table(out)
    if not rows:
        print(f"  {d}: 无热点数据")
        conn.close()
        return 0

    inserted = 0
    for r in rows:
        code_raw = str(r.get("code", ""))
        code6 = code_raw.replace("sh", "").replace("sz", "").replace("bj", "")
        try:
            cur.execute(
                "INSERT OR IGNORE INTO ths_hot_reason "
                "(code, date, name, close, change_pct, hot_reason, hot_rank) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (code6.zfill(6), d,
                 r.get("name", ""),
                 float(r.get("close", "0")) if r.get("close") else None,
                 float(r.get("change_pct", "0")) if r.get("change_pct") else None,
                 r.get("reason", r.get("hotReason", "")),
                 int(r.get("rank", "0")) if r.get("rank") else None),
            )
            if cur.rowcount > 0:
                inserted += 1
        except Exception:
            pass

    conn.commit()
    conn.close()
    print(f"  {d}: +{len(rows)} 条 (新增 {inserted})")
    return inserted


def export_csv_from_db():
    """导出 ths_hot_reason CSV"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM ths_hot_reason")
    total = cur.fetchone()[0]
    if total == 0:
        print("  DB 无同花顺热点数据")
        conn.close()
        return

    df = pd.read_sql_query(
        "SELECT code, date, name, close, change_pct, hot_reason, hot_rank "
        "FROM ths_hot_reason ORDER BY date DESC, code",
        conn,
    )
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    dates = pd.to_datetime(df["date"]).sort_values()
    print(f"  ✅ CSV 导出: {len(df)} 行, {dates.min().date()} ~ {dates.max().date()} → {CSV_PATH.name}")
    conn.close()


def daterange(start: str, end: str) -> list[str]:
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    days = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:
            days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pull", action="store_true", help="从 westock 拉取同花顺热点")
    parser.add_argument("--days", type=str, help="逗号分隔日期 YYYY-MM-DD")
    parser.add_argument("--export", action="store_true", help="从 DB 导出 CSV")
    parser.add_argument("--start", type=str, help="起始日期")
    parser.add_argument("--end", type=str, help="结束日期")
    args = parser.parse_args()

    t0 = time.time()

    if not args.pull and not args.export:
        # 默认: 拉取 + 导出
        args.pull = True
        args.export = True

    if args.pull:
        if args.days:
            days = [d.strip() for d in args.days.split(",") if d.strip()]
        elif args.start and args.end:
            days = daterange(args.start, args.end)
        else:
            days = daterange(
                (date.today() - timedelta(days=7)).strftime("%Y-%m-%d"),
                date.today().strftime("%Y-%m-%d"),
            )

        print("=" * 60)
        print(f"同花顺热点更新: {len(days)} 个交易日")
        print(f"日期: {days}")
        print("=" * 60)

        total = 0
        for d in days:
            total += pull_hot_reason(d)
        print(f"\n✅ 拉取完成: {total} 条新增 (耗时 {(time.time()-t0)/60:.1f}min)")

    if args.export:
        export_csv_from_db()

    return 0


if __name__ == "__main__":
    sys.exit(main())
