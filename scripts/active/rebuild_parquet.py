#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
rebuild_parquet.py — 从 CSV 重建 Parquet 派生层

CSV K线数据更新到 06-24 后，parquet 文件停在 06-23，需重建。
使用 build_parquet.py 的 transform 逻辑，但支持增量模式。

用法:
  python scripts/rebuild_parquet.py                              # 增量重建(默认)
  python scripts/rebuild_parquet.py --full                       # 全量重建
  python scripts/rebuild_parquet.py --dry-run                    # 预览
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "market_data" / "raw" / "kline_daily"
OUT_DIR = ROOT / "market_data" / "parquet" / "daily"

MARKET_MAP = {"sz": "SZ", "sh": "SH", "bj": "BJ"}


def transform_csv_to_df(csv_path: Path) -> pd.DataFrame:
    """CSV → DataFrame (vnpy 风格)"""
    df = pd.read_csv(csv_path, dtype={"code": str})
    print(f"  读取 {csv_path.name}: {len(df):,} 行")

    df["symbol"] = df["code"].str.zfill(6)
    df["exchange"] = df["market"].map(MARKET_MAP).fillna("OTHER")
    df["datetime"] = pd.to_datetime(df["date"])

    out = pd.DataFrame({
        "symbol":        df["symbol"],
        "exchange":      df["exchange"],
        "name":          df["name"],
        "datetime":      df["datetime"],
        "interval":      "1d",
        "open_price":    df["open"].astype(float),
        "high_price":    df["high"].astype(float),
        "low_price":     df["low"].astype(float),
        "close_price":   df["close"].astype(float),
        "volume":        df["volume"].astype(float),
        "turnover":      df["amount"].astype(float),
        "open_interest": 0.0,
    })
    return out


def rebuild_incremental() -> int:
    """增量: 只补缺失或更新已有"""
    csv_files = sorted(DATA_DIR.glob("kline_daily_*.csv"))
    if not csv_files:
        print("❌ 未找到 kline_daily_*.csv")
        return 0

    print(f"CSV 文件: {[f.name for f in csv_files]}")

    t0 = time.time()
    all_df = pd.concat(
        [transform_csv_to_df(f) for f in csv_files],
        ignore_index=True,
    )
    all_df["vt_symbol"] = all_df["symbol"] + "." + all_df["exchange"]
    print(f"  合并: {len(all_df):,} 行, {all_df['vt_symbol'].nunique()} 只股票")
    print(f"  日期: {all_df['datetime'].min()} ~ {all_df['datetime'].max()}")

    # 检查已有 parquet
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    existing = {p.stem for p in OUT_DIR.glob("*.parquet")}
    print(f"  已有 parquet: {len(existing)} 只")

    # 增量模式: 已存在的股票也覆盖写 (用最新CSV数据)
    written = 0
    for vt_symbol, grp in all_df.groupby("vt_symbol"):
        path = OUT_DIR / f"{vt_symbol}.parquet"
        grp_sorted = (
            grp.drop(columns=["vt_symbol"])
               .sort_values("datetime")
               .drop_duplicates(subset=["datetime"], keep="last")
               .reset_index(drop=True)
        )
        grp_sorted.to_parquet(path, engine="pyarrow", index=False)
        written += 1

    elapsed = time.time() - t0
    total_mb = sum(p.stat().st_size for p in OUT_DIR.glob("*.parquet")) / 1024 / 1024
    print(f"  ✅ 写入 {written} 个 parquet, 总大小 {total_mb:.1f}MB (耗时 {elapsed:.1f}s)")

    # 清理已退市股票 (CSV 中没有的 parquet)
    csv_symbols = set(all_df["vt_symbol"].unique())
    cleaned = 0
    for p in OUT_DIR.glob("*.parquet"):
        if p.stem not in csv_symbols:
            p.unlink()
            cleaned += 1
    if cleaned:
        print(f"  🧹 清理 {cleaned} 个已退市股票的 parquet")

    return written


def rebuild_full() -> int:
    """全量重建 (先删后建)"""
    csv_files = sorted(DATA_DIR.glob("kline_daily_*.csv"))
    if not csv_files:
        print("❌ 未找到 kline_daily_*.csv")
        return 0

    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 清空
    for p in OUT_DIR.glob("*.parquet"):
        p.unlink()

    all_df = pd.concat(
        [transform_csv_to_df(f) for f in csv_files],
        ignore_index=True,
    )
    all_df["vt_symbol"] = all_df["symbol"] + "." + all_df["exchange"]
    print(f"  合并: {len(all_df):,} 行, {all_df['vt_symbol'].nunique()} 只")

    written = 0
    for vt_symbol, grp in all_df.groupby("vt_symbol"):
        path = OUT_DIR / f"{vt_symbol}.parquet"
        grp_sorted = (
            grp.drop(columns=["vt_symbol"])
               .sort_values("datetime")
               .drop_duplicates(subset=["datetime"], keep="last")
               .reset_index(drop=True)
        )
        grp_sorted.to_parquet(path, engine="pyarrow", index=False)
        written += 1

    elapsed = time.time() - t0
    total_mb = sum(p.stat().st_size for p in OUT_DIR.glob("*.parquet")) / 1024 / 1024
    print(f"  ✅ 写入 {written} 个 parquet, 总大小 {total_mb:.1f}MB (耗时 {elapsed:.1f}s)")
    return written


def dry_run():
    csv_files = sorted(DATA_DIR.glob("kline_daily_*.csv"))
    print(f"CSV 源: {[f.name for f in csv_files]}")
    print(f"输出目录: {OUT_DIR}")
    existing = {p.stem for p in OUT_DIR.glob("*.parquet")} if OUT_DIR.exists() else set()
    print(f"已有 parquet: {len(existing)} 只")

    sample = pd.read_csv(csv_files[0], encoding="utf-8-sig", nrows=3)
    print(f"\n示例 CSV ({csv_files[0].name}):")
    print(f"  cols: {sample.columns.tolist()}")
    print(f"  rows: {len(sample)}")
    print(f"\n将写入: {len(csv_files)} 个 CSV → {OUT_DIR}/*.parquet")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="全量重建(清空后写)")
    parser.add_argument("--dry-run", action="store_true", help="预览")
    args = parser.parse_args()

    if args.dry_run:
        dry_run()
        return 0

    print("=" * 60)
    print(f"Parquet 重建: {'全量' if args.full else '增量'}")
    print(f"  CSV 源: {DATA_DIR}")
    print(f"  输出:   {OUT_DIR}")
    print("=" * 60)

    if args.full:
        n = rebuild_full()
    else:
        n = rebuild_incremental()

    print(f"\n✅ Parquet 重建完成! {n} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
