#!/usr/bin/env python3
"""
在线拉取沪深300基准指数 → benchmark_data 表
===========================================
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import create_engine, text
from src.config import get_config, get_db_url


def main():
    engine = create_engine(get_db_url(get_config()), echo=False)

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS benchmark_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                index_code VARCHAR(10) NOT NULL,
                trade_date DATE NOT NULL,
                close DOUBLE NOT NULL,
                pct_change DOUBLE
            )
        """))
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_bench_code_date ON benchmark_data(index_code, trade_date)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bench_code_date ON benchmark_data(index_code, trade_date)",
        ]:
            try:
                conn.execute(text(idx_sql))
            except Exception:
                pass
        conn.commit()
    print("[OK] benchmark_data 表已创建")

    import akshare as ak

    end_d = date.today().strftime("%Y%m%d")
    start_d = (date.today() - timedelta(days=365 * 3)).strftime("%Y%m%d")

    print(f">>> 拉取沪深300: {start_d} ~ {end_d}")
    try:
        df = ak.stock_zh_index_daily(symbol="sh000300")
        print(f"  原始行数: {len(df)}")
        print(f"  列: {df.columns.tolist()}")
        df = df.reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        print(f"  归一列: {df.columns.tolist()}")

        # 兼容不同的列名
        col_map = {
            "date": "trade_date",
            "close": "close",
        }
        for k, v in col_map.items():
            if k in df.columns:
                df = df.rename(columns={k: v})

        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date

        # 计算 pct_change
        if "close" in df.columns:
            df["pct_change"] = df["close"].pct_change() * 100

        df["index_code"] = "sh000300"
        df = df[["index_code", "trade_date", "close", "pct_change"]].copy()
        df = df.where(pd.notna(df), None)

        # 清空旧数据
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM benchmark_data"))
            conn.commit()

        records = df.to_dict(orient="records")
        if records:
            cols = list(df.columns)
            placeholders = ", ".join([f":{c}" for c in cols])
            cols_str = ", ".join(cols)
            with engine.connect() as conn:
                conn.execute(
                    text(f"INSERT OR IGNORE INTO benchmark_data ({cols_str}) VALUES ({placeholders})"),
                    records
                )
                conn.commit()

        with engine.connect() as conn:
            cnt = conn.execute(text("SELECT COUNT(*) FROM benchmark_data")).scalar()
            dmin, dmax = conn.execute(text("SELECT MIN(trade_date), MAX(trade_date) FROM benchmark_data")).fetchone()

        print(f"\n[完成] benchmark_data:")
        print(f"  行数: {cnt:,}")
        print(f"  日期范围: {dmin} ~ {dmax}")
    except Exception as e:
        print(f"[ERROR] {e}")


if __name__ == "__main__":
    main()
