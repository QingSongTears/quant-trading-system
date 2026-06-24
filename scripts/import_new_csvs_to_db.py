#!/usr/bin/env python3
"""
import_new_csvs_to_db.py — 把 import_from_westock_baostock_akshare.py 生成的 3 个 CSV 入库

不动 build_db.py 既有的 4 个 importer, 本脚本扩展:
  1. ALTER TABLE stock_basic ADD industry + sector + listedDate + regCapital + ... (westock profile 14 字段)
  2. CREATE TABLE benchmark_kline (westock 6 指数全 OHLCV)
  3. CREATE TABLE finance_quarterly (baostock profit_data 11 字段)

调用:
  python scripts/import_new_csvs_to_db.py                  # 导入所有 3 个表
  python scripts/import_new_csvs_to_db.py --table stock    # 只 stock_basic 扩展
  python scripts/import_new_csvs_to_db.py --table bench    # 只 benchmark_kline
  python scripts/import_new_csvs_to_db.py --table finance  # 只 finance_quarterly
  python scripts/import_new_csvs_to_db.py --dry-run        # 只打印 SQL, 不执行

设计原则:
  - 用 INSERT OR IGNORE + UNIQUE 约束保证可重入 (重跑不会重复)
  - 不破坏既有表结构, 只扩展 (ALTER TABLE ADD COLUMN 安全可重入)
  - 不动 build_db.py 既有逻辑
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "market_data"
DB_PATH = PROJECT_ROOT / "database" / "quant.db"

STOCK_PROFILE_CSV = DATA_DIR / "stock_profile.csv"
BENCHMARK_CSV = DATA_DIR / "benchmark_data.csv"
FINANCE_CSV = DATA_DIR / "finance_summary.csv"

t0 = time.time()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("import_new_csvs")


# ── Schema 扩展 ────────────────────────────────

SCHEMA_EXT = """
-- 1. 扩展 stock_basic: 加 westock profile 字段
ALTER TABLE stock_basic ADD COLUMN sector TEXT;
ALTER TABLE stock_basic ADD COLUMN listed_date_alt TEXT;
ALTER TABLE stock_basic ADD COLUMN issue_price REAL;
ALTER TABLE stock_basic ADD COLUMN reg_capital REAL;
ALTER TABLE stock_basic ADD COLUMN establish_date TEXT;
ALTER TABLE stock_basic ADD COLUMN chairman TEXT;
ALTER TABLE stock_basic ADD COLUMN website TEXT;
ALTER TABLE stock_basic ADD COLUMN business TEXT;
ALTER TABLE stock_basic ADD COLUMN reg_address TEXT;
ALTER TABLE stock_basic ADD COLUMN office_address TEXT;
ALTER TABLE stock_basic ADD COLUMN tel TEXT;
ALTER TABLE stock_basic ADD COLUMN email TEXT;

-- 2. 新表 benchmark_kline: 指数全 OHLCV (westock kline)
CREATE TABLE IF NOT EXISTS benchmark_kline (
    code TEXT NOT NULL,             -- sh000300 / sh000905 / ...
    name TEXT,                       -- 沪深300 / 中证500 / ...
    trade_date DATE NOT NULL,
    open REAL, high REAL, low REAL,
    close REAL NOT NULL,
    volume INTEGER,
    amount REAL,
    exchange_factor REAL,            -- westock 返回的换手率 proxy
    UNIQUE(code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_bk_code_date ON benchmark_kline(code, trade_date);
CREATE INDEX IF NOT EXISTS idx_bk_date ON benchmark_kline(trade_date);

-- 3. 新表 finance_quarterly: baostock profit_data 11 字段
CREATE TABLE IF NOT EXISTS finance_quarterly (
    code TEXT NOT NULL,              -- 'sz.000001' 风格, 保留原 baostock 格式
    code6 TEXT NOT NULL,             -- 6 位补零, 便于 join stock_basic
    pub_date DATE,                   -- 发布时间
    stat_date DATE NOT NULL,         -- 统计日期 (季度末)
    roe_avg REAL,                    -- 平均 ROE
    np_margin REAL,                  -- 净利润率
    gp_margin REAL,                  -- 毛利率
    net_profit REAL,                 -- 净利润 (元)
    eps_ttm REAL,                    -- 滚动 EPS
    main_revenue REAL,               -- 主营业务收入 (MBRevenue)
    total_share REAL,                -- 总股本
    liqa_share REAL,                 -- 流通股本
    UNIQUE(code, stat_date)
);
CREATE INDEX IF NOT EXISTS idx_fq_code ON finance_quarterly(code);
CREATE INDEX IF NOT EXISTS idx_fq_code6 ON finance_quarterly(code6);
CREATE INDEX IF NOT EXISTS idx_fq_code6_date ON finance_quarterly(code6, stat_date);
CREATE INDEX IF NOT EXISTS idx_fq_stat_date ON finance_quarterly(stat_date);
"""


# ── 字段映射 ────────────────────────────────

def import_stock_profile(cur: sqlite3.Cursor, full: bool = False) -> int:
    """
    ALTER TABLE 已建好 stock_basic 扩展列. 本函数:
      - 从 market_data/stock_profile.csv 读 westock profile (5205 行, 15 字段)
      - UPDATE stock_basic 的扩展列
      - code 标准化: sh600000 → 600000, sz000001 → 000001
      - industry: '银行' → 直接写入 stock_basic.industry (已存在的列)
    """
    import pandas as pd

    if not STOCK_PROFILE_CSV.exists():
        print(f"   ⚠️ {STOCK_PROFILE_CSV.name} 不存在, 跳过")
        return 0

    df = pd.read_csv(STOCK_PROFILE_CSV, dtype=str, keep_default_na=False)
    print(f"   读 {STOCK_PROFILE_CSV.name}: {len(df)} 行")
    print(f"   字段: {list(df.columns)}")

    # code 列: sh600000 → 600000
    df["code6"] = df["code"].str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6)

    # 字段映射 (CSV 列 → DB 列)
    # westock: code / name / listedDate / business / website / industry / sector
    #         / issuePrice / regCapital / establishDate / chairman / regAddress
    #         / officeAddress / tel / email
    field_map = {
        "industry": "industry",
        "sector": "sector",
        "listedDate": "listed_date_alt",
        "issuePrice": "issue_price",
        "regCapital": "reg_capital",
        "establishDate": "establish_date",
        "chairman": "chairman",
        "website": "website",
        "business": "business",
        "regAddress": "reg_address",
        "officeAddress": "office_address",
        "tel": "tel",
        "email": "email",
    }

    # 增量: 已有 code 不覆盖 (用 UPDATE 但只覆盖新行)
    # 简化: 全部 UPDATE (5100 行 1-2 秒, 重复运行无副作用)
    n_updated = 0
    for r in df.itertuples(index=False):
        code6 = r.code6
        sets = []
        vals = []
        for csv_col, db_col in field_map.items():
            v = getattr(r, csv_col, "")
            if v == "" or v == "nan":
                continue
            sets.append(f"{db_col} = ?")
            vals.append(v)
        if not sets:
            continue
        vals.append(code6)
        sql = f"UPDATE stock_basic SET {', '.join(sets)} WHERE code = ?"
        cur.execute(sql, vals)
        if cur.rowcount > 0:
            n_updated += 1

    print(f"   ✅ UPDATE stock_basic: {n_updated} 行")
    return n_updated


def import_benchmark_kline(cur: sqlite3.Cursor, full: bool = False) -> int:
    """benchmark_kline ← market_data/benchmark_data.csv (9000 行, 6 指数)"""
    import pandas as pd

    if not BENCHMARK_CSV.exists():
        print(f"   ⚠️ {BENCHMARK_CSV.name} 不存在, 跳过")
        return 0

    df = pd.read_csv(BENCHMARK_CSV, dtype=str, keep_default_na=False)
    print(f"   读 {BENCHMARK_CSV.name}: {len(df)} 行")
    print(f"   字段: {list(df.columns)}")

    # 列: code / name / date / open / last / high / low / volume / amount / exchange
    # DB 列: code / name / trade_date / open / high / low / close / volume / amount / exchange_factor
    # 映射: last → close, exchange → exchange_factor

    if full:
        cur.execute("DELETE FROM benchmark_kline")

    rows = []
    for r in df.itertuples(index=False):
        rows.append((
            r.code, r.name, r.date,
            _to_float(r.open), _to_float(r.high), _to_float(r.low),
            _to_float(r.close),
            _to_int(r.volume), _to_float(r.amount),
            _to_float(r.exchange),
        ))
    cur.executemany(
        "INSERT OR IGNORE INTO benchmark_kline "
        "(code, name, trade_date, open, high, low, close, volume, amount, exchange_factor) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT benchmark_kline: {len(rows)} 行")
    return len(rows)


def import_finance_quarterly(cur: sqlite3.Cursor, full: bool = False) -> int:
    """finance_quarterly ← market_data/finance_summary.csv (5172 行)"""
    import pandas as pd

    if not FINANCE_CSV.exists():
        print(f"   ⚠️ {FINANCE_CSV.name} 不存在, 跳过")
        return 0

    df = pd.read_csv(FINANCE_CSV, dtype=str, keep_default_na=False)
    print(f"   读 {FINANCE_CSV.name}: {len(df)} 行")
    print(f"   字段: {list(df.columns)}")

    # CSV 列: code / pubDate / statDate / roeAvg / npMargin / gpMargin
    #        / netProfit / epsTTM / MBRevenue / totalShare / liqaShare
    # DB 列: code / code6 / pub_date / stat_date / roe_avg / np_margin / gp_margin
    #        / net_profit / eps_ttm / main_revenue / total_share / liqa_share

    if full:
        cur.execute("DELETE FROM finance_quarterly")

    rows = []
    for r in df.itertuples(index=False):
        # code: sh.600000 / sz.000001 → 6位 + 6位
        code_full = r.code
        m = code_full.split(".")
        if len(m) == 2:
            market_prefix, code6 = m[0].lower(), m[1]
        else:
            code6 = code_full
            market_prefix = ""
        rows.append((
            code_full, code6.zfill(6),
            r.pubDate[:10] if r.pubDate else None,
            r.statDate[:10] if r.statDate else None,
            _to_float(r.roeAvg), _to_float(r.npMargin), _to_float(r.gpMargin),
            _to_float(r.netProfit), _to_float(r.epsTTM), _to_float(r.MBRevenue),
            _to_float(r.totalShare), _to_float(r.liqaShare),
        ))
    cur.executemany(
        "INSERT OR IGNORE INTO finance_quarterly "
        "(code, code6, pub_date, stat_date, roe_avg, np_margin, gp_margin, "
        " net_profit, eps_ttm, main_revenue, total_share, liqa_share) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT finance_quarterly: {len(rows)} 行")
    return len(rows)


def _to_float(s) -> float | None:
    if s is None or s == "" or s == "nan":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _to_int(s) -> int | None:
    f = _to_float(s)
    return int(f) if f is not None else None


# ── 主流程 ──────────────────────────────────

IMPORTERS = {
    "stock": import_stock_profile,
    "bench": import_benchmark_kline,
    "finance": import_finance_quarterly,
}


def main() -> int:
    p = argparse.ArgumentParser(
        description="把 import_from_westock_baostock_akshare.py 生成的 3 个 CSV 入库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例: python scripts/import_new_csvs_to_db.py --table finance",
    )
    p.add_argument("--table", action="append", choices=list(IMPORTERS.keys()),
                   help=f"只导入指定表 (可多次): {list(IMPORTERS.keys())}")
    p.add_argument("--full", action="store_true",
                   help="全量重建 (DELETE 后 INSERT, 默认增量 UPSERT)")
    p.add_argument("--dry-run", action="store_true", help="只打印 SQL, 不执行")
    args = p.parse_args()

    if not DB_PATH.exists():
        print(f"❌ DB 不存在: {DB_PATH}")
        print(f"   请先跑: python scripts/build_db.py")
        return 1

    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    print("[1/3] 建表/扩展 (含索引)")
    if args.dry_run:
        print("   [dry-run] SCHEMA_EXT:")
        print("\n".join("   " + ln for ln in SCHEMA_EXT.strip().split("\n")))
    else:
        # 先去掉所有 -- 注释行, 再按 ; 分割
        sql_no_comment = "\n".join(
            ln for ln in SCHEMA_EXT.split("\n") if not ln.strip().startswith("--")
        )
        create_block = []
        alter_stmts = []
        for stmt in sql_no_comment.split(";"):
            s = stmt.strip()
            if not s:
                continue
            if s.upper().startswith("ALTER TABLE"):
                alter_stmts.append(s)
            else:
                # CREATE TABLE / CREATE INDEX 等
                create_block.append(s)
        # 1. CREATE 一次性跑 (含 IF NOT EXISTS, 全部幂等)
        for s in create_block:
            cur.execute(s)
        # 2. ALTER 逐条跑 (SQLite 没有 IF NOT EXISTS, 动态检查列存在)
        for stmt in alter_stmts:
            m = re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", stmt, re.IGNORECASE)
            if not m:
                cur.execute(stmt)
                continue
            table, col = m.group(1), m.group(2)
            cur.execute(f"PRAGMA table_info({table})")
            existing_cols = {row[1] for row in cur.fetchall()}
            if col in existing_cols:
                logger.debug("   跳过已存在列: %s.%s", table, col)
                continue
            cur.execute(stmt)
            logger.info("   ALTER TABLE %s ADD COLUMN %s", table, col)
        conn.commit()
        print("   ✅ Schema 就绪")

    print(f"\n[2/3] 导入 (模式: {'全量' if args.full else '增量'})")
    tables = args.table or list(IMPORTERS.keys())
    print(f"   表: {tables}")
    total = 0
    for t in tables:
        print(f"\n   [{t}]")
        try:
            if args.dry_run:
                print(f"   [dry-run] skip")
                continue
            n = IMPORTERS[t](cur, full=args.full)
            conn.commit()
            total += n
        except Exception as e:
            conn.rollback()
            print(f"   ❌ {t} 失败: {e}")
            raise

    if not args.dry_run:
        print(f"\n[3/3] 后置处理")
        cur.execute("ANALYZE")
        conn.commit()
        print("   ✅ ANALYZE 完成")

        # 统计
        cur.execute("SELECT page_count * page_size / 1024.0 / 1024.0 FROM pragma_page_count(), pragma_page_size()")
        size_mb = cur.fetchone()[0]
        print(f"   DB 大小: {size_mb:.2f} MB")

        for t in ["stock_basic", "benchmark_kline", "finance_quarterly"]:
            try:
                cur.execute(f'SELECT COUNT(*) FROM "{t}"')
                n = cur.fetchone()[0]
                print(f"   {t:<25} {n:>12,}")
            except sqlite3.OperationalError:
                pass

    conn.close()
    print(f"\n✅ 完成 (耗时 {time.time() - t0:.1f}s, 共导入 {total:,} 行)")
    return 0


if __name__ == "__main__":
    sys.exit(main())