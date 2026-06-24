#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
增量更新 A 股数据
================

支持拉取多个交易日的:
  1. K线日线 → market_data/raw/kline_daily/kline_daily_<YEAR>.csv
  2. 融资融券 → market_data/margin_trading.csv
  3. 大宗交易 → market_data/block_trade.csv
  4. 龙虎榜   → market_data/raw/reference/dragon_tiger.csv (备份) + db
  5. 资金流  → market_data/fund_flow_120d.csv
  6. 行情快照 → market_data/raw/reference/tencent_quotes.csv

按交易日（跳过周末）逐日执行，westock-first。

用法:
  python scripts/incremental_update.py --days 2026-06-19,2026-06-22,2026-06-23,2026-06-24
  python scripts/incremental_update.py --days 2026-06-19,2026-06-24 --types kline,margin,block,lhb
  python scripts/incremental_update.py --start 2026-06-19 --end 2026-06-24
"""
import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
WESTOCK_PKG = "westock-data-clawhub@1.0.4"
BATCH = 500          # 每次请求股票数
PAUSE = 1.0          # 批次间隔秒
NODE_PATH = "C:/Users/aini7/.workbuddy/binaries/node/versions/22.22.2/node.exe"
NPM_PATH = "C:/Users/aini7/.workbuddy/binaries/node/versions/22.22.2/npx.cmd"


# ============== 工具 ==============

def load_codes() -> list[str]:
    """从 stock_profile.csv 读股票代码列表"""
    p = ROOT / "market_data" / "stock_profile.csv"
    codes = []
    with open(p, "r", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if row and row[0].strip():
                codes.append(row[0].strip())
    return codes


def run_wes(args: list[str], timeout: int = 180) -> tuple[bool, str, str]:
    """调用 westock 命令, 返回 (ok, stdout, stderr)"""
    cmd = [NPM_PATH, "-y", WESTOCK_PKG] + args
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--no-warnings"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return r.returncode == 0, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except Exception as e:
        return False, "", str(e)


def parse_markdown_table(stdout: str) -> list[dict]:
    """把 westock markdown 表格解析成 [dict, ...]"""
    rows = []
    headers = None
    in_table = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("=") or line.startswith("💡"):
            continue
        if line.startswith("|") and ("---" not in line):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if headers is None:
                # 可能是表头或第一行
                if not any(c.replace(".", "").replace("-", "").isdigit() for c in cells if c):
                    headers = cells
                    in_table = True
                    continue
                # 没表头,直接当数据行
                if in_table and cells:
                    rows.append(cells)
            else:
                if len(cells) == len(headers):
                    rows.append(dict(zip(headers, cells)))
    return rows


def is_trading_day(d: date) -> bool:
    """简单判断: 周一~周五都先尝试, 遇到空数据再回退"""
    return d.weekday() < 5  # 0=Mon, 4=Fri


def daterange(start: str, end: str) -> list[str]:
    """生成日期字符串列表"""
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    out = []
    cur = s
    while cur <= e:
        if is_trading_day(cur):
            out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


# ============== 各类更新 ==============

def update_kline_for_date(d: str, codes: list[str]) -> int:
    """
    拉取某天的 K线日线, 追加到 market_data/raw/kline_daily/kline_daily_<YEAR>.csv

    字段: code, market, name, date, open, high, low, close, volume, amount
    """
    year = d[:4]
    dst = ROOT / "market_data" / "raw" / "kline_daily" / f"kline_daily_{year}.csv"
    dst.parent.mkdir(parents=True, exist_ok=True)

    # 已存在数据
    if dst.exists():
        old = pd.read_csv(dst, encoding="utf-8-sig")
        old_codes_d = set(zip(old["code"].astype(str), old["date"].astype(str)))
    else:
        old = pd.DataFrame()
        old_codes_d = set()

    print(f"\n■ K线日线 {d}")
    print(f"  目标文件: {dst.name}  旧记录: {len(old)}")

    total = (len(codes) + BATCH - 1) // BATCH
    new_rows = []
    skipped = 0

    for b in range(total):
        chunk = codes[b * BATCH: (b + 1) * BATCH]
        ok, out, err = run_wes(["kline", ",".join(chunk),
                                "--period", "day", "--limit", "5"])
        if not ok:
            print(f"  SKIP batch {b + 1}/{total}: {err[:80]}")
            time.sleep(2)
            continue
        rows = parse_markdown_table(out)
        # 只保留指定日期的
        for r in rows:
            code = str(r.get("code", ""))
            rdate = str(r.get("date", ""))
            if rdate == d and (code, rdate) not in old_codes_d:
                # 字段映射
                row = {
                    "code":    code,
                    "market":  r.get("market", ""),
                    "name":    r.get("name", ""),
                    "date":    rdate,
                    "open":    float(r.get("open", 0) or 0),
                    "high":    float(r.get("high", 0) or 0),
                    "low":     float(r.get("low", 0) or 0),
                    "close":   float(r.get("last", r.get("close", 0)) or 0),
                    "volume":  float(r.get("volume", 0) or 0),
                    "amount":  float(r.get("amount", 0) or 0),
                }
                new_rows.append(row)
            else:
                skipped += 1
        if (b + 1) % 2 == 0 or b == total - 1:
            print(f"  batch {b + 1}/{total}: +{len(new_rows)}  skip_dup={skipped}")
        time.sleep(PAUSE)

    if not new_rows:
        print(f"  无新数据 ({d} 可能休市)")
        return 0

    df_new = pd.DataFrame(new_rows)
    if not old.empty:
        combined = pd.concat([old, df_new], ignore_index=True)
    else:
        combined = df_new
    combined = combined.drop_duplicates(subset=["code", "date"], keep="last")
    combined = combined.sort_values(["code", "date"]).reset_index(drop=True)
    combined.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"  ✅ OK 保存: {len(combined)} 条 (新增 {len(new_rows)})")
    return len(new_rows)


def update_margin_for_date(d: str, codes: list[str]) -> int:
    """拉取某天的融资融券, 追加到 market_data/margin_trading.csv"""
    dst = ROOT / "market_data" / "margin_trading.csv"
    if not dst.exists():
        old = pd.DataFrame()
    else:
        old = pd.read_csv(dst, encoding="utf-8-sig")

    print(f"\n■ 融资融券 {d}")
    print(f"  旧记录: {len(old)}")

    total = (len(codes) + BATCH - 1) // BATCH
    new_rows = []
    for b in range(total):
        chunk = codes[b * BATCH: (b + 1) * BATCH]
        ok, out, err = run_wes(["margintrade", ",".join(chunk), "--date", d])
        if not ok:
            print(f"  SKIP batch {b + 1}: {err[:80]}")
            time.sleep(2)
            continue
        rows = parse_markdown_table(out)
        for r in rows:
            row = {
                "code":   str(r.get("code", "")),
                "market": r.get("market", ""),
                "name":   r.get("name", ""),
                "date":   d,
                "rz":     r.get("FinanceValue", "0"),
                "rzmre":  r.get("SecurityValue", "0"),
                "rzche":  r.get("FinanceBuyValue", "0"),
                "rqye":   r.get("FinanceRefundValue", "0"),
                "rqmcl":  r.get("TradingValue", "0"),
                "rqchl":  r.get("TradingValueDiff", "0"),
            }
            new_rows.append(row)
        if (b + 1) % 2 == 0 or b == total - 1:
            print(f"  batch {b + 1}/{total}: +{len(new_rows)}")
        time.sleep(PAUSE)

    if not new_rows:
        print(f"  无新数据")
        return 0

    df_new = pd.DataFrame(new_rows)
    combined = pd.concat([old, df_new], ignore_index=True) if not old.empty else df_new
    combined = combined.drop_duplicates(subset=["code", "date"], keep="last")
    combined.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"  ✅ OK 保存: {len(combined)} 条 (新增 {len(new_rows)})")
    return len(new_rows)


def update_block_for_date(d: str, codes: list[str]) -> int:
    """拉取某天的大宗交易, 追加到 market_data/block_trade.csv"""
    dst = ROOT / "market_data" / "block_trade.csv"
    if not dst.exists():
        old = pd.DataFrame()
    else:
        old = pd.read_csv(dst, encoding="utf-8-sig")

    print(f"\n■ 大宗交易 {d}")
    print(f"  旧记录: {len(old)}")

    new_rows = []
    total = (len(codes) + BATCH - 1) // BATCH
    for b in range(total):
        chunk = codes[b * BATCH: (b + 1) * BATCH]
        ok, out, err = run_wes(["blocktrade", ",".join(chunk), "--date", d])
        if not ok:
            print(f"  SKIP batch {b + 1}: {err[:80]}")
            time.sleep(2)
            continue
        rows = parse_markdown_table(out)
        for r in rows:
            row = {
                "code":        str(r.get("code", "")),
                "market":      r.get("market", ""),
                "name":        r.get("name", ""),
                "date":        r.get("date", d),
                "deal_price":  r.get("DealPrice", "0"),
                "close_price": r.get("ClosePrice", "0"),
                "premium_pct": r.get("PremiumPct", "0"),
                "vol":         r.get("Vol", "0"),
                "amount":      r.get("Amount", "0"),
                "buyer":       r.get("Buyer", ""),
                "seller":      r.get("Seller", ""),
            }
            new_rows.append(row)
        if (b + 1) % 2 == 0 or b == total - 1:
            print(f"  batch {b + 1}/{total}: +{len(new_rows)}")
        time.sleep(PAUSE)

    if not new_rows:
        print(f"  无新数据")
        return 0

    df_new = pd.DataFrame(new_rows)
    combined = pd.concat([old, df_new], ignore_index=True) if not old.empty else df_new
    combined = combined.drop_duplicates(subset=["code", "date", "deal_price"], keep="last")
    combined.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"  ✅ OK 保存: {len(combined)} 条 (新增 {len(new_rows)})")
    return len(new_rows)


def update_lhb_for_date(d: str) -> int:
    """拉取某天的龙虎榜, 追加到 market_data/raw/reference/dragon_tiger.csv"""
    dst = ROOT / "market_data" / "raw" / "reference" / "dragon_tiger.csv"
    if not dst.exists():
        old = pd.DataFrame()
    else:
        old = pd.read_csv(dst, encoding="utf-8-sig")

    print(f"\n■ 龙虎榜 {d}")
    print(f"  旧记录: {len(old)}")

    # 4个 tab 都要拉
    tabs = ["jg", "yzb", "yyb", "gslmr", "gslxw"]
    new_rows = []
    for tab in tabs:
        ok, out, err = run_wes(["lhb", "--tab", tab, "--date", d], timeout=120)
        if not ok:
            print(f"  SKIP tab={tab}: {err[:80]}")
            continue
        rows = parse_markdown_table(out)
        for r in rows:
            row = {
                "code":         str(r.get("code", "")),
                "market":       r.get("market", ""),
                "name":         r.get("name", ""),
                "date":         r.get("date", d),
                "tab":          tab,
                "reason":       r.get("Reason", ""),
                "net_buy":      r.get("NetBuy", "0"),
                "turnover_pct": r.get("TurnoverPct", "0"),
            }
            new_rows.append(row)
        print(f"  tab={tab}: +{len(rows)}")
        time.sleep(1)

    if not new_rows:
        print(f"  无新数据 ({d} 可能无龙虎榜)")
        return 0

    df_new = pd.DataFrame(new_rows)
    combined = pd.concat([old, df_new], ignore_index=True) if not old.empty else df_new
    combined = combined.drop_duplicates(
        subset=["code", "date", "tab"], keep="last")
    combined.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"  ✅ OK 保存: {len(combined)} 条 (新增 {len(new_rows)})")
    return len(new_rows)


def update_fund_flow_for_date(d: str, codes: list[str]) -> int:
    """拉取某天的资金流向, 追加到 market_data/fund_flow_120d.csv"""
    dst = ROOT / "market_data" / "fund_flow_120d.csv"
    if not dst.exists():
        old = pd.DataFrame()
    else:
        old = pd.read_csv(dst, encoding="utf-8-sig")

    print(f"\n■ 资金流 {d}")
    print(f"  旧记录: {len(old)}")

    new_rows = []
    total = (len(codes) + BATCH - 1) // BATCH
    for b in range(total):
        chunk = codes[b * BATCH: (b + 1) * BATCH]
        ok, out, err = run_wes(["asfund", ",".join(chunk), "--date", d])
        if not ok:
            print(f"  SKIP batch {b + 1}: {err[:80]}")
            time.sleep(2)
            continue
        rows = parse_markdown_table(out)
        for r in rows:
            # westock 字段: code, MainNetFlow, MainNetFlow5D, MainInFlow, MainOutFlow, ...
            row = {
                "code":          str(r.get("code", "")),
                "market":        r.get("market", ""),
                "name":          r.get("name", ""),
                "date":          d,
                "main_net_flow":     r.get("MainNetFlow", "0"),
                "main_net_flow_5d":  r.get("MainNetFlow5D", "0"),
                "main_net_flow_10d": r.get("MainNetFlow10D", "0"),
                "main_net_flow_20d": r.get("MainNetFlow20D", "0"),
                "main_in_flow":      r.get("MainInFlow", "0"),
                "main_out_flow":     r.get("MainOutFlow", "0"),
                "jumbo_net_flow":    r.get("JumboNetFlow", "0"),
                "block_net_flow":    r.get("BlockNetFlow", "0"),
            }
            new_rows.append(row)
        if (b + 1) % 2 == 0 or b == total - 1:
            print(f"  batch {b + 1}/{total}: +{len(new_rows)}")
        time.sleep(PAUSE)

    if not new_rows:
        print(f"  无新数据")
        return 0

    df_new = pd.DataFrame(new_rows)
    combined = pd.concat([old, df_new], ignore_index=True) if not old.empty else df_new
    combined = combined.drop_duplicates(subset=["code", "date"], keep="last")
    combined.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"  ✅ OK 保存: {len(combined)} 条 (新增 {len(new_rows)})")
    return len(new_rows)


def update_quote_snapshot(codes: list[str]) -> int:
    """拉取最新行情快照, 覆盖 market_data/raw/reference/tencent_quotes.csv"""
    dst = ROOT / "market_data" / "raw" / "reference" / "tencent_quotes.csv"
    if dst.exists():
        old = pd.read_csv(dst, encoding="utf-8-sig")
    else:
        old = pd.DataFrame()

    print(f"\n■ 行情快照")
    print(f"  旧记录: {len(old)}")

    new_rows = []
    total = (len(codes) + BATCH - 1) // BATCH
    for b in range(total):
        chunk = codes[b * BATCH: (b + 1) * BATCH]
        ok, out, err = run_wes(["quote", ",".join(chunk)], timeout=60)
        if not ok:
            print(f"  SKIP batch {b + 1}: {err[:80]}")
            time.sleep(2)
            continue
        rows = parse_markdown_table(out)
        new_rows.extend(rows)
        if (b + 1) % 2 == 0 or b == total - 1:
            print(f"  batch {b + 1}/{total}: +{len(new_rows)}")
        time.sleep(PAUSE)

    if not new_rows:
        print(f"  无新数据")
        return 0

    df_new = pd.DataFrame(new_rows)
    if not old.empty:
        combined = pd.concat([old, df_new], ignore_index=True)
    else:
        combined = df_new
    combined = combined.drop_duplicates(subset=["code"], keep="last")
    combined.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"  ✅ OK 保存: {len(combined)} 条 (新增 {len(new_rows)})")
    return len(new_rows)


# ============== main ==============

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=str, help="逗号分隔日期 YYYY-MM-DD,YYYY-MM-DD")
    p.add_argument("--start", type=str, help="起始日期 YYYY-MM-DD (含)")
    p.add_argument("--end", type=str, help="结束日期 YYYY-MM-DD (含)")
    p.add_argument("--types", type=str, default="kline,margin,block,lhb,fund_flow",
                   help="更新类型(逗号分隔): kline/margin/block/lhb/fund_flow/quote")
    p.add_argument("--skip-mtime-check", action="store_true",
                   help="跳过重复日期检查")
    args = p.parse_args()

    # 解析日期
    if args.days:
        days = [d.strip() for d in args.days.split(",") if d.strip()]
    elif args.start and args.end:
        days = daterange(args.start, args.end)
    else:
        # 默认从 2026-06-19 (上次最后一天) 到今天
        days = daterange("2026-06-19", date.today().strftime("%Y-%m-%d"))

    types = set(t.strip() for t in args.types.split(","))

    print("=" * 60)
    print(f"增量更新: {len(days)} 个交易日, 类型: {sorted(types)}")
    print(f"日期: {days}")
    print("=" * 60)

    codes = load_codes()
    print(f"股票池: {len(codes)} 只")

    total_new = 0
    for d in days:
        print(f"\n{'=' * 60}")
        print(f"📅 {d}  ({datetime.strptime(d, '%Y-%m-%d').strftime('%A')})")
        print(f"{'=' * 60}")

        t0 = time.time()

        if "kline" in types:
            total_new += update_kline_for_date(d, codes)
        if "margin" in types:
            total_new += update_margin_for_date(d, codes)
        if "block" in types:
            total_new += update_block_for_date(d, codes)
        if "lhb" in types:
            total_new += update_lhb_for_date(d)
        if "fund_flow" in types:
            total_new += update_fund_flow_for_date(d, codes)

        dt = time.time() - t0
        print(f"\n  ⏱ {d} 耗时: {dt:.1f}秒")

    if "quote" in types:
        total_new += update_quote_snapshot(codes)

    print()
    print("=" * 60)
    print(f"✅ 全部完成! 新增 {total_new} 条记录")
    print("=" * 60)


if __name__ == "__main__":
    main()
