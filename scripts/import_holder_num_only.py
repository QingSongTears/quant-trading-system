#!/usr/bin/env python3
"""
单独导入 holder_num.csv → shareholder_count 表
(避开 dragon_tiger/margin_trading 文件缺失的问题)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import create_engine, text
from src.config import get_config, get_db_url


def main():
    csv_path = Path(__file__).parent.parent / "market_data" / "holder_num.csv"
    print(f"加载: {csv_path}")

    engine = create_engine(get_db_url(get_config()), echo=False)

    # 建表
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shareholder_count (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code VARCHAR(10) NOT NULL,
                end_date DATE,
                holder_num INTEGER,
                change_num INTEGER,
                change_ratio DOUBLE,
                avg_shares DOUBLE
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sc_code ON shareholder_count(code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sc_date ON shareholder_count(end_date)"))
        conn.commit()
    print("[OK] shareholder_count 表已创建")

    # 清空旧数据
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM shareholder_count"))
        conn.commit()

    # 读 CSV
    df = pd.read_csv(csv_path)
    print(f"读取: {len(df)} 行")

    # 列重命名
    rename_map = {
        "code": "code",
        "end_date": "end_date",
        "holder_num": "holder_num",
        "pre_holder_num": "change_num",
        "holder_change_pct": "change_ratio",
        "avg_holding": "avg_shares",
    }
    df = df.rename(columns=rename_map)
    df = df[list(rename_map.values())].copy()
    df["code"] = df["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce").dt.date
    df = df.where(pd.notna(df), None)

    # 批量写入
    records = df.to_dict(orient="records")
    if records:
        placeholders = ", ".join([f":{c}" for c in df.columns])
        cols = ", ".join(df.columns)
        with engine.connect() as conn:
            conn.execute(
                text(f"INSERT INTO shareholder_count ({cols}) VALUES ({placeholders})"),
                records
            )
            conn.commit()

    # 验证
    with engine.connect() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM shareholder_count")).scalar()
        codes = conn.execute(text("SELECT COUNT(DISTINCT code) FROM shareholder_count")).scalar()
        dmin, dmax = conn.execute(text("SELECT MIN(end_date), MAX(end_date) FROM shareholder_count")).fetchone()

    print(f"\n[OK] shareholder_count 导入完成:")
    print(f"    行数: {cnt:,}")
    print(f"    股票数: {codes}")
    print(f"    日期范围: {dmin} ~ {dmax}")


if __name__ == "__main__":
    main()
