#!/usr/bin/env python3
"""
fill_circulating_shares.py — 批量填 stock_profile.circulating_shares

数据源
------
  finance_summary.liqa_share (baostock 实测的流通股本, 股)
  - 5093 只覆盖 (沪深 + 北交, 2024Q4 单季)
  - 79 只缺 (新股 / 财报送延 / 退市)

为什么不从 tencent_quotes.csv 的 float_mcap_yi 反推?
  - tencent_quotes.csv 的 float_mcap_yi 字段 99% 是空 (CSV 直接看只有 mcap_yi 总市值有值)
  - 而 finance_summary.liqa_share 是 baostock 实测流通股本, 准确得多

注意
----
  fund_flow_scorer.py:144 原本用 regCapital × 10000 算 shares, 这是错的:
  regCapital 是注册资本 (公司设立时登记的资本总额), 不等于流通股本.
  本脚本改用 baostock 实测 liqa_share, 替换错误实现.
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
logger = logging.getLogger("fill_circulating_shares")


def main() -> int:
    if not DB_PATH.exists():
        logger.error("DB 不存在: %s", DB_PATH)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 1. 从 finance_summary.liqa_share 取最新值 (取 MAX(stat_date) 的那一条)
    logger.info("[1/3] 从 finance_summary.liqa_share 取最新流通股本")
    cur.execute("""
        SELECT code, liqa_share FROM finance_summary
        WHERE liqa_share IS NOT NULL AND liqa_share > 0
          AND _date = (SELECT MAX(_date) FROM finance_summary f2 WHERE f2.code = finance_summary.code)
    """)
    rows = cur.fetchall()
    logger.info("   取得 %d 只股的流通股本 (最新季度)", len(rows))

    # 2. UPDATE stock_profile
    n_updated = n_no_match = 0
    for code6, shares in rows:
        cur.execute(
            "UPDATE stock_profile SET circulating_shares = ? WHERE code = ?",
            (shares, code6),
        )
        if cur.rowcount > 0:
            n_updated += 1
        else:
            n_no_match += 1
    conn.commit()
    logger.info("[2/3] UPDATE stock_profile.circulating_shares: %d 行匹配, %d 行无对应",
                n_updated, n_no_match)

    # 3. 校验
    cur.execute("SELECT COUNT(*) FROM stock_profile WHERE circulating_shares IS NOT NULL")
    filled = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM stock_profile")
    total = cur.fetchone()[0]
    logger.info("[3/3] 校验: %d / %d (%.1f%%) stock_profile 有 circulating_shares",
                filled, total, filled / total * 100)

    # 仍缺数据的前 10
    cur.execute("""
        SELECT sp.code, sp.name FROM stock_profile sp
        LEFT JOIN finance_summary f ON sp.code = f.code
        WHERE sp.circulating_shares IS NULL
        GROUP BY sp.code
        LIMIT 10
    """)
    missing_sample = cur.fetchall()
    logger.info("仍缺 circulating_shares 的样本: %s", missing_sample[:5])

    # TOP 5 流通股本
    cur.execute("""
        SELECT code, name, circulating_shares FROM stock_profile
        WHERE circulating_shares IS NOT NULL
        ORDER BY circulating_shares DESC LIMIT 5
    """)
    logger.info("流通股本 TOP 5:")
    for r in cur.fetchall():
        sh_yi = r[2] / 1e8 if r[2] else 0
        logger.info("  %s %s: %.2f 亿股", r[0], r[1], sh_yi)

    conn.close()
    logger.info("✅ 完成 (耗时 %.1fs)", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())