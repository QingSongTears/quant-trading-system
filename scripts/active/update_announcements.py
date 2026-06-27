#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
update_announcements.py — 增量追补公告数据到 CSV + DB

数据源: westock reserve (业绩预告) + 网络搜索补充
当前 CSV (announcements.csv) 停在 06-16，DB 已到 06-24 (通过 fill_recent_generic 同款入口)。
本脚本从 DB 导出最新 CSV 覆盖。

对于真正需要拉取的: westock reserve 返回业绩预告/快报/年报。
公告数据(announcements)主要通过`import_lhb_institutional.py`和`fill_recent_generic.py`入库。

用法:
  python scripts/update_announcements.py                         # 从 DB 导出最新 CSV
  python scripts/update_announcements.py --pull                  # 从 westock 拉取并入库
  python scripts/update_announcements.py --days 2026-06-17,2026-06-24  # 指定日期
"""
import argparse
import csv
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "database" / "quant.db"
CSV_PATH = ROOT / "market_data" / "raw" / "reference" / "announcements.csv"
NPM_PATH = "C:/Users/aini7/.workbuddy/binaries/node/versions/22.22.2/npx.cmd"
WESTOCK_PKG = "westock-data-clawhub@1.0.4"
BATCH = 500
CREATE_NO_WINDOW = 0x08000000


def load_codes() -> list[str]:
    """从 stock_profile.csv 读取股票代码"""
    p = ROOT / "market_data" / "stock_profile.csv"
    codes = []
    with open(p, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if row and row[0].strip():
                codes.append(row[0].strip())
    return codes


def run_wes(argv: list[str], timeout: int = 120) -> tuple[bool, str, str]:
    """调用 westock CLI"""
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
    """解析 westock markdown 表格"""
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


def pull_reserve(dates: list[str], codes: list[str]) -> int:
    """从 westock reserve 拉取业绩预告/快报，写入 DB announcements 表"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    total_new = 0
    for d in dates:
        print(f"\n■ announcements {d}")
        # 查询 DB 已有
        cur.execute("SELECT code FROM announcements WHERE trade_date = ?", (d,))
        existing = {r[0] for r in cur.fetchall()}

        new_rows = []
        total_batches = (len(codes) + BATCH - 1) // BATCH
        for b in range(total_batches):
            chunk = codes[b * BATCH: (b + 1) * BATCH]
            ok, out, err = run_wes(["reserve", ",".join(chunk), "--date", d])
            if not ok:
                if b == 0:
                    print(f"  SKIP batch {b+1}: {err[:80]}")
                continue
            rows = parse_table(out)
            for r in rows:
                code6 = str(r.get("code", "")).replace("sh", "").replace("sz", "").replace("bj", "")
                if code6 in existing:
                    continue
                new_rows.append({
                    "code": code6.zfill(6),
                    "trade_date": d,
                    "type": r.get("type", r.get("Type", "")),
                    "title": r.get("title", r.get("Title", "")),
                    "url": r.get("url", r.get("Url", "")),
                })
            if (b + 1) % 3 == 0 or b == total_batches - 1:
                print(f"  batch {b+1}/{total_batches}: +{len(new_rows)} 条")

        if new_rows:
            for row in new_rows:
                try:
                    cur.execute(
                        "INSERT OR IGNORE INTO announcements (code, trade_date, type, title, url) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (row["code"], row["trade_date"], row["type"], row["title"], row["url"]),
                    )
                    if cur.rowcount > 0:
                        total_new += 1
                except Exception:
                    pass
            conn.commit()
            print(f"  ✅ 入库 {len(new_rows)} 条 (新增 {total_new})")
        else:
            print(f"  无新数据 ({d} 无业绩预告)")

    conn.close()
    return total_new


def export_csv_from_db() -> int:
    """从 DB 导出 announcements 到 CSV"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM announcements")
    total = cur.fetchone()[0]
    print(f"DB announcements: {total} 行")

    # 分批读取
    rows = []
    for row in cur.execute("""
        SELECT a.code, COALESCE(s.name, '') AS name, a.trade_date AS date,
               a.type, a.title, a.url
        FROM announcements a
        LEFT JOIN stock_basic s ON a.code = s.code
        ORDER BY a.trade_date DESC, a.code
    """):
        rows.append({
            "code": row[0], "name": row[1], "date": row[2],
            "type": row[3], "title": row[4], "url": row[5],
        })

    if rows:
        df = pd.DataFrame(rows)
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
        dates = pd.to_datetime(df["date"]).sort_values()
        print(f"✅ 导出 {len(df)} 行, {dates.min().date()} ~ {dates.max().date()} → {CSV_PATH.name}")

    conn.close()
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pull", action="store_true", help="从 westock 拉取最新公告并入库")
    parser.add_argument("--days", type=str, help="逗号分隔日期 YYYY-MM-DD")
    parser.add_argument("--export", action="store_true", help="从 DB 导出 CSV")
    args = parser.parse_args()

    t0 = time.time()

    if args.pull:
        codes = load_codes()
        print(f"股票池: {len(codes)} 只")
        if args.days:
            days = [d.strip() for d in args.days.split(",")]
        else:
            # 默认: 补最近 7 个交易日
            from datetime import date, timedelta
            today = date.today()
            days = []
            d = today - timedelta(days=7)
            while d <= today:
                if d.weekday() < 5:
                    days.append(d.strftime("%Y-%m-%d"))
                d += timedelta(days=1)
        print(f"目标日期: {days}")
        n = pull_reserve(days, codes)
        print(f"\n✅ 拉取完成: {n} 条新增 (耗时 {time.time()-t0:.1f}s)")

    if args.export or not args.pull:
        # 默认导出
        export_csv_from_db()

    return 0


if __name__ == "__main__":
    sys.exit(main())
