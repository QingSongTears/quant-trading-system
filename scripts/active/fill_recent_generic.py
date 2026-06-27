#!/usr/bin/env python3
"""
fill_recent_generic.py — 通用 westock 增量补全工具（2026-06-25 重写）

适用场景
--------
- 已知 westock subcmd, 需要补几天到几天前缺失的数据到 DB
- 当前覆盖任务:
    1. daily_price        ← westock kline        (--start --end 范围拉取)
    2. technical_indicators← westock technical    (--start --end)
    3. holder_num         ← westock shareholder  (无 --date, 解析末尾公告日期)

设计要点
--------
- 复用 scripts/fill_recent_klines.py 的 proven 模式:
  · BATCH=50 (westock 限制)
  · westock 白名单正则 ^([a-z]{2})\\d{6}(,...) $
  · CREATE_NO_WINDOW 防 npx 弹窗
  · INSERT OR IGNORE + UNIQUE(code, trade_date) 幂等
- 增量判定: SELECT MAX(trade_date) → start=max+1 → 拉到 target_date
- 失败单批 SKIP, 不中断整表

调用
----
  # 补 daily_price 6-24
  python scripts/fill_recent_generic.py --task daily_price --start 2026-06-24 --end 2026-06-24

  # 补 technical_indicators 6-17~6-24
  python scripts/fill_recent_generic.py --task technical_indicators --start 2026-06-17 --end 2026-06-24

  # 跑所有任务到今天
  python scripts/fill_recent_generic.py --task all --end 2026-06-24
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

DB_PATH = PROJECT_ROOT / "database" / "quant.db"
TQ_CSV = PROJECT_ROOT / "market_data" / "raw" / "reference" / "tencent_quotes.csv"
LOG_PATH = Path(r"C:\Users\admin\AppData\Local\Temp\fill_recent_generic.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

# ============ 可调参数 ============
BATCH = 50
RETRY_PER_BATCH = 2
PAUSE = 0.6
CREATE_NO_WINDOW = 0x08000000

# westock 入参白名单 (防命令注入)
SYMBOLS_RE = re.compile(r"^[a-z]{2}\d{6}(,[a-z]{2}\d{6})*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

t0 = time.time()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8"),
              logging.StreamHandler()],
)
logger = logging.getLogger("fill_recent_generic")


# ============ 工具函数 ============

def load_tradable_codes() -> list[tuple[str, str]]:
    """从 tencent_quotes.csv 读 (code6, market) 列表

    tencent_quotes.csv 的 code 字段实际是 'sh600000' 形式 (带市场前缀),
    market 字段为空. 我们从 code 前缀提取市场.
    """
    import csv
    import re
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    with open(TQ_CSV, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw = (row.get("code") or "").strip().lower()
            mkt = (row.get("market") or "").strip().lower()
            # 优先用 market 字段, 否则从 code 前缀提取
            if mkt not in ("sh", "sz", "bj"):
                m = re.match(r"^([a-z]{2})\d{6}$", raw)
                if not m:
                    continue
                mkt = m.group(1)
            m = re.match(r"^([a-z]{2})(\d{6})$", raw)
            if not m:
                continue
            code6 = m.group(2)
            if code6 in seen:
                continue
            seen.add(code6)
            pairs.append((code6, mkt))
    return pairs


def to_westock(code6: str, market: str) -> str:
    return f"{market.lower()}{code6}"


def _f(s):
    """宽容 float 解析: 处理 nan/-/空/None"""
    if s is None:
        return None
    if isinstance(s, str):
        s = s.strip()
        if s in ("", "-", "--", "nan", "None"):
            return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _i(s):
    f = _f(s)
    return int(f) if f is not None else None


def westock_call(codes: list[str], subcmd: str, *flags: str,
                 timeout: int = 120) -> str:
    """调 westock, 返回 stdout 文本（不解析）
    失败抛 RuntimeError
    """
    joined = ",".join(codes)
    if not SYMBOLS_RE.match(joined):
        raise ValueError(f"symbols 非法: {joined!r}")
    if len(codes) > BATCH:
        raise ValueError(f"batch {len(codes)} > {BATCH}")
    cmd = ["npx.cmd", "-y", "westock-data-clawhub@1.0.4", subcmd,
           joined, *flags]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, shell=False,
            encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"timeout ({timeout}s): {subcmd} {len(codes)} 股")
    if r.returncode != 0:
        raise RuntimeError(f"returncode={r.returncode}: {r.stderr[:200]}")
    return r.stdout


def parse_markdown_table_kline(text: str) -> list[dict]:
    """kline/technical 解析: 多种格式都支持

    格式 A (westock kline --start --end 批量):
      | symbol | date | open | last | ... |  ← header 有 symbol 列
      | sh600000 | 2026-06-25 | 8.85 | ... |

    格式 B (kline 历史 - 隐式 symbol):
      | date | open | last | ... |  ← header 第一列是 date
      | sh600000 | 2026-06-25 | 8.85 | ... |  ← 数据行多 1 列, 首列是 symbol

    格式 C (technical --date 批量):
      | code | name | date | closePrice | ma.MA_5 | ... |  ← 标准 code 列
      | sh600000 | 浦发银行 | 2026-06-24 | 8.90 | ... |

    返回: 字段名: code, date, open, high, low, close (from last), volume, amount, ...
    """
    SYMBOL_RE = re.compile(r"^[a-z]{2}\d{6}$")
    headers: list[str] | None = None
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if re.match(r"^\|\s*-+", line):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if not cells:
            continue
        if headers is None:
            headers = cells
            continue
        # 格式 A: header 第一列是 symbol
        if headers[0] == "symbol" and len(cells) == len(headers):
            row = dict(zip(headers, cells))
            row["code"] = row.pop("symbol")
            if "last" in row and "close" not in row:
                row["close"] = row.pop("last")
            rows.append(row)
        # 格式 C: header 第一列是 code (标准)
        elif headers[0] == "code" and len(cells) == len(headers):
            row = dict(zip(headers, cells))
            if "last" in row and "close" not in row:
                row["close"] = row.pop("last")
            rows.append(row)
        # 格式 B: 数据行多 1 列, 首列是 symbol
        elif SYMBOL_RE.match(cells[0]) and len(cells) == len(headers) + 1:
            row = {"code": cells[0]}
            for h, v in zip(headers, cells[1:]):
                row[h] = v
            if "last" in row and "close" not in row:
                row["close"] = row.pop("last")
            rows.append(row)
    return rows


def parse_markdown_table_simple(text: str) -> list[dict]:
    """asfund 等简单表: header 第一列是 code, 后续对应
    """
    headers: list[str] | None = None
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if re.match(r"^\|\s*-+", line):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if not cells:
            continue
        if headers is None:
            headers = cells
            continue
        if len(cells) == len(headers):
            row = dict(zip(headers, cells))
            rows.append(row)
    return rows


# ============ 任务定义 ============

TASK_DEFS = {
    "daily_price": {
        "table": "daily_price",
        "date_col": "trade_date",
        "subcmd": "kline",
        "extra_flags": ["--period", "day"],
        "parser": parse_markdown_table_kline,
        "field_map": {
            "code": "code", "date": "trade_date",
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume", "amount": "amount",
        },
        "sql": """INSERT OR IGNORE INTO daily_price
                  (code, trade_date, open, high, low, close, volume, amount)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        "row_builder": lambda r: (
            r["code"][2:], r["date"],  # 去掉 sh/sz 前缀
            _f(r.get("open")), _f(r.get("high")), _f(r.get("low")),
            _f(r.get("close")), _i(r.get("volume")), _f(r.get("amount")),
        ),
        "date_flag_format": "range",  # kline 用 --start --end 精确单日
    },
    "technical_indicators": {
        "table": "technical_indicators",
        "date_col": "trade_date",
        "subcmd": "technical",
        "extra_flags": [],
        "parser": parse_markdown_table_kline,  # 同 kline 格式 (隐式 symbol)
        "field_map": {
            "code": "code", "date": "trade_date",
            "closePrice": "_close",  # 仅辅助,不入 DB
            "macd.DIF": "macd_dif", "macd.DEA": "macd_dea", "macd.MACD": "macd_hist",
            "rsi.RSI_6": "_rsi6", "rsi.RSI_12": "_rsi12", "rsi.RSI_24": "_rsi24",
            "kdj.KDJ_K": "kdj_k", "kdj.KDJ_D": "kdj_d", "kdj.KDJ_J": "kdj_j",
            "boll.BOLL_UPPER": "boll_upper", "boll.BOLL_MID": "boll_mid", "boll.BOLL_LOWER": "boll_lower",
        },
        # technical_indicators 表只有这 12 列: macd/kdj/boll/rsi
        "sql": """INSERT OR IGNORE INTO technical_indicators
                  (code, trade_date, macd_dif, macd_dea, macd_hist,
                   rsi14, kdj_k, kdj_d, kdj_j,
                   boll_mid, boll_upper, boll_lower)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        "row_builder": lambda r: (
            r["code"][2:], r["date"],
            _f(r.get("macd.DIF")), _f(r.get("macd.DEA")), _f(r.get("macd.MACD")),
            _f(r.get("rsi.RSI_12")),  # 表里只有 rsi14, 用 RSI_12 替代
            _f(r.get("kdj.KDJ_K")), _f(r.get("kdj.KDJ_D")), _f(r.get("kdj.KDJ_J")),
            _f(r.get("boll.BOLL_MID")), _f(r.get("boll.BOLL_UPPER")), _f(r.get("boll.BOLL_LOWER")),
        ),
        "date_flag_format": "single",  # technical 用 --date 截面
    },
}


# ============ 单任务执行 ============

def run_task(task_name: str, target_end: str, start_override: str | None,
             dry_run: bool, conn: sqlite3.Connection) -> int:
    """跑一个任务, 返回新增行数"""
    task = TASK_DEFS[task_name]
    cur = conn.cursor()

    # 查 max date
    cur.execute(f"SELECT MAX({task['date_col']}) FROM {task['table']}")
    db_max = cur.fetchone()[0] or "1900-01-01"

    # 计算实际起始日期
    if start_override:
        start = start_override
    else:
        start = (datetime.strptime(db_max, "%Y-%m-%d").date()
                 + timedelta(days=1)).strftime("%Y-%m-%d")
    end = target_end

    if start > end:
        logger.info("[%s] DB 已是最新 (max=%s), 跳过", task_name, db_max)
        return 0

    # 交易日列表
    days = []
    d = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    while d <= e:
        if d.weekday() < 5:  # 跳过周末
            days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    if dry_run:
        logger.info("[DRY-RUN] %s: %s → %s (%d 个交易日), 起 max=%s",
                    task_name, start, end, len(days), db_max)
        return 0

    pairs = load_tradable_codes()
    logger.info("[%s] %s → %s (%d 个交易日, %d 只股, BATCH=%d)",
                task_name, start, end, len(days), len(pairs), BATCH)
    n_batches = (len(pairs) + BATCH - 1) // BATCH

    total_inserted = 0
    for day in days:
        # 该日期 DB 已有的 code (避免重复拉)
        cur.execute(
            f"SELECT code FROM {task['table']} WHERE {task['date_col']}=?",
            (day,))
        already = {r[0] for r in cur.fetchall()}
        if len(already) >= len(pairs) * 0.95:
            logger.info("  [%s] DB 已有 %d 只, 跳过", day, len(already))
            continue

        day_rows: list[dict] = []
        succ = fail = 0
        t1 = time.time()
        for bi in range(n_batches):
            batch_pairs = pairs[bi * BATCH: (bi + 1) * BATCH]
            ws_codes = [to_westock(c, m) for c, m in batch_pairs]
            try:
                # 范围拉取: --start day --end day
                if task["date_flag_format"] == "range":
                    flags = list(task["extra_flags"]) + ["--start", day, "--end", day]
                else:
                    flags = list(task["extra_flags"]) + ["--date", day]
                out = westock_call(ws_codes, task["subcmd"], *flags, timeout=90)
                rows = task["parser"](out)
                day_rows.extend(rows)
                succ += 1
            except Exception as e:
                fail += 1
                logger.warning("  [%s] batch %d/%d FAIL: %s", day, bi + 1, n_batches, str(e)[:100])
            if (bi + 1) % 10 == 0 or bi == n_batches - 1:
                rate = (bi + 1) / max(1e-3, time.time() - t1)
                eta = (n_batches - bi - 1) / max(1e-3, rate)
                logger.info("  [%s] %d/%d 批 succ=%d fail=%d +%d行  ETA %.1fmin",
                            day, bi + 1, n_batches, succ, fail, len(day_rows), eta / 60)
            time.sleep(PAUSE)

        # 过滤日期
        day_rows = [r for r in day_rows if r.get("date") == day]
        if not day_rows:
            logger.warning("  [%s] 无数据可插入", day)
            continue

        # INSERT OR IGNORE
        inserted = 0
        sql = task["sql"]
        rb = task["row_builder"]
        for r in day_rows:
            try:
                vals = rb(r)
            except Exception as e:
                logger.warning("  skip row (build): %s | err=%s",
                               {k: r.get(k) for k in ('code','date')}, e)
                continue
            try:
                cur.execute(sql, vals)
                if cur.rowcount > 0:
                    inserted += 1
            except sqlite3.IntegrityError as e:
                pass  # UNIQUE 冲突, 跳过
            except Exception as e:
                logger.warning("  INSERT err: vals=%s | %s", vals, e)
        conn.commit()
        total_inserted += inserted
        logger.info("  [%s] INSERT %d 行 (raw %d, succ=%d fail=%d, %.1fs)",
                    day, inserted, len(day_rows), succ, fail, time.time() - t1)

    logger.info("[%s] ✅ 完成 +%d 行 (起 max=%s)", task_name, total_inserted, db_max)
    return total_inserted


# ============ main ============

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task", action="append",
                   help=f"任务名 (可多次): {list(TASK_DEFS.keys())} 或 'all'")
    p.add_argument("--start", help="起始日 (含), 默认 = DB max+1")
    p.add_argument("--end", help="结束日 (含), 默认 = 今天")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    target_end = args.end or date.today().strftime("%Y-%m-%d")

    if args.task and "all" in args.task:
        tasks = list(TASK_DEFS.keys())
    elif args.task:
        tasks = args.task
    else:
        tasks = list(TASK_DEFS.keys())

    logger.info("=" * 60)
    logger.info("fill_recent_generic: tasks=%s, end=%s", tasks, target_end)
    logger.info("=" * 60)

    if not DB_PATH.exists():
        logger.error("DB 不存在: %s", DB_PATH)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    total = 0
    for t in tasks:
        try:
            n = run_task(t, target_end, args.start, args.dry_run, conn)
            total += n
        except Exception as e:
            logger.error("[%s] ❌ 异常: %s", t, e)
            import traceback
            logger.error(traceback.format_exc())
    conn.close()
    logger.info("=" * 60)
    logger.info("✅ 全部完成! 新增 %d 行 (耗时 %.1fs)", total, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
