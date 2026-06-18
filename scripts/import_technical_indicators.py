#!/usr/bin/env python3
"""
技术指标预计算表导入脚本 — technical_indicators.csv → quant.db
================================================================
将 263MB 的技术指标 CSV 导入 technical_indicators 表，包含：
  - MACD (DIF/DEA/HIST)
  - RSI(14)
  - KDJ (K/D/J)
  - Bollinger Bands (MID/UPPER/LOWER)

数据源: A股全市场数据/technical_indicators.csv (301 万行, 5206 只股票)
数据范围: 2024-01-02 ~ 2026-06-16

用法:
  python scripts/import_technical_indicators.py

加速效果:
  v6 超卖反转策略每次回测约需 7 分钟计算 RSI/BB，
  预计算后直接从 DB 读取，节省 90%+ 计算时间。
"""
import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

from src.models.database import Base, TechnicalIndicator
from src.config import get_config, get_db_url

# ============================================================
# 配置
# ============================================================
CSV_PATH = os.environ.get(
    "CSV_PATH",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "A股全市场数据",
        "technical_indicators.csv"
    )
)
BATCH_SIZE = 50000  # 每批插入行数


# ============================================================
# 建表
# ============================================================

def create_table(engine):
    """创建 technical_indicators 表"""
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
                print(f"  [WARN] 索引创建跳过: {e}")
        conn.commit()
    print("[OK] 索引已创建")


# ============================================================
# 导入函数
# ============================================================

def import_technical_indicators(engine, csv_path: str) -> int:
    """
    导入技术指标 CSV

    Returns:
        导入行数
    """
    if not os.path.exists(csv_path):
        print(f"[ERROR] 找不到 CSV 文件: {csv_path}")
        return 0

    print(f"[INFO] 正在读取: {csv_path}")
    print(f"[INFO] 文件大小: {os.path.getsize(csv_path) / 1024 / 1024:.1f} MB")

    total_rows = 0
    batch_num = 0
    start_time = time.time()

    # 分块读取大文件
    for chunk_idx, chunk in enumerate(pd.read_csv(
        csv_path,
        chunksize=BATCH_SIZE,
        dtype={
            "code": str,
            "date": str,
            "macd_dif": float,
            "macd_dea": float,
            "macd_hist": float,
            "rsi14": float,
            "kdj_k": float,
            "kdj_d": float,
            "kdj_j": float,
            "boll_mid": float,
            "boll_upper": float,
            "boll_lower": float,
        },
        na_values=["", " ", "nan", "NaN", "None", "null"],
        keep_default_na=True,
    )):
        batch_num += 1

        # 标准化列名
        chunk.rename(columns={"date": "trade_date"}, inplace=True)

        # 只保留非空code的行
        chunk.dropna(subset=["code"], inplace=True)
        if chunk.empty:
            continue

        # 确保 code 为 6 位字符串（去掉 .0 后缀）
        chunk["code"] = chunk["code"].astype(str).str.replace(r"\.0$", "", regex=True)

        # 转换 trade_date 为日期对象
        chunk["trade_date"] = pd.to_datetime(chunk["trade_date"], errors="coerce")
        chunk.dropna(subset=["trade_date"], inplace=True)
        chunk["trade_date"] = chunk["trade_date"].dt.date

        # 将 NaN 替换为 None（SQLite 兼容）
        chunk = chunk.where(pd.notna(chunk), None)

        # 删除可能存在的旧数据（防止重复导入导致 UNIQUE 冲突）
        # 使用逐行 UPSERT 方式
        records = chunk.to_dict(orient="records")

        # 批量写入
        with engine.connect() as conn:
            # 使用 INSERT OR REPLACE 处理重复 (code, trade_date)
            table = TechnicalIndicator.__table__
            stmt = table.insert().prefix_with("OR REPLACE")
            conn.execute(stmt, records)
            conn.commit()

        total_rows += len(records)

        elapsed = time.time() - start_time
        rate = total_rows / elapsed if elapsed > 0 else 0
        print(
            f"  [{batch_num}] 已导入 {total_rows:>8,} 行 "
            f"| 本批 {len(records):>6,} 行 "
            f"| 耗时 {elapsed:.0f}s "
            f"| {rate:.0f} 行/秒"
        )

    elapsed = time.time() - start_time
    print(f"\n[OK] 导入完成! 共 {total_rows:,} 行, 耗时 {elapsed:.1f}s")
    return total_rows


# ============================================================
# 验证
# ============================================================

def verify_import(engine):
    """验证导入的数据"""
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT COUNT(*) FROM technical_indicators")
        )
        total = result.scalar()

        result = conn.execute(
            text("SELECT COUNT(DISTINCT code) FROM technical_indicators")
        )
        stocks = result.scalar()

        result = conn.execute(
            text("SELECT MIN(trade_date), MAX(trade_date) FROM technical_indicators")
        )
        row = result.fetchone()
        date_min, date_max = row

        # 抽样检查 RSI14 非空比例
        result = conn.execute(
            text("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN rsi14 IS NOT NULL THEN 1 ELSE 0 END) as rsi_not_null
                FROM technical_indicators
            """)
        )
        r = result.fetchone()

    print(f"\n=== 数据验证 ===")
    print(f"  总行数:        {total:,}")
    print(f"  唯一股票数:    {stocks}")
    print(f"  日期范围:      {date_min} ~ {date_max}")
    print(f"  RSI14 非空率:  {r.rsi_not_null / r.total * 100:.1f}% ({r.rsi_not_null:,}/{r.total:,})")

    # 抽样展示
    print(f"\n  抽样数据:")
    sample = pd.read_sql(
        "SELECT * FROM technical_indicators WHERE code = '000001' ORDER BY trade_date LIMIT 5",
        engine
    )
    for _, row in sample.iterrows():
        print(f"    {row['code']} | {row['trade_date']} | "
              f"RSI14={row['rsi14'] or 'N/A':>8} | "
              f"MACD={row['macd_dif'] or 'N/A':>8} | "
              f"KDJ_K={row['kdj_k'] or 'N/A':>8}")

    return total


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("  技术指标预计算表导入工具 v1.0")
    print("=" * 60)

    # 数据库引擎
    config = get_config()
    db_url = get_db_url(config)
    engine = create_engine(db_url, echo=False)

    # 建表
    create_table(engine)

    # 导入
    total = import_technical_indicators(engine, CSV_PATH)

    # 验证
    if total > 0:
        verify_import(engine)
        print(f"\n✅ 导入完成! 共 {total:,} 条技术指标记录")
    else:
        print("\n❌ 导入失败，请检查 CSV 路径")

    print(f"\n💡 提示: 现在 v6 策略可从 technical_indicators 表直接读取 RSI14/Bollinger")
    print(f"   节省 ~7 分钟/次的运行时指标计算时间")


if __name__ == "__main__":
    main()
