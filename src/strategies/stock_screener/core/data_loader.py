"""
统一数据加载模块 — 惰性加载 + 内存缓存
替代 data_fetcher.py

数据来源: data/raw/ 目录下的预置 CSV 文件

用法:
    from core.data_loader import load_kline, load_quotes, load_finance
    kline = load_kline()
    quotes = load_quotes()
    finance = load_finance()
"""

import pandas as pd
from pathlib import Path
from typing import Optional, Tuple

from config import DATA_RAW_DIR, DATA_PROCESSED_DIR, REFERENCE_DATA_DIR, IMPORTED_DATA_DIR

# ============================================================
# 全局缓存
# ============================================================

_KLINE_DF: Optional[pd.DataFrame] = None
_KLINE_LOADED: bool = False

_QUOTES_DF: Optional[pd.DataFrame] = None
_QUOTES_LOADED: bool = False

_FINANCE_DF: Optional[pd.DataFrame] = None
_FINANCE_LOADED: bool = False


# ============================================================
# K线数据
# ============================================================

def load_kline(force_reload: bool = False) -> pd.DataFrame:
    """
    加载日K线数据
    支持两种数据布局（自动检测）：
      1. DATA_RAW_DIR / "kline_daily.csv"  （单文件，旧格式）
      2. DATA_RAW_DIR / "kline_daily/" 目录下的 kline_daily_YYYY.csv（分年文件，新格式）
    惰性加载 + 内存缓存，首次调用约需 2-3 秒

    返回列: code, market, name, date, open, high, low, close, volume, amount
    """
    global _KLINE_DF, _KLINE_LOADED

    if _KLINE_LOADED and not force_reload:
        return _KLINE_DF if _KLINE_DF is not None else pd.DataFrame()

    # 优先使用分年文件目录（新格式）
    split_dir = DATA_RAW_DIR / "kline_daily"
    single_file = DATA_RAW_DIR / "kline_daily.csv"

    col_names = ["code", "market", "name", "date", "open", "high", "low", "close", "volume", "amount"]
    df_parts = []

    if split_dir.is_dir():
        csv_files = sorted(split_dir.glob("kline_daily_*.csv"))
        if not csv_files:
            print(f"[ERROR] kline_daily/ 目录存在但无 CSV 文件: {split_dir}")
            _KLINE_LOADED = True
            return pd.DataFrame()
        print(f"[DATA] 加载日K线（分年文件，共 {len(csv_files)} 个）...")
        for fp in csv_files:
            # 跳过 Git LFS 指针文件（内容以 "version https://git-lfs" 开头）
            if fp.stat().st_size < 1024:
                try:
                    with open(fp) as f:
                        first_line = f.readline()
                        if first_line.startswith("version https://git-lfs"):
                            print(f"  · 跳过 LFS 指针文件: {fp.name}")
                            continue
                except Exception:
                    pass

            size_mb = fp.stat().st_size / 1e6
            print(f"  · 读取 {fp.name} ({size_mb:.1f}MB)...")
            part = pd.read_csv(fp, dtype={
                "code": str, "market": str, "name": str,
                "open": float, "high": float, "low": float, "close": float,
                "volume": float, "amount": float,
            })
            df_parts.append(part)
        df = pd.concat(df_parts, ignore_index=True)
    elif single_file.exists():
        print(f"[DATA] 加载日K线（单文件 {single_file.stat().st_size/1e6:.0f}MB）...")
        # 旧格式无表头，用 names= 指定列名
        df = pd.read_csv(single_file, names=col_names, header=None, dtype={
            "code": str, "market": str, "name": str, "date": str,
            "open": float, "high": float, "low": float, "close": float,
            "volume": float, "amount": float,
        })
    else:
        print(f"[ERROR] kline_daily 数据不存在: 已检查 {split_dir}/ 和 {single_file}")
        _KLINE_LOADED = True
        return pd.DataFrame()

    # 统一后处理
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values(["code", "date"])

    _KLINE_DF = df
    _KLINE_LOADED = True

    codes = df["code"].nunique()
    dmin = df["date"].min().strftime("%Y-%m-%d")
    dmax = df["date"].max().strftime("%Y-%m-%d")
    print(f"[DATA] ✅ K线: {len(df):,} 行, {codes} 只, {dmin} ~ {dmax}")
    return df


# ============================================================
# 实时行情
# ============================================================

def load_quotes(force_reload: bool = False) -> pd.DataFrame:
    """
    加载腾讯行情快照 (tencent_quotes.csv)
    返回列含: code, name, price, mcap_yi, pe_ttm, pb, turnover_pct 等 22 列
    """
    global _QUOTES_DF, _QUOTES_LOADED

    if _QUOTES_LOADED and not force_reload:
        return _QUOTES_DF if _QUOTES_DF is not None else pd.DataFrame()

    # 搜索多个可能的位置
    candidates = [
        DATA_RAW_DIR / "tencent_quotes.csv",
        REFERENCE_DATA_DIR / "tencent_quotes.csv",
        IMPORTED_DATA_DIR / "tencent_quotes.csv",
    ]
    path = None
    for c in candidates:
        if c.exists():
            path = c
            break

    if path is None:
        print(f"[ERROR] tencent_quotes.csv 不存在，已搜索:")
        for c in candidates:
            print(f"         {c}")
        _QUOTES_LOADED = True
        return pd.DataFrame()

    df = pd.read_csv(path)
    df["code"] = df["code"].astype(str).str.zfill(6)

    _QUOTES_DF = df
    _QUOTES_LOADED = True
    print(f"[DATA] ✅ 行情快照: {len(df)} 只 (来源: {path.parent.name}/)")
    return df


# ============================================================
# 财务数据
# ============================================================

def load_finance(force_reload: bool = False) -> pd.DataFrame:
    """
    加载财务快照 (finance_snapshot.csv)
    返回列: code, market, name, date, net_profit, revenue, total_assets,
           total_liabilities, operating_profit, operating_cashflow, roe,
           dividend, equity, growth_revenue, growth_profit, employees,
           type_code, ipo_date
    """
    global _FINANCE_DF, _FINANCE_LOADED

    if _FINANCE_LOADED and not force_reload:
        return _FINANCE_DF if _FINANCE_DF is not None else pd.DataFrame()

    # 搜索多个可能的位置
    candidates = [
        DATA_RAW_DIR / "finance_snapshot.csv",
        IMPORTED_DATA_DIR / "finance_snapshot.csv",
        REFERENCE_DATA_DIR / "finance_snapshot.csv",
    ]
    path = None
    for c in candidates:
        if c.exists():
            path = c
            break

    if path is None:
        print(f"[ERROR] finance_snapshot.csv 不存在，已搜索:")
        for c in candidates:
            print(f"         {c}")
        _FINANCE_LOADED = True
        return pd.DataFrame()

    col_names = [
        "code", "market", "name", "date", "net_profit", "revenue",
        "total_assets", "total_liabilities", "operating_profit",
        "operating_cashflow", "roe", "dividend", "equity",
        "growth_revenue", "growth_profit", "employees", "type_code", "ipo_date"
    ]
    df = pd.read_csv(path, names=col_names, header=None, dtype={
        "code": str, "market": str, "name": str,
    })

    df["code"] = df["code"].astype(str).str.zfill(6)
    # 数值列
    for col in ["net_profit", "revenue", "total_assets", "total_liabilities",
                "operating_profit", "operating_cashflow", "roe", "dividend",
                "equity", "growth_revenue", "growth_profit", "employees"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    _FINANCE_DF = df
    _FINANCE_LOADED = True
    print(f"[DATA] ✅ 财务数据: {len(df)} 只 (来源: {path.parent.name}/)")
    return df


# ============================================================
# 便捷：一次加载全部
# ============================================================

def load_all() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """一次加载 K线 + 行情 + 财务"""
    kline = load_kline()
    quotes = load_quotes()
    finance = load_finance()
    return kline, quotes, finance


# ============================================================
# 构建排除集 + 行情映射
# ============================================================

def build_exclusion_set(quotes: pd.DataFrame, exclude_sectors: list) -> set:
    """
    根据板块关键词和 ST/退市 构建排除代码集
    """
    excluded = set()
    for kw in exclude_sectors:
        excluded.update(quotes[quotes["name"].str.contains(kw, na=False)]["code"].values)
    excluded.update(quotes[quotes["name"].str.contains("ST|退", na=False)]["code"].values)
    return excluded


def build_spot_map(quotes: pd.DataFrame) -> dict:
    """
    构建 code → {name, mcap_yi, pe_ttm, pb, turnover_pct} 字典
    """
    quotes = quotes.set_index("code")
    cols = ["name", "mcap_yi", "pe_ttm", "pb", "turnover_pct"]
    available = [c for c in cols if c in quotes.columns]
    return quotes[available].to_dict("index")


# ============================================================
# 预处理缓存
# ============================================================

def save_precomputed(df: pd.DataFrame, name: str):
    """保存预计算指标到 processed/ 目录"""
    DATA_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_PROCESSED_DIR / f"{name}.parquet"
    df.to_parquet(path, index=False)
    print(f"[CACHE] 已保存: {path}")


def load_precomputed(name: str) -> Optional[pd.DataFrame]:
    """从 processed/ 加载预计算指标"""
    path = DATA_PROCESSED_DIR / f"{name}.parquet"
    if path.exists():
        print(f"[CACHE] 加载缓存: {path}")
        return pd.read_parquet(path)
    return None
