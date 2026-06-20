#!/usr/bin/env python3
"""
导入 technical_indicators CSV（已下载的部分）→ quant.db
=========================================================
CSV 文件:
  - tech_indicators_2025_part1.csv (~50MB)
  - tech_indicators_2025_part2.csv (~51MB)
  - tech_indicators_2026.csv        (~46MB)
(2024 文件未下载(LFS指针)，跳过)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import create_engine, text
from src.config import get_config, get_db_url
from src.models.database import Base, TechnicalIndicator


def main():
    data_dir = Path(__file__).parent.parent / "A股全市场数据" / "raw" / "technical_indicators"
    csv_files = [
        data_dir / "tech_indicators_2025_part1.csv",
        data_dir / "tech_indicators_2025_part2.csv",
        data_dir / "tech_indicators_2026.csv",
    ]

    engine = create_engine(get_db_url(get_config()), echo=False)

    # 建表
    Base.metadata.create_all(engine, tables=[TechnicalIndicator.__table__])
    print("[OK] technical_indicators 表已创建")

    with engine.connect() as conn:
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_ti_code ON technical_indicators(code)",
            "CREATE INDEX IF NOT EXISTS idx_ti_date ON technical_indicators(trade_date)",
            "CREATE INDEX IF NOT EXISTS idx_ti_code_date ON technical_indicators(code, trade_date)",
        ]:
            try:
                conn.execute(text(idx_sql))
            except Exception as e:
                print(f"  [WARN] 索引跳过: {e}")
        conn.commit()

    # 清空
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM technical_indicators"))
        conn.commit()
    print("[OK] 旧数据已清空")

    # 逐个 CSV 导入
    total = 0
    start_time = time.time()

    for csv_path in csv_files:
        if not csv_path.exists():
            print(f"  [SKIP] 不存在: {csv_path.name}")
            continue

        size_mb = csv_path.stat().st_size / 1024 / 1024
        print(f"\n>>> 处理: {csv_path.name} ({size_mb:.1f} MB)")

        file_total = 0
        for chunk_idx, chunk in enumerate(pd.read_csv(csv_path, chunksize=50000)):
            # 重命名 date → trade_date
            chunk = chunk.rename(columns={"date": "trade_date"})
            # code 补零
            chunk["code"] = chunk["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
            chunk["trade_date"] = pd.to_datetime(chunk["trade_date"], errors="coerce").dt.date
            chunk = chunk.where(pd.notna(chunk), None)

            records = chunk.to_dict(orient="records")
            if not records:
                continue

            cols = list(chunk.columns)
            placeholders = ", ".join([f":{c}" for c in cols])
            cols_str = ", ".join(cols)
            with engine.connect() as conn:
                conn.execute(
                    text(f"INSERT OR REPLACE INTO technical_indicators ({cols_str}) VALUES ({placeholders})"),
                    records
                )
                conn.commit()

            file_total += len(records)
            total += len(records)

            if (chunk_idx + 1) % 5 == 0:
                elapsed = time.time() - start_time
                print(f"    批次 {chunk_idx+1}: {total:,} 行累计 | {elapsed:.0f}s")

        print(f"  [OK] {csv_path.name}: {file_total:,} 行")

    # 验证
    with engine.connect() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM technical_indicators")).scalar()
        codes = conn.execute(text("SELECT COUNT(DISTINCT code) FROM technical_indicators")).scalar()
        dmin, dmax = conn.execute(text("SELECT MIN(trade_date), MAX(trade_date) FROM technical_indicators")).fetchone()

    print(f"\n[OK] technical_indicators 导入完成:")
    print(f"    总行数: {cnt:,}")
    print(f"    股票数: {codes}")
    print(f"    日期范围: {dmin} ~ {dmax}")
    print(f"    总耗时: {time.time() - start_time:.0f}s")


if __name__ == "__main__":
    main()
