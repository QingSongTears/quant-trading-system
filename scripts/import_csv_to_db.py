#!/usr/bin/env python3
"""
⚠️ DEPRECATED — 请使用 import_csv_to_db_v2.py 替代

CSV → SQLite (quant.db) 数据导入脚本
=====================================
将 tdrive 预置CSV文件导入新架构的 quant.db

数据源:
  - kline_daily.csv    → daily_price (300万行, 分批处理)
  - tencent_quotes.csv → stock_basic (5200+ 股票)
  - finance_snapshot.csv → finance_snapshot_v2 (5200+ 财报快照)

用法:
  python scripts/import_csv_to_db.py
"""
import sys
import os
import time
from datetime import datetime

# 确保项目根在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text, Column, String, Date, Float, BigInteger, Integer, Text

from src.models.database import Base, StockBasic, DailyPrice, BenchmarkData
from src.config import get_config, get_db_url

# ============================================================
# 配置
# ============================================================
CSV_DIR = os.environ.get(
    "CSV_DIR",
    "/workspace/quant-trading-system/src/strategies/stock_screener/data/raw"
)
BATCH_SIZE = 50000  # daily_price 每批插入行数

# ============================================================
# 辅助函数
# ============================================================

def safe_float(val):
    """安全转换为 float, NaN/Inf → None"""
    try:
        f = float(val)
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def safe_int(val):
    """安全转换为 int"""
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def safe_date(val):
    """安全转换日期字符串 → date 对象"""
    if val is None or val == "" or pd.isna(val):
        return None
    try:
        s = str(val).strip()[:10]  # 取 YYYY-MM-DD 部分
        return datetime.strptime(s, "%Y-%m-%d").date()
    except:
        try:
            # 尝试 YYYYMMDD 格式
            s = str(int(float(val))).strip()
            return datetime.strptime(s, "%Y%m%d").date()
        except:
            return None


def parse_market(code: str, market_hint: str = None) -> str:
    """从代码或市场提示解析交易市场"""
    if market_hint and market_hint.lower() in ("sh", "sz", "bj"):
        return market_hint.upper()
    code = str(code).strip()
    if code.startswith("6"):
        return "SH"
    elif code.startswith(("0", "3")):
        return "SZ"
    elif code.startswith(("4", "8")):
        return "BJ"
    return "UN"


# ============================================================
# 表结构扩展: finance_snapshot_v2
# ============================================================
# pipeline.py 引用了此表做 PE 过滤 (net_profit > 0)
# database.py 中未定义，这里手动创建

def create_finance_snapshot_table(engine):
    """创建 finance_snapshot_v2 表（不在 ORM 模型中）
    
    CSV 18列映射（东方财富API格式推断）:
      col_5  = total_revenue     营业总收入
      col_6  = total_cost         营业总成本 (部分为负)
      col_7  = total_assets       总资产
      col_8  = total_liabilities  总负债
      col_9  = net_profit         净利润(归母)
      col_10 = net_profit_total   净利润(含少数)
      col_11 = pe_ttm             市盈率TTM
      col_12 = market_cap         总市值
      col_13 = oper_cashflow      经营活动现金流
      col_14 = invest_cashflow    投资活动现金流
      col_15 = finance_cashflow   筹资活动现金流
      col_16 = employee_count     员工人数
      col_17 = flag               标志位(1/11/37/43)
      col_18(list_date_raw)       上市日期 YYYYMMDD
    """
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS finance_snapshot_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code VARCHAR(10) NOT NULL,
                name VARCHAR(50),
                report_date DATE,
                total_revenue DOUBLE,
                total_cost DOUBLE,
                total_assets DOUBLE,
                total_liabilities DOUBLE,
                net_profit DOUBLE,
                net_profit_total DOUBLE,
                pe_ttm DOUBLE,
                market_cap DOUBLE,
                oper_cashflow DOUBLE,
                invest_cashflow DOUBLE,
                finance_cashflow DOUBLE,
                employee_count DOUBLE,
                flag INTEGER,
                list_date_raw VARCHAR(20),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        # 创建 finance_snapshot_v2 索引
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_fs2_code ON finance_snapshot_v2(code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_fs2_profit ON finance_snapshot_v2(net_profit)"))
        conn.commit()
    print("[OK] finance_snapshot_v2 表已创建")


def create_indexes(engine):
    """创建性能索引"""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_dp_code ON daily_price(code)",
        "CREATE INDEX IF NOT EXISTS idx_dp_date ON daily_price(trade_date)",
        "CREATE INDEX IF NOT EXISTS idx_dp_code_date ON daily_price(code, trade_date)",
        "CREATE INDEX IF NOT EXISTS idx_fs_code ON finance_snapshot_v2(code)",
        "CREATE INDEX IF NOT EXISTS idx_sb_market ON stock_basic(market)",
    ]
    with engine.connect() as conn:
        for idx_sql in indexes:
            try:
                conn.execute(text(idx_sql))
            except Exception as e:
                print(f"  [WARN] 索引创建跳过: {e}")
        conn.commit()
    print("[OK] 性能索引已创建")


# ============================================================
# 导入函数
# ============================================================

def import_stock_basic(engine, csv_dir: str):
    """
    从 tencent_quotes.csv 导入 stock_basic
    补充 list_date 从 finance_snapshot.csv
    """
    quotes_path = os.path.join(csv_dir, "tencent_quotes.csv")
    finance_path = os.path.join(csv_dir, "finance_snapshot.csv")

    if not os.path.exists(quotes_path):
        print(f"[ERROR] 找不到: {quotes_path}")
        return

    print(f"\n{'='*60}")
    print(f"[1/3] 导入 stock_basic ← tencent_quotes.csv")
    print(f"{'='*60}")

    # 读取行情数据
    df = pd.read_csv(quotes_path, dtype={"code": str})
    print(f"  读取: {len(df)} 行")

    # 提取 list_date 从 finance_snapshot (col_18 = 上市日期 YYYYMMDD)
    list_date_map = {}
    if os.path.exists(finance_path):
        # finance_snapshot 无表头，第18列为上市日期
        try:
            fs = pd.read_csv(finance_path, header=None, dtype={0: str, 17: str})
            for _, row in fs.iterrows():
                code = str(row[0]).strip().zfill(6)
                raw_date = str(row[17]).strip()
                if raw_date and raw_date != "nan":
                    # YYYYMMDD → date
                    try:
                        d = datetime.strptime(raw_date[:8], "%Y%m%d").date()
                        list_date_map[code] = d
                    except:
                        pass
            print(f"  从 finance_snapshot 提取 list_date: {len(list_date_map)} 条")
        except Exception as e:
            print(f"  [WARN] 提取 list_date 失败: {e}")

    # 构建 StockBasic 记录
    records = []
    seen = set()
    for _, row in df.iterrows():
        code = str(row["code"]).strip().zfill(6)
        if code in seen:
            continue
        seen.add(code)

        name = str(row.get("name", "")).strip()
        market = parse_market(code, str(row.get("market", "")))
        list_date = list_date_map.get(code)

        records.append({
            "code": code,
            "name": name,
            "market": market,
            "list_date": list_date,
        })

    # 批量插入
    if records:
        # 先清空
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM stock_basic"))
            conn.commit()

        # 分批插入
        chunk_size = 1000
        for i in range(0, len(records), chunk_size):
            chunk = records[i:i+chunk_size]
            df_chunk = pd.DataFrame(chunk)
            df_chunk.to_sql("stock_basic", engine, if_exists="append", index=False)
        print(f"  导入完成: {len(records)} 条")

    return len(records)


def import_daily_price(engine, csv_dir: str):
    """
    从 kline_daily.csv 导入 daily_price
    无表头CSV，字段: code, market, name, trade_date, open, high, low, close, volume, amount
    300万行 → 分批处理
    """
    kline_path = os.path.join(csv_dir, "kline_daily.csv")

    if not os.path.exists(kline_path):
        print(f"[ERROR] 找不到: {kline_path}")
        return

    print(f"\n{'='*60}")
    print(f"[2/3] 导入 daily_price ← kline_daily.csv (300万行, 大任务)")
    print(f"{'='*60}")

    # 先清空
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM daily_price"))
        conn.commit()

    total_rows = 0
    start_time = time.time()

    # 无表头CSV, 指定列名
    col_names = ["code", "market", "name", "trade_date", "open", "high", "low",
                 "close", "volume", "amount"]

    for chunk_idx, chunk in enumerate(pd.read_csv(
        kline_path,
        header=None,
        names=col_names,
        dtype={
            "code": str, "market": str, "name": str,
            "open": float, "high": float, "low": float,
            "close": float, "volume": float, "amount": float,
        },
        chunksize=BATCH_SIZE,
    )):
        # 数据处理
        chunk["code"] = chunk["code"].str.strip().str.zfill(6)
        chunk["trade_date"] = pd.to_datetime(chunk["trade_date"], errors="coerce")
        chunk = chunk.dropna(subset=["trade_date"])

        # 只保留DB需要的列
        db_chunk = pd.DataFrame({
            "code": chunk["code"],
            "trade_date": chunk["trade_date"].dt.date,
            "open": chunk["open"].astype(float),
            "high": chunk["high"].astype(float),
            "low": chunk["low"].astype(float),
            "close": chunk["close"].astype(float),
            "volume": chunk["volume"].fillna(0).astype(int),
            "amount": chunk["amount"].apply(safe_float),
            "pct_change": None,   # 后续可计算
            "turnover": None,     # kline不含换手率
        })

        # 写入DB
        db_chunk.to_sql("daily_price", engine, if_exists="append", index=False)
        total_rows += len(db_chunk)

        elapsed = time.time() - start_time
        rate = total_rows / elapsed if elapsed > 0 else 0
        if (chunk_idx + 1) % 10 == 0 or chunk_idx == 0:
            print(f"  批次 {chunk_idx+1}: 已导入 {total_rows:,} 行 "
                  f"({rate:,.0f} 行/秒, 耗时 {elapsed:.1f}s)")

    elapsed = time.time() - start_time
    print(f"  导入完成: {total_rows:,} 行, 总耗时 {elapsed:.1f}s "
          f"({total_rows/elapsed:,.0f} 行/秒)")

    return total_rows


def import_finance_snapshot(engine, csv_dir: str):
    """
    从 finance_snapshot.csv 导入 finance_snapshot_v2
    无表头CSV，18列: code, market, name, report_date, c5..c17
    """
    finance_path = os.path.join(csv_dir, "finance_snapshot.csv")

    if not os.path.exists(finance_path):
        print(f"[ERROR] 找不到: {finance_path}")
        return

    print(f"\n{'='*60}")
    print(f"[3/3] 导入 finance_snapshot_v2 ← finance_snapshot.csv")
    print(f"{'='*60}")

    # 先清空
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM finance_snapshot_v2"))
        conn.commit()

    df = pd.read_csv(finance_path, header=None, dtype={0: str, 17: str})
    print(f"  读取: {len(df)} 行, {df.shape[1]} 列")

    db_rows = []
    for _, row in df.iterrows():
        code = str(row[0]).strip().zfill(6)
        name = str(row[2]).strip() if pd.notna(row[2]) else ""
        report_date = safe_date(row[3])

        # 上市日期 (col_18 = index 17)
        list_date_raw = str(row[17]).strip() if pd.notna(row[17]) else None

        db_rows.append({
            "code": code,
            "name": name,
            "report_date": report_date,
            "total_revenue": safe_float(row[5]) if pd.notna(row[5]) else None,
            "total_cost": safe_float(row[6]) if pd.notna(row[6]) else None,
            "total_assets": safe_float(row[7]) if pd.notna(row[7]) else None,
            "total_liabilities": safe_float(row[8]) if pd.notna(row[8]) else None,
            "net_profit": safe_float(row[9]) if pd.notna(row[9]) else None,
            "net_profit_total": safe_float(row[10]) if pd.notna(row[10]) else None,
            "pe_ttm": safe_float(row[11]) if pd.notna(row[11]) else None,
            "market_cap": safe_float(row[12]) if pd.notna(row[12]) else None,
            "oper_cashflow": safe_float(row[13]) if pd.notna(row[13]) else None,
            "invest_cashflow": safe_float(row[14]) if pd.notna(row[14]) else None,
            "finance_cashflow": safe_float(row[15]) if pd.notna(row[15]) else None,
            "employee_count": safe_float(row[16]) if pd.notna(row[16]) else None,
            "flag": safe_int(row[17]) if pd.notna(row[17]) else None,
            "list_date_raw": list_date_raw,
        })

    if db_rows:
        df_db = pd.DataFrame(db_rows)
        df_db.to_sql("finance_snapshot_v2", engine, if_exists="append", index=False)
        print(f"  导入完成: {len(db_rows)} 条")

    return len(db_rows)


# ============================================================
# 数据验证
# ============================================================

def verify_data(engine):
    """验证各表数据完整性"""
    print(f"\n{'='*60}")
    print(f"[验证] 数据完整性检查")
    print(f"{'='*60}")

    queries = {
        "stock_basic": "SELECT COUNT(*) as cnt, COUNT(DISTINCT code) as codes FROM stock_basic",
        "daily_price": """
            SELECT COUNT(*) as cnt, COUNT(DISTINCT code) as codes,
                   MIN(trade_date) as date_from, MAX(trade_date) as date_to
            FROM daily_price
        """,
        "finance_snapshot_v2": """
            SELECT COUNT(*) as cnt, COUNT(DISTINCT code) as codes,
                   COUNT(CASE WHEN net_profit > 0 THEN 1 END) as profit_positive
            FROM finance_snapshot_v2
        """,
    }

    for table, sql in queries.items():
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            row = result.fetchone()
            if row:
                print(f"  [{table}]")
                for key in row._mapping:
                    print(f"    {key}: {row._mapping[key]}")
            else:
                print(f"  [{table}] 空表!")

    # 交叉验证: daily_price 中的股票是否都在 stock_basic 中
    with engine.connect() as conn:
        orphan = conn.execute(text("""
            SELECT COUNT(DISTINCT dp.code) FROM daily_price dp
            LEFT JOIN stock_basic sb ON dp.code = sb.code
            WHERE sb.code IS NULL
        """)).scalar()
        print(f"  daily_price 孤儿股票(不在stock_basic中): {orphan}")


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("CSV → quant.db 数据导入")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 获取配置
    config = get_config()
    db_url = get_db_url(config)
    print(f"\n数据库: {db_url}")
    print(f"CSV目录: {CSV_DIR}")

    # 创建引擎
    engine = create_engine(db_url, echo=False)

    # Step 0: 建表
    print(f"\n[0/4] 建表...")
    Base.metadata.create_all(engine, tables=[
        StockBasic.__table__,
        DailyPrice.__table__,
        BenchmarkData.__table__,
    ])
    print("[OK] ORM 表已创建")

    # 额外建表: finance_snapshot_v2 (pipeline.py 需要)
    create_finance_snapshot_table(engine)

    # Step 1: stock_basic
    n_stocks = import_stock_basic(engine, CSV_DIR)

    # Step 2: daily_price (最耗时)
    n_prices = import_daily_price(engine, CSV_DIR)

    # Step 3: finance_snapshot_v2
    n_finance = import_finance_snapshot(engine, CSV_DIR)

    # Step 4: 索引
    print(f"\n[索引] 创建性能索引...")
    create_indexes(engine)

    # Step 5: 验证
    verify_data(engine)

    # 完成
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "database", "quant.db"
    )
    size_mb = os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0

    print(f"\n{'='*60}")
    print(f"导入完成!")
    print(f"  stock_basic:        {n_stocks} 条")
    print(f"  daily_price:        {n_prices:,} 条")
    print(f"  finance_snapshot_v2: {n_finance} 条")
    print(f"  DB 文件大小:        {size_mb:.1f} MB")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
