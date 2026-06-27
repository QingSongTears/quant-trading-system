#!/usr/bin/env python3
"""
import_small_csvs.py — 把剩余小 reference CSV 入库 (代码引用但 DB 缺表)

代码引用但 DB 缺表的 3 张:
  - research_report   (sentiment_scorer.py / news_event_scorer.py 用)
  - em_global_news    (v6_pipeline_hybrid.py 用, 字段 sentiment)
  - ths_hot_reason    (import_ths_hot_reason.py 用, 138 行)

数据源:
  market_data/raw/reference/research_report.csv  (2040 行, 8 列)
  market_data/raw/reference/em_global_news.csv   (101 行,  5 列)
  market_data/raw/reference/ths_hot_reason.csv   (138 行, 11 列)

调用:
  python scripts/import_small_csvs.py
"""

from __future__ import annotations

import csv
import logging
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "market_data" / "raw" / "reference"
DB_PATH = PROJECT_ROOT / "database" / "quant.db"

t0 = time.time()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("import_small_csvs")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    date DATE,
    rating TEXT,
    rating_change TEXT,
    title TEXT,
    author TEXT,
    institution TEXT,
    url TEXT,
    UNIQUE(code, date, title)
);
CREATE INDEX IF NOT EXISTS idx_rr_code_date ON research_report(code, date);
CREATE INDEX IF NOT EXISTS idx_rr_date ON research_report(date);

CREATE TABLE IF NOT EXISTS em_global_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE,
    title TEXT,
    url TEXT,
    summary TEXT,
    source TEXT,
    UNIQUE(date, title)
);
CREATE INDEX IF NOT EXISTS idx_egn_date ON em_global_news(date);

CREATE TABLE IF NOT EXISTS ths_hot_reason (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE,
    rank INTEGER,
    code TEXT,
    name TEXT,
    hot_value REAL,
    concept TEXT,
    reason TEXT,
    change_pct REAL,
    UNIQUE(date, code)
);
CREATE INDEX IF NOT EXISTS idx_ths_date ON ths_hot_reason(date);
CREATE INDEX IF NOT EXISTS idx_ths_code ON ths_hot_reason(code);
"""


def _to_float(s):
    if not s or s == "nan":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def import_research_report(cur: sqlite3.Cursor) -> int:
    csv_path = DATA_DIR / "research_report.csv"
    if not csv_path.exists():
        return 0
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [
            (
                r.get("code", "").strip().zfill(6) if r.get("code", "").isdigit() else r.get("code", "").strip(),
                r.get("date", "")[:10] if r.get("date") else None,
                r.get("rating") or None,
                r.get("rating_change") or None,
                r.get("title") or None,
                r.get("author") or None,
                r.get("institution") or None,
                r.get("url") or None,
            )
            for r in reader
        ]
    cur.executemany(
        "INSERT OR IGNORE INTO research_report "
        "(code, date, rating, rating_change, title, author, institution, url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    cur.execute("SELECT COUNT(*) FROM research_report")
    logger.info("   ✅ INSERT research_report: %d 行 (CSV %d, 表内总 %d)",
                len(rows), len(rows), cur.fetchone()[0])
    return len(rows)


def import_em_global_news(cur: sqlite3.Cursor) -> int:
    csv_path = DATA_DIR / "em_global_news.csv"
    if not csv_path.exists():
        return 0
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [
            (
                r.get("date", "")[:10] if r.get("date") else None,
                r.get("title") or None,
                r.get("url") or None,
                r.get("summary") or None,
                r.get("source") or None,
            )
            for r in reader
        ]
    cur.executemany(
        "INSERT OR IGNORE INTO em_global_news "
        "(date, title, url, summary, source) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    cur.execute("SELECT COUNT(*) FROM em_global_news")
    logger.info("   ? INSERT em_global_news: %d �� (CSV %d, ������ %d)",
                len(rows), len(rows), cur.fetchone()[0])
    return len(rows)


def import_ths_hot_reason(cur: sqlite3.Cursor) -> int:
    csv_path = DATA_DIR / "ths_hot_reason.csv"
    if not csv_path.exists():
        return 0
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            code_raw = r.get("code", "").strip()
            code = code_raw.zfill(6) if code_raw.isdigit() else code_raw
            rows.append((
                r.get("date", "")[:10] if r.get("date") else None,
                int(r["rank"]) if r.get("rank", "").isdigit() else None,
                code,
                r.get("name") or None,
                _to_float(r.get("hot_value")),
                r.get("concept") or None,
                r.get("reason") or None,
                _to_float(r.get("change_pct")),
            ))
    cur.executemany(
        "INSERT OR IGNORE INTO ths_hot_reason "
        "(date, rank, code, name, hot_value, concept, reason, change_pct) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    cur.execute("SELECT COUNT(*) FROM ths_hot_reason")
    logger.info("   ✅ INSERT ths_hot_reason: %d 行 (CSV %d, 表内总 %d)",
                len(rows), len(rows), cur.fetchone()[0])
    return len(rows)


def main() -> int:
    if not DB_PATH.exists():
        logger.error("DB 不存在: %s", DB_PATH)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    logger.info("[1/2] CREATE TABLE (IF NOT EXISTS)")
    cur.executescript(SCHEMA_SQL)
    conn.commit()

    logger.info("[2/2] IMPORT 数据")
    import_research_report(cur)
    import_em_global_news(cur)
    import_ths_hot_reason(cur)
    conn.commit()

    cur.execute("ANALYZE")
    conn.commit()

    # 校验
    logger.info("[校验]")
    for t in ("research_report", "em_global_news", "ths_hot_reason"):
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        logger.info("  %s: %d 行", t, cur.fetchone()[0])

    conn.close()
    logger.info("✅ 完成 (耗时 %.1fs)", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())