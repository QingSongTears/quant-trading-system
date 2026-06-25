#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
update_tech_indicators_csv.py — 从 quant.db technical_indicators 表导出最新 CSV

tech_indicators_2026.csv 停在 06-16，但 DB 已经到 06-24 (fill_recent_generic 已补)。
本脚本从 DB 导出 2026 年数据覆盖 CSV，避免重新请求 westock。

用法:
  python scripts/update_tech_indicators_csv.py                     # 导出 2026 年覆盖
  python scripts/update_tech_indicators_csv.py --year 2026         # 指定年份
  python scripts/update_tech_indicators_csv.py --all-years         # 导出所有年份
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import sqlite3

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "database" / "quant.db"
OUT_DIR = ROOT / "market_data" / "raw" / "technical_indicators"


def export_year(year: int, conn: sqlite3.Connection) -> int:
    """从 DB 导出某一年的技术指标到 CSV"""
    cur = conn.cursor()

    # 逐批读取避免内存溢出
    sql = """
        SELECT code, trade_date AS date, macd_dif, macd_dea, macd_hist,
               rsi14, kdj_k, kdj_d, kdj_j, boll_mid, boll_upper, boll_lower
        FROM technical_indicators
        WHERE trade_date >= ? AND trade_date <= ?
        ORDER BY code, trade_date
    """
    start = f"{year}-01-01"
    end = f"{year}-12-31"

    rows = []
    for row in cur.execute(sql, (start, end)):
        rows.append({
            "code": row[0],
            "date": row[1],
            "macd_dif": row[2],
            "macd_dea": row[3],
            "macd_hist": row[4],
            "rsi14": row[5],
            "kdj_k": row[6],
            "kdj_d": row[7],
            "kdj_j": row[8],
            "boll_mid": row[9],
            "boll_upper": row[10],
            "boll_lower": row[11],
        })

    if not rows:
        print(f"  {year}: 无数据")
        return 0

    df = pd.DataFrame(rows)
    # code 补零到 6 位
    df["code"] = df["code"].astype(str).str.zfill(6)

    out_path = OUT_DIR / f"tech_indicators_{year}.csv"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 如果是增量模式且文件已存在，合并去重
    if out_path.exists():
        old = pd.read_csv(out_path, encoding="utf-8-sig", dtype={"code": str})
        combined = pd.concat([old, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["code", "date"], keep="last")
        combined = combined.sort_values(["code", "date"]).reset_index(drop=True)
    else:
        combined = df

    combined.to_csv(out_path, index=False, encoding="utf-8-sig")
    date_range = f"{combined['date'].min()} ~ {combined['date'].max()}"
    print(f"  {year}: {len(combined)} 行, {date_range} → {out_path.name}")
    return len(combined)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, help="指定年份")
    parser.add_argument("--all-years", action="store_true", help="导出所有年份")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"❌ DB 不存在: {DB_PATH}")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 查有哪些年份
    cur.execute("SELECT DISTINCT substr(trade_date,1,4) FROM technical_indicators ORDER BY 1")
    years_in_db = [int(r[0]) for r in cur.fetchall()]
    print(f"DB 中技术指标年份: {years_in_db}")

    if args.year:
        years = [args.year]
    elif args.all_years:
        years = years_in_db
    else:
        # 默认: 导出 DB 中最大年份 (2026)
        years = [max(years_in_db)]

    print(f"导出年份: {years}")

    t0 = time.time()
    total = 0
    for y in years:
        n = export_year(y, conn)
        total += n
        conn.commit()

    conn.close()
    print(f"\n✅ 完成! 导出 {total} 行 (耗时 {time.time()-t0:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
