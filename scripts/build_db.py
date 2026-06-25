#!/usr/bin/env python3
"""
build_db.py — 统一的 CSV → SQLite 重建工具
=========================================

数据架构 (LIVE_TRADING_ROADMAP §数据流):
  market_data/  (CSV 源, Git 提交)
      ↓
  database/quant.db  (派生, .gitignore, 本地生成)

用法:
    python scripts/build_db.py              # 全量重建 (清空 DB 后从 CSV 重建)
    python scripts/build_db.py --incremental  # 增量更新 (只导入 CSV 中比 DB 新的行)
    python scripts/build_db.py --table daily_price  # 只重建一个表

⚠️ 警告: 全量重建会 DELETE 现有 DB 数据, 然后从 CSV 重新填充
   建议在 run.py 启动前运行一次
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "market_data"
DB_PATH = PROJECT_ROOT / "database" / "quant.db"

t0 = time.time()


# ── 建表 SQL ────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stock_basic (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    list_date DATE,
    delist_date DATE,
    industry TEXT
);

CREATE TABLE IF NOT EXISTS daily_price (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    amount REAL,
    pct_change REAL,
    turnover REAL,
    UNIQUE(code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_dp_code_date ON daily_price(code, trade_date);
CREATE INDEX IF NOT EXISTS idx_dp_date ON daily_price(trade_date);
CREATE INDEX IF NOT EXISTS idx_dp_date_code ON daily_price(trade_date, code);

CREATE TABLE IF NOT EXISTS benchmark_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    index_code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    close REAL NOT NULL,
    pct_change REAL,
    UNIQUE(index_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_bd_code_date ON benchmark_data(index_code, trade_date);

CREATE TABLE IF NOT EXISTS fund_flow_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    main_net REAL,
    super_large_net REAL,
    large_net REAL,
    medium_net REAL,
    small_net REAL,
    UNIQUE(code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_ff_code_date ON fund_flow_data(code, trade_date);
CREATE INDEX IF NOT EXISTS idx_ff_date ON fund_flow_data(trade_date);

CREATE TABLE IF NOT EXISTS technical_indicators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    macd_dif REAL, macd_dea REAL, macd_hist REAL,
    rsi14 REAL,
    kdj_k REAL, kdj_d REAL, kdj_j REAL,
    boll_mid REAL, boll_upper REAL, boll_lower REAL,
    UNIQUE(code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_ti_code_date ON technical_indicators(code, trade_date);

CREATE TABLE IF NOT EXISTS finance_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    _date DATE,
    ROE REAL, ROETTM REAL, EPS REAL, NAPS REAL,
    OperatingRevenue REAL, NPParentCompanyOwnersTTM REAL,
    TotalShareholderEquity REAL, DebtAssetsRatio REAL,
    -- 其他字段按需扩展
    UNIQUE(code, _date)
);
CREATE INDEX IF NOT EXISTS idx_fs_code ON finance_summary(code);
CREATE INDEX IF NOT EXISTS idx_fs_code_date ON finance_summary(code, _date);
"""


# ── 各表导入函数 ──────────────────────────────

def import_stock_basic(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """
    导入 stock_basic 表

    源: market_data/raw/reference/tencent_quotes.csv
         或 market_data/stock_basic.csv (fallback)
    """
    candidates = [
        data_dir / "raw" / "reference" / "tencent_quotes.csv",
        data_dir / "stock_basic.csv",
    ]
    csv_path = next((p for p in candidates if p.exists()), None)
    if not csv_path:
        print(f"   ⚠️ 未找到 stock_basic CSV, 跳过")
        return 0

    import pandas as pd
    df = pd.read_csv(csv_path, dtype={"code": str}, low_memory=False)
    if full:
        cur.execute("DELETE FROM stock_basic")
    cur.executemany(
        "INSERT OR REPLACE INTO stock_basic (code, name, market) VALUES (?, ?, ?)",
        [
            (str(r.code).zfill(6), str(r.name) if pd.notna(r.name) else "",
             {"sz": "SZ", "sh": "SH", "bj": "BJ"}.get(
                 str(r.market).lower() if pd.notna(r.market) else "", "SH"))
            for r in df.itertuples(index=False)
        ],
    )
    return len(df)


def import_daily_price(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """
    导入 daily_price 表

    源: market_data/raw/kline_daily/kline_daily_*.csv
    """
    import pandas as pd
    kline_dir = data_dir / "raw" / "kline_daily"
    if not kline_dir.exists():
        print(f"   ⚠️ {kline_dir} 不存在, 跳过")
        return 0

    files = sorted(kline_dir.glob("kline_daily_*.csv"))
    if not files:
        print(f"   ⚠️ {kline_dir} 无 kline_daily_*.csv 文件, 跳过")
        return 0

    print(f"   发现 {len(files)} 个 CSV: {[f.name for f in files]}")
    dfs = []
    for f in files:
        df = pd.read_csv(f, dtype={"code": str}, low_memory=False)
        df["source_file"] = f.name
        dfs.append(df)
    df = pd.concat(dfs, ignore_index=True)

    # 字段清洗
    df = df.dropna(subset=["code", "date", "close"])
    df["trade_date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["trade_date"])
    df["code"] = df["code"].astype(str).str.replace(r"^(sz|sh|bj)", "", regex=True).str.zfill(6)

    if full:
        cur.execute("DELETE FROM daily_price")

    # 增量模式: 只导入比 DB 现有 max date 更新的行
    if not full:
        cur.execute("SELECT MAX(trade_date) FROM daily_price")
        max_date = cur.fetchone()[0]
        if max_date:
            df = df[df["trade_date"] > max_date]
            print(f"   增量模式: 仅导入 > {max_date} 的行")

    rows = [
        (r.code, r.trade_date, float(r.open), float(r.high),
         float(r.low), float(r.close), int(r.volume),
         float(r.amount) if pd.notna(r.amount) else None)
        for r in df.itertuples(index=False)
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO daily_price (code, trade_date, open, high, low, close, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def import_fund_flow(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """
    导入 fund_flow_data 表

    源: market_data/fund_flow_120d.csv
    """
    import pandas as pd
    csv_path = data_dir / "fund_flow_120d.csv"
    if not csv_path.exists():
        print(f"   ⚠️ {csv_path} 不存在, 跳过")
        return 0

    df = pd.read_csv(csv_path, dtype={"code": str}, low_memory=False)
    df = df.dropna(subset=["code", "date"])
    df["trade_date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["trade_date"])
    df["code"] = df["code"].astype(str).str.replace(r"^(sz|sh|bj)", "", regex=True).str.zfill(6)

    if full:
        cur.execute("DELETE FROM fund_flow_data")

    def _to_float(s):
        """宽容处理: 空 / nan / '-' / 'None' / 数字字符串"""
        if pd.isna(s):
            return None
        if isinstance(s, str):
            s = s.strip()
            if s in ("", "-", "--", "None", "nan"):
                return None
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    rows = [
        (r.code, r.trade_date,
         _to_float(r.main_net),
         _to_float(r.super_large_net),
         _to_float(r.large_net),
         _to_float(r.medium_net),
         _to_float(r.small_net))
        for r in df.itertuples(index=False)
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO fund_flow_data "
        "(code, trade_date, main_net, super_large_net, large_net, medium_net, small_net) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def import_technical_indicators(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """
    导入 technical_indicators 表

    源: market_data/raw/technical_indicators/tech_indicators_*.csv
    """
    import pandas as pd
    ti_dir = data_dir / "raw" / "technical_indicators"
    if not ti_dir.exists():
        print(f"   ⚠️ {ti_dir} 不存在, 跳过")
        return 0

    files = sorted(ti_dir.glob("tech_indicators_*.csv"))
    if not files:
        print(f"   ⚠️ {ti_dir} 无 tech_indicators_*.csv 文件, 跳过")
        return 0

    print(f"   发现 {len(files)} 个 CSV")
    # 跳过冗余备份 (part1/part2 是 2025 的拆分备份, 与 tech_indicators_2025.csv 重复)
    files = [f for f in files if "part" not in f.name]
    print(f"   跳过 part1/part2 后剩余 {len(files)} 个 CSV")
    dfs = [pd.read_csv(f, dtype={"code": str}, low_memory=False) for f in files]
    df = pd.concat(dfs, ignore_index=True)

    df = df.dropna(subset=["code", "date"])
    df["code"] = df["code"].astype(str).str.replace(r"^(sz|sh|bj)", "", regex=True).str.zfill(6)

    if full:
        cur.execute("DELETE FROM technical_indicators")

    rows = []
    cols = ["code", "trade_date", "macd_dif", "macd_dea", "macd_hist",
            "rsi14", "kdj_k", "kdj_d", "kdj_j",
            "boll_mid", "boll_upper", "boll_lower"]
    # 把 CSV 'date' 列重命名为 'trade_date' 以匹配 DB schema
    if "date" in df.columns and "trade_date" not in df.columns:
        df = df.rename(columns={"date": "trade_date"})
    for r in df.itertuples(index=False):
        rd = r._asdict() if hasattr(r, "_asdict") else r._asdict()
        row = []
        for c in cols:
            v = rd.get(c, None)
            if c == "code":
                row.append(str(v).zfill(6) if v else None)
            elif c == "trade_date":
                row.append(str(v)[:10] if v else None)
            else:
                row.append(float(v) if pd.notna(v) else None)
        rows.append(tuple(row))
    cur.executemany(
        f"INSERT OR IGNORE INTO technical_indicators ({','.join(cols)}) "
        f"VALUES ({','.join(['?'] * len(cols))})",
        rows,
    )
    return len(rows)


# ── 主流程 ──────────────────────────────────

IMPORTERS = {
    "stock_basic": import_stock_basic,
    "daily_price": import_daily_price,
    "fund_flow": import_fund_flow,
    "technical_indicators": import_technical_indicators,
    # 其他 importer 在文件下方定义, 通过末尾 IMPORTERS.update() 注入
}


def build(tables: list[str] | None = None, full: bool = True, data_dir: Path = DATA_DIR):
    """主构建流程"""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 1. 建表
    print("[1/4] 建表 (含索引)")
    cur.executescript(SCHEMA_SQL)
    conn.commit()
    print(f"   ✅ 表结构就绪: {DB_PATH.name}")

    # 2. 选择要导入的表
    if tables is None:
        tables = list(IMPORTERS.keys())
    print(f"\n[2/4] 导入表: {tables}")
    print(f"   模式: {'全量 (DELETE + INSERT)' if full else '增量 (仅新行)'}")

    # 3. 逐表导入
    total = 0
    for t in tables:
        if t not in IMPORTERS:
            print(f"   ⚠️ 未知表: {t}, 跳过")
            continue
        print(f"\n   [{t}]")
        try:
            n = IMPORTERS[t](cur, data_dir, full)
            conn.commit()
            print(f"   ✅ {t}: {n:,} 行")
            total += n
        except Exception as e:
            print(f"   ❌ {t} 失败: {e}")
            conn.rollback()
            raise

    # 4. 后置处理: ANALYZE + VACUUM
    print(f"\n[3/4] 后置处理 (ANALYZE + VACUUM)")
    cur.execute("ANALYZE")
    conn.commit()
    cur.execute("VACUUM")
    print(f"   ✅ ANALYZE + VACUUM 完成")

    # 5. 统计
    print(f"\n[4/4] 数据库统计")
    cur.execute("SELECT page_count * page_size / 1024.0 / 1024.0 FROM pragma_page_count(), pragma_page_size()")
    size_mb = cur.fetchone()[0]
    print(f"   大小: {size_mb:.2f} MB")

    for t in tables:
        cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        n = cur.fetchone()[0]
        print(f"   {t:<25} {n:>12,}")

    conn.close()
    elapsed = time.time() - t0
    print(f"\n✅ 完成 (耗时 {elapsed:.1f}s, 共导入 {total:,} 行)")


def main():
    parser = argparse.ArgumentParser(
        description="从 CSV 重建 quant.db (LIVE_TRADING_ROADMAP 数据流)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--incremental", action="store_true",
        help="增量模式: 仅导入比 DB 现有数据更新的行",
    )
    parser.add_argument(
        "--table", action="append", choices=list(IMPORTERS.keys()),
        help=f"只导入指定表 (可多次指定), 可选: {list(IMPORTERS.keys())}",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=DATA_DIR,
        help=f"数据目录 (默认 {DATA_DIR.name})",
    )
    args = parser.parse_args()

    build(
        tables=args.table,
        full=not args.incremental,
        data_dir=args.data_dir,
    )


# ── 新增 importers (整合 consolidate_db / import_small_csvs 逻辑) ─────

def import_block_trade(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """大宗交易 ← raw/reference/block_trade.csv

    CSV 字段: code, market, name, date, deal_price, close_price, premium_pct,
              vol, amount, buyer, seller
    DB 字段: code, trade_date, name, deal_price, close_price, premium_pct,
             volume, amount, buyer, seller
    映射: date→trade_date, vol→volume
    """
    import pandas as pd
    csv_path = data_dir / "raw" / "reference" / "block_trade.csv"
    if not csv_path.exists():
        print(f"   ⚠️ {csv_path.name} 不存在, 跳过")
        return 0
    df = pd.read_csv(csv_path, dtype={"code": str}, low_memory=False)
    # 字段重命名
    df = df.rename(columns={"date": "trade_date", "vol": "volume"})
    df = df.dropna(subset=["code", "trade_date"])
    df["code"] = df["code"].astype(str).str.replace(r"^(sz|sh|bj)", "", regex=True).str.zfill(6)
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["trade_date"])
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
    n_updated = 0
    for row in rows:
        cur.execute("""
            UPDATE stock_basic SET
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


def import_research_report(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """研报 ← raw/reference/research_report.csv"""
    import csv as _csv
    csv_path = data_dir / "raw" / "reference" / "research_report.csv"
    if not csv_path.exists():
        print(f"   ⚠️ {csv_path.name} 不存在, 跳过")
        return 0
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for r in _csv.DictReader(f):
            code = r.get("code", "").strip()
            code = code.zfill(6) if code.isdigit() else code
            rows.append((
                code,
                r.get("date", "")[:10] if r.get("date") else None,
                r.get("rating") or None,
                r.get("rating_change") or None,
                r.get("title") or None,
                r.get("author") or None,
                r.get("institution") or None,
                r.get("url") or None,
            ))
    cur.executemany(
        "INSERT OR IGNORE INTO research_report "
        "(code, date, rating, rating_change, title, author, institution, url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT research_report: {len(rows)} 行")
    return len(rows)


def import_em_global_news(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """财经新闻 ← raw/reference/em_global_news.csv"""
    import csv as _csv
    csv_path = data_dir / "raw" / "reference" / "em_global_news.csv"
    if not csv_path.exists():
        print(f"   ⚠️ {csv_path.name} 不存在, 跳过")
        return 0
    rows = [
        (r.get("date", "")[:10] if r.get("date") else None,
         r.get("title") or None, r.get("url") or None,
         r.get("summary") or None, r.get("source") or None)
        for r in _csv.DictReader(open(csv_path, "r", encoding="utf-8-sig", newline=""))
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO em_global_news "
        "(date, title, url, summary, source) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT em_global_news: {len(rows)} 行")
    return len(rows)


def import_ths_hot_reason(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """同花顺热股 ← raw/reference/ths_hot_reason.csv"""
    import csv as _csv
    csv_path = data_dir / "raw" / "reference" / "ths_hot_reason.csv"
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
            code_raw = r.get("code", "").strip()
            code = code_raw.zfill(6) if code_raw.isdigit() else code_raw
            rows.append((
                r.get("date", "")[:10] if r.get("date") else None,
                int(r["rank"]) if r.get("rank", "").isdigit() else None,
                code, r.get("name") or None,
                _f(r.get("hot_value")), r.get("concept") or None,
                r.get("reason") or None, _f(r.get("change_pct")),
            ))
    cur.executemany(
        "INSERT OR IGNORE INTO ths_hot_reason "
        "(date, rank, code, name, hot_value, concept, reason, change_pct) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT ths_hot_reason: {len(rows)} 行")
    return len(rows)


# ── 更新 IMPORTERS 字典 ────────────────────────
IMPORTERS.update({
    "block_trade": import_block_trade,
    "dividend": import_dividend,
    "announcements": import_announcements,
    "holder_num": import_holder_num,
    "benchmark": import_benchmark,
    "finance": import_finance,
    "stock_profile": import_stock_profile,
    "research_report": import_research_report,
    "em_global_news": import_em_global_news,
    "ths_hot_reason": import_ths_hot_reason,
})


if __name__ == "__main__":
    main()
