#!/usr/bin/env python3
"""
akshare_finance_batch.py — 用 akshare 批量拉全市场业绩报表，输出兼容 finance_summary.csv

替代 baostock 的 task_finance，一次拉全市场一个季度（8-15秒/季度）。

用法:
  python scripts/akshare_finance_batch.py --start-year 2017 --end-year 2024 --quarters 1 2 3 4
  python scripts/akshare_finance_batch.py --dry-run

输出: market_data/finance_summary.csv (追加模式，自动跳过已存在的 code+statDate)
"""

from __future__ import annotations
import argparse
import csv
import logging
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
MARKET_DATA = ROOT / "market_data"
FINANCE_CSV = MARKET_DATA / "finance_summary.csv"
TENCENT_QUOTES_CSV = MARKET_DATA / "raw" / "reference" / "tencent_quotes.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("akshare_finance")


# 季度 → 报表日期映射
QUARTER_TO_DATE = {
    1: "0331",
    2: "0630",
    3: "0930",
    4: "1231",
}

# baostock finance_summary.csv 字段
FIELDNAMES = [
    "code", "pubDate", "statDate",
    "roeAvg", "npMargin", "gpMargin",
    "netProfit", "epsTTM",
    "MBRevenue", "totalShare", "liqaShare",
]


def load_existing_codes() -> set[tuple[str, str]]:
    """读已有 finance_summary.csv，返回 (code, statDate) 集合用于增量跳过"""
    existing: set[tuple[str, str]] = set()
    if FINANCE_CSV.exists():
        with open(FINANCE_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row.get("code", "").strip()
                stat = row.get("statDate", "")[:10].strip()
                if code and stat:
                    existing.add((code, stat))
    return existing


def load_tradable_codes() -> set[str]:
    """从 tencent_quotes.csv 读 6 位代码集合，用于过滤"""
    codes: set[str] = set()
    if TENCENT_QUOTES_CSV.exists():
        with open(TENCENT_QUOTES_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw = (row.get("code") or "").strip()
                mkt = (row.get("market") or "").strip().lower()
                if raw and raw.isdigit() and mkt in ("sh", "sz"):
                    codes.add(f"{mkt}.{raw.zfill(6)}")
    return codes


def code6_to_baostock(code6: str) -> str:
    """6位纯数字代码 → baostock 格式 sz.000001 / sh.600000"""
    if code6.startswith(("60", "68", "90", "11", "13")):
        return f"sh.{code6}"
    else:
        return f"sz.{code6}"


def fetch_quarter_akshare(year: int, quarter: int) -> list[dict]:
    """用 akshare stock_yjbb_em 拉一个季度全市场业绩报表，返回兼容 finance_summary 格式的行"""
    import akshare as ak

    date_suffix = QUARTER_TO_DATE[quarter]
    report_date = f"{year}{date_suffix}"

    logger.info("  拉取 %dQ%d (date=%s) ...", year, quarter, report_date)
    t0 = time.time()

    try:
        df = ak.stock_yjbb_em(date=report_date)
    except Exception as e:
        logger.error("  akshare 失败: %s", str(e)[:200])
        return []

    elapsed = time.time() - t0
    logger.info("  获取 %d 行, 耗时 %.1fs", len(df), elapsed)

    rows: list[dict] = []
    for _, r in df.iterrows():
        code6 = str(r.get("股票代码", "")).strip().zfill(6)
        if not code6 or not code6.isdigit():
            continue

        bs_code = code6_to_baostock(code6)

        # 字段映射
        # akshare: 每股收益, 营业总收入-营业总收入, 净利润-净利润, 净资产收益率(%), 销售毛利率(%)
        # baostock: epsTTM, MBRevenue, netProfit, roeAvg(小数), gpMargin(小数)
        eps = r.get("每股收益")
        revenue = r.get("营业总收入-营业总收入")
        net_profit = r.get("净利润-净利润")
        roe_pct = r.get("净资产收益率")  # 百分比, 如 9.74
        gross_margin_pct = r.get("销售毛利率")  # 百分比, 如 24.42
        pub_date = str(r.get("最新公告日期", "")).strip()[:10]

        # ROE 百分比 → 小数 (baostock 格式)
        roe_avg = ""
        if roe_pct is not None and str(roe_pct) != "nan" and str(roe_pct) != "":
            try:
                roe_avg = f"{float(roe_pct) / 100:.6f}"
            except (ValueError, TypeError):
                pass

        # 毛利率百分比 → 小数
        gp_margin = ""
        if gross_margin_pct is not None and str(gross_margin_pct) != "nan" and str(gross_margin_pct) != "":
            try:
                gp_margin = f"{float(gross_margin_pct) / 100:.6f}"
            except (ValueError, TypeError):
                pass

        # 净利润
        net_profit_str = ""
        if net_profit is not None and str(net_profit) != "nan":
            try:
                net_profit_str = f"{float(net_profit):.6f}"
            except (ValueError, TypeError):
                pass

        # EPS (akshare 是单季 EPS, baostock epsTTM 是 TTM, 不完全等价但可近似)
        eps_str = ""
        if eps is not None and str(eps) != "nan":
            try:
                eps_str = f"{float(eps):.6f}"
            except (ValueError, TypeError):
                pass

        # 营业总收入
        revenue_str = ""
        if revenue is not None and str(revenue) != "nan":
            try:
                revenue_str = f"{float(revenue):.6f}"
            except (ValueError, TypeError):
                pass

        # npMargin = 净利润 / 营业收入
        np_margin = ""
        if net_profit_str and revenue_str:
            try:
                rev = float(revenue_str)
                if rev != 0:
                    np_margin = f"{float(net_profit_str) / rev:.6f}"
            except (ValueError, TypeError, ZeroDivisionError):
                pass

        stat_date = f"{year}-{date_suffix[:2]}-{date_suffix[2:]}"

        row = {
            "code": bs_code,
            "pubDate": pub_date,
            "statDate": stat_date,
            "roeAvg": roe_avg,
            "npMargin": np_margin,
            "gpMargin": gp_margin,
            "netProfit": net_profit_str,
            "epsTTM": eps_str,
            "MBRevenue": revenue_str,
            "totalShare": "",  # akshare 业绩报表无此字段，留空
            "liqaShare": "",  # 同上
        }
        rows.append(row)

    return rows


def append_to_csv(new_rows: list[dict], existing: set[tuple[str, str]]) -> int:
    """追加新行到 finance_summary.csv，跳过已存在的 (code, statDate)"""
    # 过滤已存在
    filtered = []
    for row in new_rows:
        key = (row["code"], row["statDate"])
        if key not in existing:
            filtered.append(row)
            existing.add(key)

    if not filtered:
        logger.info("  无新数据 (全部已存在)")
        return 0

    # 追加写
    file_exists = FINANCE_CSV.exists()
    with open(FINANCE_CSV, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerows(filtered)

    logger.info("  追加 %d 行 → %s", len(filtered), FINANCE_CSV.name)
    return len(filtered)


def main() -> int:
    p = argparse.ArgumentParser(
        description="akshare 批量拉全市场业绩报表 → finance_summary.csv",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例: python scripts/akshare_finance_batch.py --start-year 2017 --end-year 2024 --quarters 1 2 3 4",
    )
    p.add_argument("--start-year", type=int, default=2017, help="起始年份 (默认 2017)")
    p.add_argument("--end-year", type=int, default=2024, help="结束年份 (含, 默认 2024)")
    p.add_argument("--quarters", type=int, nargs="+", default=[1, 2, 3, 4],
                   help="季度列表 (默认 1 2 3 4)")
    p.add_argument("--dry-run", action="store_true", help="只打印不写文件")
    args = p.parse_args()

    jobs = [(y, q) for y in range(args.start_year, args.end_year + 1) for q in args.quarters]
    logger.info("待跑任务: %d 个 (year x quarter)", len(jobs))
    logger.info("任务列表: %s", jobs)

    existing = load_existing_codes()
    logger.info("已有 (code, statDate): %d 条", len(existing))

    if args.dry_run:
        for y, q in jobs:
            logger.info("  [dry-run] %dQ%d → date=%d%s", y, q, y, QUARTER_TO_DATE[q])
        return 0

    total_new = 0
    grand_t0 = time.time()

    for yi, (yy, qq) in enumerate(jobs):
        logger.info("[%d/%d] %dQ%d 开始", yi + 1, len(jobs), yy, qq)
        rows = fetch_quarter_akshare(yy, qq)
        if rows:
            added = append_to_csv(rows, existing)
            total_new += added
        else:
            logger.warning("  %dQ%d 无数据", yy, qq)

        # 季度间间隔 2 秒，避免限流
        if yi < len(jobs) - 1:
            time.sleep(2)

    grand_elapsed = time.time() - grand_t0
    logger.info("=" * 60)
    logger.info("全部完成: 新增 %d 行, 总耗时 %.1fmin", total_new, grand_elapsed / 60)

    # 统计最终行数
    if FINANCE_CSV.exists():
        with open(FINANCE_CSV, "r", encoding="utf-8-sig") as f:
            total_lines = sum(1 for _ in f) - 1
        logger.info("finance_summary.csv 当前总行数: %d", total_lines)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
