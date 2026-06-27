#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
并发追跑融资融券 + 大宗交易
==========================

westock margintrade/blocktrade 接口每次只返 1 只, 用 8 线程并发把全市场灌完。
"""
import argparse
import csv
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
WESTOCK_PKG = "westock-data-clawhub@1.0.4"
NODE_PATH = "C:/Users/aini7/.workbuddy/binaries/node/versions/22.22.2/node.exe"
NPM_PATH = "C:/Users/aini7/.workbuddy/binaries/node/versions/22.22.2/npx.cmd"
MAX_WORKERS = 8  # 并发线程
BATCH_PAUSE = 0.2

# 全局锁 + 写入缓冲
write_lock = threading.Lock()
margin_buf = []
block_buf = []


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


def fetch_one(code: str, d: str, kind: str) -> dict | None:
    """单只单日请求"""
    mc = to_market_code(code)
    cmd = [NPM_PATH, "-y", WESTOCK_PKG,
           "margintrade" if kind == "margin" else "blocktrade",
           mc, "--date", d]
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--no-warnings"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=30, env=env)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    # 解析 markdown 表格
    out = r.stdout
    if "| code |" not in out:
        return None
    rows = []
    headers = None
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("=") or "---" in line:
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if headers is None:
                # 找表头行 (无数字)
                if not any(c.replace(".", "").replace("-", "").isdigit() for c in cells if c):
                    headers = cells
                continue
            if len(cells) == len(headers):
                rows.append(dict(zip(headers, cells)))
    if not rows:
        return None
    r0 = rows[0]
    if kind == "margin":
        return {
            "code": str(r0.get("code", code)).replace("sh", "").replace("sz", "").replace("bj", ""),
            "market": r0.get("market", mc[:2]),
            "name": r0.get("name", ""),
            "date": r0.get("date", d),
            "rz": r0.get("FinanceValue", "0"),
            "rzmre": r0.get("SecurityValue", "0"),
            "rzche": r0.get("FinanceBuyValue", "0"),
            "rqye": r0.get("FinanceRefundValue", "0"),
            "rqmcl": r0.get("TradingValue", "0"),
            "rqchl": r0.get("TradingValueDif", "0"),
        }
    else:
        return {
            "code": str(r0.get("code", code)).replace("sh", "").replace("sz", "").replace("bj", ""),
            "market": r0.get("market", mc[:2]),
            "name": r0.get("name", ""),
            "date": r0.get("date", d),
            "deal_price": r0.get("DealPrice", "0"),
            "close_price": r0.get("ClosePrice", "0"),
            "premium_pct": r0.get("PremiumPct", "0"),
            "vol": r0.get("Vol", "0"),
            "amount": r0.get("Amount", "0"),
            "buyer": r0.get("Buyer", ""),
            "seller": r0.get("Seller", ""),
        }


def flush_margin():
    if not margin_buf:
        return
    dst = ROOT / "market_data" / "margin_trading.csv"
    old = pd.read_csv(dst, encoding="utf-8-sig") if dst.exists() else pd.DataFrame()
    df_new = pd.DataFrame(margin_buf)
    margin_buf.clear()
    combined = pd.concat([old, df_new], ignore_index=True) if not old.empty else df_new
    combined = combined.drop_duplicates(subset=["code", "date"], keep="last")
    combined.to_csv(dst, index=False, encoding="utf-8-sig")


def flush_block():
    if not block_buf:
        return
    dst = ROOT / "market_data" / "block_trade.csv"
    old = pd.read_csv(dst, encoding="utf-8-sig") if dst.exists() else pd.DataFrame()
    df_new = pd.DataFrame(block_buf)
    block_buf.clear()
    combined = pd.concat([old, df_new], ignore_index=True) if not old.empty else df_new
    combined = combined.drop_duplicates(subset=["code", "date", "deal_price"], keep="last")
    combined.to_csv(dst, index=False, encoding="utf-8-sig")


def process_day(d: str, codes: list[str], kinds: list[str]):
    print(f"\n=== {d} ===")
    tasks = [(code, kind) for code in codes for kind in kinds]
    total = len(tasks)
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        future_map = {}
        for code, kind in tasks:
            fut = ex.submit(fetch_one, code, d, kind)
            future_map[fut] = (code, kind)
        for fut in as_completed(future_map):
            code, kind = future_map[fut]
            done += 1
            try:
                row = fut.result()
            except Exception:
                row = None
            if row:
                if kind == "margin":
                    with write_lock:
                        margin_buf.append(row)
                        if len(margin_buf) >= 200:
                            flush_margin()
                else:
                    with write_lock:
                        block_buf.append(row)
                        if len(block_buf) >= 200:
                            flush_block()
            if done % 500 == 0 or done == total:
                print(f"  {done}/{total}  margin_buf={len(margin_buf)}  block_buf={len(block_buf)}", flush=True)
    flush_margin()
    flush_block()
    print(f"  ✅ {d} 完成")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", required=True, help="逗号分隔日期")
    ap.add_argument("--kinds", default="margin,block", help="margin/block")
    ap.add_argument("--limit", type=int, default=0, help="限制股票数(测试用)")
    args = ap.parse_args()

    days = [d.strip() for d in args.days.split(",")]
    kinds = [k.strip() for k in args.kinds.split(",")]
    codes = load_codes()
    if args.limit:
        codes = codes[:args.limit]
    print(f"开始并发追跑: {len(days)}天 × {len(kinds)}类 × {len(codes)}只 = {len(days)*len(kinds)*len(codes)} 次请求")
    print(f"  并发线程: {MAX_WORKERS}")
    print(f"  预计耗时: 约 {len(days)*len(kinds)*len(codes)/MAX_WORKERS*0.3/60:.1f} 分钟")

    t0 = time.time()
    for d in days:
        process_day(d, codes, kinds)
    print(f"\n总耗时: {(time.time()-t0)/60:.1f} 分钟")


if __name__ == "__main__":
    main()