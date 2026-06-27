#!/usr/bin/env python3.11
"""
import_from_westock_baostock_akshare.py — 一站式补齐缺失的市场基础数据

数据源分工（按当前网络实际可达性 2026-06-24 调整）
----------------------------------------------------
  westock profile (npx 批量)  → stock_profile.csv  (industry + regCapital + listedDate 等)
  baostock profit_data loop   → finance_summary.csv (ROE/EPS/净利润, 单股循环)
  westock kline (6 个指数)     → benchmark_data.csv  (沪深300/中证500/上证50/创业板/科创50/中证1000)

AKShare 当前网络断开 (RemoteDisconnected 2026-06-24), 故不依赖

调用
----
  python scripts/import_from_westock_baostock_akshare.py --task profile --dry-run
  python scripts/import_from_westock_baostock_akshare.py --task profile --batch-size 100
  python scripts/import_from_westock_baostock_akshare.py --task finance --year 2024 --quarter 4
  python scripts/import_from_westock_baostock_akshare.py --task benchmark --limit 1500
  python scripts/import_from_westock_baostock_akshare.py --task all --batch-size 100

安全
----
  westock CLI 调用一律 shell=False + argv 列表, 入参白名单正则
  baostock 直接调 API (Python SDK, 无 shell 注入风险)
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
MARKET_DATA = ROOT / "market_data"
REFERENCE = MARKET_DATA / "raw" / "reference"
TENCENT_QUOTES_CSV = REFERENCE / "tencent_quotes.csv"

PROFILE_CSV = MARKET_DATA / "stock_profile.csv"          # 顶层, scripts/update_all_data.py 也读它
FINANCE_CSV = MARKET_DATA / "finance_summary.csv"
BENCHMARK_CSV = MARKET_DATA / "benchmark_data.csv"

WESTOCK_PKG = "westock-data-clawhub@1.0.4"
_NPM_BIN = ["npx.cmd", "-y", WESTOCK_PKG]

# ===== 入参白名单 (防命令注入) =====
_SYMBOLS_RE = re.compile(r"^[a-z]{2}\d{6}(,[a-z]{2}\d{6})*$")
_PERIOD_RE = re.compile(r"^(day|week|month|season|year)$")

# ===== Logging =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("import")


# ============================================================
# 通用工具
# ============================================================

def load_tradable_codes() -> list[tuple[str, str]]:
    """从 tencent_quotes.csv 读 (code6, market) 列表

    tencent_quotes.csv 的 code 是 int 1-6 位, 必须补 0 到 6 位
    market 字段: 'sh' / 'sz' (权威, 直接用)

    返回: [("000001", "sz"), ("000002", "sz"), ("600000", "sh"), ...]
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    with open(TENCENT_QUOTES_CSV, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = (row.get("code") or "").strip()
            mkt = (row.get("market") or "").strip().lower()
            if not raw or not raw.isdigit():
                continue
            if mkt not in ("sh", "sz"):
                continue
            code6 = raw.zfill(6)
            if code6 in seen:
                continue
            seen.add(code6)
            pairs.append((code6, mkt))
    return pairs


def to_westock(code6: str, market: str) -> str:
    """6 位数字 + 市场 → westock 格式 (sh600000 / sz000001)"""
    if market not in ("sh", "sz"):
        raise ValueError(f"unknown market: {market}")
    return f"{market}{code6}"


def parse_markdown_table(text: str) -> list[dict]:
    """通用 westock markdown 表格解析器

    支持两种 westock 输出格式:
    1. 显式 header:  | code | name | ... |   data: | sh600000 | 浦发银行 | ... |
    2. 隐式 symbol:  | date | open | last | ... |  data: | sh000300 | 2024-... | ... |
       (westock kline 子命令格式, 数据行比 header 多 1 列, 首列是 symbol code)

    返回: [{"code": "sh600000", ...}, ...] 或 [{"code": "sh000300", "date": "2024-...", ...}, ...]
    """
    SYMBOL_RE = re.compile(r"^[a-z]{2}\d{6}$")
    rows: list[dict] = []
    headers: list[str] | None = None
    has_implicit_symbol = False
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if "[Batch]" in line or "总数" in line or "成功" in line or "失败" in line:
            continue
        if re.match(r"^\|\s*-+(\s*\|\s*-+)*\s*\|?$", line):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if headers is None:
            # header 行: 第一列是 code / symbol / date 之一
            if cells and cells[0] in ("code", "symbol", "date"):
                headers = cells
                # kline 子命令: header 第一列是 date 但实际数据首列是 symbol code
                has_implicit_symbol = (cells[0] == "date"
                                       and "open" in cells  # kline 标志
                                       and len(cells) >= 7)
            continue
        if not cells:
            continue
        # 检测隐式 symbol: 数据行比 header 多 1 列, 首列是 symbol 格式
        if has_implicit_symbol and len(cells) == len(headers) + 1 and SYMBOL_RE.match(cells[0]):
            # 把首列插入为 code, 剩余 cells 与 headers 对齐
            row = {"code": cells[0]}
            for h, v in zip(headers, cells[1:]):
                row[h] = v
            # kline 子命令额外重命名 last → close (与其他子命令一致)
            if "last" in row and "close" not in row:
                row["close"] = row.pop("last")
            rows.append(row)
        elif len(cells) == len(headers):
            row = dict(zip(headers, cells))
            if "last" in row and "close" not in row:
                row["close"] = row.pop("last")
            rows.append(row)
    return rows


def westock_batch(symbols: list[str], subcommand: str, *flags: str,
                 timeout: int = 240) -> list[dict]:
    """westock 批量调用（逗号分隔多股）

    安全: shell=False + argv 列表 + 入参白名单
    返回: 解析后的字典列表 (失败/部分失败时可能为空)

    调用格式: `westock-data <subcommand> <codes> <flags>`
    示例:
        westock_batch([sh600000, sh600519], "profile")               # 无 flags
        westock_batch([sh600000], "kline", "--period", "day", ...)   # flags 在 codes 后

    Windows 隐藏 cmd 窗口: subprocess.run 用 CREATE_NO_WINDOW 标志
    """
    if not symbols:
        return []
    joined = ",".join(symbols)
    if not _SYMBOLS_RE.match(joined):
        raise ValueError(f"symbols 格式非法 (拒执行): {joined!r}")
    if not re.match(r"^[a-z][a-z0-9-]{0,30}$", subcommand):
        raise ValueError(f"subcommand 格式非法: {subcommand!r}")
    cmd = _NPM_BIN + [subcommand, joined] + list(flags)
    logger.debug("执行: %s ...", " ".join(cmd))
    # Windows CREATE_NO_WINDOW 防止 npx.cmd 弹黑窗
    CREATE_NO_WINDOW = 0x08000000
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, shell=False,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        logger.error("westock 超时: %d 股", len(symbols))
        return []
    if r.returncode != 0:
        logger.error("westock 失败: %s", r.stderr[:200])
        return []
    return parse_markdown_table(r.stdout)


def write_csv(path: Path, rows: list[dict]) -> None:
    """统一写 CSV (utf-8-sig, 让 Excel 也能直接打开)"""
    if not rows:
        logger.warning("无数据, 不写 %s", path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # 稳定字段顺序: 用第一个 row 的 key
    fieldnames = list(rows[0].keys())
    # 兼容: 后续 row 可能有额外 key (westock 部分股字段缺失)
    extra = set()
    for r in rows:
        for k in r.keys():
            if k not in fieldnames:
                extra.add(k)
    if extra:
        logger.info("补充字段: %s", sorted(extra))
        fieldnames.extend(sorted(extra))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    logger.info("写入 %s: %d 行, %d 字段", path, len(rows), len(fieldnames))


# ============================================================
# Task 1: stock_profile.csv ← westock profile 批量
# ============================================================

def task_profile(batch_size: int, dry_run: bool, skip_existing: bool) -> None:
    pairs = load_tradable_codes()
    logger.info("[profile] tencent_quotes.csv 可用股票: %d 只", len(pairs))
    ws_codes = [to_westock(c, m) for c, m in pairs]

    # 增量: 读已有 stock_profile.csv 的 code, 跳过
    existing: set[str] = set()
    if skip_existing and PROFILE_CSV.exists():
        with open(PROFILE_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get("code") or "").strip()
                if code:
                    existing.add(code)
        logger.info("[profile] 已有 %d 条, 增量跳过", len(existing))

    targets = [c for c in ws_codes if c not in existing]
    logger.info("[profile] 待采集: %d 条 (BATCH=%d, 共 %d 批)",
                len(targets), batch_size, (len(targets) + batch_size - 1) // batch_size)

    if dry_run:
        logger.info("[dry-run] 示例前 5: %s", targets[:5])
        logger.info("[dry-run] 不执行 npx, 不写文件")
        return

    batches = [targets[i:i + batch_size] for i in range(0, len(targets), batch_size)]
    all_rows: list[dict] = []
    succ_batches = fail_batches = 0
    t0 = time.time()
    for i, batch in enumerate(batches):
        rows = westock_batch(batch, "profile", timeout=240)
        if rows:
            all_rows.extend(rows)
            succ_batches += 1
        else:
            fail_batches += 1
            logger.warning("  batch %d/%d (%d 股) FAIL", i + 1, len(batches), len(batch))
        if (i + 1) % 5 == 0 or i == len(batches) - 1:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed else 0
            eta = (len(batches) - i - 1) / rate if rate else 0
            logger.info("  batch %d/%d  累计 %d 行  succ=%d fail=%d  "
                        "%.1f 批/s  ETA %.1fmin",
                        i + 1, len(batches), len(all_rows),
                        succ_batches, fail_batches, rate, eta / 60)
        time.sleep(0.4)  # 限速, 避免 npx 拉包压力

    logger.info("[profile] 总行数: %d  成功批: %d  失败批: %d  耗时: %.1fs",
                len(all_rows), succ_batches, fail_batches, time.time() - t0)
    write_csv(PROFILE_CSV, all_rows)


# ============================================================
# Task 2: finance_summary.csv ← baostock profit_data loop
# ============================================================

def task_finance(year: int, quarter: int, dry_run: bool, start_year: int | None,
                 end_year: int | None, quarters: list[int] | None) -> None:
    """财务 loop 入口

    简单模式: --year Y --quarter Q (单次, 兼容旧行为)
    多年模式: --start-year 2015 --end-year 2024 --quarters 1 2 3 4 (批量)
    """
    import baostock as bs

    # 计算要跑的所有 (year, quarter) 组合
    if start_year is not None and end_year is not None and quarters:
        jobs = [(y, q) for y in range(start_year, end_year + 1) for q in quarters]
    else:
        jobs = [(year, quarter)]

    logger.info("[finance] 待跑任务: %d 个 (year x quarter): %s",
                len(jobs), jobs[:3] + (["..."] if len(jobs) > 3 else []))

    # baostock 一次登录即可 (整个 loop 期间复用)
    lg = bs.login()
    if lg.error_code != "0":
        logger.error("[finance] baostock 登录失败: %s", lg.error_msg)
        return
    logger.info("[finance] baostock 登录 OK")

    pairs = load_tradable_codes()
    logger.info("[finance] 待采集股票: %d 只", len(pairs))
    bs_codes = [f"{m}.{c}" for c, m in pairs]

    if dry_run:
        logger.info("[dry-run] 任务数: %d, 示例前 5: %s", len(jobs), bs_codes[:5])
        bs.logout()
        return

    # 增量: 读已有 finance_summary.csv, 跳过已存在的 (code, stat_date)
    existing: set[tuple[str, str]] = set()
    if FINANCE_CSV.exists():
        try:
            import csv as _csv
            with open(FINANCE_CSV, "r", encoding="utf-8-sig") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    code = row.get("code", "").strip()
                    stat = row.get("statDate", "")[:10].strip()
                    if code and stat:
                        existing.add((code, stat))
            logger.info("[finance] 已存在 (code, stat_date): %d 条, 增量跳过", len(existing))
        except Exception as e:
            logger.warning("读已有 finance CSV 失败: %s", e)

    all_rows: list[dict] = []
    grand_t0 = time.time()
    for ji, (yy, qq) in enumerate(jobs):
        logger.info("[finance] [%d/%d] year=%d Q%d 开始", ji + 1, len(jobs), yy, qq)
        job_rows: list[dict] = []
        fail_codes: list[str] = []
        t0 = time.time()
        consecutive_errors = 0
        for i, bscode in enumerate(bs_codes):
            # 重试: baostock 服务端有时 WinError 10054 断连, 用 retry + backoff
            rs = None
            for retry in range(3):
                try:
                    rs = bs.query_profit_data(code=bscode, year=yy, quarter=qq)
                    consecutive_errors = 0  # 成功 reset
                    break
                except Exception as e:
                    consecutive_errors += 1
                    if consecutive_errors >= 20:
                        # 连续 20 个错误, 大概率服务端 ban 了 IP, 退避 30s
                        logger.warning("连续 %d 错误, 退避 30s: %s",
                                       consecutive_errors, str(e)[:80])
                        time.sleep(30)
                        consecutive_errors = 0
                    else:
                        time.sleep(min(2 ** retry, 8))  # 1s, 2s, 4s, 8s...
                    continue
            if rs is None:
                fail_codes.append(bscode)
                continue
            if rs.error_code != "0":
                fail_codes.append(bscode)
                continue
            if not rs.fields:
                continue
            rows_count = 0
            while rs.next():
                row_dict = dict(zip(rs.fields, rs.get_row_data()))
                stat_date = row_dict.get("statDate", "")[:10]
                if (bscode, stat_date) in existing:
                    continue
                job_rows.append(row_dict)
                rows_count += 1
            if rows_count == 0:
                fail_codes.append(bscode)
            if (i + 1) % 500 == 0:
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed if elapsed else 0
                eta = (len(bs_codes) - i - 1) / rate if rate else 0
                logger.info("    %d/%d  +%d 累计 %d  %.1f 股/s  ETA %.1fmin",
                            i + 1, len(bs_codes), len(job_rows), len(all_rows) + len(job_rows),
                            rate, eta / 60)

        all_rows.extend(job_rows)
        elapsed = time.time() - t0
        logger.info("[finance] year=%d Q%d 完成: %d 行, 失败 %d, 耗时 %.1fmin",
                    yy, qq, len(job_rows), len(fail_codes), elapsed / 60)

    bs.logout()
    grand_elapsed = time.time() - grand_t0
    logger.info("[finance] 全部完成: 新增 %d 行, 总耗时 %.1fmin",
                len(all_rows), grand_elapsed / 60)

    if not all_rows:
        logger.warning("[finance] 无新数据, 不写 CSV")
        return

    # 追加模式 (a) 写到现有 CSV
    if FINANCE_CSV.exists():
        import csv as _csv
        with open(FINANCE_CSV, "r", encoding="utf-8-sig", newline="") as f:
            reader = _csv.DictReader(f)
            old_fieldnames = reader.fieldnames or []
            old_rows = list(reader)
        merged_rows = old_rows + all_rows
        # 字段合并
        all_keys = list(old_fieldnames)
        for r in all_rows:
            for k in r.keys():
                if k not in all_keys:
                    all_keys.append(k)
        with open(FINANCE_CSV, "w", encoding="utf-8-sig", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(merged_rows)
        logger.info("[finance] 追加写 %s: 新增 %d, 总 %d 行", FINANCE_CSV, len(all_rows), len(merged_rows))
    else:
        write_csv(FINANCE_CSV, all_rows)


# ============================================================
# Task 3: benchmark_data.csv ← westock kline (6 个指数)
# ============================================================

BENCHMARKS = [
    ("sh000300", "沪深300"),
    ("sh000905", "中证500"),
    ("sh000016", "上证50"),
    ("sh000852", "中证1000"),
    ("sh000688", "科创50"),
    ("sz399006", "创业板指"),
]


def task_benchmark(limit: int, dry_run: bool) -> None:
    logger.info("[benchmark] 待采集指数: %d 个 (limit=%d)", len(BENCHMARKS), limit)
    if dry_run:
        for sym, name in BENCHMARKS:
            logger.info("  - %s (%s)", name, sym)
        return

    all_rows: list[dict] = []
    t0 = time.time()
    for sym, name in BENCHMARKS:
        # 入参校验
        if not _SYMBOLS_RE.match(sym):
            logger.warning("跳过非法 code: %s", sym)
            continue
        # westock kline 单股调用不返回 symbol 列 (批量调用才会返回), 需手动补 code
        r = westock_batch([sym], "kline", "--period", "day",
                          "--limit", str(limit), timeout=120)
        for row in r:
            row["code"] = sym
            row["name"] = name
            # 统一字段: last → close (与 daily_price 一致)
            if "last" in row and "close" not in row:
                row["close"] = row.pop("last")
        all_rows.extend(r)
        logger.info("  %s (%s): %d 行", name, sym, len(r))
        time.sleep(0.6)

    logger.info("[benchmark] 总行数: %d  耗时: %.1fs",
                len(all_rows), time.time() - t0)
    write_csv(BENCHMARK_CSV, all_rows)


# ============================================================
# Main
# ============================================================

def main() -> int:
    p = argparse.ArgumentParser(
        description="一站式补齐缺失的市场基础数据 (westock + baostock)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例: python scripts/import_from_westock_baostock_akshare.py --task profile --batch-size 50",
    )
    p.add_argument("--task", choices=["profile", "finance", "benchmark", "all"],
                   default="profile", help="要执行的任务 (默认 profile)")
    p.add_argument("--batch-size", type=int, default=100,
                   help="westock 批量大小 (默认 100, 调小可降低单批失败影响)")
    p.add_argument("--year", type=int, default=2024, help="finance 年份 (默认 2024, 简单模式)")
    p.add_argument("--quarter", type=int, default=4, help="finance 季度 (1-4, 默认 4, 简单模式)")
    p.add_argument("--start-year", type=int, default=None,
                   help="finance 起始年份 (多年模式; 与 --end-year --quarters 配合)")
    p.add_argument("--end-year", type=int, default=None,
                   help="finance 结束年份 (含)")
    p.add_argument("--quarters", type=int, nargs="+", default=None,
                   help="finance 季度列表, 如 --quarters 1 2 3 4")
    p.add_argument("--limit", type=int, default=1500,
                   help="benchmark kline limit (默认 1500 ≈ 6 年日线)")
    p.add_argument("--skip-existing", action="store_true",
                   help="profile 任务: 跳过 stock_profile.csv 已存在的 code (增量)")
    p.add_argument("--dry-run", action="store_true", help="只打印不执行 npx / baostock, 不写文件")
    args = p.parse_args()

    logger.info("task=%s batch=%d year=%dQ%d limit=%d dry_run=%s skip_existing=%s",
                args.task, args.batch_size, args.year, args.quarter,
                args.limit, args.dry_run, args.skip_existing)

    try:
        if args.task in ("profile", "all"):
            task_profile(args.batch_size, args.dry_run, args.skip_existing)
        if args.task in ("finance", "all"):
            task_finance(args.year, args.quarter, args.dry_run,
                         args.start_year, args.end_year, args.quarters)
        if args.task in ("benchmark", "all"):
            task_benchmark(args.limit, args.dry_run)
    except KeyboardInterrupt:
        logger.warning("用户中断")
        return 130
    except Exception as e:
        logger.exception("执行失败: %s", e)
        return 1
    logger.info("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())