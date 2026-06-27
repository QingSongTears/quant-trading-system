#!/usr/bin/env python3
"""
fill_recent_holder_num.py — 用 westock shareholder 补 holder_num 表

数据源
------
westock-data shareholder <codes>  (无 --date flag, 总是返回最新可得的季度数据)

每只股票返回 markdown 多段:
  #### sh600000 浦发银行 (2026-03-31)
  **十大股东** ... (略)
  **十大流通股东** ... (略)
  **股东户数统计**
  | date | totalSHNum | aSHNum | avgHoldShares | aAvgHoldShares |
  | 2026-03-31 | 151091 | 151091 | 220435.62 | 220435.62 |
  | 2026-02-28 | 160813 | 160813 | 207109.12 | 207109.12 |
  ...

我们只解析 **股东户数统计** 段, 写 (code, end_date, holder_num, pre_holder_num,
holder_change_pct, avg_holding) 到 DB.

注意
----
- westock 季度数据最新只到 2026-03-31 (Q2 披露是 6-30)
- 本脚本只能补 2026-03-31 及更早季度, **无法**把 DB max(end_date) 推到 6-24
- 用途: 给 ~1539 只没 holder_num 数据的股票补 Q1 2026 数据

调用
----
  python scripts/fill_recent_holder_num.py --dry-run
  python scripts/fill_recent_holder_num.py --batch 50
"""
from __future__ import annotations
import argparse
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

DB_PATH = PROJECT_ROOT / "database" / "quant.db"
TQ_CSV = PROJECT_ROOT / "market_data" / "raw" / "reference" / "tencent_quotes.csv"
LOG_PATH = Path(r"C:\Users\admin\AppData\Local\Temp\fill_recent_holder_num.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

BATCH = 50
CREATE_NO_WINDOW = 0x08000000
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
logger = logging.getLogger("fill_recent_holder_num")


def load_tradable_codes() -> list[tuple[str, str]]:
    """同 fill_recent_generic.py 的实现"""
    import csv
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    with open(TQ_CSV, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw = (row.get("code") or "").strip().lower()
            mkt = (row.get("market") or "").strip().lower()
            if mkt not in ("sh", "sz", "bj"):
                m = re.match(r"^([a-z]{2})\d{6}$", raw)
                if not m: continue
                mkt = m.group(1)
            m = re.match(r"^([a-z]{2})(\d{6})$", raw)
            if not m: continue
            code6 = m.group(2)
            if code6 in seen: continue
            seen.add(code6)
            pairs.append((code6, mkt))
    return pairs


def to_westock(code6: str, market: str) -> str:
    return f"{market.lower()}{code6}"


def westock_call(codes: list[str], subcmd: str, timeout: int = 180) -> str:
    joined = ",".join(codes)
    if not SYMBOLS_RE.match(joined):
        raise ValueError(f"symbols 非法: {joined!r}")
    if len(codes) > BATCH:
        raise ValueError(f"batch {len(codes)} > {BATCH}")
    cmd = ["npx.cmd", "-y", "westock-data-clawhub@1.0.4", subcmd, joined]
    r = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, shell=False,
        encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if r.returncode != 0:
        raise RuntimeError(f"returncode={r.returncode}: {r.stderr[:200]}")
    return r.stdout


def parse_holder_num(text: str) -> dict[str, list[tuple[str, int, float]]]:
    """解析 westock shareholder 输出 → {code6: [(date, totalSHNum, avgHoldShares), ...]}

    每只股票有 markdown 段:
      #### sh600000 浦发银行 (2026-03-31)
      ...
      **股东户数统计**
      | date | totalSHNum | aSHNum | avgHoldShares | aAvgHoldShares |
      | 2026-03-31 | 151091 | 151091 | 220435.62 | 220435.62 |
    """
    result: dict[str, list[tuple[str, int, float]]] = {}
    current_code: str | None = None
    in_stats = False

    for line in text.splitlines():
        line_stripped = line.strip()
        # 检测 #### sh600000 浦发银行 (2026-03-31)
        m = re.match(r"^####\s+(sh|sz|bj)(\d{6})", line_stripped)
        if m:
            current_code = m.group(2)
            in_stats = False
            continue
        # 检测 **股东户数统计**
        if "股东户数统计" in line_stripped:
            in_stats = True
            continue
        # 检测下一个 **段** 退出统计
        if in_stats and line_stripped.startswith("**") and "股东户数" not in line_stripped:
            in_stats = False
            continue
        if not in_stats or not current_code:
            continue
        # 数据行: | date | totalSHNum | aSHNum | avgHoldShares | aAvgHoldShares |
        if not line_stripped.startswith("|"):
            continue
        cells = [c.strip() for c in line_stripped.split("|")[1:-1]]
        if len(cells) < 3:
            continue
        date_str = cells[0]
        if not DATE_RE.match(date_str):
            continue
        try:
            total = int(cells[1])
            avg = float(cells[3])
        except (ValueError, IndexError):
            continue
        result.setdefault(current_code, []).append((date_str, total, avg))

    # 按日期降序
    for code in result:
        result[code].sort(key=lambda x: x[0], reverse=True)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--batch", type=int, default=BATCH)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--source", default="westock",
                   help="写入 holder_num.source 字段, 默认 westock")
    args = p.parse_args()

    if not DB_PATH.exists():
        logger.error("DB 不存在: %s", DB_PATH)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    pairs = load_tradable_codes()
    # 只跑 DB 里没 holder_num 数据的股票 (省时间)
    cur.execute("SELECT DISTINCT code FROM holder_num")
    have_codes = {r[0] for r in cur.fetchall()}
    targets = [(c, m) for c, m in pairs if c not in have_codes]
    logger.info("总股 %d, 已有 holder_num %d, 待补 %d",
                len(pairs), len(have_codes), len(targets))

    if args.dry_run:
        logger.info("[DRY-RUN] 待跑 %d 只, BATCH=%d, 共 %d 批",
                    len(targets), args.batch, (len(targets) + args.batch - 1) // args.batch)
        return 0

    n_batches = (len(targets) + args.batch - 1) // args.batch
    total_inserted = 0
    succ = fail = 0
    t1 = time.time()
    for bi in range(n_batches):
        batch = targets[bi * args.batch: (bi + 1) * args.batch]
        ws_codes = [to_westock(c, m) for c, m in batch]
        try:
            out = westock_call(ws_codes, "shareholder", timeout=180)
            rows = parse_holder_num(out)
            succ += 1
        except Exception as e:
            fail += 1
            logger.warning("batch %d/%d FAIL: %s", bi + 1, n_batches, str(e)[:100])
            time.sleep(2)
            continue

        # 写 DB
        n = 0
        for code6, items in rows.items():
            # 按日期升序, 这样 pre_holder_num 容易算
            items_sorted = sorted(items, key=lambda x: x[0])
            for i, (date, total, avg) in enumerate(items_sorted):
                # pre_holder_num = 上一季度 (i+1)
                pre_total = items_sorted[i + 1][1] if i + 1 < len(items_sorted) else None
                change_pct = None
                if pre_total and pre_total > 0:
                    change_pct = round((total - pre_total) / pre_total * 100, 4)
                try:
                    cur.execute(
                        """INSERT OR IGNORE INTO holder_num
                           (code, end_date, holder_num, pre_holder_num,
                            holder_change_pct, avg_holding, source)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (code6, date, total, pre_total, change_pct, avg, args.source),
                    )
                    if cur.rowcount > 0:
                        n += 1
                except sqlite3.IntegrityError:
                    pass
                except Exception as e:
                    logger.debug("INSERT err: %s", e)
        conn.commit()
        total_inserted += n
        if (bi + 1) % 5 == 0 or bi == n_batches - 1:
            elapsed = time.time() - t1
            rate = (bi + 1) / max(1e-3, elapsed)
            eta = (n_batches - bi - 1) / max(1e-3, rate)
            logger.info("%d/%d 批 succ=%d fail=%d +%d行 累计 +%d  %.1f批/s  ETA %.1fmin",
                        bi + 1, n_batches, succ, fail, n, total_inserted,
                        rate, eta / 60)
        time.sleep(0.6)

    logger.info("✅ 完成 +%d 行 (耗时 %.1fs)", total_inserted, time.time() - t0)
    cur.execute("SELECT COUNT(*), MAX(end_date) FROM holder_num")
    n, mx = cur.fetchone()
    logger.info("DB holder_num: %d 行, max(end_date) = %s", n, mx)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())