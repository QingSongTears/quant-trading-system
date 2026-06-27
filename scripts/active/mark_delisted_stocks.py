#!/usr/bin/env python3
"""
mark_delisted_stocks.py — 批量填 stock_basic.delist_date

退市判断策略 (启发式)
---------------------
  1. 名字含 "退" (例如 "退市岩石" / "退市紫晶"): 必退市, delist_date = max(2026-06-18, last_trade)
  2. 名字含 "ST" 或 "*ST" 且 daily_price 最后 < 2024-01-01: 大概率退市
     但 ST 状态可能是风险警示未退, 这里保守只标 daily_price 已停 + 名字带 "退" 的

回测时使用
-----------
  SELECT * FROM stock_basic WHERE delist_date IS NULL OR delist_date >= :as_of_date
  这样回测早期 (2024-01 时) 时, 退市股还包含在池子里
"""
from __future__ import annotations
import logging
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "database" / "quant.db"

t0 = time.time()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("mark_delisted")


def main() -> int:
    if not DB_PATH.exists():
        return 1
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 策略 1: 名字含 "退" + daily_price 还在 (停牌中退市流程股, 默认 delist_date=2026-06-30)
    logger.info("[策略 1] 名字含 '退' 且 daily_price 仍活跃 (停牌中退市股)")
    cur.execute("""
        SELECT sb.code, sb.name, MAX(dp.trade_date) FROM stock_basic sb
        JOIN daily_price dp ON dp.code = sb.code
        WHERE sb.delist_date IS NULL AND sb.name LIKE '%退%'
        GROUP BY sb.code
    """)
    type1 = cur.fetchall()
    for code, name, last_trade in type1:
        # 退市股: 标记为最近交易日 (或 2026-06-30 估计)
        delist_date = "2026-06-30"
        cur.execute("UPDATE stock_basic SET delist_date = ? WHERE code = ?", (delist_date, code))
        logger.info("   %s %s → delist_date=%s", code, name, delist_date)

    # 策略 2: 名字含 "退" 且 daily_price 已停 (已退市股, 用 daily_price 最后交易日)
    logger.info("[策略 2] 名字含 '退' 且 daily_price 已停 (已退市股, 用 daily_price 末日)")
    cur.execute("""
        SELECT sb.code, sb.name, MAX(dp.trade_date) FROM stock_basic sb
        JOIN daily_price dp ON dp.code = sb.code
        WHERE sb.delist_date IS NULL AND sb.name LIKE '%退%'
        GROUP BY sb.code
        HAVING MAX(dp.trade_date) < '2024-01-01'
    """)
    type2 = cur.fetchall()
    for code, name, last_trade in type2:
        cur.execute("UPDATE stock_basic SET delist_date = ? WHERE code = ?", (last_trade, code))
        logger.info("   %s %s → delist_date=%s", code, name, last_trade)

    # 策略 3: 名字含 "退" 且 daily_price 也没数据 (用 holder_num/block_trade 最后日期推断)
    logger.info("[策略 3] 名字含 '退' 且 daily_price 没数据 (用 holder_num 末日推断)")
    cur.execute("""
        SELECT sb.code, sb.name,
               MAX(hn.end_date) AS last_holder,
               MAX(bt.trade_date) AS last_block
        FROM stock_basic sb
        LEFT JOIN daily_price dp ON dp.code = sb.code
        LEFT JOIN holder_num hn ON hn.code = sb.code
        LEFT JOIN block_trade bt ON bt.code = sb.code
        WHERE sb.delist_date IS NULL AND sb.name LIKE '%退%'
        GROUP BY sb.code
        HAVING MAX(dp.trade_date) IS NULL
    """)
    type3 = cur.fetchall()
    for code, name, last_holder, last_block in type3:
        # 取两者中较新的
        candidates = [d for d in (last_holder, last_block) if d]
        if not candidates:
            # 没任何数据 - 标 "unknown"
            inferred = "2023-12-31"  # 默认 2023 年底 (保守)
            source = "inferred"
        else:
            inferred = max(candidates)
            source = "holder_num/block_trade"
        cur.execute("UPDATE stock_basic SET delist_date = ? WHERE code = ?", (inferred, code))
        logger.info("   %s %s → delist_date=%s (源=%s)", code, name, inferred, source)

    conn.commit()

    # 校验
    cur.execute("SELECT COUNT(*) FROM stock_basic WHERE delist_date IS NOT NULL")
    n = cur.fetchone()[0]
    logger.info("\n[校验] stock_basic.delist_date 已填: %d 只", n)

    # 列出所有已标记
    cur.execute("""
        SELECT code, name, market, list_date, delist_date FROM stock_basic
        WHERE delist_date IS NOT NULL
        ORDER BY delist_date
    """)
    for r in cur.fetchall():
        print(f"  {r[0]} {r[1]:15s} mkt={r[2]} list={r[3]} delist={r[4]}")

    conn.close()
    logger.info("✅ 完成 (耗时 %.1fs)", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())