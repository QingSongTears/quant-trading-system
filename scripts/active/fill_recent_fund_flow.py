#!/usr/bin/env python3
"""
fill_recent_fund_flow.py — 用 westock asfund 增量补 fund_flow_data

数据源
------
westock-data asfund <codes> --date YYYY-MM-DD  (BATCH=50, 截面查询)

字段映射 (DB schema: code, trade_date, main_net, super_large_net, large_net,
medium_net, small_net)
  MainNetFlow      → main_net
  JumboNetFlow     → super_large_net
  BlockNetFlow     → large_net (近似)
  MidNetFlow       → medium_net
  SmallNetFlow     → small_net

调用
----
  python scripts/fill_recent_fund_flow.py --dry-run
  python scripts/fill_recent_fund_flow.py --end 2026-06-24
  python scripts/fill_recent_fund_flow.py --start 2026-06-19 --end 2026-06-24
"""
from __future__ import annotations
import argparse
import logging
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# 复用 fill_recent_generic 的工具函数
from fill_recent_generic import (
    load_tradable_codes, to_westock, westock_call,
    parse_markdown_table_simple, _f, BATCH, LOG_PATH as _LOG,
)

DB_PATH = PROJECT_ROOT / "database" / "quant.db"
LOG_PATH = Path(r"C:\Users\admin\AppData\Local\Temp\fill_recent_fund_flow.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

t0 = time.time()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8"),
              logging.StreamHandler()],
)
logger = logging.getLogger("fill_recent_fund_flow")


def parse_asfund(text: str) -> list[dict]:
    """asfund 输出 header 第一列是 code, 解析标准 markdown 表"""
    return parse_markdown_table_simple(text)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", help="起始日 (含), 默认 = DB max+1")
    p.add_argument("--end", help="结束日 (含), 默认 = 今天")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--batch", type=int, default=BATCH)
    args = p.parse_args()

    target_end = args.end or date.today().strftime("%Y-%m-%d")

    if not DB_PATH.exists():
        logger.error("DB 不存在: %s", DB_PATH)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # DB max date
    cur.execute("SELECT MAX(trade_date) FROM fund_flow_data")
    db_max = cur.fetchone()[0] or "1900-01-01"
    if args.start:
        start = args.start
    else:
        start = (datetime.strptime(db_max, "%Y-%m-%d").date()
                 + timedelta(days=1)).strftime("%Y-%m-%d")

    if start > target_end:
        logger.info("DB 已是最新 (max=%s)", db_max)
        conn.close()
        return 0

    # 交易日列表
    days = []
    d = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(target_end, "%Y-%m-%d").date()
    while d <= e:
        if d.weekday() < 5:
            days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    if args.dry_run:
        logger.info("[DRY-RUN] %s → %s (%d 个交易日), 起 max=%s",
                    start, target_end, len(days), db_max)
        conn.close()
        return 0

    pairs = load_tradable_codes()
    logger.info("[fund_flow_data] %s → %s (%d 个交易日, %d 只股, BATCH=%d)",
                start, target_end, len(days), len(pairs), args.batch)

    sql = """INSERT OR IGNORE INTO fund_flow_data
             (code, trade_date, main_net, super_large_net, large_net,
              medium_net, small_net)
             VALUES (?, ?, ?, ?, ?, ?, ?)"""

    total = 0
    n_batches = (len(pairs) + args.batch - 1) // args.batch
    for day in days:
        # DB 已有的跳过
        cur.execute("SELECT code FROM fund_flow_data WHERE trade_date=?", (day,))
        already = {r[0] for r in cur.fetchall()}
        if len(already) >= len(pairs) * 0.95:
            logger.info("  [%s] DB 已有 %d 只, 跳过", day, len(already))
            continue

        day_rows: list[dict] = []
        succ = fail = 0
        t1 = time.time()
        for bi in range(n_batches):
            batch = pairs[bi * args.batch: (bi + 1) * args.batch]
            ws_codes = [to_westock(c, m) for c, m in batch]
            try:
                out = westock_call(ws_codes, "asfund", "--date", day, timeout=90)
                rows = parse_asfund(out)
                # asfund 返回 rows 里有 EndDate, 过滤
                day_rows.extend(r for r in rows if r.get("EndDate", "").startswith(day))
                succ += 1
            except Exception as e:
                fail += 1
                logger.warning("  [%s] batch %d/%d FAIL: %s",
                               day, bi + 1, n_batches, str(e)[:100])
            if (bi + 1) % 10 == 0 or bi == n_batches - 1:
                rate = (bi + 1) / max(1e-3, time.time() - t1)
                eta = (n_batches - bi - 1) / max(1e-3, rate)
                logger.info("  [%s] %d/%d 批 succ=%d fail=%d +%d行  ETA %.1fmin",
                            day, bi + 1, n_batches, succ, fail, len(day_rows), eta / 60)
            time.sleep(0.6)

        inserted = 0
        for r in day_rows:
            code = r.get("code", "")
            if not (code.startswith("sh") or code.startswith("sz")):
                continue
            code6 = code[2:]
            vals = (
                code6, day,
                _f(r.get("MainNetFlow")),
                _f(r.get("JumboNetFlow")),
                _f(r.get("BlockNetFlow")),  # 大单近似 large_net
                _f(r.get("MidNetFlow")),
                _f(r.get("SmallNetFlow")),
            )
            try:
                cur.execute(sql, vals)
                if cur.rowcount > 0:
                    inserted += 1
            except sqlite3.IntegrityError:
                pass
            except Exception as e:
                logger.debug("INSERT err: %s", e)
        conn.commit()
        total += inserted
        logger.info("  [%s] INSERT %d 行 (raw %d, %.1fs)",
                    day, inserted, len(day_rows), time.time() - t1)

    logger.info("[fund_flow_data] ✅ +%d 行 (起 max=%s)", total, db_max)
    cur.execute("SELECT COUNT(*), MAX(trade_date) FROM fund_flow_data")
    n, mx = cur.fetchone()
    logger.info("DB: %d 行, max=%s", n, mx)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())