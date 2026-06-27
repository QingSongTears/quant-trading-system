#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
修复 kline_daily_2026.csv 的 code 格式
======================================

现象: code 列是整数 1/2/4, 应该和 fund_flow 一致用 6位字符串 (000001)
修复: 利用 market 字段, code='000001' + market='sz' → 替换为 '000001'

不动原始文件, 写一个 normalized 版本到 raw/kline_daily/kline_daily_2026_normalized.csv
"""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "market_data" / "raw" / "kline_daily" / "kline_daily_2026.csv"
DST = ROOT / "market_data" / "raw" / "kline_daily" / "kline_daily_2026_normalized.csv"


def fix_code(row):
    code = str(row["code"])
    market = str(row["market"]).lower()
    if code.isdigit() and len(market) in (2, 3):
        return f"{market}{code.zfill(6)}"
    return code


def main():
    print(f"读取: {SRC}")
    df = pd.read_csv(SRC, encoding="utf-8-sig", low_memory=False)
    print(f"  总行数: {len(df):,}")
    print(f"  原 code 前 5 个示例: {df['code'].head(5).tolist()}")
    df["code_new"] = df.apply(fix_code, axis=1)
    print(f"  新 code 前 5 个示例: {df['code_new'].head(5).tolist()}")
    n_unique = df["code_new"].nunique()
    print(f"  唯一 code 数: {n_unique:,}")
    df["code"] = df["code_new"]
    df = df.drop(columns=["code_new"])
    cols = ["code", "market", "name", "date", "open", "high", "low", "close", "volume", "amount"]
    df = df[cols]
    df.to_csv(DST, index=False, encoding="utf-8-sig")
    print(f"✅ 已保存: {DST}")
    print(f"  总行数: {len(df):,}")
    print(f"  日期范围: {df['date'].min()} ~ {df['date'].max()}")


if __name__ == "__main__":
    main()