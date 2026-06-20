#!/usr/bin/env python3
"""
在线拉取龙虎榜数据（AKShare）→ dragon_tiger_data 表
=========================================================
AKShare 新版接口: stock_lhb_detail_em(start_date, end_date)
"""
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import create_engine, text
from src.config import get_config, get_db_url


def main():
    engine = create_engine(get_db_url(get_config()), echo=False)

    # 建表
    with engine.connect() as conn:
        conn.execute(text("""
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
        """))
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_dt_code ON dragon_tiger_data(code)",
            "CREATE INDEX IF NOT EXISTS idx_dt_date ON dragon_tiger_data(trade_date)",
            "CREATE INDEX IF NOT EXISTS idx_dt_code_date ON dragon_tiger_data(code, trade_date)",
        ]:
            try:
                conn.execute(text(idx_sql))
            except Exception:
                pass
        conn.commit()
    print("[OK] dragon_tiger_data 表已创建")

    # 仅拉取最近 60 天（避免频率限制）
    import akshare as ak
    end_d = date.today()
    start_d = end_d - timedelta(days=60)

    print(f">>> 拉取龙虎榜: {start_d} ~ {end_d}")
    total = 0
    try:
        df = ak.stock_lhb_detail_em(
            start_date=start_d.strftime("%Y%m%d"),
            end_date=end_d.strftime("%Y%m%d"),
        )
        print(f"  AKShare 返回: {len(df)} 行")
        print(f"  列: {df.columns.tolist()}")

        # 清空旧数据
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM dragon_tiger_data"))
            conn.commit()

        # 映射列 (AKShare 中文列名 → DB 列名)
        rename_map = {
            "代码": "code",
            "名称": "name",
            "上榜日": "trade_date",
            "解读": "reason",
            "龙虎榜净买额": "net_buy_wan",
            "净买额占总成交比": "turnover_pct",
        }
        available_map = {k: v for k, v in rename_map.items() if v and k in df.columns}
        print(f"  实际可用列: {available_map}")
        df_use = df.rename(columns=available_map)[list(available_map.values())].copy()
        if "code" in df_use.columns:
            df_use["code"] = df_use["code"].astype(str).str.zfill(6)
        if "trade_date" in df_use.columns:
            df_use["trade_date"] = pd.to_datetime(df_use["trade_date"], errors="coerce").dt.date
        df_use = df_use.where(pd.notna(df_use), None)

        records = df_use.to_dict(orient="records")
        if records:
            cols = list(df_use.columns)
            placeholders = ", ".join([f":{c}" for c in cols])
            cols_str = ", ".join(cols)
            with engine.connect() as conn:
                conn.execute(
                    text(f"INSERT INTO dragon_tiger_data ({cols_str}) VALUES ({placeholders})"),
                    records
                )
                conn.commit()
        total = len(records)
    except Exception as e:
        print(f"[ERROR] 拉取失败: {e}")
        print("        可能原因: AKShare 接口频率限制/网络问题")

    with engine.connect() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM dragon_tiger_data")).scalar()
        codes = conn.execute(text("SELECT COUNT(DISTINCT code) FROM dragon_tiger_data")).scalar()

    print(f"\n[完成] dragon_tiger_data:")
    print(f"  新增: {total:,}")
    print(f"  现有: {cnt:,} 行 / {codes} 只股票")


if __name__ == "__main__":
    main()
