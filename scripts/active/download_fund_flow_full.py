"""
资金面全量数据下载 — 东方财富 push2his API
=========================================
为全市场 ~5000 只 A 股下载主力资金流向日线数据。
并发下载, 断点续传, 批量写入 SQLite。

API: push2his.eastmoney.com/api/qt/stock/fflow/daykline/get
输出: quant.db → fund_flow 表 (INSERT OR IGNORE)
"""

import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "database" / "quant.db"
DATA_DIR = PROJECT_ROOT / "data"

# ── 配置 ──
MAX_WORKERS = 8          # 并发线程数
BATCH_SIZE = 50          # 每批 DB 写入条数
DAYS_LOOKBACK = 120      # 下载近120个交易日
RATE_LIMIT = 0.15        # 请求间隔(秒)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
})


def get_all_codes(conn) -> list:
    """获取所有A股代码"""
    cursor = conn.cursor()
    codes = []
    for row in cursor.execute(
        "SELECT DISTINCT code FROM stock_basic WHERE code NOT LIKE '8%' AND code NOT LIKE '9%'"
    ):
        codes.append(row[0])
    return codes


def get_existing_codes(conn) -> set:
    """获取已有资金面数据的股票"""
    cursor = conn.cursor()
    existing = set()
    for row in cursor.execute("SELECT DISTINCT code FROM fund_flow"):
        existing.add(row[0])
    return existing


def download_fund_flow(code: str, market: int) -> list:
    """
    下载单只股票资金流向

    Returns:
        [(code, market, name, date, main_net, super_large_net, large_net, medium_net, small_net), ...]
    """
    secid = f"{market}.{code}"
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        f"?secid={secid}"
        f"&fields1=f1,f2,f3,f7"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
        f"&lmt={DAYS_LOOKBACK}"
        f"&klt=101"
    )

    try:
        resp = session.get(url, timeout=15)
        data = resp.json()
        if data is None or data.get("data") is None:
            return []
        klines = data["data"].get("klines") or []
        name = data["data"].get("name", "")

        records = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            # parts[0]=日期, [1]=主力净流入, [2]=小单净流入, [3]=中单净流入, [4]=大单净流入, [5]=超大单净流入
            records.append((
                code,
                "sh" if market == 1 else "sz",
                name,
                parts[0],
                float(parts[1]) if parts[1] != "-" else 0,  # main_net
                float(parts[5]) if parts[5] != "-" else 0,  # super_large_net
                float(parts[4]) if parts[4] != "-" else 0,  # large_net
                float(parts[3]) if parts[3] != "-" else 0,  # medium_net
                float(parts[2]) if parts[2] != "-" else 0,  # small_net
            ))
        return records
    except Exception as e:
        # 静默处理
        return []


def determine_market(code: str) -> int:
    """判断交易所: 0=深交所, 1=上交所"""
    if code.startswith(("6", "9")):
        return 1  # 上海
    return 0  # 深圳


def main():
    print("资金面全量数据下载 (push2his API)")
    print(f"DB: {DB_PATH}")
    print(f"线程: {MAX_WORKERS}, 回看: {DAYS_LOOKBACK}天\n")

    conn = sqlite3.connect(str(DB_PATH))

    all_codes = get_all_codes(conn)
    existing = get_existing_codes(conn)
    todo_codes = [c for c in all_codes if c not in existing]

    print(f"全市场: {len(all_codes)} 只")
    print(f"已有数据: {len(existing)} 只")
    print(f"待下载: {len(todo_codes)} 只\n")

    if not todo_codes:
        print("✅ 数据已完整, 无需下载")
        conn.close()
        return

    total_inserted = 0
    batch_buffer = []
    downloaded = 0
    failed = 0

    start_time = time.time()
    cursor = conn.cursor()

    # 确保表存在
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fund_flow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            market TEXT,
            name TEXT,
            date DATETIME,
            main_net REAL DEFAULT 0,
            super_large_net REAL DEFAULT 0,
            large_net REAL DEFAULT 0,
            medium_net REAL DEFAULT 0,
            small_net REAL DEFAULT 0,
            UNIQUE(code, date)
        )
    """)
    conn.commit()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for code in todo_codes:
            market = determine_market(code)
            futures[executor.submit(download_fund_flow, code, market)] = code
            time.sleep(RATE_LIMIT / MAX_WORKERS)  # 限速

        for future in as_completed(futures):
            code = futures[future]
            try:
                records = future.result()
                if records:
                    batch_buffer.extend(records)
                    downloaded += 1
                    total_inserted += len(records)
                else:
                    failed += 1
            except Exception:
                failed += 1

            # 批量写入
            if len(batch_buffer) >= BATCH_SIZE * 50:
                cursor.executemany(
                    """INSERT OR IGNORE INTO fund_flow
                       (code, market, name, date, main_net, super_large_net, large_net, medium_net, small_net)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    batch_buffer,
                )
                conn.commit()
                batch_buffer.clear()

            # 进度
            done = downloaded + failed
            if done % 100 == 0 or done == len(todo_codes):
                elapsed = time.time() - start_time
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(todo_codes) - done) / rate if rate > 0 else 0
                print(f"  进度: {done}/{len(todo_codes)} "
                      f"({done/len(todo_codes)*100:.1f}%) "
                      f"OK={downloaded} FAIL={failed} "
                      f"速率={rate:.1f}/s "
                      f"ETA={eta:.0f}s")

    # 写入剩余
    if batch_buffer:
        cursor.executemany(
            """INSERT OR IGNORE INTO fund_flow
               (code, market, name, date, main_net, super_large_net, large_net, medium_net, small_net)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            batch_buffer,
        )
        conn.commit()

    elapsed = time.time() - start_time

    # 统计
    final_count = conn.execute("SELECT COUNT(*) FROM fund_flow").fetchone()[0]
    final_stocks = conn.execute("SELECT COUNT(DISTINCT code) FROM fund_flow").fetchone()[0]

    print(f"\n{'='*60}")
    print(f"下载完成!")
    print(f"  耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"  成功: {downloaded} 只, 失败: {failed} 只")
    print(f"  总行数: {final_count}, 股票数: {final_stocks}")
    print(f"  速率: {downloaded/elapsed:.1f} stocks/s")

    # 导出CSV
    csv_path = DATA_DIR / "fund_flow_full.csv"
    df = pd.read_sql("SELECT * FROM fund_flow ORDER BY code, date", conn)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"  CSV: {csv_path} ({len(df)} rows)")

    conn.close()


if __name__ == "__main__":
    main()
