#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
研报增量
========

westock report: 全市场研报列表
https://github.com/MiniMax-Platform/...
"""
import subprocess
import csv
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent.parent
WESTOCK_PKG = "westock-data-clawhub@1.0.4"
NODE_PATH = "C:/Users/aini7/.workbuddy/binaries/node/versions/22.22.2/node.exe"
WESTOCK_SCRIPT = "C:/Users/aini7/.workbuddy/plugins/marketplaces/experts/plugins/stock-partner-team/skills/westock-data/scripts/index.js"
DST = ROOT / "market_data" / "research_report.csv"


def run(cmd, timeout=60):
    env = os.environ.copy()
    env["NODE_OPTIONS"] = "--no-warnings"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode == 0, r.stdout, r.stderr
    except Exception as e:
        return False, "", str(e)


def parse(stdout: str) -> list[dict]:
    headers = None
    rows = []
    for line in stdout.splitlines():
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
                rows.append(dict(zip(headers, cells)))
    return rows


def fetch_recent(days: int = 14, limit: int = 200, offset: int = 0) -> list[dict]:
    """研报列表 (dehydrated), 按 offset/limit 分页"""
    cmd = [NODE_PATH, WESTOCK_SCRIPT, "dehydrated",
           "--limit", str(limit), "--offset", str(offset)]
    ok, out, err = run(cmd, timeout=60)
    if not ok:
        print(f"  FAIL: {err[:80]}")
        return []
    return parse(out)


def main():
    print("=== 研报增量 ===")
    print(f"目标: 2026-06-09 ~ 2026-06-24 (近 14 天)")
    old = pd.read_csv(DST, encoding="utf-8-sig", low_memory=False) if DST.exists() else pd.DataFrame()
    print(f"  旧记录: {len(old)}")
    if not old.empty and "日期" in old.columns:
        old_max = pd.to_datetime(old["日期"]).max()
        print(f"  旧最新: {old_max.strftime('%Y-%m-%d')}")
    else:
        old_max = None

    new_rows = []
    # 翻 8 页 = 1600 条, 覆盖近 2 周 (offset 200 时返回 8 条, 400 时 32 条, 600 后空)
    for offset in [0, 200, 400, 500, 600, 700, 800, 1000, 1200, 1500]:
        rows = fetch_recent(limit=200, offset=offset)
        print(f"  offset={offset}  +{len(rows)} 条")
        new_rows.extend(rows)
        time.sleep(0.3)
    if not new_rows:
        print("  (无新数据)")
        return
    df_new = pd.DataFrame(new_rows)
    combined = pd.concat([old, df_new], ignore_index=True) if not old.empty else df_new
    combined = combined.drop_duplicates(keep="last")
    combined.to_csv(DST, index=False, encoding="utf-8-sig")
    print(f"  ✅ 保存完成: {len(combined)} 条 (新增 {len(combined)-len(old)} 条)")


if __name__ == "__main__":
    main()