#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
sync_csv_to_db.py — CSV → DB 全量桥接脚本

把 market_data 下各 CSV 的最新数据同步到 quant.db:
  - margin_trading.csv → DB (margin_trading 表)
  - block_trade.csv → DB (block_trade 表, 增量)
  - fund_flow_120d.csv → DB (fund_flow_data 表, 增量)
  - stock_profile.csv → DB (stock_profile 表, 全量覆盖)
  - share_structure.csv → DB (share_structure 表, 全量覆盖)
  - 各 reference CSV → DB (从已有 DB 导出或补充)

幂等设计: INSERT OR IGNORE / INSERT OR REPLACE, 可反复跑。

用法:
  python scripts/sync_csv_to_db.py                                # 全部
  python scripts/sync_csv_to_db.py --task margin                  # 单表
  python scripts/sync_csv_to_db.py --tasks margin,block,fund_flow
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import sqlite3

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "database" / "quant.db"
MARKET_DATA = ROOT / "market_data"


def _f(s):
    if s is None or s == "" or s == "nan":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _i(s):
    f = _f(s)
    return int(f) if f is not None else None


# ============ 各表同步 ============

def sync_margin_trading() -> int:
    """margin_trading.csv → DB margin_trading 表"""
    csv_path = MARKET_DATA / "margin_trading.csv"
    if not csv_path.exists():
        print("  ⚠️ margin_trading.csv 不存在")
        return 0

    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"code": str})
    print(f"  读 {len(df)} 行, cols={df.columns.tolist()[:6]}...")

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS margin_trading (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code VARCHAR(10) NOT NULL,
            trade_date DATE NOT NULL,
            name VARCHAR(50),
            rz REAL, rzmre REAL, rzche REAL,
            rqye REAL, rqmcl REAL, rqchl REAL,
            UNIQUE(code, trade_date)
        )
    """)

    inserted = 0
    for _, r in df.iterrows():
        try:
            cur.execute(
                "INSERT OR IGNORE INTO margin_trading "
                "(code, trade_date, name, rz, rzmre, rzche, rqye, rqmcl, rqchl) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(r["code"]).zfill(6), str(r["date"]), str(r.get("name", "")),
                 _f(r.get("rz")), _f(r.get("rzmre")), _f(r.get("rzche")),
                 _f(r.get("rqye")), _f(r.get("rqmcl")), _f(r.get("rqchl"))),
            )
            if cur.rowcount > 0:
                inserted += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    print(f"  ✅ margin_trading: 入库 {inserted}/{len(df)} 行 (新增)")
    return inserted


def sync_block_trade() -> int:
    """block_trade.csv → DB block_trade 表 (增量)"""
    csv_path = MARKET_DATA / "block_trade.csv"
    if not csv_path.exists():
        print("  ⚠️ block_trade.csv 不存在")
        return 0

    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"code": str})
    print(f"  读 {len(df)} 行")

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    inserted = 0
    for _, r in df.iterrows():
        try:
            cur.execute(
                "INSERT OR IGNORE INTO block_trade "
                "(code, trade_date, name, deal_price, close_price, premium_pct, volume, amount, buyer, seller) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(r["code"]).zfill(6), str(r["date"]), str(r.get("name", "")),
                 _f(r.get("deal_price")), _f(r.get("close_price")),
                 _f(r.get("premium_pct")), _i(r.get("vol")),
                 _f(r.get("amount")), str(r.get("buyer", "")), str(r.get("seller", ""))),
            )
            if cur.rowcount > 0:
                inserted += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    print(f"  ✅ block_trade: 入库 {inserted}/{len(df)} 行 (新增)")
    return inserted


def sync_fund_flow() -> int:
    """fund_flow_120d.csv → DB fund_flow_data 表 (增量)"""
    csv_path = MARKET_DATA / "fund_flow_120d.csv"
    if not csv_path.exists():
        print("  ⚠️ fund_flow_120d.csv 不存在")
        return 0

    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"code": str}, low_memory=False)
    print(f"  读 {len(df)} 行, cols={df.columns.tolist()[:8]}...")

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 确保表存在
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fund_flow_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code VARCHAR(10) NOT NULL,
            trade_date DATE NOT NULL,
            main_net REAL, super_large_net REAL, large_net REAL,
            medium_net REAL, small_net REAL,
            UNIQUE(code, trade_date)
        )
    """)

    inserted = 0
    for _, r in df.iterrows():
        try:
            cur.execute(
                "INSERT OR IGNORE INTO fund_flow_data "
                "(code, trade_date, main_net, super_large_net, large_net, medium_net, small_net) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(r["code"]).zfill(6), str(r["date"]),
                 _f(r.get("main_net")), _f(r.get("super_large_net")),
                 _f(r.get("large_net")), _f(r.get("medium_net")),
                 _f(r.get("small_net"))),
            )
            if cur.rowcount > 0:
                inserted += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    print(f"  ✅ fund_flow_data: 入库 {inserted}/{len(df)} 行 (新增)")
    return inserted


def sync_dividend() -> int:
    """dividend.csv → DB dividend 表"""
    csv_path = MARKET_DATA / "raw" / "reference" / "dividend.csv"
    if not csv_path.exists():
        print("  ⚠️ dividend.csv 不存在")
        return 0

    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"code": str})
    print(f"  读 {len(df)} 行")

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    inserted = 0
    for _, r in df.iterrows():
        try:
            code_raw = str(r.get("code", ""))
            code6 = code_raw.replace("sh", "").replace("sz", "").replace("bj", "").zfill(6)
            cur.execute(
                "INSERT OR IGNORE INTO dividend "
                "(code, ex_div_date, name, pre_tax_bonus, after_tax_bonus, bonus_shares, "
                " placement_shares, record_date, announce_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code6, str(r.get("ex_div_date", "")),
                 str(r.get("name", "")),
                 _f(r.get("pre_tax_bonus")), _f(r.get("after_tax_bonus")),
                 _f(r.get("bonus_shares")), _f(r.get("placement_shares")),
                 str(r.get("record_date", "")), str(r.get("announce_date", ""))),
            )
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            if inserted == 0:
                print(f"    (first err: {e})")
            pass
    conn.commit()
    conn.close()
    print(f"  ✅ dividend: 入库 {inserted}/{len(df)} 行 (新增)")
    return inserted


# ============ 任务注册 ============

TASKS = {
    "margin": sync_margin_trading,
    "block": sync_block_trade,
    "fund_flow": sync_fund_flow,
    "dividend": sync_dividend,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=str, default="all",
                        help=f"逗号分隔: {','.join(TASKS.keys())} 或 'all'")
    args = parser.parse_args()

    if args.tasks == "all":
        tasks = list(TASKS.keys())
    else:
        tasks = [t.strip() for t in args.tasks.split(",") if t.strip() in TASKS]

    print("=" * 60)
    print(f"CSV → DB 同步: {tasks}")
    print("=" * 60)

    t0 = time.time()
    total = 0
    for task in tasks:
        print(f"\n■ {task}")
        total += TASKS[task]()

    print(f"\n{'=' * 60}")
    print(f"✅ 同步完成! 新增 {total} 行 (耗时 {time.time()-t0:.1f}s)")
    print(f"{'=' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
