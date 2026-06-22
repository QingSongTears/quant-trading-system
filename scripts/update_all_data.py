#!/usr/bin/env python3.11
"""
批量更新 A 股数据到 2026-06-18
1. margin_trading.csv  — WeStock margintrade
2. block_trade.csv     — WeStock blocktrade
3. dragon_tiger.csv   — WeStock lhb
"""

import subprocess
import csv
import time
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent.parent
STOCKS_CSV = ROOT / "market_data" / "stock_profile.csv"
DATE = "2026-06-18"
BATCH = 500


# === 通用工具 ==============================

def load_codes():
    codes = []
    with open(STOCKS_CSV, "r", encoding="utf-8-sig") as f:
        next(csv.reader(f))
        for row in csv.reader(f):
            if row and row[0].strip():
                codes.append(row[0].strip())
    return codes


def run_wes(cmd_str, timeout=120):
    full = "NODE_OPTIONS='--no-warnings' " + cmd_str
    r = subprocess.run(
        full, shell=True,
        capture_output=True, text=True, timeout=timeout
    )
    return r.returncode == 0, r.stdout, r.stderr


def parse_stdout(stdout):
    """把 westock markdown 表格解析成 [dict, ...]"""
    lines = [l.strip() for l in stdout.splitlines() if l.strip()]
    headers = None
    rows = []
    in_table = False
    for l in lines:
        if l.startswith("| code ") or l.startswith("| name "):
            headers = [h.strip() for h in l.split("|")[1:-1]]
            in_table = True
            continue
        if "---" in l:
            continue
        if in_table and l.startswith("|"):
            vals = [v.strip() for v in l.split("|")[1:-1]]
            if len(vals) == len(headers):
                rows.append(dict(zip(headers, vals)))
    return rows


# === 1. margin_trading ========================

def update_margin():
    print("\n■ margin_trading.csv")
    dst = ROOT / "market_data" / "margin_trading.csv"
    old = pd.read_csv(dst, encoding="utf-8-sig")
    print(f"  旧记录: {len(old)}")

    codes = load_codes()
    total = (len(codes) + BATCH - 1) // BATCH
    new_rows = []

    for b in range(total):
        chunk = codes[b * BATCH : (b + 1) * BATCH]
        cmd = (
            "npx -y westock-data-clawhub@1.0.4 "
            "margintrade " + ",".join(chunk) + " --date " + DATE
        )
        ok, out, err = run_wes(cmd)
        if not ok:
            print(f"  SKIP batch {b+1}: {err[:60]}")
            time.sleep(2)
            continue
        rows = parse_stdout(out)
        print(f"  batch {b+1}/{total}: +{len(rows)} 条")
        new_rows.extend(rows)
        time.sleep(1)

    print(f"  共获取: {len(new_rows)} 条")
    if not new_rows:
        print("  (无新数据)")
        return

    # 映射字段
    mapped = []
    for r in new_rows:
        row = {}
        row["code"]       = r.get("code", "")
        row["market"]     = r.get("market", "")
        row["name"]       = r.get("name", "")
        row["date"]       = DATE
        row["rz"]         = r.get("FinanceValue", "0")
        row["rzmre"]     = r.get("SecurityValue", "0")
        row["rzche"]     = r.get("FinanceBuyValue", "0")
        row["rqye"]      = r.get("FinanceRefundValue", "0")
        row["rqmcl"]     = r.get("TradingValue", "0")
        row["rqchl"]     = r.get("TradingValueDiff", "0")
        mapped.append(row)

    df_new = pd.DataFrame(mapped)
    combined = pd.concat([old, df_new], ignore_index=True)
    combined = combined.drop_duplicates(subset=["code","date"], keep="last")
    combined.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"  OK 保存完成: {len(combined)} 条")


# === 2. block_trade ===========================

def update_block():
    print("\n■ block_trade.csv")
    dst = ROOT / "market_data" / "block_trade.csv"
    old = pd.read_csv(dst, encoding="utf-8-sig")
    print(f"  旧记录: {len(old)}")

    codes = load_codes()
    total = (len(codes) + BATCH - 1) // BATCH
    new_rows = []

    for b in range(total):
        chunk = codes[b * BATCH : (b + 1) * BATCH]
        cmd = (
            "npx -y westock-data-clawhub@1.0.4 "
            "blocktrade " + ",".join(chunk) + " --date " + DATE
        )
        ok, out, err = run_wes(cmd)
        if not ok:
            print(f"  SKIP batch {b+1}: {err[:60]}")
            time.sleep(2)
            continue
        rows = parse_stdout(out)
        print(f"  batch {b+1}/{total}: +{len(rows)} 条")
        new_rows.extend(rows)
        time.sleep(1)

    print(f"  共获取: {len(new_rows)} 条")
    if not new_rows:
        print("  (无新数据)")
        return

    mapped = []
    for r in new_rows:
        row = {}
        row["code"]        = r.get("code", "")
        row["market"]     = r.get("market", "")
        row["name"]      = r.get("name", "")
        row["date"]      = r.get("date", DATE)
        row["deal_price"] = r.get("DealPrice", "0")
        row["close_price"]= r.get("ClosePrice", "0")
        row["premium_pct"]= r.get("PremiumPct", "0")
        row["vol"]        = r.get("Vol", "0")
        row["amount"]     = r.get("Amount", "0")
        row["buyer"]     = r.get("Buyer", "")
        row["seller"]    = r.get("Seller", "")
        mapped.append(row)

    df_new = pd.DataFrame(mapped)
    combined = pd.concat([old, df_new], ignore_index=True)
    combined = combined.drop_duplicates(subset=["code","date"], keep="last")
    combined.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"  OK 保存完成: {len(combined)} 条")


# === 3. dragon_tiger ==========================

def update_lhb():
    print("\n■ dragon_tiger.csv")
    dst = ROOT / "market_data" / "dragon_tiger.csv"
    old = pd.read_csv(dst, encoding="utf-8-sig")
    print(f"  旧记录: {len(old)}")

    cmd = "npx -y westock-data-clawhub@1.0.4 lhb --date " + DATE
    ok, out, err = run_wes(cmd, timeout=60)
    if not ok:
        print(f"  FAIL: {err[:100]}")
        return

    rows = parse_stdout(out)
    print(f"  获取: {len(rows)} 条")
    if not rows:
        print("  (今日无龙虎榜数据)")
        return

    mapped = []
    for r in rows:
        row = {}
        row["code"]       = r.get("code", "")
        row["market"]    = r.get("market", "")
        row["name"]      = r.get("name", "")
        row["date"]      = r.get("date", DATE)
        row["reason"]    = r.get("Reason", "")
        row["net_buy"]   = r.get("NetBuy", "0")
        row["turnover_pct"] = r.get("TurnoverPct", "0")
        mapped.append(row)

    df_new = pd.DataFrame(mapped)
    combined = pd.concat([old, df_new], ignore_index=True)
    combined = combined.drop_duplicates(subset=["code","date"], keep="last")
    combined.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"  OK 保存完成: {len(combined)} 条")


# === main ====================================

if __name__ == "__main__":
    print("=" * 60)
    print(f"批量更新数据: {DATE}")
    print("=" * 60)

    update_margin()
    update_block()
    update_lhb()

    print()
    print("=" * 60)
    print("OK 全部完成!")
    print("=" * 60)
