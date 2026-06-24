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

    输入示例:
        [Batch] 状态: success | 总数: 3 | 成功: 3 | 失败: 0
        | code | name | listedDate | business | industry | ... |
        | --- | --- | --- | --- |
        | sh600000 | 浦发银行 | 1999-11-10 | ... | 银行 | ... |

    返回: [{"code": "sh600000", "name": "浦发银行", "listedDate": "1999-11-10", ...}, ...]
    """
    rows: list[dict] = []
    headers: list[str] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        # 跳过 [Batch] 元信息行
        if "[Batch]" in line or "总数" in line or "成功" in line or "失败" in line:
            continue
        # 分隔行 | --- | --- |
        if re.match(r"^\|\s*-+(\s*\|\s*-+)*\s*\|?$", line):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        # header 行: 第一列是 code / symbol / date 之一
        if headers is None:
            if cells and cells[0] in ("code", "symbol", "date"):
                headers = cells
            continue
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
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
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, shell=False,
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

def task_finance(year: int, quarter: int, dry_run: bool, start_year: int | None) -> None:
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        logger.error("[finance] baostock 登录失败: %s", lg.error_msg)
        return
    logger.info("[finance] baostock 登录 OK")

    pairs = load_tradable_codes()
    logger.info("[finance] 待采集股票: %d 只, year=%d Q%d", len(pairs), year, quarter)

    # baostock code 格式: sh.600000
    bs_codes = [f"{m}.{c}" for c, m in pairs]

    if dry_run:
        logger.info("[dry-run] 示例前 5: %s", bs_codes[:5])
        bs.logout()
        return

    all_rows: list[dict] = []
    fail_codes: list[str] = []
    t0 = time.time()
    for i, bscode in enumerate(bs_codes):
        rs = bs.query_profit_data(code=bscode, year=year, quarter=quarter)
        if rs.error_code != "0":
            fail_codes.append(bscode)
            continue
        if not rs.fields:
            continue
        rows_count = 0
        while rs.next():
            all_rows.append(dict(zip(rs.fields, rs.get_row_data())))
            rows_count += 1
        if rows_count == 0:
            fail_codes.append(bscode)  # 该季度无数据
        if (i + 1) % 200 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed else 0
            eta = (len(bs_codes) - i - 1) / rate if rate else 0
            logger.info("  %d/%d  累计 %d 行  %.1f 股/s  ETA %.1fmin",
                        i + 1, len(bs_codes), len(all_rows), rate, eta / 60)

    bs.logout()
    elapsed = time.time() - t0
    logger.info("[finance] 总行数: %d  无数据/失败: %d  耗时: %.1fmin",
                len(all_rows), len(fail_codes), elapsed / 60)
    if fail_codes:
        logger.info("[finance] 失败示例 (前 10): %s", fail_codes[:10])
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
        # westock kline 是单股接口 (没有逗号批量语义), 参数顺序: code 在前, --period 在后
        r = westock_batch([sym], "kline", "--period", "day",
                          "--limit", str(limit), timeout=120)
        # 改名: 加 name + symbol 列
        for row in r:
            row["name"] = name
            # 字段名标准化 (westock 用 symbol, 我们统一 code)
            if "symbol" in row and "code" not in row:
                row["code"] = row.pop("symbol")
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
    p.add_argument("--year", type=int, default=2024, help="finance 年份 (默认 2024)")
    p.add_argument("--quarter", type=int, default=4, help="finance 季度 (1-4, 默认 4)")
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
            task_finance(args.year, args.quarter, args.dry_run, None)
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