#!/usr/bin/env python3
"""
CSV → SQLite (quant.db) 数据导入脚本 v2
=========================================
将 tdrive 预置CSV文件导入 quant.db，含派生指标计算

数据源:
  - kline_daily.csv      → daily_price (300万行)
  - tencent_quotes.csv   → stock_basic (5200+ 股票)
  - finance_snapshot.csv → finance_snapshot_v2 (含ROE/EPS/BPS等派生指标)
  - dividend.csv         → dividend_data (4万行分红历史)
  - fund_flow_full.csv   → fund_flow_data (36万行资金流向)

用法:
  python scripts/import_csv_to_db.py
"""
import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

from src.models.database import Base, StockBasic, DailyPrice, BenchmarkData
from src.config import get_config, get_db_url

# ============================================================
# 配置
# ============================================================
CSV_DIR = os.environ.get(
    "CSV_DIR",
    "/workspace/quant-trading-system/data/raw"
)
BATCH_SIZE = 50000

# ============================================================
# 辅助函数
# ============================================================

def safe_float(val):
    try:
        f = float(val)
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None

def safe_int(val):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None

def safe_date(val):
    if val is None or val == "" or (isinstance(val, float) and np.isnan(val)):
        return None
    try:
        s = str(val).strip()[:10]
        return datetime.strptime(s, "%Y-%m-%d").date()
    except:
        try:
            s = str(int(float(val))).strip()
            return datetime.strptime(s, "%Y%m%d").date()
        except:
            return None

def parse_market(code: str, market_hint: str = None) -> str:
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
# 表创建
# ============================================================

def create_tables(engine):
    """创建所有表"""
    # ORM 表
    Base.metadata.create_all(engine, tables=[
        StockBasic.__table__,
        DailyPrice.__table__,
        BenchmarkData.__table__,
    ])
    print("[OK] ORM 表已创建")

    with engine.connect() as conn:
        # 先删除旧表（可能有旧schema）
        conn.execute(text("DROP TABLE IF EXISTS finance_snapshot_v2"))
        conn.execute(text("DROP TABLE IF EXISTS dividend_data"))
        conn.execute(text("DROP TABLE IF EXISTS fund_flow_data"))
        conn.commit()

        # finance_snapshot_v2 — 含派生指标
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
                -- 派生指标 (from tencent_quotes join)
                shares DOUBLE,
                total_equity DOUBLE,
                roe DOUBLE,
                eps DOUBLE,
                bps DOUBLE,
                debt_ratio DOUBLE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        # 索引
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_fs2_code ON finance_snapshot_v2(code)",
            "CREATE INDEX IF NOT EXISTS idx_fs2_profit ON finance_snapshot_v2(net_profit)",
            "CREATE INDEX IF NOT EXISTS idx_fs2_roe ON finance_snapshot_v2(roe)",
            "CREATE INDEX IF NOT EXISTS idx_dp_code ON daily_price(code)",
            "CREATE INDEX IF NOT EXISTS idx_dp_date ON daily_price(trade_date)",
            "CREATE INDEX IF NOT EXISTS idx_dp_code_date ON daily_price(code, trade_date)",
            "CREATE INDEX IF NOT EXISTS idx_sb_market ON stock_basic(market)",
        ]:
            try:
                conn.execute(text(idx_sql))
            except Exception as e:
                print(f"  [WARN] 索引跳过: {e}")

        # dividend_data 表
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dividend_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code VARCHAR(10) NOT NULL,
                market VARCHAR(10),
                name VARCHAR(50),
                ex_div_date DATE,
                pre_tax_bonus DOUBLE,
                transfer_ratio DOUBLE,
                bonus_ratio DOUBLE,
                record_date DATE
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_div_code ON dividend_data(code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_div_date ON dividend_data(ex_div_date)"))

        # fund_flow_data 表
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS fund_flow_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code VARCHAR(10) NOT NULL,
                market VARCHAR(10),
                name VARCHAR(50),
                trade_date DATE,
                main_net DOUBLE,
                super_large_net DOUBLE,
                large_net DOUBLE,
                medium_net DOUBLE,
                small_net DOUBLE
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ff_code ON fund_flow_data(code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ff_date ON fund_flow_data(trade_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ff_code_date ON fund_flow_data(code, trade_date)"))

        conn.commit()
    print("[OK] 所有表及索引已创建")


# ============================================================
# 导入函数
# ============================================================

def import_stock_basic(engine, csv_dir: str):
    """从 tencent_quotes.csv 导入 stock_basic，补充 list_date"""
    quotes_path = os.path.join(csv_dir, "tencent_quotes.csv")
    finance_path = os.path.join(csv_dir, "finance_snapshot.csv")

    if not os.path.exists(quotes_path):
        print(f"[ERROR] 找不到: {quotes_path}")
        return 0

    print(f"\n{'='*60}")
    print(f"[1/5] 导入 stock_basic ← tencent_quotes.csv")
    print(f"{'='*60}")

    df = pd.read_csv(quotes_path, dtype={"code": str})
    print(f"  读取: {len(df)} 行, {df.shape[1]} 列")

    # 提取 list_date
    list_date_map = {}
    if os.path.exists(finance_path):
        try:
            fs = pd.read_csv(finance_path, header=None, dtype={0: str, 17: str})
            for _, row in fs.iterrows():
                code = str(row[0]).strip().zfill(6)
                raw_date = str(row[17]).strip()
                if raw_date and raw_date != "nan":
                    try:
                        d = datetime.strptime(raw_date[:8], "%Y%m%d").date()
                        list_date_map[code] = d
                    except:
                        pass
        except Exception as e:
            print(f"  [WARN] 提取 list_date 失败: {e}")

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
        records.append({"code": code, "name": name, "market": market, "list_date": list_date})

    if records:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM stock_basic"))
            conn.commit()
        for i in range(0, len(records), 1000):
            pd.DataFrame(records[i:i+1000]).to_sql("stock_basic", engine, if_exists="append", index=False)
        print(f"  导入完成: {len(records)} 条")

    return len(records)


def import_daily_price(engine, csv_dir: str):
    """从 kline_daily.csv 导入 daily_price"""
    kline_path = os.path.join(csv_dir, "kline_daily.csv")

    if not os.path.exists(kline_path):
        print(f"[ERROR] 找不到: {kline_path}")
        return 0

    print(f"\n{'='*60}")
    print(f"[2/5] 导入 daily_price ← kline_daily.csv (大任务)")
    print(f"{'='*60}")

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM daily_price"))
        conn.commit()

    total_rows = 0
    start_time = time.time()
    col_names = ["code", "market", "name", "trade_date", "open", "high", "low",
                 "close", "volume", "amount"]

    for chunk_idx, chunk in enumerate(pd.read_csv(
        kline_path, header=None, names=col_names,
        dtype={"code": str, "market": str, "name": str,
               "open": float, "high": float, "low": float,
               "close": float, "volume": float, "amount": float},
        chunksize=BATCH_SIZE,
    )):
        chunk["code"] = chunk["code"].str.strip().str.zfill(6)
        chunk["trade_date"] = pd.to_datetime(chunk["trade_date"], errors="coerce")
        chunk = chunk.dropna(subset=["trade_date"])

        db_chunk = pd.DataFrame({
            "code": chunk["code"],
            "trade_date": chunk["trade_date"].dt.date,
            "open": chunk["open"].astype(float),
            "high": chunk["high"].astype(float),
            "low": chunk["low"].astype(float),
            "close": chunk["close"].astype(float),
            "volume": chunk["volume"].fillna(0).astype(int),
            "amount": chunk["amount"].apply(safe_float),
            "pct_change": None,
            "turnover": None,
        })
        db_chunk.to_sql("daily_price", engine, if_exists="append", index=False)
        total_rows += len(db_chunk)

        elapsed = time.time() - start_time
        if (chunk_idx + 1) % 10 == 0 or chunk_idx == 0:
            rate = total_rows / elapsed if elapsed > 0 else 0
            print(f"  批次 {chunk_idx+1}: {total_rows:,} 行 ({rate:,.0f} 行/s, {elapsed:.1f}s)")

    elapsed = time.time() - start_time
    print(f"  导入完成: {total_rows:,} 行, 耗时 {elapsed:.1f}s ({total_rows/elapsed:,.0f} 行/s)")
    return total_rows


def import_finance_snapshot(engine, csv_dir: str):
    """
    从 finance_snapshot.csv 导入 finance_snapshot_v2
    并 join tencent_quotes 计算派生指标:
      shares = mcap_yi * 1e8 / price
      total_equity = mcap_yi * 1e8 / pb
      roe = net_profit / total_equity
      eps = net_profit / shares
      bps = total_equity / shares
      debt_ratio = (total_assets - total_equity) / total_assets
    """
    finance_path = os.path.join(csv_dir, "finance_snapshot.csv")
    quotes_path = os.path.join(csv_dir, "tencent_quotes.csv")

    if not os.path.exists(finance_path):
        print(f"[ERROR] 找不到: {finance_path}")
        return 0

    print(f"\n{'='*60}")
    print(f"[3/5] 导入 finance_snapshot_v2 ← finance_snapshot.csv")
    print(f"{'='*60}")

    # 读取 tencent_quotes 获取 pb/mcap_yi/price
    quotes_df = pd.read_csv(quotes_path, dtype={"code": str})
    quotes_df["code"] = quotes_df["code"].str.strip().str.zfill(6)
    quotes = quotes_df.set_index("code")
    print(f"  tencent_quotes: {len(quotes)} 条")

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM finance_snapshot_v2"))
        conn.commit()

    df = pd.read_csv(finance_path, header=None, dtype={0: str, 17: str})
    print(f"  读取: {len(df)} 行, {df.shape[1]} 列")

    db_rows = []
    derived_ok = 0
    for _, row in df.iterrows():
        code = str(row[0]).strip().zfill(6)
        name = str(row[2]).strip() if pd.notna(row[2]) else ""
        report_date = safe_date(row[3])
        list_date_raw = str(row[17]).strip() if pd.notna(row[17]) else None

        total_assets = safe_float(row[7]) if pd.notna(row[7]) else None
        net_profit = safe_float(row[9]) if pd.notna(row[9]) else None

        # --- 派生指标 ---
        shares = None
        total_equity = None
        roe = None
        eps = None
        bps = None
        debt_ratio = None

        if code in quotes.index:
            q = quotes.loc[code]
            if isinstance(q, pd.DataFrame):
                q = q.iloc[0]

            mcap_yi = safe_float(q.get("mcap_yi"))
            pb = safe_float(q.get("pb"))
            price = safe_float(q.get("price"))

            if mcap_yi and mcap_yi > 0 and pb and pb > 0:
                mcap = mcap_yi * 1e8  # 亿 → 元
                total_equity = mcap / pb
                if total_equity and total_equity > 0:
                    if net_profit and net_profit != 0:
                        roe = net_profit / total_equity
                    if total_assets and total_assets > 0:
                        debt_ratio = (total_assets - total_equity) / total_assets
                        debt_ratio = max(0, min(1, debt_ratio))  # clamp [0, 1]

            if price and price > 0 and mcap_yi and mcap_yi > 0:
                shares = mcap_yi * 1e8 / price
                if shares > 0:
                    if net_profit:
                        eps = net_profit / shares
                    if total_equity:
                        bps = total_equity / shares

            if roe is not None:
                derived_ok += 1

        db_rows.append({
            "code": code,
            "name": name,
            "report_date": report_date,
            "total_revenue": safe_float(row[5]) if pd.notna(row[5]) else None,
            "total_cost": safe_float(row[6]) if pd.notna(row[6]) else None,
            "total_assets": total_assets,
            "total_liabilities": safe_float(row[8]) if pd.notna(row[8]) else None,
            "net_profit": net_profit,
            "net_profit_total": safe_float(row[10]) if pd.notna(row[10]) else None,
            "pe_ttm": safe_float(row[11]) if pd.notna(row[11]) else None,
            "market_cap": safe_float(row[12]) if pd.notna(row[12]) else None,
            "oper_cashflow": safe_float(row[13]) if pd.notna(row[13]) else None,
            "invest_cashflow": safe_float(row[14]) if pd.notna(row[14]) else None,
            "finance_cashflow": safe_float(row[15]) if pd.notna(row[15]) else None,
            "employee_count": safe_float(row[16]) if pd.notna(row[16]) else None,
            "flag": safe_int(row[17]) if pd.notna(row[17]) else None,
            "list_date_raw": list_date_raw,
            "shares": shares,
            "total_equity": total_equity,
            "roe": roe,
            "eps": eps,
            "bps": bps,
            "debt_ratio": debt_ratio,
        })

    if db_rows:
        df_db = pd.DataFrame(db_rows)
        # 处理inf值
        df_db = df_db.replace([np.inf, -np.inf], None)
        df_db.to_sql("finance_snapshot_v2", engine, if_exists="append", index=False)
        print(f"  导入完成: {len(db_rows)} 条, 派生指标可用: {derived_ok} 条")

    return len(db_rows)


def import_dividend_data(engine, csv_dir: str):
    """从 dividend.csv 导入 dividend_data"""
    div_path = os.path.join(csv_dir, "dividend.csv")

    if not os.path.exists(div_path):
        print(f"[WARN] 找不到: {div_path}, 跳过")
        return 0

    print(f"\n{'='*60}")
    print(f"[4/5] 导入 dividend_data ← dividend.csv")
    print(f"{'='*60}")

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM dividend_data"))
        conn.commit()

    df = pd.read_csv(div_path, dtype={"code": str})
    df["code"] = df["code"].str.strip().str.zfill(6)
    print(f"  读取: {len(df)} 行")

    db_rows = []
    for _, row in df.iterrows():
        db_rows.append({
            "code": row["code"],
            "market": str(row.get("market", "")).strip() if pd.notna(row.get("market")) else None,
            "name": str(row.get("name", "")).strip() if pd.notna(row.get("name")) else None,
            "ex_div_date": safe_date(row.get("ex_div_date")),
            "pre_tax_bonus": safe_float(row.get("pre_tax_bonus")),
            "transfer_ratio": safe_float(row.get("transfer_ratio")),
            "bonus_ratio": safe_float(row.get("bonus_ratio")),
            "record_date": safe_date(row.get("record_date")),
        })

    df_db = pd.DataFrame(db_rows)
    df_db.to_sql("dividend_data", engine, if_exists="append", index=False)
    print(f"  导入完成: {len(db_rows)} 条")
    return len(db_rows)


def import_fund_flow_data(engine, csv_dir: str):
    """从 fund_flow_full.csv 导入 fund_flow_data"""
    ff_path = os.path.join(csv_dir, "fund_flow_full.csv")

    if not os.path.exists(ff_path):
        print(f"[WARN] 找不到: {ff_path}, 跳过")
        return 0

    print(f"\n{'='*60}")
    print(f"[5/5] 导入 fund_flow_data ← fund_flow_full.csv")
    print(f"{'='*60}")

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM fund_flow_data"))
        conn.commit()

    # fund_flow_full 有 BOM 头
    col_names = ["fid", "code", "market", "name", "date",
                 "main_net", "super_large_net", "large_net", "medium_net", "small_net"]
    total_rows = 0

    for chunk in pd.read_csv(
        ff_path, dtype={"code": str},
        chunksize=BATCH_SIZE,
    ):
        # 处理 BOM
        cols = list(chunk.columns)
        if cols[0].startswith('\ufeff'):
            chunk = chunk.rename(columns={cols[0]: 'id'})

        chunk["code"] = chunk["code"].str.strip().str.zfill(6)
        chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce")

        db_chunk = pd.DataFrame({
            "code": chunk["code"],
            "market": chunk.get("market", ""),
            "name": chunk.get("name", ""),
            "trade_date": chunk["date"].dt.date,
            "main_net": chunk["main_net"].apply(safe_float),
            "super_large_net": chunk["super_large_net"].apply(safe_float),
            "large_net": chunk["large_net"].apply(safe_float),
            "medium_net": chunk["medium_net"].apply(safe_float),
            "small_net": chunk["small_net"].apply(safe_float),
        })
        db_chunk = db_chunk.dropna(subset=["trade_date"])
        db_chunk.to_sql("fund_flow_data", engine, if_exists="append", index=False)
        total_rows += len(db_chunk)

    print(f"  导入完成: {total_rows:,} 条")
    return total_rows


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
                   COUNT(CASE WHEN net_profit > 0 THEN 1 END) as profit_positive,
                   COUNT(CASE WHEN roe IS NOT NULL THEN 1 END) as roe_available,
                   ROUND(AVG(roe)*100, 2) as avg_roe_pct,
                   ROUND(AVG(debt_ratio)*100, 2) as avg_debt_ratio_pct
            FROM finance_snapshot_v2
        """,
        "dividend_data": """
            SELECT COUNT(*) as cnt, COUNT(DISTINCT code) as codes,
                   MIN(ex_div_date) as date_from, MAX(ex_div_date) as date_to
            FROM dividend_data
        """,
        "fund_flow_data": """
            SELECT COUNT(*) as cnt, COUNT(DISTINCT code) as codes,
                   MIN(trade_date) as date_from, MAX(trade_date) as date_to
            FROM fund_flow_data
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

    # 交叉验证
    with engine.connect() as conn:
        orphan = conn.execute(text("""
            SELECT COUNT(DISTINCT dp.code) FROM daily_price dp
            LEFT JOIN stock_basic sb ON dp.code = sb.code
            WHERE sb.code IS NULL
        """)).scalar()
        print(f"  daily_price 孤儿股票: {orphan}")


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("CSV → quant.db 数据导入 v2")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    config = get_config()
    db_url = get_db_url(config)
    print(f"\n数据库: {db_url}")
    print(f"CSV目录: {CSV_DIR}")

    engine = create_engine(db_url, echo=False)

    # Step 0: 建表
    print(f"\n[0/6] 建表...")
    create_tables(engine)

    # Step 1-5: 导入
    n_stocks = import_stock_basic(engine, CSV_DIR)
    n_prices = import_daily_price(engine, CSV_DIR)
    n_finance = import_finance_snapshot(engine, CSV_DIR)
    n_div = import_dividend_data(engine, CSV_DIR)
    n_ff = import_fund_flow_data(engine, CSV_DIR)

    # Step 6: 验证
    verify_data(engine)

    # 文件大小
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "database", "quant.db"
    )
    # fallback
    if not os.path.exists(db_path):
        db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "quant.db"
        )

    size_mb = os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0

    print(f"\n{'='*60}")
    print(f"导入完成!")
    print(f"  stock_basic:         {n_stocks} 条")
    print(f"  daily_price:         {n_prices:,} 条")
    print(f"  finance_snapshot_v2: {n_finance} 条")
    print(f"  dividend_data:       {n_div:,} 条")
    print(f"  fund_flow_data:      {n_ff:,} 条")
    print(f"  DB 文件大小:         {size_mb:.1f} MB")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
