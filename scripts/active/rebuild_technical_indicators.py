#!/usr/bin/env python3.11
"""
重新计算技术指标并写入 technical_indicators.csv
基于 kline_daily.csv 全量数据
计算：MACD / RSI(14) / KDJ / BOLL(20)
"""

import pandas as pd
import numpy as np
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
KLINE_CSV = REPO_ROOT / "market_data" / "kline_daily.csv"
OUTPUT_CSV = REPO_ROOT / "market_data" / "technical_indicators.csv"


# ============================================================
# 技术指标计算函数
# ============================================================

def calc_macd(df, fast=12, slow=26, signal=9):
    """计算 MACD: DIF, DEA, HIST"""
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist


def calc_rsi(df, period=14):
    """计算 RSI"""
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_kdj(df, n=9, m1=3, m2=3):
    """计算 KDJ: K, D, J"""
    low_n = df['low'].rolling(window=n).min()
    high_n = df['high'].rolling(window=n).max()
    rsv = (df['close'] - low_n) / (high_n - low_n) * 100

    # 初始值：RSV 的前 m1 个均值
    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def calc_boll(df, period=20, std_dev=2):
    """计算布林带: MID, UPPER, LOWER"""
    mid = df['close'].rolling(window=period).mean()
    std = df['close'].rolling(window=period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return mid, upper, lower


# ============================================================
# 主流程
# ============================================================

def process_stock(code, df_stock):
    """对单只股票计算所有技术指标"""
    if len(df_stock) < 26:  # 至少需要 26 天
        return None

    df_stock = df_stock.sort_values('date').reset_index(drop=True)

    dif, dea, hist = calc_macd(df_stock)
    rsi = calc_rsi(df_stock)
    k, d, j = calc_kdj(df_stock)
    boll_mid, boll_upper, boll_lower = calc_boll(df_stock)

    result = pd.DataFrame({
        'code': code,
        'date': df_stock['date'],
        'macd_dif': dif,
        'macd_dea': dea,
        'macd_hist': hist,
        'rsi14': rsi,
        'kdj_k': k,
        'kdj_d': d,
        'kdj_j': j,
        'boll_mid': boll_mid,
        'boll_upper': boll_upper,
        'boll_lower': boll_lower,
    })

    return result.dropna(subset=['macd_dif'])


def main():
    print("=" * 60)
    print("重新计算技术指标")
    print("=" * 60)

    # 1. 读取 K 线数据
    print("\n[1/4] 读取 K 线数据...")
    df_kline = pd.read_csv(KLINE_CSV, encoding='utf-8-sig')
    print(f"  ✅ {len(df_kline)} 条 K 线记录")
    print(f"  股票数: {df_kline['code'].nunique()}")
    print(f"  日期范围: {df_kline['date'].min()} ~ {df_kline['date'].max()}")

    # 2. 逐只股票计算
    print("\n[2/4] 逐只股票计算技术指标...")
    codes = df_kline['code'].unique()
    total = len(codes)
    print(f"  共 {total} 只股票")

    all_results = []
    start_time = time.time()

    for i, code in enumerate(codes):
        df_stock = df_kline[df_kline['code'] == code].copy()
        result = process_stock(code, df_stock)
        if result is not None:
            all_results.append(result)

        if (i + 1) % 500 == 0:
            elapsed = time.time() - start_time
            print(f"  进度: {i+1}/{total} ({((i+1)/total*100):.1f}%) | 耗时: {elapsed:.1f}s")

    print(f"  ✅ 计算完成，共 {len(all_results)} 只股票有有效数据")

    # 3. 合并
    print("\n[3/4] 合并数据...")
    df_result = pd.concat(all_results, ignore_index=True)
    print(f"  总记录数: {len(df_result)}")

    # 4. 保存
    print(f"\n[4/4] 保存到 {OUTPUT_CSV}...")
    df_result.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
    print(f"  ✅ 保存完成！")
    print(f"  文件大小: {OUTPUT_CSV.stat().st_size / 1024 / 1024:.1f} MB")

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"✅ 技术指标计算完成！")
    print(f"   总耗时: {elapsed:.1f}s ({elapsed/60:.1f}min)")
    print(f"   总记录: {len(df_result)} 条")
    print(f"   股票数: {df_result['code'].nunique()}")
    print(f"   最新日期: {df_result['date'].max()}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    import time
    main()
