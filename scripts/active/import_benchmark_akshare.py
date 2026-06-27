#!/usr/bin/env python3
"""
在线拉取沪深300基准指数 → benchmark_data 表
===========================================

数据源: AKShare stock_zh_index_daily(symbol="sh000300")
支持重试（AKShare 上游偶尔不稳定）。
"""
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import text
from src.db.engine import get_engine
from src.config import get_config


def _fetch_with_retry(max_retries=3, delay=5):
    """带重试的 AKShare 基准数据拉取"""
    import akshare as ak

    for attempt in range(max_retries):
        try:
            df = ak.stock_zh_index_daily(symbol="sh000300")
            if df is not None and not df.empty:
                return df
            print(f"  尝试 {attempt + 1}/{max_retries}: 返回空数据，{delay}s 后重试...")
        except Exception as e:
            print(f"  尝试 {attempt + 1}/{max_retries} 失败: {e}")
        if attempt < max_retries - 1:
            time.sleep(delay * (attempt + 1))  # 线性退避

    raise RuntimeError(f"AKShare 拉取沪深300失败，已重试 {max_retries} 次")


def main():
    engine = get_engine()

    # Step 1: 确保表存在
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
    print("[OK] benchmark_data 表已就绪")

    # Step 2: 拉取数据（带重试）
    end_d = date.today().strftime("%Y%m%d")
    start_d = (date.today() - timedelta(days=365 * 3)).strftime("%Y%m%d")
    start_date = date.today() - timedelta(days=365 * 3)

    print(f">>> 拉取沪深300: {start_d} ~ {end_d}")
    try:
        df = _fetch_with_retry()
        print(f"  原始行数: {len(df)}")

        df = df.reset_index()
        df.columns = [str(c).lower() for c in df.columns]

        # 兼容不同的列名
        col_map = {"date": "trade_date", "close": "close"}
        for k, v in col_map.items():
            if k in df.columns:
                df = df.rename(columns={k: v})

        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date

        # 按日期范围过滤（只保留近 3 年）
        if "trade_date" in df.columns:
            df = df[df["trade_date"] >= start_date]
            print(f"  过滤后行数: {len(df)}")

        # 计算 pct_change
        if "close" in df.columns:
            df["pct_change"] = df["close"].pct_change() * 100

        df["index_code"] = "sh000300"
        df = df[["index_code", "trade_date", "close", "pct_change"]].copy()
        df = df.where(pd.notna(df), None)

        # Step 3: 清空旧数据并写入
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

        # Step 4: 验证
        with engine.connect() as conn:
            cnt = conn.execute(text("SELECT COUNT(*) FROM benchmark_data")).scalar()
            dmin, dmax = conn.execute(
                text("SELECT MIN(trade_date), MAX(trade_date) FROM benchmark_data")
            ).fetchone()

        print(f"\n[完成] benchmark_data:")
        print(f"  行数: {cnt:,}")
        print(f"  日期范围: {dmin} ~ {dmax}")

        if cnt == 0:
            print("\n[WARNING] 写入 0 行！请检查 AKShare 是否可用。")
            print("  备选方案: python scripts/import_benchmark_baostock.py")
            sys.exit(1)

    except RuntimeError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] 导入失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
