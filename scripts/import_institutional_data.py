#!/usr/bin/env python3
"""
机构相关数据导入脚本 — dragon_tiger/margin_trading/holder_num → quant.db
======================================================================
将龙虎榜、融资融券、股东户数 CSV 导入 quant.db，供 InstitutionalScorer 使用。

数据源:
  - dragon_tiger.csv    (2.9MB,  26,732行) → dragon_tiger_data    — 龙虎榜全量
  - margin_trading.csv  (5.6MB,  76,072行) → margin_trading       — 融资融券
  - holder_num.csv      (419KB,   5,332行) → shareholder_count    — 股东户数

用法:
  python scripts/import_institutional_data.py

影响:
  InstitutionalScorer 从这些表中读取数据，完成 6 维机构评分 (0-20分)。
"""
import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

from src.config import get_config, get_db_url

# ============================================================
# 配置
# ============================================================
DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "A股全市场数据"
)
BATCH_SIZE = 50000


# ============================================================
# 建表
# ============================================================

CREATE_TABLES_SQL = {
    "dragon_tiger_data": """
        CREATE TABLE IF NOT EXISTS dragon_tiger_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code VARCHAR(10) NOT NULL,
            market VARCHAR(10),
            name VARCHAR(50),
            trade_date DATE NOT NULL,
            reason TEXT,
            net_buy_wan DOUBLE,
            turnover_pct DOUBLE
        )
    """,
    "margin_trading": """
        CREATE TABLE IF NOT EXISTS margin_trading (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code VARCHAR(10) NOT NULL,
            market VARCHAR(10),
            name VARCHAR(50),
            trade_date DATE NOT NULL,
            rzye DOUBLE,
            rzmre DOUBLE,
            rzche DOUBLE,
            rqye DOUBLE,
            rqmcl DOUBLE,
            rqchl DOUBLE
        )
    """,
    "shareholder_count": """
        CREATE TABLE IF NOT EXISTS shareholder_count (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code VARCHAR(10) NOT NULL,
            market VARCHAR(10),
            name VARCHAR(50),
            end_date DATE,
            holder_num INTEGER,
            change_num INTEGER,
            change_ratio DOUBLE,
            avg_shares DOUBLE
        )
    """,
}

INDEXES_SQL = {
    "dragon_tiger_data": [
        "CREATE INDEX IF NOT EXISTS idx_dt_code ON dragon_tiger_data(code)",
        "CREATE INDEX IF NOT EXISTS idx_dt_date ON dragon_tiger_data(trade_date)",
        "CREATE INDEX IF NOT EXISTS idx_dt_code_date ON dragon_tiger_data(code, trade_date)",
    ],
    "margin_trading": [
        "CREATE INDEX IF NOT EXISTS idx_mt_code ON margin_trading(code)",
        "CREATE INDEX IF NOT EXISTS idx_mt_date ON margin_trading(trade_date)",
        "CREATE INDEX IF NOT EXISTS idx_mt_code_date ON margin_trading(code, trade_date)",
    ],
    "shareholder_count": [
        "CREATE INDEX IF NOT EXISTS idx_sc_code ON shareholder_count(code)",
        "CREATE INDEX IF NOT EXISTS idx_sc_date ON shareholder_count(end_date)",
    ],
}


def create_tables(engine):
    """创建所有表"""
    with engine.connect() as conn:
        for table_name, ddl in CREATE_TABLES_SQL.items():
            conn.execute(text(ddl))
            print(f"[OK] 表已创建: {table_name}")

        for table_name, idx_list in INDEXES_SQL.items():
            for idx_sql in idx_list:
                try:
                    conn.execute(text(idx_sql))
                except Exception as e:
                    print(f"  [WARN] 索引跳过 ({table_name}): {e}")
        conn.commit()
    print("[OK] 所有表及索引已创建")


# ============================================================
# 导入函数
# ============================================================

def import_csv(engine, csv_name: str, table_name: str, col_map: dict,
               date_col: str = None, code_prefix: bool = True) -> int:
    """
    导入 CSV 到指定表

    Args:
        csv_name: CSV 文件名
        table_name: 目标表名
        col_map: {csv_column: db_column} 映射
        date_col: 需要转换的日期列名
        code_prefix: 是否将 code 补零到6位

    Returns:
        导入行数
    """
    csv_path = os.path.join(DATA_DIR, csv_name)
    if not os.path.exists(csv_path):
        print(f"[ERROR] 找不到: {csv_path}")
        return 0

    file_size = os.path.getsize(csv_path) / 1024 / 1024
    print(f"\n[INFO] [{table_name}] 读取: {csv_name} ({file_size:.1f} MB)")

    total_rows = 0
    batch_num = 0
    start_time = time.time()

    # 先清空旧数据（断点续跑的幂等性）
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM {table_name}"))
        conn.commit()

    for chunk in pd.read_csv(csv_path, chunksize=BATCH_SIZE):
        batch_num += 1

        # 列重命名
        chunk.rename(columns=col_map, inplace=True)

        # 只保留需要的列
        db_cols = list(col_map.values())
        chunk = chunk[[c for c in db_cols if c in chunk.columns]]

        # 日期转换
        if date_col and date_col in chunk.columns:
            chunk[date_col] = pd.to_datetime(chunk[date_col], errors="coerce")
            chunk.dropna(subset=[date_col], inplace=True)
            chunk[date_col] = chunk[date_col].dt.date

        # code 补零到6位
        if code_prefix and "code" in chunk.columns:
            chunk["code"] = chunk["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)

        # NaN → None (SQLite 兼容)
        chunk = chunk.where(pd.notna(chunk), None)
        records = chunk.to_dict(orient="records")
        if not records:
            continue

        # 批量写入 (INSERT OR REPLACE)
        with engine.connect() as conn:
            placeholders = ", ".join([f":{c}" for c in db_cols])
            cols = ", ".join(db_cols)
            conn.execute(
                text(f"INSERT OR REPLACE INTO {table_name} ({cols}) VALUES ({placeholders})"),
                records
            )
            conn.commit()

        total_rows += len(records)
        elapsed = time.time() - start_time
        print(f"  [{batch_num}] {table_name}: {total_rows:>8,} 行 | {elapsed:.0f}s")

    elapsed = time.time() - start_time
    print(f"[OK] {table_name}: 共 {total_rows:,} 行, 耗时 {elapsed:.1f}s")
    return total_rows


# ============================================================
# 验证
# ============================================================

def verify_import(engine):
    """验证所有表"""
    tables_to_check = [
        ("dragon_tiger_data", "code", "trade_date"),
        ("margin_trading", "code", "trade_date"),
        ("shareholder_count", "code", "end_date"),
    ]

    print("\n=== 数据验证 ===")
    for table_name, code_col, date_col in tables_to_check:
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            total = result.scalar()
            result = conn.execute(text(f"SELECT COUNT(DISTINCT {code_col}) FROM {table_name}"))
            stocks = result.scalar()
            result = conn.execute(
                text(f"SELECT MIN({date_col}), MAX({date_col}) FROM {table_name}")
            )
            row = result.fetchone()
            dmin, dmax = row if row else ("N/A", "N/A")

        print(f"\n  {table_name}:")
        print(f"    行数: {total:,}")
        print(f"    股票数: {stocks}")
        print(f"    日期范围: {dmin} ~ {dmax}")

    # 抽样
    print(f"\n  抽样 dragon_tiger_data:")
    sample = pd.read_sql(
        "SELECT code, trade_date, net_buy_wan, turnover_pct, reason "
        "FROM dragon_tiger_data ORDER BY trade_date DESC LIMIT 5",
        engine
    )
    for _, row in sample.iterrows():
        print(f"    {row['code']} | {row['trade_date']} | 净买入={row['net_buy_wan']:>8.0f}万 | {str(row['reason'])[:30]}")

    print(f"\n  抽样 margin_trading:")
    sample = pd.read_sql(
        "SELECT code, trade_date, rzye, rzmre, rqye FROM margin_trading LIMIT 3",
        engine
    )
    for _, row in sample.iterrows():
        print(f"    {row['code']} | {row['trade_date']} | 融资余额={row['rzye']:>12.0f}")


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("  机构相关数据导入工具 v1.0")
    print("  数据: dragon_tiger + margin_trading + holder_num")
    print("=" * 60)

    config = get_config()
    db_url = get_db_url(config)
    engine = create_engine(db_url, echo=False)

    # 建表
    create_tables(engine)

    # 1) dragon_tiger.csv → dragon_tiger_data
    import_csv(
        engine,
        csv_name="dragon_tiger.csv",
        table_name="dragon_tiger_data",
        col_map={
            "code": "code",
            "market": "market",
            "name": "name",
            "date": "trade_date",
            "reason": "reason",
            "net_buy_wan": "net_buy_wan",
            "turnover_pct": "turnover_pct",
        },
        date_col="trade_date",
    )

    # 2) margin_trading.csv → margin_trading
    import_csv(
        engine,
        csv_name="margin_trading.csv",
        table_name="margin_trading",
        col_map={
            "code": "code",
            "market": "market",
            "name": "name",
            "date": "trade_date",
            "rzye": "rzye",
            "rzmre": "rzmre",
            "rzche": "rzche",
            "rqye": "rqye",
            "rqmcl": "rqmcl",
            "rqchl": "rqchl",
        },
        date_col="trade_date",
    )

    # 3) holder_num.csv → shareholder_count
    import_csv(
        engine,
        csv_name="holder_num.csv",
        table_name="shareholder_count",
        col_map={
            "code": "code",
            "market": "market",
            "name": "name",
            "end_date": "end_date",
            "holder_num": "holder_num",
            "change_num": "change_num",
            "change_ratio": "change_ratio",
            "avg_shares": "avg_shares",
        },
        date_col="end_date",
    )

    # 验证
    verify_import(engine)

    print(f"\n✅ 全部导入完成!")
    print(f"💡 InstitutionalScorer 现在可以使用 dragon_tiger_data / margin_trading / shareholder_count 表")


if __name__ == "__main__":
    main()
