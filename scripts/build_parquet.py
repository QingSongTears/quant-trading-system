"""
build_parquet.py — CSV → Parquet 派生层 (借鉴 vnpy AlphaLab)

目标:
  market_data/raw/kline_daily/*.csv  →  market_data/parquet/daily/{vt_symbol}.parquet

vnpy 风格:
  - 每只股票一个 parquet 文件 (按 vt_symbol 切)
  - 列存 + 强类型, 加速读
  - 增量更新: pl.concat + unique + sort (这里是 pandas + pyarrow 等价)

CSV 源 (Tencent 格式):
  code,market,name,date,open,high,low,close,volume,amount
  1,sz,平安银行,2024-01-02,9.39,9.42,9.21,9.21,1158366.0,1075742208.0

输出 parquet 格式:
  symbol,exchange,name,datetime,interval,open_price,high_price,low_price,close_price,
  volume,turnover,open_interest

Usage:
  python scripts/build_parquet.py                    # 重建全部
  python scripts/build_parquet.py --incremental      # 增量 (只补缺失)
  python scripts/build_parquet.py --data-dir PATH    # 指定 CSV 源
  python scripts/build_parquet.py --out-dir PATH     # 指定输出
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd

# 允许脚本独立运行
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("build_parquet")


# ── 常量 ───────────────────────────────────────

MARKET_MAP = {"sz": "SZ", "sh": "SH", "bj": "BJ"}


def code_to_vt_symbol(code: int | str) -> str:
    """
    000001 → '000001.SZ' / 600519 → '600519.SH'
    """
    code = str(code).zfill(6)
    if code.startswith(("4", "8")):
        exchange = "BJ"
    elif code.startswith("6"):
        exchange = "SH"
    else:
        exchange = "SZ"
    return f"{code}.{exchange}"


# ── 转换逻辑 ──────────────────────────────────


def transform_csv_to_df(csv_path: Path) -> pd.DataFrame:
    """
    加载 Tencent 格式 CSV, 转 vnpy 风格的 BarData 字段

    Returns:
        DataFrame with columns:
          symbol, exchange, name, datetime, interval, open_price, high_price,
          low_price, close_price, volume, turnover, open_interest
    """
    df = pd.read_csv(csv_path, dtype={"code": str})
    log.info(f"  读取 {csv_path.name}: {len(df):,} 行")

    # code 转 6 位字符串
    df["symbol"] = df["code"].str.zfill(6)

    # market 转 exchange
    df["exchange"] = df["market"].map(MARKET_MAP).fillna("OTHER")

    # date 转 datetime (兼容字符串/Timestamp)
    df["datetime"] = pd.to_datetime(df["date"])

    # 字段重命名 + 选列
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


def write_parquet_per_symbol(df: pd.DataFrame, out_dir: Path) -> int:
    """
    按 vt_symbol 切分, 写每只股票一个 parquet 文件

    Returns: 写入文件数
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # vt_symbol = symbol.exchange
    df["vt_symbol"] = df["symbol"] + "." + df["exchange"]

    written = 0
    for vt_symbol, grp in df.groupby("vt_symbol"):
        path = out_dir / f"{vt_symbol}.parquet"
        # 排序 + 去重
        grp_sorted = (
            grp.drop(columns=["vt_symbol"])
               .sort_values("datetime")
               .drop_duplicates(subset=["datetime"], keep="last")
               .reset_index(drop=True)
        )
        grp_sorted.to_parquet(path, engine="pyarrow", index=False)
        written += 1

    return written


# ── 主流程 ────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="CSV (Tencent 格式) → Parquet 派生 (vnpy 风格)"
    )
    parser.add_argument(
        "--data-dir",
        default=str(PROJECT_ROOT / "market_data" / "raw" / "kline_daily"),
        help="CSV 源目录 (默认: market_data/raw/kline_daily)",
    )
    parser.add_argument(
        "--out-dir",
        default=str(PROJECT_ROOT / "market_data" / "parquet" / "daily"),
        help="Parquet 输出目录 (默认: market_data/parquet/daily)",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="增量模式: 跳过已存在的 parquet (只补缺失)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)

    log.info("=" * 70)
    log.info(f"CSV → Parquet 构建器")
    log.info(f"  CSV 源: {data_dir}")
    log.info(f"  输出:   {out_dir}")
    log.info(f"  增量:   {args.incremental}")
    log.info("=" * 70)

    csv_files = sorted(data_dir.glob("kline_daily_*.csv"))
    if not csv_files:
        log.error(f"未找到 kline_daily_*.csv 文件 in {data_dir}")
        sys.exit(1)

    log.info(f"发现 {len(csv_files)} 个 CSV: {[f.name for f in csv_files]}")

    t0 = time.time()
    all_df = pd.concat(
        [transform_csv_to_df(f) for f in csv_files],
        ignore_index=True,
    )
    log.info(f"合并后: {len(all_df):,} 行, {all_df['vt_symbol' if 'vt_symbol' in all_df.columns else 'symbol'].nunique() if 'vt_symbol' in all_df.columns else all_df['symbol'].nunique()} 只股票")
    log.info(f"  日期范围: {all_df['datetime'].min()} ~ {all_df['datetime'].max()}")
    log.info(f"  转换耗时: {time.time()-t0:.1f}s")

    # vt_symbol 列
    all_df["vt_symbol"] = all_df["symbol"] + "." + all_df["exchange"]

    # 增量模式: 跳过已存在的 parquet
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.incremental:
        existing = {p.stem for p in out_dir.glob("*.parquet")}
        before = all_df["vt_symbol"].nunique()
        all_df = all_df[~all_df["vt_symbol"].isin(existing)]
        after = all_df["vt_symbol"].nunique()
        log.info(f"增量模式: 已存在 {before - after} 只, 补 {after} 只")

    if all_df.empty:
        log.info("无新增数据, 退出")
        return

    # 写入
    t1 = time.time()
    written = write_parquet_per_symbol(all_df, out_dir)
    elapsed = time.time() - t1
    log.info(f"写入 {written:,} 个 parquet, 耗时 {elapsed:.1f}s")

    # 总览
    total_size = sum(p.stat().st_size for p in out_dir.glob("*.parquet"))
    log.info(f"输出目录总大小: {total_size/1024/1024:.1f} MB")
    log.info(f"完成! 总耗时: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()