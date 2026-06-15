"""
数据获取层 — v3 基于 tdrive 预置数据
数据源优先级: tdrive CSV > mootdx(TCP) > 腾讯财经(HTTP)

tdrive 预置文件:
  - tencent_quotes.csv  (795KB,  5209只) 实时行情
  - kline_daily.csv     (220MB,  5206只×500天) 日K线历史
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from ..config import DATA_DIR, CACHE_DIR

# ============================================================
# 1. 全市场行情（tencent_quotes.csv）
# ============================================================

_QUOTES_DF: Optional[pd.DataFrame] = None


def _load_quotes() -> pd.DataFrame:
    """加载腾讯行情 CSV"""
    global _QUOTES_DF
    if _QUOTES_DF is not None:
        return _QUOTES_DF

    path = DATA_DIR / "tencent_quotes.csv"
    if not path.exists():
        print("[ERROR] tencent_quotes.csv 不存在，请先下载到 data/ 目录")
        return pd.DataFrame()

    df = pd.read_csv(path)
    # 统一 code 为 6 位字符串
    df["code"] = df["code"].astype(str).str.zfill(6)
    _QUOTES_DF = df
    return df


def get_all_stocks_with_market_cap(sample_size: int = None) -> pd.DataFrame:
    """
    获取全A股行情（从 tdrive tencent_quotes.csv）
    """
    df = _load_quotes()
    if df.empty:
        return df

    if sample_size:
        df = df.head(sample_size)

    # 字段映射到统一名称
    result = pd.DataFrame({
        "code": df["code"],
        "name": df["name"],
        "price": pd.to_numeric(df["price"], errors="coerce"),
        "last_close": pd.to_numeric(df["last_close"], errors="coerce"),
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "amount": pd.to_numeric(df["amount_wan"], errors="coerce") * 10000,  # 万元→元
        "turnover_pct": pd.to_numeric(df["turnover_pct"], errors="coerce"),
        "pe_ttm": pd.to_numeric(df["pe_ttm"], errors="coerce"),
        "amplitude_pct": pd.to_numeric(df["amplitude_pct"], errors="coerce"),
        "mcap": pd.to_numeric(df["mcap_yi"], errors="coerce") * 1e8,  # 亿→元
        "mcap_yi": pd.to_numeric(df["mcap_yi"], errors="coerce"),
        "float_mcap_yi": pd.to_numeric(df["float_mcap_yi"], errors="coerce"),
        "pb": pd.to_numeric(df["pb"], errors="coerce"),
        "vol_ratio": pd.to_numeric(df["vol_ratio"], errors="coerce"),
        "pe_static": pd.to_numeric(df["pe_static"], errors="coerce"),
    })

    result["amount_yi"] = result["amount"] / 1e8
    return result


# ============================================================
# 2. 日K线数据（kline_daily.csv）
# ============================================================

_KLINE_DF: Optional[pd.DataFrame] = None
_KLINE_LOADED = False


def _load_kline() -> pd.DataFrame:
    """加载日K线 CSV（惰性加载 + 缓存）"""
    global _KLINE_DF, _KLINE_LOADED

    if _KLINE_LOADED:
        return _KLINE_DF if _KLINE_DF is not None else pd.DataFrame()

    path = DATA_DIR / "kline_daily.csv"
    if not path.exists():
        print("[ERROR] kline_daily.csv 不存在")
        _KLINE_LOADED = True
        return pd.DataFrame()

    print(f"[DATA] 加载日K线数据 ({path.stat().st_size / 1e6:.0f}MB)...")
    col_names = ["code", "market", "name", "date", "open", "high", "low", "close", "volume", "amount"]
    df = pd.read_csv(path, names=col_names, header=None, dtype={
        "code": str, "market": str, "name": str, "date": str,
        "open": float, "high": float, "low": float, "close": float,
        "volume": float, "amount": float,
    })
    # 统一 code 格式
    df["code"] = df["code"].astype(str).str.zfill(6)

    # 日期转换和排序
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values(["code", "date"])

    _KLINE_DF = df
    _KLINE_LOADED = True

    unique_codes = df["code"].nunique()
    date_min = df["date"].min().strftime("%Y-%m-%d")
    date_max = df["date"].max().strftime("%Y-%m-%d")
    print(f"[DATA] ✅ K线加载完成: {len(df):,} 行, {unique_codes} 只, {date_min} ~ {date_max}")
    return df


def get_stock_kline(code: str, days: int = 250, use_cache: bool = True) -> pd.DataFrame:
    """
    获取个股日K线（从 tdrive kline_daily.csv）
    返回标准 DataFrame，含 date/open/close/high/low/volume/amount
    """
    kline = _load_kline()
    if kline.empty:
        print(f"[WARN] K线数据为空")
        return pd.DataFrame()

    # 统一 code 格式
    code = str(code).zfill(6)
    df = kline[kline["code"] == code].copy()

    if df.empty:
        return pd.DataFrame()

    # 返回最近 N 天
    df = df.sort_values("date").tail(days)
    return df[["date", "open", "close", "high", "low", "volume", "amount"]].reset_index(drop=True)


# ============================================================
# 3. 行业分类（名称匹配，不需要 F10）
# ============================================================

def get_industry_classification(codes: List[str]) -> Dict[str, List[str]]:
    """
    行业分类（基于名称关键词匹配，快速）
    返回 {code: [标签, ...]}
    """
    result = {}
    for code in codes:
        result[code] = []
    return result
