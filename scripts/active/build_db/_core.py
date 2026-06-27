"""build_db._core — 4 个核心数据 importer

- stock_basic    : A 股列表
- daily_price    : 日 K 线 (2023~)
- fund_flow      : 资金流 (120d)
- technical_indicators : 技术指标
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def import_stock_basic(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """
    导入 stock_basic 表

    源: market_data/raw/reference/tencent_quotes.csv
         或 market_data/stock_basic.csv (fallback)
    """
    candidates = [
        data_dir / "raw" / "reference" / "tencent_quotes.csv",
        data_dir / "stock_basic.csv",
    ]
    csv_path = next((p for p in candidates if p.exists()), None)
    if not csv_path:
        print(f"   ⚠️ 未找到 stock_basic CSV, 跳过")
        return 0

    import pandas as pd
    df = pd.read_csv(csv_path, dtype={"code": str}, low_memory=False)
    if full:
        cur.execute("DELETE FROM stock_basic")
    cur.executemany(
        "INSERT OR REPLACE INTO stock_basic (code, name, market) VALUES (?, ?, ?)",
        [
            (str(r.code).zfill(6), str(r.name) if pd.notna(r.name) else "",
             {"sz": "SZ", "sh": "SH", "bj": "BJ"}.get(
                 str(r.market).lower() if pd.notna(r.market) else "", "SH"))
            for r in df.itertuples(index=False)
        ],
    )
    return len(df)


def import_daily_price(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """
    导入 daily_price 表

    源: market_data/raw/kline_daily/kline_daily_*.csv
    """
    import pandas as pd
    kline_dir = data_dir / "raw" / "kline_daily"
    if not kline_dir.exists():
        print(f"   ⚠️ {kline_dir} 不存在, 跳过")
        return 0

    files = sorted(kline_dir.glob("kline_daily_*.csv"))
    if not files:
        print(f"   ⚠️ {kline_dir} 无 kline_daily_*.csv 文件, 跳过")
        return 0

    print(f"   发现 {len(files)} 个 CSV: {[f.name for f in files]}")
    dfs = []
    for f in files:
        df = pd.read_csv(f, dtype={"code": str}, low_memory=False)
        df["source_file"] = f.name
        dfs.append(df)
    df = pd.concat(dfs, ignore_index=True)

    # 字段清洗
    df = df.dropna(subset=["code", "date", "close"])
    df["trade_date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["trade_date"])
    df["code"] = df["code"].astype(str).str.replace(r"^(sz|sh|bj)", "", regex=True).str.zfill(6)

    if full:
        cur.execute("DELETE FROM daily_price")

    # 增量模式: 只导入比 DB 现有 max date 更新的行
    if not full:
        cur.execute("SELECT MAX(trade_date) FROM daily_price")
        max_date = cur.fetchone()[0]
        if max_date:
            df = df[df["trade_date"] > max_date]
            print(f"   增量模式: 仅导入 > {max_date} 的行")

    rows = [
        (r.code, r.trade_date, float(r.open), float(r.high),
         float(r.low), float(r.close), int(r.volume),
         float(r.amount) if pd.notna(r.amount) else None)
        for r in df.itertuples(index=False)
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO daily_price (code, trade_date, open, high, low, close, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def import_fund_flow(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """
    导入 fund_flow_data 表

    源: market_data/fund_flow_120d.csv
    """
    import pandas as pd
    csv_path = data_dir / "fund_flow_120d.csv"
    if not csv_path.exists():
        print(f"   ⚠️ {csv_path} 不存在, 跳过")
        return 0

    df = pd.read_csv(csv_path, dtype={"code": str}, low_memory=False)
    df = df.dropna(subset=["code", "date"])
    df["trade_date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["trade_date"])
    df["code"] = df["code"].astype(str).str.replace(r"^(sz|sh|bj)", "", regex=True).str.zfill(6)

    if full:
        cur.execute("DELETE FROM fund_flow_data")

    def _to_float(s):
        """宽容处理: 空 / nan / '-' / 'None' / 数字字符串"""
        if pd.isna(s):
            return None
        if isinstance(s, str):
            s = s.strip()
            if s in ("", "-", "--", "None", "nan"):
                return None
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    rows = [
        (r.code, r.trade_date,
         _to_float(r.main_net),
         _to_float(r.super_large_net),
         _to_float(r.large_net),
         _to_float(r.medium_net),
         _to_float(r.small_net))
        for r in df.itertuples(index=False)
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO fund_flow_data "
        "(code, trade_date, main_net, super_large_net, large_net, medium_net, small_net) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def import_technical_indicators(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """
    导入 technical_indicators 表

    源: market_data/raw/technical_indicators/tech_indicators_*.csv
    """
    import pandas as pd
    ti_dir = data_dir / "raw" / "technical_indicators"
    if not ti_dir.exists():
        print(f"   ⚠️ {ti_dir} 不存在, 跳过")
        return 0

    files = sorted(ti_dir.glob("tech_indicators_*.csv"))
    if not files:
        print(f"   ⚠️ {ti_dir} 无 tech_indicators_*.csv 文件, 跳过")
        return 0

    print(f"   发现 {len(files)} 个 CSV")
    # 跳过冗余备份 (part1/part2 是 2025 的拆分备份, 与 tech_indicators_2025.csv 重复)
    files = [f for f in files if "part" not in f.name]
    print(f"   跳过 part1/part2 后剩余 {len(files)} 个 CSV")
    dfs = [pd.read_csv(f, dtype={"code": str}, low_memory=False) for f in files]
    df = pd.concat(dfs, ignore_index=True)

    df = df.dropna(subset=["code", "date"])
    df["code"] = df["code"].astype(str).str.replace(r"^(sz|sh|bj)", "", regex=True).str.zfill(6)

    if full:
        cur.execute("DELETE FROM technical_indicators")

    rows = []
    cols = ["code", "trade_date", "macd_dif", "macd_dea", "macd_hist",
            "rsi14", "kdj_k", "kdj_d", "kdj_j",
            "boll_mid", "boll_upper", "boll_lower"]
    # 把 CSV 'date' 列重命名为 'trade_date' 以匹配 DB schema
    if "date" in df.columns and "trade_date" not in df.columns:
        df = df.rename(columns={"date": "trade_date"})
    for r in df.itertuples(index=False):
        rd = r._asdict() if hasattr(r, "_asdict") else r._asdict()
        row = []
        for c in cols:
            v = rd.get(c, None)
            if c == "code":
                row.append(str(v).zfill(6) if v else None)
            elif c == "trade_date":
                row.append(str(v)[:10] if v else None)
            else:
                row.append(float(v) if pd.notna(v) else None)
        rows.append(tuple(row))
    cur.executemany(
        f"INSERT OR IGNORE INTO technical_indicators ({','.join(cols)}) "
        f"VALUES ({','.join(['?'] * len(cols))})",
        rows,
    )
    return len(rows)
