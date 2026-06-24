#!/usr/bin/env python3
"""
add_bj_stocks.py — 把北交所股 (92/8/43/4 前缀) 补到 stock_basic + stock_profile

为什么需要
----------
tencent_quotes.csv 只覆盖沪深 (5209 只), 北交所股 (920xxx/8xxxx/43xxxx/4xxxx) 不在 stock_basic 里.
但 holder_num / block_trade / dividend / announcements 等表里有北交所数据 → 孤儿.
回测策略若按 stock_basic.code 过滤, 会漏掉北交所股.

步骤
----
1. 收集北交所 unique code (从 holder_num/block_trade/dividend/announcements 各表)
2. westock profile 批量拉 14 字段 (industry/sector/regCapital 等)
3. INSERT INTO stock_basic (code, name, market='BJ', industry, sector, reg_capital, ...)
4. INSERT INTO stock_profile (code, code6, name, industry, sector, reg_capital, ...)
5. UPDATE stock_basic.industry = stock_profile.industry (join)

调用
----
  python scripts/add_bj_stocks.py
  python scripts/add_bj_stocks.py --dry-run
"""

from __future__ import annotations

import argparse
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
logger = logging.getLogger("add_bj_stocks")


def collect_bj_codes(cur: sqlite3.Cursor) -> list[str]:
    """从各表收集所有未在 stock_basic 的 unique code

    包括:
      - 北交所前缀 92/8/43/4 (老北交/新北交/老三板)
      - 沪深前缀 00/30/60/68 (tencent_quotes 历史快照里漏的小盘/退市股)
    """
    candidates = set()
    for table, code_col in [
        ("holder_num", "code"), ("block_trade", "code"), ("dividend", "code"),
        ("announcements", "code"), ("fund_flow_data", "code"),
        ("daily_price", "code"), ("technical_indicators", "code"),
    ]:
        try:
            cur.execute(f"""
                SELECT DISTINCT t.{code_col} FROM {table} t
                LEFT JOIN stock_basic sb ON t.{code_col} = sb.code
                WHERE sb.code IS NULL
            """)
            for (c,) in cur.fetchall():
                c = str(c).strip().zfill(6)
                # 接受 6 位纯数字 + 任何股票前缀 (北交所 + 沪深)
                if c.isdigit() and len(c) == 6:
                    candidates.add(c)
        except sqlite3.OperationalError as e:
            logger.warning("  跳过 %s.%s: %s", table, code_col, e)

    return sorted(candidates)


def infer_market(code6: str) -> str:
    """6 位 → 市场 (SH/SZ/BJ)"""
    if code6.startswith(("60", "68", "11", "13", "5", "9")):
        return "SH"
    if code6.startswith(("00", "30", "12", "15", "16", "20")):
        return "SZ"
    return "BJ"  # 92/8/43/4 默认北交所


def fetch_westock_profiles(codes6: list[str]) -> dict[str, dict]:
    """westock profile 批量拉 → {code6: profile_dict}"""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from import_from_westock_baostock_akshare import westock_batch

    results = {}
    BATCH = 50
    total = (len(codes6) + BATCH - 1) // BATCH
    for i in range(0, len(codes6), BATCH):
        batch = codes6[i:i + BATCH]
        # 按市场前缀分别拼 (bj920xxx / sh600xxx / sz000xxx)
        ws_codes = []
        for c in batch:
            market = infer_market(c).lower()
            ws_codes.append(f"{market}{c}")
        rows = westock_batch(ws_codes, "profile", timeout=180)
        for row in rows:
            full = row.get("code", "")
            # 拆前缀 → code6
            if len(full) == 8 and full[:2] in ("sh", "sz", "bj"):
                code6 = full[2:]
                results[code6] = row
        logger.info("  进度 %d/%d (累计 %d/%d, 失败 %d)",
                    i // BATCH + 1, total, len(results), len(codes6),
                    len(batch) - sum(1 for c in batch if c in results))
    return results


def insert_to_db(cur: sqlite3.Cursor, profiles: dict[str, dict],
                 missing: set[str], dry_run: bool) -> None:
    """INSERT INTO stock_basic + stock_profile

    profiles: westock 返回的 (code6 → dict)
    missing: westock 找不到的 code6 集合 (标已退市)
    """
    if dry_run:
        logger.info("[dry-run] 准备 INSERT %d 只 (含 %d 已退市占位)",
                    len(profiles), len(missing))
        for c in list(profiles.keys())[:3]:
            logger.info("  示例 (有数据): %s -> %s", c, profiles[c].get("name"))
        for c in list(missing)[:3]:
            logger.info("  示例 (占位): %s -> 已退市", c)
        return

    n_basic = n_profile = 0
    for code6, p in profiles.items():
        market = infer_market(code6)
        cur.execute("""
            INSERT OR IGNORE INTO stock_basic
            (code, name, market, industry, sector, listed_date_alt,
             issue_price, reg_capital, establish_date, chairman, website, business,
             reg_address, office_address, tel, email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            code6,
            p.get("name", ""),
            market,
            p.get("industry") or None,
            p.get("sector") or None,
            p.get("listedDate") or None,
            _to_float(p.get("issuePrice")),
            _to_float(p.get("regCapital")),
            p.get("establishDate") or None,
            p.get("chairman") or None,
            p.get("website") or None,
            p.get("business") or None,
            p.get("regAddress") or None,
            p.get("officeAddress") or None,
            p.get("tel") or None,
            p.get("email") or None,
        ))
        if cur.rowcount > 0:
            n_basic += 1

        cur.execute("""
            INSERT OR IGNORE INTO stock_profile
            (code, name, listed_date, industry, sector, issue_price, reg_capital,
             chairman, establish_date, website, business, reg_address, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'westock')
        """, (
            code6,
            p.get("name", ""),
            p.get("listedDate") or None,
            p.get("industry") or None,
            p.get("sector") or None,
            _to_float(p.get("issuePrice")),
            _to_float(p.get("regCapital")),
            p.get("chairman") or None,
            p.get("establishDate") or None,
            p.get("website") or None,
            p.get("business") or None,
            p.get("regAddress") or None,
        ))
        if cur.rowcount > 0:
            n_profile += 1

    # 退市占位
    n_delisted = 0
    for code6 in missing:
        market = infer_market(code6)
        cur.execute("""
            INSERT OR IGNORE INTO stock_basic
            (code, name, market, delist_date, industry)
            VALUES (?, '已退市', ?, '2024-01-01', NULL)
        """, (code6, market))
        if cur.rowcount > 0:
            n_delisted += 1
    logger.info("   ✅ INSERT stock_basic: %d 行 (含 %d 已退市占位)",
                n_basic + n_delisted, n_delisted)
    logger.info("   ✅ INSERT stock_profile: %d 行 (仅 westock 有数据的)", n_profile)


def _to_float(s):
    if not s or s == "nan":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not DB_PATH.exists():
        logger.error("DB 不存在: %s", DB_PATH)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 1. 收集北交所 code
    logger.info("[1/3] 收集北交所 code")
    bj_codes = collect_bj_codes(cur)
    logger.info("   找到 %d 只北交所 code (92/8/43/4 前缀)", len(bj_codes))

    if not bj_codes:
        logger.info("   无北交所股, 退出")
        conn.close()
        return 0

    # 2. westock profile 批量拉
    logger.info("[2/3] westock profile 拉 %d 只 (BATCH=50)", len(bj_codes))
    profiles = fetch_westock_profiles(bj_codes)
    missing = set(bj_codes) - set(profiles.keys())
    logger.info("   成功获取 %d / %d (失败 %d 将标已退市)",
                len(profiles), len(bj_codes), len(missing))

    # 3. INSERT
    logger.info("[3/3] INSERT INTO stock_basic + stock_profile")
    insert_to_db(cur, profiles, missing, dry_run=args.dry_run)
    conn.commit()

    # 4. 校验外键一致性
    if not args.dry_run:
        cur.execute("""
            SELECT COUNT(DISTINCT code) FROM holder_num
            WHERE code NOT IN (SELECT code FROM stock_basic)
        """)
        remaining = cur.fetchone()[0]
        logger.info("[校验] holder_num 剩余孤儿: %d", remaining)

        cur.execute("SELECT COUNT(*) FROM stock_basic WHERE market = 'BJ'")
        logger.info("[校验] stock_basic BJ 市场股数: %d", cur.fetchone()[0])

    conn.close()
    logger.info("✅ 完成 (耗时 %.1fs)", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())