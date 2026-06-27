#!/usr/bin/env python3
"""
fill_recent_klines.py — 用 westock kline 批量补抓近期缺失的日线

策略
----
  1. westock kline 支持逗号批量 (BATCH=50 只/次)
     5209 只 / 50 = 105 次 npx 调用 × 5s = 8 分钟/天
  2. 增量: 先查 DB 已有 (code, trade_date), 已存在直接跳过
  3. 双层白名单: 入参 regex 校验 + CREATE_NO_WINDOW 防 cmd 弹窗

为什么不用 build_db.py:
  - build_db.py 是从 CSV 重建, 不增量补具体日期
  - 这里专门补 2 天行情, 用 westock 批量实时拉

调用:
  python scripts/fill_recent_klines.py --date 2026-06-22 --date 2026-06-23
  python scripts/fill_recent_klines.py --start-index 2801   # 跳过前 2800 只 (已跑过)
"""
from __future__ import annotations
import argparse
import csv
import logging
import re
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

DEFAULT_DATES = ["2026-06-22", "2026-06-23"]
_BATCH = 50  # westock 批量每批多少只 (运行时可被 --batch 覆盖)
BATCH = _BATCH
_SYMBOLS_RE = re.compile(r"^[a-z]{2}\d{6}(,[a-z]{2}\d{6})*$")

t0 = time.time()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("fill_recent")


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


def fetch_batch_kline(ws_codes: list[str], date: str) -> list[dict]:
    """westock kline 批量 (逗号分隔), 拉指定日期

    ws_codes: ['sh600000', 'sh600519', ...]
    date: 目标日期 (YYYY-MM-DD)
    """
    joined = ",".join(ws_codes)
    if not _SYMBOLS_RE.match(joined):
        raise ValueError(f"symbols 格式非法: {joined!r}")
    if len(ws_codes) > BATCH:
        raise ValueError(f"batch 太大 {len(ws_codes)} > {BATCH}")

    # 用 --start --end 精确范围 (而非 --limit, limit 返回"最近 N 天"不是指定日)
    cmd = ["npx.cmd", "-y", "westock-data-clawhub@1.0.4", "kline",
           joined, "--period", "day", "--start", date, "--end", date]
    CREATE_NO_WINDOW = 0x08000000
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, shell=False,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        logger.error("westock kline 超时: %d 只", len(ws_codes))
        return []
    if r.returncode != 0:
        logger.error("westock 失败: %s", r.stderr[:200])
        return []
    return parse_markdown_table(r.stdout)


def main() -> int:
    global BATCH  # 必须放在最前 (line 103 default=BATCH 也算 "use")
    p = argparse.ArgumentParser()
    p.add_argument("--date", action="append", default=None,
                   help="要补的日期 (可多次), 默认 6-22 + 6-23")
    p.add_argument("--start-index", type=int, default=1,
                   help="从第几只开始 (跳过前 N-1 只)")
    p.add_argument("--limit", type=int, default=1, help="westock limit (默认 1 = 只拉当天)")
    p.add_argument("--batch", type=int, default=BATCH, help="westock 批量大小")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    BATCH = args.batch

    dates = args.date or DEFAULT_DATES
    if not DB_PATH.exists():
        return 1

    dates = args.date or DEFAULT_DATES
    if not DB_PATH.exists():
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 1. 拿所有 code+market
    pairs = load_tradable_codes()
    logger.info("总股票 %d 只 (从 index %d 开始)", len(pairs), args.start_index)

    if args.start_index > 1:
        pairs = pairs[args.start_index - 1:]
        logger.info("   跳过前 %d 只, 剩 %d 只", args.start_index - 1, len(pairs))

    if args.dry_run:
        logger.info("[dry-run] 日期: %s, 待拉 %d 只, 批数 %d",
                    dates, len(pairs), (len(pairs) + BATCH - 1) // BATCH)
        conn.close()
        return 0

    # 2. 对每一天批量拉
    for date in dates:
        # 预查 DB 已有的
        cur.execute("SELECT code FROM daily_price WHERE trade_date = ?", (date,))
        already = {r[0] for r in cur.fetchall()}
        logger.info("[%s] DB 已有 %d 只", date, len(already))

        # 分批
        n_batches = (len(pairs) + BATCH - 1) // BATCH
        all_rows = []
        n_skipped = 0
        t1 = time.time()
        for bi in range(n_batches):
            batch_pairs = pairs[bi * BATCH: (bi + 1) * BATCH]
            # 跳过 already
            todo = [(c, m) for c, m in batch_pairs if c not in already]
            if not todo:
                n_skipped += len(batch_pairs)
                continue
            ws_codes = [f"{m}{c}" for c, m in todo]
            rows = fetch_batch_kline(ws_codes, date=date)
            # 过滤出 date 匹配的行
            for row in rows:
                if row.get("date") == date:
                    row["code"] = row.pop("symbol")  # 改名 code
                    row["code6"] = row["code"][2:]  # sh600000 → 600000
                    row["market"] = row["code"][:2].upper()
                    all_rows.append(row)
            if (bi + 1) % 10 == 0 or bi == n_batches - 1:
                elapsed = time.time() - t1
                rate = (bi + 1) / elapsed if elapsed else 0
                eta = (n_batches - bi - 1) / rate if rate else 0
                logger.info("   %s 进度 %d/%d 批 累计 %d 行  %.1f 批/s  ETA %.1fmin",
                            date, bi + 1, n_batches, len(all_rows), rate, eta / 60)

        logger.info("[%s] 拉取 %d 行 (跳过 already_have %d, 本次新增 %d), 耗时 %.1fs",
                    date, len(all_rows), n_skipped, len(all_rows), time.time() - t1)

        # 3. INSERT daily_price
        if all_rows:
            inserted = 0
            for row in all_rows:
                cur.execute("""
                    INSERT OR IGNORE INTO daily_price
                    (code, trade_date, open, high, low, close, volume, amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row["code6"], date,
                    _f(row.get("open")), _f(row.get("high")),
                    _f(row.get("low")), _f(row.get("close")),
                    _i(row.get("volume")), _f(row.get("amount")),
                ))
                if cur.rowcount > 0:
                    inserted += 1
            conn.commit()
            logger.info("[%s] INSERT daily_price: %d 行 (UNIQUE 跳过 %d)",
                        date, inserted, len(all_rows) - inserted)
        else:
            logger.warning("[%s] 无数据可插入", date)

    # 4. 校验
    cur.execute("SELECT trade_date, COUNT(DISTINCT code) FROM daily_price WHERE trade_date IN ({}) GROUP BY trade_date".format(
        ",".join("?" for _ in dates)
    ), dates)
    for r in cur.fetchall():
        logger.info("[校验] daily_price %s: %d unique codes", r[0], r[1])
    cur.execute("SELECT MAX(trade_date), COUNT(*) FROM daily_price")
    mx, tot = cur.fetchone()
    logger.info("[校验] daily_price max=%s 总=%s", mx, tot)

    conn.close()
    logger.info("✅ 完成 (耗时 %.1fs)", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())