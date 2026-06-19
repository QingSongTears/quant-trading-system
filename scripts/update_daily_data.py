#!/usr/bin/env python3
"""
日K线增量更新脚本 — 从 AKShare 拉取最新日线数据写入 quant.db
======================================================================
用法:
  python scripts/update_daily_data.py

行为:
  1. 查询 daily_price 最新日期
  2. 从 最新日期+1 到 今天，逐只股票拉取 AKShare 数据
  3. 增量写一个临时 CSV，再 UPSERT 到 daily_price 表
  4. 同步更新 technical_indicators 表（可选）

环境要求:
  - 需要能访问 AKShare 的数据源（东方财富/新浪）— 在有网机器上运行
  - AKShare 安装: pip install akshare
"""

import sys
import os
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

from src.config import get_config, get_db_url


# ===== 配置 =====
REQUEST_INTERVAL = 0.5   # 请求间隔（秒）— 避免触发反爬
LOOKBACK_DAYS = 7     # 如果 DB 最新日期 >7 天前，也只补最近 7 天


def get_latest_date(engine) -> str:
    """获取 daily_price 最新日期"""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT MAX(trade_date) FROM daily_price"))
        latest = result.scalar()
        return latest if latest else None


def update_daily_price(engine, start_date: str, end_date: str) -> int:
    """
    从 AKShare 拉取全市场日线数据，增量写入 daily_price
    返回: 新增/更新行数
    """
    import akshare as ak

    print(f"[增量更新] {start_date} ~ {end_date}")

    # 获取 A 股列表
    print("  获取 A股列表...")
    try:
        stock_list = ak.stock_info_a_code_name()
    except Exception as e:
        print(f"  [ERROR] 获取股票列表失败: {e}")
        return 0

    total_stocks = len(stock_list)
    print(f"  共 {total_stocks} 只 A股")

    new_rows = []
    error_count = 0
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    for idx, (code, name) in enumerate(stock_list.values.tolist()):
        try:
            # AKShare: stock_zh_a_hist
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_dt.strftime("%Y%m%d"),
                end_date=end_dt.strftime("%Y%m%d"),
                adjust="",
            )
            if df.empty:
                continue

            # 标准化列名
            df = df.rename(columns={
                "日期": "trade_date",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
                "成交额": "amount",
                "涨跌幅": "pct_change",
                "换手率": "turnover",
            })

            for _, row in df.iterrows():
                new_rows.append({
                    "code": code.zfill(6),
                    "trade_date": row["trade_date"],
                    "open": float(row["open"]) if pd.notna(row["open"]) else None,
                    "high": float(row["high"]) if pd.notna(row["high"]) else None,
                    "low": float(row["low"]) if pd.notna(row["low"]) else None,
                    "close": float(row["close"]) if pd.notna(row["close"]) else None,
                    "volume": int(float(row["volume"])) if pd.notna(row["volume"]) else None,
                    "amount": float(row["amount"]) if pd.notna(row["amount"]) else None,
                    "pct_change": float(row["pct_change"]) if pd.notna(row["pct_change"]) else None,
                    "turnover": float(row["turnover"]) if pd.notna(row["turnover"]) else None,
                })

            if (idx + 1) % 500 == 0:
                print(f"  进度: {idx+1}/{total_stocks} 只股票, 已缓存 {len(new_rows)} 行")

        except Exception as e:
            error_count += 1
            if error_count <= 10:
                print(f"  [WARN] {code} 拉取失败: {e}")
            if error_count == 10:
                print("  [WARN] 后续错误不再打印...")

        time.sleep(REQUEST_INTERVAL)

    print(f"\n  写入数据库: {len(new_rows)} 行...")
    if not new_rows:
        print("  [INFO] 无新数据")
        return 0

    # 批量 UPSERT
    with engine.connect() as conn:
        table = text(
            "INSERT OR REPLACE INTO daily_price "
            "(code, trade_date, open, high, low, close, volume, amount, pct_change, turnover) "
            "VALUES (:code, :trade_date, :open, :high, :low, :close, :volume, :amount, :pct_change, :turnover)"
        )
        conn.execute(table, new_rows)
        conn.commit()

    print(f"  [OK] 写入完成: {len(new_rows)} 行")
    return len(new_rows)


def main():
    print("=" * 60)
    print("  日 K 线增量更新工具")
    print("=" * 60)

    config = get_config()
    db_url = get_db_url(config)
    engine = create_engine(db_url, echo=False)

    # 查询最新日期
    latest = get_latest_date(engine)
    if latest is None:
        print("[ERROR] daily_price 表为空，请先运行全量导入")
        return

    latest_dt = datetime.strptime(str(latest), "%Y-%m-%d")
    today = datetime.now()

    # 只补最近 LOOKBACK_DAYS 天
    earliest_to_update = today - timedelta(days=LOOKBACK_DAYS)
    if latest_dt < earliest_to_update:
        start_date = earliest_to_update.strftime("%Y-%m-%d")
        print(f"[INFO] DB 最新: {latest}, 将补 {start_date} ~ {today.strftime('%Y-%m-%d')}")
    else:
        start_date = (latest_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"[INFO] DB 最新: {latest}, 将从 {start_date} 开始增量更新")

    end_date = today.strftime("%Y-%m-%d")

    if start_date > end_date:
        print("[INFO] 数据已是最新，无需更新")
        return

    print(f"[INFO] 更新范围: {start_date} ~ {end_date}")
    confirm = input("确认开始更新? [y/N]: ")
    if confirm.lower() != "y":
        print("[INFO] 已取消")
        return

    n = update_daily_price(engine, start_date, end_date)
    print(f"\n✅ 更新完成! 新增/更新 {n} 行")


if __name__ == "__main__":
    main()
