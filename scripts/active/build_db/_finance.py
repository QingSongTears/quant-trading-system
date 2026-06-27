"""build_db._finance — 财务相关 importer

- finance       : 财务摘要 (baostock profit_data)
- stock_profile : 股票简介 (westock profile, 同时 UPDATE stock_basic)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def import_finance(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """财务摘要 ← market_data/finance_summary.csv (baostock profit_data)"""
    import csv as _csv
    csv_path = data_dir / "finance_summary.csv"
    if not csv_path.exists():
        print(f"   ⚠️ {csv_path.name} 不存在, 跳过")
        return 0
    def _f(s):
        if not s or s == "nan":
            return None
        try:
            return float(s)
        except (ValueError, TypeError):
            return None
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for r in _csv.DictReader(f):
            code_full = r["code"]
            code6 = code_full.split(".", 1)[1].zfill(6) if "." in code_full else code_full.zfill(6)
            stat_date = r["statDate"][:10]
            pub_date = r["pubDate"][:10]
            row = {
                "code": code6, "_date": stat_date, "EndDate": stat_date,
                "ROE": _f(r["roeAvg"]), "ROETTM": _f(r["roeAvg"]),
                "ROEWeighted": _f(r["roeAvg"]),
                "EPS": _f(r["epsTTM"]), "EPSTTM": _f(r["epsTTM"]),
                "BasicEPS": _f(r["epsTTM"]), "DilutedEPS": _f(r["epsTTM"]),
                "NetProfitRatio": _f(r["npMargin"]),
                "NetProfitRatioTTM": _f(r["npMargin"]),
                "OperatingRevenue": _f(r["MBRevenue"]),
                "OperatingRevenueTTM": _f(r["MBRevenue"]),
                "TotalOperatingRevenue": _f(r["MBRevenue"]),
                "NPParentCompanyOwners": _f(r["netProfit"]),
                "NPParentCompanyOwnersTTM": _f(r["netProfit"]),
                "baostock_pub_date": pub_date,
                "roe_avg": _f(r["roeAvg"]),
                "np_margin": _f(r["npMargin"]),
                "gp_margin": _f(r["gpMargin"]),
                "net_profit": _f(r["netProfit"]),
                "eps_ttm": _f(r["epsTTM"]),
                "main_revenue": _f(r["MBRevenue"]),
                "total_share": _f(r["totalShare"]),
                "liqa_share": _f(r["liqaShare"]),
                "source": "baostock",
            }
            rows.append(row)
    cur.execute("DELETE FROM finance_summary WHERE source = 'baostock'")
    cols = list(rows[0].keys())
    placeholders = ", ".join([f":{c}" for c in cols])
    col_names = ", ".join(cols)
    sql = f"INSERT INTO finance_summary ({col_names}) VALUES ({placeholders})"
    cur.executemany(sql, rows)
    print(f"   ✅ INSERT finance_summary: {len(rows)} 行 (baostock)")
    return len(rows)


def import_stock_profile(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """股票简介 ← market_data/stock_profile.csv (westock profile)

    同时 UPDATE stock_basic 扩展列 (兜底路径给 v_leader_features.py).
    stock_profile 表被 REBUILD (DROP + CREATE, 按 ORM schema + circulating_shares).
    """
    import csv as _csv
    csv_path = data_dir / "stock_profile.csv"
    if not csv_path.exists():
        print(f"   ⚠️ {csv_path.name} 不存在, 跳过")
        return 0
    def _f(s):
        if not s or s == "nan":
            return None
        try:
            return float(s)
        except (ValueError, TypeError):
            return None
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for r in _csv.DictReader(f):
            code = r["code"]
            code6 = code[2:].zfill(6) if code[:2] in ("sh", "sz", "bj") else code.zfill(6)
            rows.append({
                "code": code6,
                "name": r["name"],
                "listed_date": r["listedDate"] or None,
                "industry": r["industry"] or None,
                "sector": r["sector"] or None,
                "issue_price": _f(r["issuePrice"]),
                "reg_capital": _f(r["regCapital"]),
                "chairman": r["chairman"] or None,
                "establish_date": r["establishDate"] or None,
                "website": r["website"] or None,
                "business": r["business"] or None,
                "reg_address": r["regAddress"] or None,
            })
    # 1. UPDATE stock_basic 扩展列 (兜底路径)
    # 2026-06-25 修复: 原来 UPDATE 到 listed_date_alt, 应写到 list_date
    n_updated = 0
    for row in rows:
        cur.execute("""
            UPDATE stock_basic SET
                list_date = COALESCE(list_date, ?),
                industry = COALESCE(?, industry),
                sector = COALESCE(?, sector),
                listed_date_alt = COALESCE(?, listed_date_alt),
                issue_price = COALESCE(?, issue_price),
                reg_capital = COALESCE(?, reg_capital),
                establish_date = COALESCE(?, establish_date),
                chairman = COALESCE(?, chairman),
                website = COALESCE(?, website),
                business = COALESCE(?, business),
                reg_address = COALESCE(?, reg_address)
            WHERE code = ?
        """, (
            row["listed_date"],
            row["industry"], row["sector"], row["listed_date"],
            row["issue_price"], row["reg_capital"], row["establish_date"],
            row["chairman"], row["website"], row["business"], row["reg_address"],
            row["code"],
        ))
        if cur.rowcount > 0:
            n_updated += 1
    print(f"   ✅ UPDATE stock_basic 扩展列: {n_updated} 行")

    # 2. REBUILD stock_profile 表
    cur.execute("DROP TABLE IF EXISTS stock_profile")
    cur.execute("""
        CREATE TABLE stock_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            listed_date TEXT,
            industry TEXT,
            sector TEXT,
            issue_price REAL,
            reg_capital REAL,
            chairman TEXT,
            establish_date TEXT,
            website TEXT,
            business TEXT,
            reg_address TEXT,
            circulating_shares REAL,
            source TEXT DEFAULT 'westock',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX idx_sp_code ON stock_profile(code)")
    cur.execute("CREATE INDEX idx_sp_industry ON stock_profile(industry)")
    cur.execute("CREATE INDEX idx_sp_sector ON stock_profile(sector)")
    insert_rows = [
        (r["code"], r["name"], r["listed_date"], r["industry"], r["sector"],
         r["issue_price"], r["reg_capital"], r["chairman"], r["establish_date"],
         r["website"], r["business"], r["reg_address"], None, "westock")
        for r in rows
    ]
    cur.executemany(
        "INSERT INTO stock_profile "
        "(code, name, listed_date, industry, sector, issue_price, reg_capital, "
        " chairman, establish_date, website, business, reg_address, "
        " circulating_shares, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        insert_rows,
    )
    print(f"   ✅ REBUILD stock_profile: {len(insert_rows)} 行")
    return n_updated + len(insert_rows)
