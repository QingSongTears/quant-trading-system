"""
翻倍股研究 — 阶段1：找出近两年所有翻倍案例
扫描 tdrive K线数据，找出所有 120 个交易日内涨幅 ≥ 100% 的案例
"""
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from collections import defaultdict

from config import DATA_DIR, OUTPUT_DIR


def find_all_doublers(
    window_days: int = 120,       # 查找窗口（交易日）
    min_gain_pct: float = 100.0,  # 最低涨幅 %
    min_data_days: int = 180,     # 股票最少需要多少K线
) -> pd.DataFrame:
    """
    主函数：扫描全市场，找出所有翻倍案例
    
    算法：对每只股票，滑动窗口检查是否有区间内涨幅≥min_gain_pct
    """
    print("=" * 60)
    print(f"🔍 扫描翻倍股: 窗口={window_days}天, 涨幅≥{min_gain_pct}%")
    print("=" * 60)

    # 加载数据
    kline = _load_kline()
    if kline.empty:
        return pd.DataFrame()

    print(f"[DATA] {kline['code'].nunique()} 只股票, {len(kline):,} 行")

    # 按股票分组扫描
    all_cases = []
    total_codes = kline["code"].nunique()

    for i, (code, group) in enumerate(kline.groupby("code")):
        if i % 500 == 0:
            print(f"[SCAN] {i}/{total_codes}... 已发现 {len(all_cases)} 例")

        group = group.sort_values("date").reset_index(drop=True)

        if len(group) < min_data_days:
            continue

        closes = group["close"].values
        dates = group["date"].values
        name = group["name"].iloc[0]

        # 滑动窗口扫描
        found_ranges = _find_run_up_ranges(closes, dates, window_days, min_gain_pct, code, name)
        all_cases.extend(found_ranges)

    df = pd.DataFrame(all_cases)
    if not df.empty:
        df = df.sort_values("start_date")

    print(f"\n[RESULT] 共发现 {len(df)} 例翻倍案例")
    print(f"         涉及 {df['code'].nunique()} 只股票")
    if not df.empty:
        print(f"         涨幅范围: {df['gain_pct'].min():.0f}% ~ {df['gain_pct'].max():.0f}%")
        print(f"         耗时范围: {df['days'].min():.0f} ~ {df['days'].max():.0f} 个交易日")
        print(f"         最早的: {df['start_date'].min()}")
        print(f"         最新的: {df['start_date'].max()}")

    return df


def _load_kline() -> pd.DataFrame:
    """加载K线"""
    path = DATA_DIR / "kline_daily.csv"
    if not path.exists():
        print(f"[ERROR] {path} 不存在")
        return pd.DataFrame()

    col_names = ["code", "market", "name", "date", "open", "high", "low", "close", "volume", "amount"]
    df = pd.read_csv(
        path, names=col_names, header=None,
        dtype={"code": str, "market": str, "name": str, "date": str,
               "open": float, "high": float, "low": float, "close": float,
               "volume": float, "amount": float},
    )
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values(["code", "date"])
    return df


def _find_run_up_ranges(
    closes: np.ndarray,
    dates: np.ndarray,
    window: int,
    min_gain: float,
    code: str,
    name: str,
) -> List[Dict]:
    """
    在单只股票的K线中找出所有翻倍波段
    使用滑动窗口 + 记录非重叠的翻倍区间
    """
    cases = []
    n = len(closes)
    min_gain_ratio = 1 + min_gain / 100  # 2.0 表示翻倍

    # 已覆盖的区间（避免重复）
    covered_until = 0

    for start in range(0, n - 10):
        if start < covered_until:
            continue

        # 窗口内找最大涨幅
        end_limit = min(start + window, n)
        start_price = closes[start]

        # 找窗口内的最低点和最高点
        segment = closes[start:end_limit]
        min_idx_local = np.argmin(segment)
        max_idx_local = np.argmax(segment)

        # 低点必须在前面
        if max_idx_local > min_idx_local:
            low_price = segment[min_idx_local]
            high_price = segment[max_idx_local]
            gain = high_price / low_price

            if gain >= min_gain_ratio:
                actual_start = start + int(min_idx_local)
                actual_end = start + int(max_idx_local)
                actual_days = actual_end - actual_start

                cases.append({
                    "code": code,
                    "name": name,
                    "start_date": pd.Timestamp(dates[actual_start]).strftime("%Y-%m-%d"),
                    "end_date": pd.Timestamp(dates[actual_end]).strftime("%Y-%m-%d"),
                    "start_price": round(float(low_price), 2),
                    "end_price": round(float(high_price), 2),
                    "gain_pct": round((gain - 1) * 100, 1),
                    "days": int(actual_days),
                    "daily_return_pct": round((gain ** (1/actual_days) - 1) * 100, 2),
                })

                # 跳到翻倍结束点之后
                covered_until = actual_end + 1

    return cases


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    df = find_all_doublers()
    if not df.empty:
        output_path = OUTPUT_DIR / "doubler_cases.csv"
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"\n💾 已保存: {output_path}")
