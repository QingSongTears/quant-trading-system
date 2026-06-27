#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
update_dragon_tiger.py — 龙虎榜增量更新 CSV + DB

westock lhb 支持按日期查全市场龙虎榜，5 个 tab (jg/yzb/yyb/gslmr/gslxw)。
当前 CSV 文件缺失，DB 也无龙虎榜表，需从零建。

用法:
  python scripts/update_dragon_tiger.py --days 2026-06-17,2026-06-18,...,2026-06-24
  python scripts/update_dragon_tiger.py --start 2026-06-17 --end 2026-06-24
  python scripts/update_dragon_tiger.py                          # 自动补最近7个交易日
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
CSV_PATH = ROOT / "market_data" / "raw" / "reference" / "dragon_tiger.csv"
NPM_PATH = "C:/Users/aini7/.workbuddy/binaries/node/versions/22.22.2/npx.cmd"
WESTOCK_PKG = "westock-data-clawhub@1.0.4"
CREATE_NO_WINDOW = 0x08000000

# 5 个龙虎榜 tab
LHB_TABS = ["jg", "yzb", "yyb", "gslmr", "gslxw"]


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


def fetch_lhb_date(d: str) -> list[dict]:
    """拉某天全部 tab 的龙虎榜"""
    all_rows = []
    for tab in LHB_TABS:
        ok, out, err = run_wes(["lhb", "--tab", tab, "--date", d], timeout=120)
        if not ok:
            print(f"  SKIP tab={tab}: {err[:80]}")
            time.sleep(1)
            continue
        rows = parse_table(out)
        for r in rows:
            code_raw = str(r.get("code", ""))
            code6 = code_raw.replace("sh", "").replace("sz", "").replace("bj", "")
            all_rows.append({
                "code": code6.zfill(6),
                "market": code_raw[:2] if len(code_raw) >= 8 else "",
                "name": r.get("name", ""),
                "date": d,
                "tab": tab,
                "reason": r.get("Reason", r.get("reason", "")),
                "net_buy": r.get("NetBuy", r.get("netBuy", "0")),
                "buy_amount": r.get("BuyValue", r.get("Buy", "0")),
                "sell_amount": r.get("SellValue", r.get("Sell", "0")),
                "turnover_pct": r.get("TurnoverPct", r.get("turnoverPct", "0")),
            })
        print(f"  tab={tab}: +{len(rows)} 条")
        time.sleep(0.5)
    return all_rows


def ensure_db_table(conn: sqlite3.Connection):
    """建龙虎榜表(幂等)"""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dragon_tiger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code VARCHAR(10) NOT NULL,
            trade_date DATE NOT NULL,
            name VARCHAR(50),
            tab VARCHAR(10),
            reason TEXT,
            net_buy REAL,
            buy_amount REAL,
            sell_amount REAL,
            turnover_pct REAL,
            UNIQUE(code, trade_date, tab)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dt_date ON dragon_tiger(trade_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dt_code ON dragon_tiger(code)")
    conn.commit()


def update_db(rows: list[dict], conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    inserted = 0
    for r in rows:
        try:
            cur.execute(
                "INSERT OR IGNORE INTO dragon_tiger "
                "(code, trade_date, name, tab, reason, net_buy, buy_amount, sell_amount, turnover_pct) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r["code"], r["date"], r["name"], r["tab"], r["reason"],
                 float(r["net_buy"]) if r["net_buy"] and r["net_buy"] != "0" else None,
                 float(r["buy_amount"]) if r.get("buy_amount") and r["buy_amount"] != "0" else None,
                 float(r["sell_amount"]) if r.get("sell_amount") and r["sell_amount"] != "0" else None,
                 float(r["turnover_pct"]) if r["turnover_pct"] and r["turnover_pct"] != "0" else None),
            )
            if cur.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return inserted


def update_csv():
    """从 DB 导出龙虎榜 CSV"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM dragon_tiger")
    total = cur.fetchone()[0]
    if total == 0:
        print("  DB 无龙虎榜数据，跳过 CSV 导出")
        conn.close()
        return

    df = pd.read_sql_query(
        "SELECT code, trade_date AS date, name, tab, reason, net_buy, buy_amount, sell_amount, turnover_pct "
        "FROM dragon_tiger ORDER BY trade_date DESC, code, tab",
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
    parser.add_argument("--days", type=str, help="逗号分隔日期 YYYY-MM-DD")
    parser.add_argument("--start", type=str, help="起始日期")
    parser.add_argument("--end", type=str, help="结束日期")
    parser.add_argument("--csv-only", action="store_true", help="仅从 DB 导出 CSV")
    args = parser.parse_args()

    if args.csv_only:
        update_csv()
        return 0

    # 解析日期
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
    print(f"龙虎榜增量更新: {len(days)} 个交易日")
    print(f"日期: {days}")
    print("=" * 60)

    conn = sqlite3.connect(str(DB_PATH))
    ensure_db_table(conn)

    total_new = 0
    t0 = time.time()
    for d in days:
        print(f"\n📅 {d}")
        rows = fetch_lhb_date(d)
        if not rows:
            print(f"  无数据 ({d} 可能无龙虎榜)")
            continue
        n = update_db(rows, conn)
        total_new += n
        print(f"  ✅ 入库 {n} 条 (新增)")

    conn.close()

    # 导出 CSV
    update_csv()

    print(f"\n✅ 龙虎榜更新完成! 新增 {total_new} 条 (耗时 {(time.time()-t0)/60:.1f}min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
