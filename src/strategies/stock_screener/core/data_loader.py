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

from config import DATA_RAW_DIR, DATA_PROCESSED_DIR

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
    加载日K线数据 (kline_daily.csv)
    惰性加载 + 内存缓存，首次调用约需 2-3 秒

    返回列: code, market, name, date, open, high, low, close, volume, amount
    """
    global _KLINE_DF, _KLINE_LOADED

    if _KLINE_LOADED and not force_reload:
        return _KLINE_DF if _KLINE_DF is not None else pd.DataFrame()

    path = DATA_RAW_DIR / "kline_daily.csv"
    if not path.exists():
        print(f"[ERROR] kline_daily.csv 不存在: {path}")
        _KLINE_LOADED = True
        return pd.DataFrame()

    size_mb = path.stat().st_size / 1e6
    print(f"[DATA] 加载日K线 ({size_mb:.0f}MB)...")

    col_names = ["code", "market", "name", "date", "open", "high", "low", "close", "volume", "amount"]
    df = pd.read_csv(path, names=col_names, header=None, dtype={
        "code": str, "market": str, "name": str, "date": str,
        "open": float, "high": float, "low": float, "close": float,
        "volume": float, "amount": float,
    })

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

    path = DATA_RAW_DIR / "tencent_quotes.csv"
    if not path.exists():
        print(f"[ERROR] tencent_quotes.csv 不存在: {path}")
        _QUOTES_LOADED = True
        return pd.DataFrame()

    df = pd.read_csv(path)
    df["code"] = df["code"].astype(str).str.zfill(6)

    _QUOTES_DF = df
    _QUOTES_LOADED = True
    print(f"[DATA] ✅ 行情快照: {len(df)} 只")
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

    path = DATA_RAW_DIR / "finance_snapshot.csv"
    if not path.exists():
        print(f"[ERROR] finance_snapshot.csv 不存在: {path}")
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
    print(f"[DATA] ✅ 财务数据: {len(df)} 只")
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
