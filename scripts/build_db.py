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

    源: market_data/reference/tencent_quotes.csv
         或 market_data/stock_basic.csv (fallback)
    """
    candidates = [
        data_dir / "reference" / "tencent_quotes.csv",
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

    rows = [
        (r.code, r.trade_date,
         float(r.main_net) if pd.notna(r.main_net) else None,
         float(r.super_large_net) if pd.notna(r.super_large_net) else None,
         float(r.large_net) if pd.notna(r.large_net) else None,
         float(r.medium_net) if pd.notna(r.medium_net) else None,
         float(r.small_net) if pd.notna(r.small_net) else None)
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
    dfs = [pd.read_csv(f, dtype={"code": str}, low_memory=False) for f in files]
    df = pd.concat(dfs, ignore_index=True)

    df = df.dropna(subset=["code", "trade_date"])
    df["code"] = df["code"].astype(str).str.replace(r"^(sz|sh|bj)", "", regex=True).str.zfill(6)

    if full:
        cur.execute("DELETE FROM technical_indicators")

    rows = []
    cols = ["code", "trade_date", "macd_dif", "macd_dea", "macd_hist",
            "rsi14", "kdj_k", "kdj_d", "kdj_j",
            "boll_mid", "boll_upper", "boll_lower"]
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


if __name__ == "__main__":
    main()
