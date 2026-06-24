#!/usr/bin/env python3
"""
fill_recent_klines.py — 用 westock 补抓 2026-06-22/06-23 两天的 daily_price 数据

背景:
  - CSV 快照生成日 2026-06-22, 但行情数据只到 2026-06-18
  - 缺 2026-06-22 (周一) 和 2026-06-23 (周二) 两天 (端午节 6-19 休市)
  - westock kline 实时拉能拿到 (已验证)

为什么不用 build_db.py:
  - build_db.py 是从 CSV 重建, 不增量补具体日期
  - 这里专门补 2 天行情, 用 westock CLI 单股调用更快

调用:
  python scripts/fill_recent_klines.py
"""
from __future__ import annotations
import csv
import json
import logging
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from import_from_westock_baostock_akshare import (
    parse_markdown_table, load_tradable_codes, to_westock,
)

DB_PATH = PROJECT_ROOT / "database" / "quant.db"
TQ_CSV = PROJECT_ROOT / "market_data" / "raw" / "reference" / "tencent_quotes.csv"

DATES_TO_FETCH = ["2026-06-22", "2026-06-23"]

t0 = time.time()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("fill_recent")


def fetch_day_klines(date: str, codes6_market: list[tuple[str, str]]) -> list[dict]:
    """westock kline 单股调用拉一天, 自动跳过无数据的"""
    import time as _t
    rows_all = []
    # Windows CREATE_NO_WINDOW (0x08000000) 防止 npx.cmd 弹黑色 cmd 窗口
    CREATE_NO_WINDOW = 0x08000000
    for i, (code6, mkt) in enumerate(codes6_market):
        ws = f"{mkt}{code6}"
        try:
            r = subprocess.run(
                ["npx.cmd", "-y", "westock-data-clawhub@1.0.4",
                 "kline", ws, "--period", "day", "--start", date, "--end", date],
                capture_output=True, text=True, timeout=30, shell=False,
                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except subprocess.TimeoutExpired:
            continue
        if r.returncode != 0:
            continue
        parsed = parse_markdown_table(r.stdout)
        for row in parsed:
            row["code6"] = code6
            row["market"] = mkt
            row["trade_date"] = date
            rows_all.append(row)
        if (i + 1) % 200 == 0:
            elapsed = _t.time() - t0
            rate = (i + 1) / elapsed if elapsed else 0
            eta = (len(codes6_market) - i - 1) / rate if rate else 0
            logger.info("   %s 进度 %d/%d 累计 %d  %.1f 股/s  ETA %.1fmin",
                        date, i + 1, len(codes6_market), len(rows_all), rate, eta / 60)
    return rows_all


def main() -> int:
    if not DB_PATH.exists():
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 1. 拿所有 6 位 + 市场
    pairs = load_tradable_codes()
    logger.info("总股票 %d 只", len(pairs))

    # 2. 对每一天拉数据
    for date in DATES_TO_FETCH:
        # 检查是否已经 import 过 (UNIQUE 防重复)
        cur.execute("SELECT COUNT(*) FROM daily_price WHERE trade_date = ?", (date,))
        existing = cur.fetchone()[0]
        if existing > 0:
            logger.info("[%s] DB 已存在 %d 行, 跳过", date, existing)
            continue

        logger.info("[%s] westock kline 拉全市场 %d 只", date, len(pairs))
        rows = fetch_day_klines(date, pairs)

        if not rows:
            logger.warning("[%s] westock 返回空 (可能非交易日)", date)
            continue

        # 3. INSERT daily_price
        inserted = 0
        for row in rows:
            code6 = row.get("code6")
            if not code6 or not row.get("trade_date"):
                continue
            try:
                cur.execute("""
                    INSERT OR IGNORE INTO daily_price
                    (code, trade_date, open, high, low, close, volume, amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    code6, row["trade_date"],
                    _f(row.get("open")), _f(row.get("high")),
                    _f(row.get("low")), _f(row.get("close")),
                    _i(row.get("volume")), _f(row.get("amount")),
                ))
                if cur.rowcount > 0:
                    inserted += 1
            except Exception as e:
                logger.warning("INSERT 失败 %s: %s", code6, e)
        conn.commit()
        logger.info("[%s] INSERT daily_price: %d 行", date, inserted)

    # 4. 校验
    cur.execute("SELECT MAX(trade_date), COUNT(*) FROM daily_price")
    max_date, total = cur.fetchone()
    logger.info("[校验] daily_price max(trade_date)=%s 总 %s 行", max_date, total)

    conn.close()
    logger.info("✅ 完成 (耗时 %.1fs)", time.time() - t0)
    return 0


def _f(s):
    if not s or s == "nan" or s == "-":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _i(s):
    f = _f(s)
    return int(f) if f is not None else None


if __name__ == "__main__":
    sys.exit(main())