#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
并发追跑技术指标
================

westock technical 接口每次只返 1 只 + 1 个指标组。
ma + macd + kdj + rsi + boll + bias + wr + dmi 共 8 组,
每只 1 天 8 次请求, 5026 只 × 4 天 × 8 组 = 16万 次 (用 8 线程约 1.5 小时)

为提速: 改用 -g ma (一次取 MA5/10/20/60 + BOLL + KDJ + RSI + MACD 全指标),
westock 的 group=all 会一次性返回所有指标。
"""
import argparse
import csv
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
WESTOCK_PKG = "westock-data-clawhub@1.0.4"
NPM_PATH = "C:/Users/aini7/.workbuddy/binaries/node/versions/22.22.2/npx.cmd"
MAX_WORKERS = 6
DST = ROOT / "market_data" / "raw" / "technical" / "tech_indicators_2026.csv"
LOCK = threading.Lock()
BUF: list[dict] = []


def load_codes() -> list[str]:
    p = ROOT / "market_data" / "stock_profile.csv"
    codes = []
    with open(p, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if row and row[0].strip():
                codes.append(row[0].strip())
    return codes


def to_market_code(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "2", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return code


def fetch_one(code: str, d: str) -> list[dict]:
    """单只单日拉所有指标 (group=all 一次取 MA+MACD+KDJ+RSI+BOLL+BIAS+WR+DMI)"""
    mc = to_market_code(code)
    cmd = [NPM_PATH, "-y", WESTOCK_PKG,
           "technical", mc, "--group", "all", "--date", d]
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--no-warnings"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    except Exception:
        return []
    if r.returncode != 0:
        return []
    rows = []
    headers = None
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("=") or "---" in line:
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if headers is None:
                if not any(c.replace(".", "").replace("-", "").isdigit() for c in cells if c):
                    headers = cells
                continue
            if len(cells) == len(headers):
                row = dict(zip(headers, cells))
                row["code"] = str(row.get("code", code)).replace("sh", "").replace("sz", "").replace("bj", "")
                row["market"] = row.get("market", mc[:2])
                row["date"] = row.get("date", d)
                rows.append(row)
    return rows


def flush():
    if not BUF:
        return
    with LOCK:
        rows = list(BUF)
        BUF.clear()
    DST.parent.mkdir(parents=True, exist_ok=True)
    new = pd.DataFrame(rows)
    if DST.exists():
        old = pd.read_csv(DST, encoding="utf-8-sig", low_memory=False)
        combined = pd.concat([old, new], ignore_index=True)
    else:
        combined = new
    combined = combined.drop_duplicates(subset=["code", "date"], keep="last")
    combined.to_csv(DST, index=False, encoding="utf-8-sig")


def process_day(d: str, codes: list[str]):
    print(f"\n=== {d} ===", flush=True)
    total = len(codes)
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        future_map = {ex.submit(fetch_one, code, d): code for code in codes}
        for fut in as_completed(future_map):
            code = future_map[fut]
            done += 1
            try:
                rows = fut.result()
            except Exception:
                rows = []
            if rows:
                with LOCK:
                    BUF.extend(rows)
                if len(BUF) >= 500:
                    flush()
            if done % 200 == 0 or done == total:
                elapsed = time.time() - t0
                speed = done / max(elapsed, 0.01)
                eta = (total - done) / max(speed, 0.1)
                print(f"  {done}/{total}  buf={len(BUF)}  {speed:.1f}只/s  ETA={eta:.0f}s", flush=True)
    flush()
    print(f"  ✅ {d} 完成 ({time.time()-t0:.1f}s)", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    days = [d.strip() for d in args.days.split(",")]
    codes = load_codes()
    if args.limit:
        codes = codes[:args.limit]
    total = len(days) * len(codes)
    print(f"技术指标并发追跑: {len(days)}天 × {len(codes)}只 = {total} 次请求")
    print(f"  并发: {MAX_WORKERS}  预计: {total/MAX_WORKERS*0.3/60:.1f} 分钟")

    t0 = time.time()
    for d in days:
        process_day(d, codes)
    print(f"\n总耗时: {(time.time()-t0)/60:.1f} 分钟")


if __name__ == "__main__":
    main()