#!/usr/bin/env python3
"""
fill_eastmoney.py — 用东方财富公开 API 增量补 announcements / research_report

数据源 (官方公开接口, 无需登录/Token)
------------------------------------
1. announcements: https://np-anotice-stock.eastmoney.com/api/security/ann
   - 全市场按日期范围分页 (默认 50 条/页)
   - 支持 begin_time / end_time 过滤
   - 单日 2026-06-24: ~1670 条公告

2. research_report: https://reportapi.eastmoney.com/report/list
   - 全市场按日期范围分页 (默认 50 条/页)
   - 2026-06-15~6-25 共 ~120 篇研报
   - 含买卖评级 / 预测 EPS / 研报员 / 机构

调用
----
  # 抓 2026-06-17 ~ 2026-06-24 的公告 (DB max=6-16)
  python scripts/fill_eastmoney.py --task announcements --start 2026-06-17 --end 2026-06-24

  # 抓 2026-06-16 ~ 2026-06-24 的研报 (DB max=6-15)
  python scripts/fill_eastmoney.py --task research_report --start 2026-06-16 --end 2026-06-24

  # 一起跑
  python scripts/fill_eastmoney.py --task all --end 2026-06-24

注意
----
- 单 IP 限速未知, 默认每次请求 sleep 0.3s
- 公告量大(单日 1.6k), 8 天可能 1 万+, 跑 ~10-20 分钟
- 研报量小(每天 10-30), 9 天 ~150 篇, ~1-2 分钟
"""
from __future__ import annotations
import argparse
import json
import logging
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "database" / "quant.db"
LOG_PATH = Path(r"C:\Users\admin\AppData\Local\Temp\fill_eastmoney.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

t0 = time.time()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8"),
              logging.StreamHandler()],
)
logger = logging.getLogger("fill_eastmoney")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def http_get_json(url: str, timeout: int = 30) -> dict:
    """请求 URL 返回 JSON, 自动处理 jQuery callback 包裹"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Referer": "https://data.eastmoney.com/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace").strip()
    # 处理 jQuery(...) 包裹
    if body.startswith("jQuery("):
        body = body[len("jQuery("):].rstrip(");")
    return json.loads(body)


# ============== announcements ==============

def fetch_announcements(date_str: str, page_size: int = 50) -> list[dict]:
    """抓某天的全市场公告 (返回 list of {code, name, date, type, title, art_code})

    API: https://np-anotice-stock.eastmoney.com/api/security/ann
    """
    base = ("https://np-anotice-stock.eastmoney.com/api/security/ann"
            "?cb=jQuery&page_size={ps}&page_index={pi}"
            "&ann_type=A&client_source=web"
            "&f_node=0&s_node=0"
            "&begin_time={d}&end_time={d}")
    out: list[dict] = []
    page = 1
    while True:
        url = base.format(ps=page_size, pi=page, d=date_str)
        try:
            data = http_get_json(url)
        except Exception as e:
            logger.warning("  [%s] page %d FAIL: %s", date_str, page, str(e)[:100])
            time.sleep(2)
            return out
        items = (data.get("data") or {}).get("list") or []
        if not items:
            break
        for it in items:
            codes = it.get("codes") or []
            if not codes:
                continue
            c = codes[0]
            # 只取已上市的 A 股 (有 stock_code 6 位数字)
            stock_code = c.get("stock_code", "")
            if not (stock_code.isdigit() and len(stock_code) == 6):
                continue
            cols = it.get("columns") or []
            type_str = ",".join(cc.get("column_name", "") for cc in cols) or None
            out.append({
                "code": stock_code,
                "name": c.get("short_name", ""),
                "date": it.get("notice_date", "")[:10],
                "type": type_str,
                "title": it.get("title", "")[:500],
                "art_code": it.get("art_code", ""),
            })
        total = (data.get("data") or {}).get("total_hits", 0)
        if page * page_size >= total:
            break
        page += 1
        time.sleep(0.3)
    return out


def fill_announcements(start: str, end: str, dry_run: bool,
                       conn: sqlite3.Connection) -> int:
    """补 announcements 表, 返回新增行数"""
    cur = conn.cursor()
    # 加载已有 (code, date, type, title) 元组做 dedup
    cur.execute("SELECT code, trade_date, type, title FROM announcements")
    existing = {(r[0], r[1], r[2], r[3]) for r in cur.fetchall()}
    logger.info("[announcements] 已有 %d 行 (含重复, 用于 dedup)", len(existing))

    days = []
    d = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    while d <= e:
        if d.weekday() < 5:
            days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    if dry_run:
        logger.info("[DRY-RUN] announcements: %s ~ %s (%d 个交易日)",
                    start, end, len(days))
        return 0

    total_inserted = 0
    for day in days:
        t1 = time.time()
        items = fetch_announcements(day)
        if not items:
            logger.info("  [%s] 无数据", day)
            continue
        n = 0
        for it in items:
            key = (it["code"], it["date"], it["type"], it["title"])
            if key in existing:
                continue
            url = (f"https://data.eastmoney.com/notices/detail/"
                   f"{it['code']}/{it['art_code']}.html") if it["art_code"] else None
            try:
                cur.execute(
                    """INSERT INTO announcements
                       (code, trade_date, type, title, url)
                       VALUES (?, ?, ?, ?, ?)""",
                    (it["code"], it["date"], it["type"], it["title"], url),
                )
                if cur.rowcount > 0:
                    n += 1
                    existing.add(key)  # 防止同 batch 重复
            except sqlite3.IntegrityError:
                pass
            except Exception as e:
                logger.debug("  INSERT err: %s", e)
        conn.commit()
        total_inserted += n
        logger.info("  [%s] +%d / raw %d (%.1fs)",
                    day, n, len(items), time.time() - t1)

    logger.info("[announcements] ✅ +%d 行", total_inserted)
    return total_inserted


# ============== research_report ==============

def fetch_research_reports(start: str, end: str, page_size: int = 50) -> list[dict]:
    """按日期范围抓全市场研报"""
    base = ("https://reportapi.eastmoney.com/report/list"
            "?cb=&pageSize={ps}&pageNo={pi}"
            "&industryCode=*&industry=*&rating=*&ratingChange=*"
            "&beginTime={s}&endTime={e}"
            "&fields=&qType=0&orgCode=&code=*&rcode=&_=")
    out: list[dict] = []
    page = 1
    while True:
        url = base.format(ps=page_size, pi=page, s=start, e=end)
        try:
            data = http_get_json(url)
        except Exception as e:
            logger.warning("  page %d FAIL: %s", page, str(e)[:100])
            time.sleep(2)
            return out
        items = data.get("data") or []
        if not items:
            break
        for it in items:
            stock_code = str(it.get("stockCode", ""))
            if not (stock_code.isdigit() and len(stock_code) == 6):
                continue
            out.append({
                "code": stock_code,
                "name": it.get("stockName", ""),
                "date": str(it.get("publishDate", ""))[:10],
                "title": it.get("title", "")[:500],
                "rating": it.get("emRatingName", "") or it.get("sRatingName", ""),
                "rating_change": (str(it.get("ratingChange", ""))
                                  if it.get("ratingChange") not in (None, "") else None),
                "author": it.get("researcher", ""),
                "institution": it.get("orgName", ""),
                "info_code": it.get("infoCode", ""),
            })
        total = data.get("hits", 0)
        if page * page_size >= total:
            break
        page += 1
        time.sleep(0.3)
    return out


def fill_research_report(start: str, end: str, dry_run: bool,
                         conn: sqlite3.Connection) -> int:
    """补 research_report 表, 返回新增行数"""
    cur = conn.cursor()
    if dry_run:
        logger.info("[DRY-RUN] research_report: %s ~ %s", start, end)
        return 0

    t1 = time.time()
    items = fetch_research_reports(start, end)
    logger.info("[research_report] fetched %d items (%.1fs)", len(items), time.time() - t1)

    n = 0
    for it in items:
        url = None
        if it.get("info_code"):
            url = f"https://data.eastmoney.com/report/zw_strategy.jshtml?encodeUrl=&infoCode={it['info_code']}"
        try:
            cur.execute(
                """INSERT OR IGNORE INTO research_report
                   (code, date, rating, rating_change, title,
                    author, institution, url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (it["code"], it["date"], it["rating"], it["rating_change"],
                 it["title"], it["author"], it["institution"], url),
            )
            if cur.rowcount > 0:
                n += 1
        except Exception as e:
            logger.debug("  INSERT err: %s", e)
    conn.commit()
    logger.info("[research_report] ✅ +%d 行", n)
    return n


# ============== main ==============

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task", action="append",
                   help="任务 (可多次: announcements / research_report / all)")
    p.add_argument("--start", help="起始日期 (含), 默认: announcements=6-17, research_report=6-16")
    p.add_argument("--end", default=date.today().strftime("%Y-%m-%d"),
                   help="结束日期 (含), 默认今天")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not args.task or "all" in args.task:
        tasks = ["announcements", "research_report"]
    else:
        tasks = args.task

    if not DB_PATH.exists():
        logger.error("DB 不存在: %s", DB_PATH)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    total = 0
    for task in tasks:
        try:
            if task == "announcements":
                start = args.start or "2026-06-17"
                n = fill_announcements(start, args.end, args.dry_run, conn)
            elif task == "research_report":
                start = args.start or "2026-06-16"
                n = fill_research_report(start, args.end, args.dry_run, conn)
            total += n
        except Exception as e:
            import traceback
            logger.error("[%s] ❌ 异常: %s\n%s", task, e, traceback.format_exc())
    conn.close()
    logger.info("=" * 60)
    logger.info("✅ 全部完成! 新增 %d 行 (耗时 %.1fs)", total, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())