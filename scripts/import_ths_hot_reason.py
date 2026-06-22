#!/usr/bin/env python3
"""
导入 ths_hot_reason.csv（同花顺题材热点）→ quant.db
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import create_engine, text
from src.config import get_config, get_db_url


def main():
    csv_path = Path(__file__).parent.parent / "market_data" / "reference" / "ths_hot_reason.csv"
    print(f"加载: {csv_path}")

    engine = create_engine(get_db_url(get_config()), echo=False)

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ths_hot_reason (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code VARCHAR(10) NOT NULL,
                market VARCHAR(10),
                name VARCHAR(50),
                close DOUBLE,
                change_pct DOUBLE,
                turnover_pct DOUBLE,
                volume DOUBLE,
                amount DOUBLE,
                dde_net DOUBLE,
                reason TEXT,
                trade_date DATE
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ths_code ON ths_hot_reason(code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ths_date ON ths_hot_reason(trade_date)"))
        conn.commit()

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM ths_hot_reason"))
        conn.commit()
    print("[OK] 表已创建，旧数据已清空")

    df = pd.read_csv(csv_path)
    print(f"读取: {len(df)} 行")

    df["code"] = df["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    if "date" in df.columns and "trade_date" not in df.columns:
        df = df.rename(columns={"date": "trade_date"})
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
    df = df.where(pd.notna(df), None)

    records = df.to_dict(orient="records")
    cols = list(df.columns)
    placeholders = ", ".join([f":{c}" for c in cols])
    cols_str = ", ".join(cols)
    with engine.connect() as conn:
        conn.execute(
            text(f"INSERT INTO ths_hot_reason ({cols_str}) VALUES ({placeholders})"),
            records
        )
        conn.commit()

    with engine.connect() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM ths_hot_reason")).scalar()
        codes = conn.execute(text("SELECT COUNT(DISTINCT code) FROM ths_hot_reason")).scalar()

    print(f"\n[OK] ths_hot_reason 导入完成:")
    print(f"    行数: {cnt}")
    print(f"    股票数: {codes}")


if __name__ == "__main__":
    main()
