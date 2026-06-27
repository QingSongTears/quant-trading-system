"""build_db._extended — 5 个扩展数据 importer

- block_trade    : 大宗交易
- dividend       : 分红送转
- announcements  : 公告
- holder_num     : 股东户数
- benchmark      : 指数 K 线
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def import_block_trade(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """大宗交易 ← raw/reference/block_trade.csv

    CSV 字段: code, market, name, date, deal_price, close_price, premium_pct,
              vol, amount, buyer, seller
    DB 字段: code, trade_date, name, deal_price, close_price, premium_pct,
             volume, amount, buyer, seller
    映射: date→trade_date, vol→volume
    """
    import pandas as pd
    # 2026-06-25 修复: 同时读 raw/reference/ (历史全量) + 根目录 (近期增量),
    # raw 里的数据从 2000 年到 6-18, 根目录是 6-18~6-24 的近期 CSV
    csv_paths = [
        data_dir / "raw" / "reference" / "block_trade.csv",
        data_dir / "block_trade.csv",
    ]
    csv_paths = [p for p in csv_paths if p.exists()]
    if not csv_paths:
        print("   ⚠️ block_trade CSV 不存在, 跳过")
        return 0
    dfs = []
    for p in csv_paths:
        try:
            d = pd.read_csv(p, dtype={"code": str}, low_memory=False)
            d["source_file"] = p.name
            dfs.append(d)
            print(f"   读取 {p.name}: {len(d):,} 行")
        except Exception as e:
            print(f"   ⚠️ 读 {p.name} 失败: {e}")
    if not dfs:
        return 0
    df = pd.concat(dfs, ignore_index=True)
    # 字段重命名
    df = df.rename(columns={"date": "trade_date", "vol": "volume"})
    df = df.dropna(subset=["code", "trade_date"])
    df["code"] = df["code"].astype(str).str.replace(r"^(sz|sh|bj)", "", regex=True).str.zfill(6)
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["trade_date"])
    # 同 (code, trade_date, deal_price) 去重, 保留最新 source (根目录优先)
    df = df.sort_values("source_file", ascending=False).drop_duplicates(
        subset=["code", "trade_date", "deal_price"], keep="first")
    if full:
        cur.execute("DELETE FROM block_trade")
    rows = [
        (r.code, r.trade_date,
         getattr(r, "name", None) if pd.notna(getattr(r, "name", None)) else None,
         float(r.deal_price) if pd.notna(r.deal_price) else None,
         float(r.close_price) if pd.notna(r.close_price) else None,
         float(r.premium_pct) if pd.notna(r.premium_pct) else None,
         int(r.volume) if pd.notna(r.volume) else None,
         float(r.amount) if pd.notna(r.amount) else None,
         getattr(r, "buyer", None) if pd.notna(getattr(r, "buyer", None)) else None,
         getattr(r, "seller", None) if pd.notna(getattr(r, "seller", None)) else None)
        for r in df.itertuples(index=False)
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO block_trade "
        "(code, trade_date, name, deal_price, close_price, premium_pct, "
        " volume, amount, buyer, seller) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT block_trade: {len(rows)} 行")
    return len(rows)


def import_dividend(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """分红 ← raw/reference/dividend.csv

    CSV 字段: code, market, name, ex_div_date, pre_tax_bonus, transfer_ratio,
              bonus_ratio, record_date (直接对齐)
    """
    import pandas as pd
    csv_path = data_dir / "raw" / "reference" / "dividend.csv"
    if not csv_path.exists():
        print(f"   ⚠️ {csv_path.name} 不存在, 跳过")
        return 0
    df = pd.read_csv(csv_path, dtype={"code": str}, low_memory=False)
    df = df.dropna(subset=["code"])
    df["code"] = df["code"].astype(str).str.replace(r"^(sz|sh|bj)", "", regex=True).str.zfill(6)
    if full:
        cur.execute("DELETE FROM dividend")
    rows = []
    for r in df.itertuples(index=False):
        def _date(s):
            return str(s)[:10] if pd.notna(s) else None
        rows.append((
            r.code,
            _date(r.ex_div_date),
            float(r.pre_tax_bonus) if pd.notna(r.pre_tax_bonus) else None,
            float(r.transfer_ratio) if pd.notna(r.transfer_ratio) else None,
            float(r.bonus_ratio) if pd.notna(r.bonus_ratio) else None,
            _date(r.record_date),
        ))
    cur.executemany(
        "INSERT OR IGNORE INTO dividend "
        "(code, ex_div_date, pre_tax_bonus, transfer_ratio, bonus_ratio, record_date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT dividend: {len(rows)} 行")
    return len(rows)


def import_announcements(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """公告 ← raw/reference/announcements.csv

    CSV 字段: code, market, name, date, type, title, url
    DB 字段: code, trade_date, type, title, url (date→trade_date)
    """
    import pandas as pd
    csv_path = data_dir / "raw" / "reference" / "announcements.csv"
    if not csv_path.exists():
        print(f"   ⚠️ {csv_path.name} 不存在, 跳过")
        return 0
    df = pd.read_csv(csv_path, dtype={"code": str}, low_memory=False)
    df = df.rename(columns={"date": "trade_date"})
    df = df.dropna(subset=["code"])
    df["code"] = df["code"].astype(str).str.replace(r"^(sz|sh|bj)", "", regex=True).str.zfill(6)
    if full:
        cur.execute("DELETE FROM announcements")
    rows = []
    for r in df.itertuples(index=False):
        rows.append((
            r.code,
            str(r.trade_date)[:10] if pd.notna(r.trade_date) else None,
            r.type if pd.notna(r.type) else None,
            r.title if pd.notna(r.title) else None,
            r.url if pd.notna(r.url) else None,
        ))
    cur.executemany(
        "INSERT OR IGNORE INTO announcements (code, trade_date, type, title, url) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT announcements: {len(rows)} 行")
    return len(rows)


def import_holder_num(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """股东户数 ← holder_num.csv (新表)"""
    import csv as _csv
    csv_path = data_dir / "holder_num.csv"
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
            rows.append((
                r["code"].zfill(6), r["end_date"][:10],
                int(r["holder_num"]) if r.get("holder_num", "").isdigit() else None,
                int(r["pre_holder_num"]) if r.get("pre_holder_num", "").isdigit() else None,
                _f(r.get("holder_change_pct")),
                _f(r.get("avg_holding")),
                "tencent",
            ))
    cur.executemany(
        "INSERT OR IGNORE INTO holder_num "
        "(code, end_date, holder_num, pre_holder_num, holder_change_pct, avg_holding, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT holder_num: {len(rows)} 行")
    return len(rows)


def import_benchmark(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """指数 K 线 ← market_data/benchmark_data.csv (westock 6 指数)"""
    import csv as _csv
    csv_path = data_dir / "benchmark_data.csv"
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
            rows.append((
                r["code"], r["date"][:10], _f(r["close"]), None,
                r.get("name") or None,
                _f(r["open"]), _f(r["high"]), _f(r["low"]),
                int(r["volume"]) if r.get("volume", "").isdigit() else None,
                _f(r["amount"]), _f(r["exchange"]),
                r["code"], "westock",
            ))
    cur.execute("DELETE FROM benchmark_data WHERE source = 'westock'")
    cur.executemany(
        "INSERT INTO benchmark_data "
        "(index_code, trade_date, close, pct_change, name, open, high, low, "
        " volume, amount, exchange_factor, code, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT benchmark_data: {len(rows)} 行")
    return len(rows)
