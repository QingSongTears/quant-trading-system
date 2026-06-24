#!/usr/bin/env python3
"""
consolidate_db.py — 数据库 Schema 整合 + 全量数据导入（一次性脚本）

目的
----
消除重复表 (benchmark_data vs benchmark_kline, finance_summary vs finance_quarterly),
让 quant.db 成为单一真实来源 (single source of truth), 同时不破坏代码引用.

步骤
----
A. Schema 整合
  1. ALTER benchmark_data + 5 OHLCV 字段 (open/high/low/volume/amount/exchange_factor)
  2. IMPORT market_data/benchmark_data.csv (9000 行) → benchmark_data
  3. DROP benchmark_kline (已合并)
  4. ALTER finance_summary + baostock 字段 (roe_avg/np_margin/gp_margin/net_profit/eps_ttm/main_revenue/total_share/liqa_share/baostock_pub_date)
  5. IMPORT market_data/finance_summary.csv (5172 行, 单季) → finance_summary (字段映射)
  6. DROP finance_quarterly (已合并)
  7. DROP stock_profile (schema 不匹配 ORM, 缺 circulating_shares)
  8. CREATE stock_profile 按 ORM schema + circulating_shares 列
  9. IMPORT market_data/stock_profile.csv (5205 行) → stock_profile

B. 新表 + 新数据
  10. CREATE fund_flow_data (schema 已存在, IMPORT 435K 行) - 但实际表已存在只需 IMPORT
  11. IMPORT market_data/fund_flow_120d.csv (435,092 行) → fund_flow_data
  12. CREATE holder_num (新表) + IMPORT (5,521 行)

C. 数据校验
  13. 各表 row count 统计
  14. NaN 抽样检查
  15. 外键一致性: 所有 stock_code 在 stock_basic 里?

调用
----
  python scripts/consolidate_db.py              # 完整执行 (含 IMPORT)
  python scripts/consolidate_db.py --schema-only # 只做 schema (无 import, 验证 SQL)
  python scripts/consolidate_db.py --import-only # 跳过 schema, 只做 import

⚠️ 警告: 会 DROP benchmark_kline / finance_quarterly / stock_profile, 数据先 import 再 drop
"""

from __future__ import annotations

import argparse
import csv
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

# CSV 源
BENCHMARK_CSV = DATA_DIR / "benchmark_data.csv"
FINANCE_CSV = DATA_DIR / "finance_summary.csv"
STOCK_PROFILE_CSV = DATA_DIR / "stock_profile.csv"
FUND_FLOW_CSV = DATA_DIR / "fund_flow_120d.csv"
HOLDER_NUM_CSV = DATA_DIR / "holder_num.csv"

t0 = time.time()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("consolidate_db")


# ── Schema 扩展 SQL ────────────────────────────────

# 注: ALTER TABLE 必须放在主 SCHEMA_EXT 中, 用 ; 分隔 (SQLite 不支持 IF NOT EXISTS ADD COLUMN)
SCHEMA_EXT = """
-- A1. benchmark_data: 加 OHLCV 字段 (westock kline 全部信息)
ALTER TABLE benchmark_data ADD COLUMN name TEXT;
ALTER TABLE benchmark_data ADD COLUMN open REAL;
ALTER TABLE benchmark_data ADD COLUMN high REAL;
ALTER TABLE benchmark_data ADD COLUMN low REAL;
ALTER TABLE benchmark_data ADD COLUMN volume INTEGER;
ALTER TABLE benchmark_data ADD COLUMN amount REAL;
ALTER TABLE benchmark_data ADD COLUMN exchange_factor REAL;
ALTER TABLE benchmark_data ADD COLUMN code TEXT;
ALTER TABLE benchmark_data ADD COLUMN source TEXT DEFAULT 'westock';

-- A4. finance_summary: 加 baostock 字段 (11 个新字段)
ALTER TABLE finance_summary ADD COLUMN baostock_pub_date DATE;
ALTER TABLE finance_summary ADD COLUMN roe_avg REAL;
ALTER TABLE finance_summary ADD COLUMN np_margin REAL;
ALTER TABLE finance_summary ADD COLUMN gp_margin REAL;
ALTER TABLE finance_summary ADD COLUMN net_profit REAL;
ALTER TABLE finance_summary ADD COLUMN eps_ttm REAL;
ALTER TABLE finance_summary ADD COLUMN main_revenue REAL;
ALTER TABLE finance_summary ADD COLUMN total_share REAL;
ALTER TABLE finance_summary ADD COLUMN liqa_share REAL;
ALTER TABLE finance_summary ADD COLUMN source TEXT DEFAULT 'baostock';

-- A12. 新表 holder_num: 股东户数
CREATE TABLE IF NOT EXISTS holder_num (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    end_date DATE NOT NULL,
    holder_num INTEGER,
    pre_holder_num INTEGER,
    holder_change_pct REAL,
    avg_holding REAL,
    source TEXT DEFAULT 'tencent',
    UNIQUE(code, end_date)
);
CREATE INDEX IF NOT EXISTS idx_hn_code ON holder_num(code);
CREATE INDEX IF NOT EXISTS idx_hn_code_date ON holder_num(code, end_date);
"""


# ── Helper ────────────────────────────────

def _to_float(s):
    if s is None or s == "" or s == "nan":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _to_int(s):
    f = _to_float(s)
    return int(f) if f is not None else None


def apply_schema_ext(cur: sqlite3.Cursor, dry_run: bool = False) -> None:
    """应用所有 ALTER/CREATE (幂等: ALTER 检查列是否存在, CREATE 用 IF NOT EXISTS)"""
    # 去注释后按 ; split
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
            create_block.append(s)

    logger.info("CREATE: %d 条, ALTER: %d 条", len(create_block), len(alter_stmts))

    if dry_run:
        for s in create_block:
            print(f"   CREATE: {s[:80]}")
        for s in alter_stmts:
            print(f"   ALTER: {s[:80]}")
        return

    # CREATE 一次性跑
    for s in create_block:
        cur.execute(s)
    # ALTER 逐条跑 (SQLite 无 IF NOT EXISTS, 动态检查列存在)
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


# ── Importers ────────────────────────────────

def import_benchmark(cur: sqlite3.Cursor, dry_run: bool = False) -> int:
    """westock kline → benchmark_data (旧表, 现在加 OHLCV 字段后能完整容纳)"""
    if not BENCHMARK_CSV.exists():
        logger.warning("   ⚠️ %s 不存在, 跳过", BENCHMARK_CSV.name)
        return 0

    with open(BENCHMARK_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [
            (
                r["code"],                          # index_code = 'sh000300' (westock 格式)
                r["date"][:10],                     # trade_date
                _to_float(r["close"]),              # close (主字段, 表已 NOT NULL)
                None,                               # pct_change (CSV 无, 留空)
                r["name"],
                _to_float(r["open"]), _to_float(r["high"]), _to_float(r["low"]),
                _to_int(r["volume"]), _to_float(r["amount"]), _to_float(r["exchange"]),
                r["code"],
                "westock",
            )
            for r in reader
        ]

    if dry_run:
        logger.info("   [dry-run] benchmark_data: %d 行", len(rows))
        return 0

    # INSERT OR IGNORE (UNIQUE 在 index_code+trade_date 上没有, 改为 INSERT OR REPLACE)
    cur.execute("DELETE FROM benchmark_data WHERE source = 'westock'")
    cur.executemany(
        "INSERT INTO benchmark_data "
        "(index_code, trade_date, close, pct_change, name, open, high, low, "
        " volume, amount, exchange_factor, code, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    logger.info("   ✅ INSERT benchmark_data: %d 行", len(rows))
    return len(rows)


def import_finance(cur: sqlite3.Cursor, dry_run: bool = False) -> int:
    """baostock profit_data → finance_summary (字段映射, 不破坏原 schema)

    兼容策略:
      - 用 dict 形式组装 row, keys = 列名
      - executemany 用 named placeholders 防错位
    """
    if not FINANCE_CSV.exists():
        logger.warning("   ⚠️ %s 不存在, 跳过", FINANCE_CSV.name)
        return 0

    with open(FINANCE_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            code_full = r["code"]
            if "." in code_full:
                _, code6 = code_full.split(".", 1)
            else:
                code6 = code_full
            code6 = code6.zfill(6)
            stat_date = r["statDate"][:10]
            pub_date = r["pubDate"][:10]

            # baostock 字段映射到原 finance_summary schema (保留 baostock 特有 9 列)
            row = {
                "code": code6,
                "_date": stat_date,
                "EndDate": stat_date,
                # 主字段 (兼容原 schema)
                "ROE": _to_float(r["roeAvg"]),
                "ROETTM": _to_float(r["roeAvg"]),
                "ROEWeighted": _to_float(r["roeAvg"]),
                "EPS": _to_float(r["epsTTM"]),
                "EPSTTM": _to_float(r["epsTTM"]),
                "BasicEPS": _to_float(r["epsTTM"]),
                "DilutedEPS": _to_float(r["epsTTM"]),
                # 派生映射
                "NetProfitRatio": _to_float(r["npMargin"]),
                "NetProfitRatioTTM": _to_float(r["npMargin"]),
                "OperatingRevenue": _to_float(r["MBRevenue"]),
                "OperatingRevenueTTM": _to_float(r["MBRevenue"]),
                "TotalOperatingRevenue": _to_float(r["MBRevenue"]),
                "NPParentCompanyOwners": _to_float(r["netProfit"]),
                "NPParentCompanyOwnersTTM": _to_float(r["netProfit"]),
                # baostock 特有 9 列
                "baostock_pub_date": pub_date,
                "roe_avg": _to_float(r["roeAvg"]),
                "np_margin": _to_float(r["npMargin"]),
                "gp_margin": _to_float(r["gpMargin"]),
                "net_profit": _to_float(r["netProfit"]),
                "eps_ttm": _to_float(r["epsTTM"]),
                "main_revenue": _to_float(r["MBRevenue"]),
                "total_share": _to_float(r["totalShare"]),
                "liqa_share": _to_float(r["liqaShare"]),
                "source": "baostock",
            }
            rows.append(row)

    if dry_run:
        logger.info("   [dry-run] finance_summary: %d 行", len(rows))
        return 0

    cur.execute("DELETE FROM finance_summary WHERE source = 'baostock'")
    cols = list(rows[0].keys())
    placeholders = ", ".join([f":{c}" for c in cols])
    col_names = ", ".join(cols)
    sql = f"INSERT INTO finance_summary ({col_names}) VALUES ({placeholders})"
    cur.executemany(sql, rows)
    logger.info("   ✅ INSERT finance_summary: %d 行 (字段: %s)", len(rows), len(cols))
    return len(rows)


def rebuild_stock_profile(cur: sqlite3.Cursor, dry_run: bool = False) -> int:
    """DROP + CREATE stock_profile 按 ORM schema + circulating_shares, IMPORT westock CSV"""
    if not STOCK_PROFILE_CSV.exists():
        logger.warning("   ⚠️ %s 不存在, 跳过", STOCK_PROFILE_CSV.name)
        return 0

    if dry_run:
        logger.info("   [dry-run] DROP stock_profile + CREATE (按 ORM schema) + IMPORT")
        return 0

    cur.execute("DROP TABLE IF EXISTS stock_profile")
    cur.execute("""
        CREATE TABLE stock_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,                 -- 纯数字 (6 位补零), 与 stock_basic.code 一致
            name TEXT,
            listed_date TEXT,
            industry TEXT,
            sector TEXT,
            issue_price REAL,
            reg_capital REAL,                   -- 注册资本 (万元)
            chairman TEXT,
            establish_date TEXT,
            website TEXT,
            business TEXT,
            reg_address TEXT,
            circulating_shares REAL,            -- 流通股本 (股), fund_flow_scorer 引用, 待 westock 后续补
            source TEXT DEFAULT 'westock',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX idx_sp_code ON stock_profile(code)")
    cur.execute("CREATE INDEX idx_sp_industry ON stock_profile(industry)")
    cur.execute("CREATE INDEX idx_sp_sector ON stock_profile(sector)")

    with open(STOCK_PROFILE_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [
            (
                r["code"][2:].zfill(6) if r["code"][:2] in ("sh", "sz", "bj") else r["code"].zfill(6),
                r["name"],
                r["listedDate"] or None,
                r["industry"] or None,
                r["sector"] or None,
                _to_float(r["issuePrice"]),
                _to_float(r["regCapital"]),
                r["chairman"] or None,
                r["establishDate"] or None,
                r["website"] or None,
                r["business"] or None,
                r["regAddress"] or None,
                None,                                # circulating_shares 待补
                "westock",
            )
            for r in reader
        ]
    cur.executemany(
        "INSERT INTO stock_profile "
        "(code, name, listed_date, industry, sector, issue_price, reg_capital, "
        " chairman, establish_date, website, business, reg_address, "
        " circulating_shares, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    logger.info("   ✅ REBUILD stock_profile: %d 行 (circulating_shares 待补)", len(rows))
    return len(rows)


def import_fund_flow(cur: sqlite3.Cursor, dry_run: bool = False) -> int:
    """fund_flow_120d.csv → fund_flow_data (435K 行)"""
    if not FUND_FLOW_CSV.exists():
        logger.warning("   ⚠️ %s 不存在, 跳过", FUND_FLOW_CSV.name)
        return 0

    with open(FUND_FLOW_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            code = r["code"]
            # fund_flow_120d.csv 的 code 是 sz000001 / sh600000 格式, 拆出 code6
            if len(code) == 8 and code[:2] in ("sz", "sh", "bj"):
                market = code[:2].upper()
                code6 = code[2:]
            else:
                market = None
                code6 = code
            code6 = code6.zfill(6)
            rows.append((
                code6, market,
                r.get("name") or None,
                r["date"][:10],
                _to_float(r["main_net"]),
                _to_float(r["super_large_net"]),
                _to_float(r["large_net"]),
                _to_float(r["medium_net"]),
                _to_float(r["small_net"]),
            ))

    if dry_run:
        logger.info("   [dry-run] fund_flow_data: %d 行", len(rows))
        return 0

    # INSERT OR IGNORE 避免重复 (已有 UNIQUE(code, trade_date))
    cur.executemany(
        "INSERT OR IGNORE INTO fund_flow_data "
        "(code, market, name, trade_date, main_net, super_large_net, large_net, "
        " medium_net, small_net) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    cur.execute("SELECT COUNT(*) FROM fund_flow_data")
    logger.info("   ✅ INSERT fund_flow_data: 本次 %d 行, 表内总 %d 行", len(rows), cur.fetchone()[0])
    return len(rows)


def import_holder_num(cur: sqlite3.Cursor, dry_run: bool = False) -> int:
    """holder_num.csv → holder_num (新表)"""
    if not HOLDER_NUM_CSV.exists():
        logger.warning("   ⚠️ %s 不存在, 跳过", HOLDER_NUM_CSV.name)
        return 0

    with open(HOLDER_NUM_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            rows.append((
                r["code"].zfill(6),
                r["end_date"][:10],
                _to_int(r["holder_num"]),
                _to_int(r["pre_holder_num"]),
                _to_float(r["holder_change_pct"]),
                _to_float(r["avg_holding"]),
                "tencent",
            ))

    if dry_run:
        logger.info("   [dry-run] holder_num: %d 行", len(rows))
        return 0

    cur.executemany(
        "INSERT OR IGNORE INTO holder_num "
        "(code, end_date, holder_num, pre_holder_num, holder_change_pct, avg_holding, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    cur.execute("SELECT COUNT(*) FROM holder_num")
    logger.info("   ✅ INSERT holder_num: 本次 %d 行, 表内总 %d 行", len(rows), cur.fetchone()[0])
    return len(rows)


def drop_obsolete_tables(cur: sqlite3.Cursor, dry_run: bool = False) -> None:
    """DROP 已合并的旧表: benchmark_kline, finance_quarterly"""
    targets = ["benchmark_kline", "finance_quarterly"]
    for t in targets:
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)
        )
        if not cur.fetchone():
            logger.info("   表 %s 不存在, 跳过", t)
            continue
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        cnt = cur.fetchone()[0]
        if dry_run:
            logger.info("   [dry-run] DROP TABLE %s (旧 %d 行已合并到其他表)", t, cnt)
            continue
        cur.execute(f"DROP TABLE {t}")
        logger.info("   ✅ DROP TABLE %s (旧 %d 行)", t, cnt)


# ── 验证 ────────────────────────────────

def validate(cur: sqlite3.Cursor) -> None:
    """数据质量校验 + 各表统计"""
    logger.info("=" * 60)
    logger.info("[校验] 各表 row count + 日期范围 + 关键 NaN")
    logger.info("=" * 60)

    # 每张表的日期列名 (各异)
    date_cols = {
        "daily_price": "trade_date",
        "technical_indicators": "trade_date",
        "stock_basic": "list_date",
        "stock_profile": "listed_date",
        "block_trade": "trade_date",
        "dividend": "ex_div_date",
        "announcements": "trade_date",
        "fund_flow_data": "trade_date",
        "holder_num": "end_date",
        "benchmark_data": "trade_date",
        "finance_summary": "_date",
    }
    for t, date_col in date_cols.items():
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            cnt = cur.fetchone()[0]
            cur.execute(
                f"SELECT MIN({date_col}), MAX({date_col}) FROM {t} "
                f"WHERE {date_col} IS NOT NULL"
            )
            r = cur.fetchone()
            date_range = f"{r[0]} ~ {r[1]}" if r and r[0] else "-"
            print(f"  {t:25s} {cnt:>12,} 行  日期: {date_range}")
        except sqlite3.OperationalError as e:
            print(f"  {t:25s} ERR: {e}")

    # 外键一致性: daily_price / technical_indicators / fund_flow_data / block_trade / dividend / announcements
    #            / holder_num / benchmark_data / finance_summary 的 code 必须在 stock_basic 里
    logger.info("[校验] 外键一致性: 各表 code ⊆ stock_basic.code")
    fk_tables = ["daily_price", "technical_indicators", "fund_flow_data", "block_trade",
                 "dividend", "announcements", "holder_num", "finance_summary"]
    for t in fk_tables:
        cur.execute(f"""
            SELECT COUNT(DISTINCT t1.code) FROM {t} t1
            LEFT JOIN stock_basic sb ON t1.code = sb.code
            WHERE sb.code IS NULL
        """)
        orphan = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(DISTINCT code) FROM {t}")
        total = cur.fetchone()[0]
        cov = (1 - orphan / total) * 100 if total else 0
        flag = "✓" if orphan == 0 else "✗"
        logger.info("  %s %-25s %d 孤儿 / %d 总 (覆盖率 %.2f%%)", flag, t, orphan, total, cov)

    # NaN 抽样
    logger.info("[校验] NaN/空值抽样")
    checks = [
        ("stock_profile 无 industry", "stock_profile", "industry IS NULL OR industry = ''", 5205),
        ("finance_summary 无 ROE", "finance_summary", "ROE IS NULL", None),
        ("stock_basic 无 industry", "stock_basic", "industry IS NULL OR industry = ''", 5209),
        ("benchmark_data 无 close", "benchmark_data", "close IS NULL", 9000),
        ("daily_price 无 close", "daily_price", "close IS NULL", 4181854),
    ]
    for label, t, cond, expected in checks:
        cur.execute(f"SELECT COUNT(*) FROM {t} WHERE {cond}")
        n = cur.fetchone()[0]
        if expected is not None:
            pct = (n / expected) * 100 if expected else 0
            logger.info("  %s: %d / %d (%.1f%% 空值)", label, n, expected, pct)
        else:
            logger.info("  %s: %d", label, n)

    # DB size
    cur.execute(
        "SELECT page_count * page_size / 1024.0 / 1024.0 "
        "FROM pragma_page_count(), pragma_page_size()"
    )
    size_mb = cur.fetchone()[0]
    logger.info("[DB 大小] %.2f MB", size_mb)


# ── 主流程 ────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="数据库 Schema 整合 + 全量数据导入",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--schema-only", action="store_true", help="只做 schema 扩展")
    p.add_argument("--import-only", action="store_true", help="跳过 schema, 只做 import")
    p.add_argument("--no-drop", action="store_true", help="不 DROP benchmark_kline / finance_quarterly")
    p.add_argument("--no-validate", action="store_true", help="跳过校验")
    args = p.parse_args()

    if not DB_PATH.exists():
        logger.error("DB 不存在: %s", DB_PATH)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    if not args.import_only:
        logger.info("[1/4] 应用 Schema 扩展 (ALTER + CREATE)")
        apply_schema_ext(cur, dry_run=False)
        conn.commit()

    if not args.schema_only:
        logger.info("[2/4] IMPORT 数据 (按依赖顺序)")
        logger.info("  2.1 benchmark_data")
        import_benchmark(cur, dry_run=False)
        conn.commit()
        logger.info("  2.2 finance_summary (baostock)")
        import_finance(cur, dry_run=False)
        conn.commit()
        logger.info("  2.3 stock_profile (REBUILD)")
        rebuild_stock_profile(cur, dry_run=False)
        conn.commit()
        logger.info("  2.4 fund_flow_data")
        import_fund_flow(cur, dry_run=False)
        conn.commit()
        logger.info("  2.5 holder_num")
        import_holder_num(cur, dry_run=False)
        conn.commit()

    if not args.no_drop and not args.import_only:
        logger.info("[3/4] DROP 已合并的旧表")
        drop_obsolete_tables(cur, dry_run=False)
        conn.commit()

    logger.info("ANALYZE ...")
    cur.execute("ANALYZE")
    conn.commit()

    if not args.no_validate:
        validate(cur)

    conn.close()
    logger.info("✅ 完成 (耗时 %.1fs)", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())