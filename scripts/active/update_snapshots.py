#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
update_snapshots.py — 快照类数据刷新（股票概况/行情快照/分红送转/投资日历/全球快讯/限售解禁/板块/研报/股本结构）

这些数据不是逐日新增的，而是 snapshot 全覆盖。刷新方式:
  - 股票概况: westock profile 全量 → stock_profile.csv + DB stock_profile 表
  - 行情快照: westock quote 全量 → tencent_quotes.csv
  - 分红送转: westock dividend 全量 → dividend.csv
  - 投资日历: westock calendar → investment_calendar.csv
  - 全球快讯: westock 无直接命令 → 保留现有, 或从 em_global_news 确认
  - 限售解禁: westock 无直接命令 → 保留现有
  - 板块成份: westock sector → sector_board.csv
  - 研报: DB research_report 表已有最新 → 导出 CSV
  - 股本结构: share_structure.csv → 同步到 DB

用法:
  python scripts/update_snapshots.py                              # 刷新全部
  python scripts/update_snapshots.py --task profile               # 单任务
  python scripts/update_snapshots.py --task quote,profile         # 多任务
  python scripts/update_snapshots.py --tasks profile,quote,dividend,calendar,sector,report,shares
"""
import argparse
import csv
import os
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "database" / "quant.db"
MARKET_DATA = ROOT / "market_data"
REFERENCE = MARKET_DATA / "raw" / "reference"
NPM_PATH = "C:/Users/aini7/.workbuddy/binaries/node/versions/22.22.2/npx.cmd"
WESTOCK_PKG = "westock-data-clawhub@1.0.4"
CREATE_NO_WINDOW = 0x08000000

BATCH_PROFILE = 100
BATCH_QUOTE = 200


def load_codes_from_profile() -> list[str]:
    p = MARKET_DATA / "stock_profile.csv"
    codes = []
    if p.exists():
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


def parse_table(stdout: str) -> tuple[list[dict], list[str]]:
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
    return rows, headers or []


# ============ 各任务 ============

def refresh_profile() -> int:
    """刷新 stock_profile.csv"""
    print("\n■ 股票概况 (profile)")
    codes = load_codes_from_profile()
    if not codes:
        codes = load_codes_from_kline()
    print(f"  股票池: {len(codes)} 只")

    dst = MARKET_DATA / "stock_profile.csv"
    old_header = None
    if dst.exists():
        with open(dst, "r", encoding="utf-8-sig") as f:
            old_header = next(csv.reader(f))

    all_rows = []
    total = (len(codes) + BATCH_PROFILE - 1) // BATCH_PROFILE
    t0 = time.time()
    for b in range(total):
        chunk = codes[b * BATCH_PROFILE: (b + 1) * BATCH_PROFILE]
        ok, out, err = run_wes(["profile", ",".join(chunk)], timeout=180)
        if not ok:
            print(f"  ❌ batch {b+1}/{total}: {err[:80]}")
            time.sleep(2)
            continue
        rows, _ = parse_table(out)
        all_rows.extend(rows)
        if (b + 1) % 10 == 0 or b == total - 1:
            print(f"  batch {b+1}/{total}: +{len(rows)} 条 (累计 {len(all_rows)})")
        time.sleep(0.3)

    if not all_rows:
        print("  ❌ 无数据")
        return 0

    # 去重 + 写入
    seen = set()
    uniq = []
    for r in all_rows:
        c = r.get("code", "")
        if c and c not in seen:
            seen.add(c)
            uniq.append(r)

    fieldnames = list(uniq[0].keys()) if uniq else (old_header or [])
    with open(dst, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(uniq)

    print(f"  ✅ 保存 {len(uniq)} 行 → {dst.name} (耗时 {time.time()-t0:.1f}s)")

    # 同步到 DB stock_profile 表
    sync_profile_to_db(uniq)
    return len(uniq)


def load_codes_from_kline() -> list[str]:
    codes = set()
    for p in (MARKET_DATA / "raw" / "kline_daily").glob("kline_daily_*.csv"):
        with open(p, "r", encoding="utf-8-sig") as f:
            r = csv.reader(f)
            h = next(r)
            ci = h.index("code") if "code" in h else 0
            for row in r:
                if len(row) > ci and row[ci].strip():
                    codes.add(row[ci].strip())
    return sorted(codes)


def sync_profile_to_db(rows: list[dict]):
    """同步 stock_profile 到 DB"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    # 确保表存在 (幂等)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_profile (
            code TEXT PRIMARY KEY,
            code6 TEXT NOT NULL,
            name TEXT,
            listed_date TEXT,
            business TEXT,
            website TEXT,
            industry TEXT,
            sector TEXT,
            issue_price REAL,
            reg_capital REAL,
            establish_date TEXT,
            chairman TEXT,
            reg_address TEXT,
            office_address TEXT,
            tel TEXT,
            email TEXT,
            source TEXT DEFAULT 'westock',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    n = 0
    for r in rows:
        code_raw = str(r.get("code", "")).replace("sh", "").replace("sz", "").replace("bj", "")
        try:
            cur.execute(
                "INSERT OR REPLACE INTO stock_profile "
                "(code, code6, name, listed_date, business, website, industry, sector, "
                " issue_price, reg_capital, establish_date, chairman, "
                " reg_address, office_address, tel, email) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code_raw.zfill(6), code_raw.zfill(6),
                 r.get("name", ""), r.get("listedDate", ""), r.get("business", ""),
                 r.get("website", ""), r.get("industry", ""), r.get("sector", ""),
                 float(r.get("issuePrice", 0)) if r.get("issuePrice") else None,
                 float(r.get("regCapital", 0)) if r.get("regCapital") else None,
                 r.get("establishDate", ""), r.get("chairman", ""),
                 r.get("regAddress", ""), r.get("officeAddress", ""),
                 r.get("tel", ""), r.get("email", "")),
            )
            n += 1
        except Exception as e:
            if n == 0:
                print(f"    DB sync err (first): {e}")
    conn.commit()
    conn.close()
    print(f"  ✅ DB stock_profile: {n} 行")


def refresh_quote() -> int:
    """刷新 tencent_quotes.csv (行情快照)"""
    print("\n■ 行情快照 (quote)")
    codes = load_codes_from_profile()
    if not codes:
        codes = load_codes_from_kline()
    print(f"  股票池: {len(codes)} 只")

    dst = REFERENCE / "tencent_quotes.csv"
    old_header = None
    if dst.exists():
        with open(dst, "r", encoding="utf-8-sig") as f:
            old_header = next(csv.reader(f))

    all_rows = []
    total = (len(codes) + BATCH_QUOTE - 1) // BATCH_QUOTE
    t0 = time.time()
    for b in range(total):
        chunk = codes[b * BATCH_QUOTE: (b + 1) * BATCH_QUOTE]
        ok, out, err = run_wes(["quote", ",".join(chunk)], timeout=180)
        if not ok:
            print(f"  ❌ batch {b+1}/{total}: {err[:80]}")
            time.sleep(2)
            continue
        rows, _ = parse_table(out)
        all_rows.extend(rows)
        if (b + 1) % 5 == 0 or b == total - 1:
            print(f"  batch {b+1}/{total}: +{len(rows)} 条 (累计 {len(all_rows)})")
        time.sleep(0.2)

    if not all_rows:
        print("  ❌ 无数据")
        return 0

    seen = set()
    uniq = []
    for r in all_rows:
        c = r.get("code", "")
        if c and c not in seen:
            seen.add(c)
            uniq.append(r)

    fieldnames = list(uniq[0].keys()) if uniq else (old_header or [])
    REFERENCE.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(uniq)

    print(f"  ✅ 保存 {len(uniq)} 行 → {dst.name} (耗时 {time.time()-t0:.1f}s)")
    return len(uniq)


def refresh_dividend() -> int:
    """刷新 dividend.csv (分红送转)"""
    print("\n■ 分红送转 (dividend)")
    codes = load_codes_from_profile()
    if not codes:
        codes = load_codes_from_kline()
    print(f"  股票池: {len(codes)} 只")

    dst = REFERENCE / "dividend.csv"
    old = pd.DataFrame()
    if dst.exists():
        old = pd.read_csv(dst, encoding="utf-8-sig")

    all_rows = []
    BATCH = 500
    total = (len(codes) + BATCH - 1) // BATCH
    t0 = time.time()
    for b in range(total):
        chunk = codes[b * BATCH: (b + 1) * BATCH]
        ok, out, err = run_wes(["dividend", ",".join(chunk)], timeout=180)
        if not ok:
            print(f"  ❌ batch {b+1}/{total}: {err[:80]}")
            time.sleep(2)
            continue
        rows, _ = parse_table(out)
        all_rows.extend(rows)
        if (b + 1) % 10 == 0 or b == total - 1:
            print(f"  batch {b+1}/{total}: +{len(rows)} 条 (累计 {len(all_rows)})")
        time.sleep(0.3)

    if not all_rows:
        print("  ❌ 无数据")
        return 0

    df_new = pd.DataFrame(all_rows)
    if not old.empty:
        combined = pd.concat([old, df_new], ignore_index=True)
        combined = combined.drop_duplicates(subset=["code", "ex_div_date"], keep="last")
    else:
        combined = df_new

    combined.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"  ✅ 保存 {len(combined)} 行 → {dst.name} (耗时 {time.time()-t0:.1f}s)")
    return len(combined)


def refresh_calendar() -> int:
    """刷新 investment_calendar.csv (投资日历)"""
    print("\n■ 投资日历 (calendar)")
    # 拉未来几天
    today = date.today()
    days = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(-1, 8)]

    all_rows = []
    for d in days:
        ok, out, err = run_wes(["calendar", d], timeout=60)
        if not ok:
            print(f"  {d}: {err[:80]}")
            continue
        rows, _ = parse_table(out)
        all_rows.extend(rows)
        print(f"  {d}: +{len(rows)} 条")
        time.sleep(0.3)

    if all_rows:
        df = pd.DataFrame(all_rows)
        dst = REFERENCE / "investment_calendar.csv"
        REFERENCE.mkdir(parents=True, exist_ok=True)
        df.to_csv(dst, index=False, encoding="utf-8-sig")
        print(f"  ✅ 保存 {len(df)} 行 → {dst.name}")
    else:
        print("  ⚠️ 无数据")
    return len(all_rows)


def refresh_sector() -> int:
    """刷新 sector_board.csv (板块成份)"""
    print("\n■ 板块成份 (sector)")
    ok, out, err = run_wes(["board"], timeout=60)
    if not ok:
        print(f"  ❌ fail: {err[:80]}")
        return 0
    rows, _ = parse_table(out)
    if rows:
        df = pd.DataFrame(rows)
        dst = REFERENCE / "sector_board.csv"
        REFERENCE.mkdir(parents=True, exist_ok=True)
        df.to_csv(dst, index=False, encoding="utf-8-sig")
        print(f"  ✅ 保存 {len(df)} 行 → {dst.name}")
    else:
        print("  ⚠️ 无数据")
    return len(rows)


def export_research_report_csv() -> int:
    """从 DB 导出 research_report CSV"""
    print("\n■ 研报导出")
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        "SELECT code, date, rating, rating_change, title, author, institution, url "
        "FROM research_report ORDER BY date DESC, code",
        conn,
    )
    conn.close()

    if len(df) > 0:
        dst = REFERENCE / "research_report.csv"
        REFERENCE.mkdir(parents=True, exist_ok=True)
        df.to_csv(dst, index=False, encoding="utf-8-sig")
        dates = pd.to_datetime(df["date"]).sort_values()
        print(f"  ✅ 导出 {len(df)} 行, {dates.min().date()} ~ {dates.max().date()} → {dst.name}")
    else:
        print("  ⚠️ 无研报数据")
    return len(df)


def sync_shares_to_db() -> int:
    """从 share_structure.csv 同步到 DB"""
    print("\n■ 股本结构同步 (shares)")
    p = MARKET_DATA / "share_structure.csv"
    if not p.exists():
        print(f"  ⚠️ {p.name} 不存在")
        return 0

    df = pd.read_csv(p, encoding="utf-8-sig")
    print(f"  读 {len(df)} 行")

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS share_structure (
            code TEXT PRIMARY KEY,
            code6 TEXT NOT NULL,
            name TEXT,
            total_share REAL,
            liqa_share REAL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    inserted = 0
    for _, r in df.iterrows():
        code_full = str(r.get("code", ""))
        mkt_match = code_full.split(".")
        code6 = mkt_match[1].zfill(6) if len(mkt_match) == 2 else code_full.zfill(6)
        try:
            cur.execute(
                "INSERT OR REPLACE INTO share_structure (code, code6, name, total_share, liqa_share) "
                "VALUES (?, ?, ?, ?, ?)",
                (code6, code6, str(r.get("name", "")),
                 float(r["totalShare"]) if pd.notna(r.get("totalShare")) and str(r.get("totalShare", "")) else None,
                 float(r["liqaShare"]) if pd.notna(r.get("liqaShare")) and str(r.get("liqaShare", "")) else None),
            )
            inserted += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    print(f"  ✅ DB share_structure: {inserted} 行")
    return inserted


# ============ main ============

TASKS = {
    "profile": refresh_profile,
    "quote": refresh_quote,
    "dividend": refresh_dividend,
    "calendar": refresh_calendar,
    "sector": refresh_sector,
    "report": export_research_report_csv,
    "shares": sync_shares_to_db,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=str, default="all",
                        help=f"逗号分隔任务: {','.join(TASKS.keys())} 或 'all'")
    args = parser.parse_args()

    if args.tasks == "all":
        tasks = list(TASKS.keys())
    else:
        tasks = [t.strip() for t in args.tasks.split(",") if t.strip() in TASKS]

    print("=" * 60)
    print(f"快照数据刷新: {tasks}")
    print("=" * 60)

    t0 = time.time()
    total = 0
    for task in tasks:
        total += TASKS[task]()

    print(f"\n{'=' * 60}")
    print(f"✅ 快照刷新完成! 总计 {total} 行 (耗时 {(time.time()-t0)/60:.1f}min)")
    print(f"{'=' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
