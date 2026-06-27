"""
import_more_csv.py — 把 raw/reference/ 和 raw/technical_indicators/ 下的 CSV 全部 import 到 SQLite

源:
  - raw/technical_indicators/tech_indicators_*.csv  (343 MB, MACD/KDJ/BOLL)
  - raw/reference/block_trade.csv                   (9.2 MB, 大宗交易)
  - raw/reference/dividend.csv                      (1.6 MB, 分红送股)
  - raw/reference/announcements.csv                 (0.7 MB, 公告)

目标:
  - technical_indicators 表
  - block_trade 表 (新建)
  - dividend 表 (新建)
  - announcements 表 (新建)

vnpy 借鉴: 数据导入统一 build_db 风格, --incremental 支持断点续传
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import text

# 独立运行
PROJECT_ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.engine import get_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("import_more_csv")


# ── 表 schema ─────────────────────────────────────


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS technical_indicators (
    code         VARCHAR(10) NOT NULL,
    trade_date   DATE        NOT NULL,
    macd_dif     FLOAT,
    macd_dea     FLOAT,
    macd_hist    FLOAT,
    rsi14        FLOAT,
    kdj_k        FLOAT,
    kdj_d        FLOAT,
    kdj_j        FLOAT,
    boll_mid     FLOAT,
    boll_upper   FLOAT,
    boll_lower   FLOAT,
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_ti_code_date ON technical_indicators(code, trade_date);
CREATE INDEX IF NOT EXISTS idx_ti_date ON technical_indicators(trade_date);

CREATE TABLE IF NOT EXISTS block_trade (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          VARCHAR(10) NOT NULL,
    trade_date    DATE        NOT NULL,
    name          VARCHAR(50),
    deal_price    FLOAT,
    close_price   FLOAT,
    premium_pct   FLOAT,
    volume        BIGINT,
    amount        FLOAT,
    buyer         VARCHAR(100),
    seller        VARCHAR(100)
);
CREATE INDEX IF NOT EXISTS idx_bt_code ON block_trade(code);
CREATE INDEX IF NOT EXISTS idx_bt_date ON block_trade(trade_date);
CREATE INDEX IF NOT EXISTS idx_bt_code_date ON block_trade(code, trade_date);

CREATE TABLE IF NOT EXISTS dividend (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    code              VARCHAR(10) NOT NULL,
    ex_div_date       DATE,
    pre_tax_bonus     FLOAT,
    transfer_ratio    FLOAT,
    bonus_ratio       FLOAT,
    record_date       DATE
);
CREATE INDEX IF NOT EXISTS idx_div_code ON dividend(code);
CREATE INDEX IF NOT EXISTS idx_div_date ON dividend(ex_div_date);

CREATE TABLE IF NOT EXISTS announcements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        VARCHAR(10) NOT NULL,
    trade_date  DATE,
    type        VARCHAR(100),
    title       VARCHAR(500),
    url         VARCHAR(500)
);
CREATE INDEX IF NOT EXISTS idx_ann_code ON announcements(code);
CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(trade_date);
"""


# ── Importers ──────────────────────────────────────


def import_technical_indicators(
    engine, csv_dir: Path, incremental: bool = False
) -> int:
    """Import technical_indicators"""
    csv_files = sorted(csv_dir.glob("tech_indicators_*.csv"))
    if not csv_files:
        log.warning(f"未找到 tech_indicators_*.csv in {csv_dir}")
        return 0

    # 跳过冗余: 2025.csv 已包含 part1/part2 全部数据
    csv_files = [f for f in csv_files if "_part" not in f.name]

    log.info(f"导入 technical_indicators: {len(csv_files)} 个 CSV (跳过 part1/part2 冗余)")

    # 增量模式: 已存在的 (code, trade_date) 不再插
    existing = set()
    if incremental:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT code, trade_date FROM technical_indicators"
            )).fetchall()
            existing = {(r[0], r[1]) for r in rows}
        log.info(f"  增量模式: 已存在 {len(existing)} 条")

    total_inserted = 0
    with engine.begin() as conn:
        for csv in csv_files:
            t0 = time.time()
            df = pd.read_csv(csv, dtype={"code": str})
            log.info(f"  读取 {csv.name}: {len(df):,} 行")

            # code 转 6 位字符串
            df["code"] = df["code"].str.zfill(6)
            df["trade_date"] = pd.to_datetime(df["date"]).dt.date

            # 增量去重
            if existing:
                mask = ~df.apply(lambda r: (r["code"], r["trade_date"]) in existing, axis=1)
                df = df[mask]
                log.info(f"  增量去重后: {len(df):,} 行")

            # 写库
            df[["code", "trade_date", "macd_dif", "macd_dea", "macd_hist",
                "rsi14", "kdj_k", "kdj_d", "kdj_j",
                "boll_mid", "boll_upper", "boll_lower"]].to_sql(
                "technical_indicators", conn, if_exists="append", index=False
            )
            total_inserted += len(df)
            log.info(f"  耗时 {time.time()-t0:.1f}s")

    log.info(f"✅ technical_indicators: 新增 {total_inserted:,} 行")
    return total_inserted


def import_block_trade(engine, csv: Path) -> int:
    """Import block_trade (大宗交易)"""
    log.info(f"导入 block_trade: {csv.name}")
    t0 = time.time()
    df = pd.read_csv(csv, dtype={"code": str})
    log.info(f"  读取: {len(df):,} 行")

    df["code"] = df["code"].str.zfill(6)
    df["trade_date"] = pd.to_datetime(df["date"]).dt.date

    with engine.begin() as conn:
        # 全删全插 (block_trade 量小 ~几千行)
        conn.execute(text("DELETE FROM block_trade"))
        df[["code", "trade_date", "name", "deal_price", "close_price",
            "premium_pct", "vol", "amount", "buyer", "seller"]].rename(
            columns={"vol": "volume"}
        ).to_sql("block_trade", conn, if_exists="append", index=False)

    log.info(f"✅ block_trade: {len(df):,} 行, 耗时 {time.time()-t0:.1f}s")
    return len(df)


def import_dividend(engine, csv: Path) -> int:
    """Import dividend (分红送股)"""
    log.info(f"导入 dividend: {csv.name}")
    t0 = time.time()
    df = pd.read_csv(csv, dtype={"code": str})
    log.info(f"  读取: {len(df):,} 行")

    df["code"] = df["code"].str.zfill(6)
    df["ex_div_date"] = pd.to_datetime(df["ex_div_date"]).dt.date
    df["record_date"] = pd.to_datetime(df["record_date"]).dt.date

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM dividend"))
        df[["code", "ex_div_date", "pre_tax_bonus", "transfer_ratio",
            "bonus_ratio", "record_date"]].to_sql(
            "dividend", conn, if_exists="append", index=False
        )

    log.info(f"✅ dividend: {len(df):,} 行, 耗时 {time.time()-t0:.1f}s")
    return len(df)


def import_announcements(engine, csv: Path) -> int:
    """Import announcements (公告)"""
    log.info(f"导入 announcements: {csv.name}")
    t0 = time.time()
    df = pd.read_csv(csv, dtype={"code": str})
    log.info(f"  读取: {len(df):,} 行")

    df["code"] = df["code"].str.zfill(6)
    df["trade_date"] = pd.to_datetime(df["date"]).dt.date

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM announcements"))
        df[["code", "trade_date", "type", "title", "url"]].to_sql(
            "announcements", conn, if_exists="append", index=False
        )

    log.info(f"✅ announcements: {len(df):,} 行, 耗时 {time.time()-t0:.1f}s")
    return len(df)


# ── 主流程 ────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Import 额外 CSV (technical_indicators / block_trade / dividend / announcements)"
    )
    parser.add_argument("--incremental", action="store_true",
                        help="增量模式 (只补缺失)")
    parser.add_argument("--only", default="all",
                        help="只 import 哪个 (technical/block_trade/dividend/announcements)")
    args = parser.parse_args()

    engine = get_engine()

    # 建表
    log.info("=" * 70)
    log.info("建表...")
    with engine.begin() as conn:
        for stmt in SCHEMA_SQL.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    log.info("✅ 表结构创建完成")

    only = args.only
    total_t0 = time.time()

    if only in ("all", "technical"):
        tech_dir = PROJECT_ROOT / "market_data" / "raw" / "technical_indicators"
        import_technical_indicators(engine, tech_dir, incremental=args.incremental)

    if only in ("all", "block_trade"):
        block_csv = PROJECT_ROOT / "market_data" / "raw" / "reference" / "block_trade.csv"
        if block_csv.exists():
            import_block_trade(engine, block_csv)

    if only in ("all", "dividend"):
        div_csv = PROJECT_ROOT / "market_data" / "raw" / "reference" / "dividend.csv"
        if div_csv.exists():
            import_dividend(engine, div_csv)

    if only in ("all", "announcements"):
        ann_csv = PROJECT_ROOT / "market_data" / "raw" / "reference" / "announcements.csv"
        if ann_csv.exists():
            import_announcements(engine, ann_csv)

    log.info(f"\n总耗时: {time.time()-total_t0:.1f}s")

    # 报告
    log.info("\n" + "=" * 70)
    log.info("Import 后数据库状态:")
    with engine.connect() as conn:
        for table in ["technical_indicators", "block_trade", "dividend", "announcements"]:
            try:
                cnt = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()[0]
                log.info(f"  {table:30s} {cnt:>12,} 行")
            except Exception as e:
                log.info(f"  {table:30s} ERR: {e}")


if __name__ == "__main__":
    main()