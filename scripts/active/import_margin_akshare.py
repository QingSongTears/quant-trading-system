#!/usr/bin/env python3
"""
在线拉取融资融券数据（AKShare）→ margin_trading 表
=========================================================
沪市接口: stock_margin_detail_sse(date)
深市接口: stock_margin_detail_szse(date)
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


def main():
    engine = get_engine()

    with engine.connect() as conn:
        conn.execute(text("""
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
        """))
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_mt_code ON margin_trading(code)",
            "CREATE INDEX IF NOT EXISTS idx_mt_date ON margin_trading(trade_date)",
            "CREATE INDEX IF NOT EXISTS idx_mt_code_date ON margin_trading(code, trade_date)",
        ]:
            try:
                conn.execute(text(idx_sql))
            except Exception:
                pass
        conn.commit()
    print("[OK] margin_trading 表已创建")

    import akshare as ak
    end_d = date.today()
    start_d = end_d - timedelta(days=30)  # 最近 30 个交易日

    # 获取交易日历
    print(f">>> 拉取融资融券: {start_d} ~ {end_d}")
    try:
        # 使用交易日历
        trade_cal = ak.tool_trade_date_hist_sina()
        trade_cal["trade_date"] = pd.to_datetime(trade_cal["trade_date"]).dt.date
        trade_dates = [d for d in trade_cal["trade_date"]
                       if start_d <= d <= end_d]
        print(f"  交易日数: {len(trade_dates)}")
    except Exception as e:
        print(f"  [WARN] 交易日历失败, 用日列表: {e}")
        trade_dates = [end_d - timedelta(days=i) for i in range(30)]

    all_rows = []
    for d in trade_dates[-15:]:  # 限制最多 15 天，避免频率限制
        ds = d.strftime("%Y%m%d")
        # 沪市
        try:
            df_sh = ak.stock_margin_detail_sse(date=ds)
            if len(df_sh) > 0:
                df_sh["market"] = "SH"
                all_rows.append(df_sh)
        except Exception as e:
            print(f"  [SH {ds}] 失败: {str(e)[:60]}")

        # 深市
        try:
            df_sz = ak.stock_margin_detail_szse(date=ds)
            if len(df_sz) > 0:
                df_sz["market"] = "SZ"
                all_rows.append(df_sz)
        except Exception as e:
            print(f"  [SZ {ds}] 失败: {str(e)[:60]}")

        time.sleep(1.5)  # 避免频率限制

    if not all_rows:
        print("[ERROR] 没有拉到任何数据")
        return

    df_all = pd.concat(all_rows, ignore_index=True)
    print(f"  原始行数: {len(df_all)}")

    # 列重命名
    rename_map = {
        "信用交易日期": "trade_date",
        "标的证券代码": "code",
        "标的证券简称": "name",
        "融资余额": "rzye",
        "融资买入额": "rzmre",
        "融资偿还额": "rzche",
        "融券余量": "rqye",
        "融券卖出量": "rqmcl",
        "融券偿还量": "rqchl",
    }
    for k, v in rename_map.items():
        if k in df_all.columns:
            df_all = df_all.rename(columns={k: v})

    keep_cols = ["code", "market", "name", "trade_date", "rzye", "rzmre", "rzche", "rqye", "rqmcl", "rqchl"]
    df_use = df_all[[c for c in keep_cols if c in df_all.columns]].copy()

    if "code" in df_use.columns:
        df_use["code"] = df_use["code"].astype(str).str.zfill(6)
        # 过滤空 code
        df_use = df_use[df_use["code"].notna() & (df_use["code"] != "000000") & (df_use["code"] != "nan")]
        df_use = df_use[df_use["code"].str.len() == 6]
    if "trade_date" in df_use.columns:
        df_use["trade_date"] = pd.to_datetime(df_use["trade_date"], errors="coerce").dt.date
        df_use = df_use[df_use["trade_date"].notna()]
    df_use = df_use.where(pd.notna(df_use), None)
    print(f"  过滤后行数: {len(df_use)}")

    # 清空旧数据
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM margin_trading"))
        conn.commit()

    records = df_use.to_dict(orient="records")
    if records:
        cols = list(df_use.columns)
        placeholders = ", ".join([f":{c}" for c in cols])
        cols_str = ", ".join(cols)
        with engine.connect() as conn:
            conn.execute(
                text(f"INSERT INTO margin_trading ({cols_str}) VALUES ({placeholders})"),
                records
            )
            conn.commit()

    with engine.connect() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM margin_trading")).scalar()
        codes = conn.execute(text("SELECT COUNT(DISTINCT code) FROM margin_trading")).scalar()
        dmin, dmax = conn.execute(text("SELECT MIN(trade_date), MAX(trade_date) FROM margin_trading")).fetchone()

    print(f"\n[完成] margin_trading:")
    print(f"  行数: {cnt:,}")
    print(f"  股票数: {codes}")
    print(f"  日期范围: {dmin} ~ {dmax}")


if __name__ == "__main__":
    main()
