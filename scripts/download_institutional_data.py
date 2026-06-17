"""
机构面数据下载 v2 — 高效快照模式
===============================
- LHB: 近6个月全量 (已完成, 5054条)
- Margin: 仅取最新交易日快照
- Shareholder: 仅取最新季度快照
"""

import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "database" / "quant.db"
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATACENTER_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
TIMEOUT = 15

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
})


def call_datacenter(report_name, extra_params=None, page_size=500, max_pages=30):
    """通用 API 调用, 返回 (data_list, total_count)"""
    all_data = []
    total = 0
    for page in range(1, max_pages + 1):
        params = {
            "source": "WEB", "client": "WEB",
            "pageNumber": page, "pageSize": page_size,
            "reportName": report_name, "columns": "ALL",
        }
        if extra_params:
            params.update(extra_params)

        try:
            resp = session.get(DATACENTER_BASE, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                break
            result = data.get("result")
            if result is None:
                break
            page_data = result.get("data") or []
            if total == 0:
                total = result.get("count", 0) or len(page_data)
            all_data.extend(page_data)
            if len(page_data) < page_size:
                break
            time.sleep(0.2)
        except Exception as e:
            print(f"  page {page} ERROR: {e}")
            break
    return all_data, total


def download_margin_snapshot():
    """融资融券 — 仅最新交易日"""
    print("\n" + "=" * 60)
    print("2/3 融资融券快照 (最新交易日)")
    print("=" * 60)

    latest_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    params = {
        "filter": f"(DATE>='{latest_date}')",
        "sortColumns": "DATE",
        "sortTypes": "-1",
    }
    data, total = call_datacenter("RPTA_WEB_RZRQ_GGMX", params, page_size=500, max_pages=20)
    print(f"  total={total}, fetched={len(data)}")

    if not data:
        # Try without date filter
        print("  (date filter failed, trying unfiltered...)")
        data, total = call_datacenter("RPTA_WEB_RZRQ_GGMX", {"sortColumns": "DATE", "sortTypes": "-1"},
                                      page_size=500, max_pages=5)
        if not data:
            return pd.DataFrame()

    records = []
    for rec in data:
        try:
            code = str(rec.get("SCODE", "")).zfill(6)
            records.append({
                "code": code,
                "date": str(rec.get("DATE", ""))[:10],
                "rzye": float(rec.get("RZYE") or 0),
                "rzmre": float(rec.get("RZMRE") or 0),
                "rzche": float(rec.get("RZCHE") or 0),
                "rzjme": float(rec.get("RZJME") or 0),
                "rqye": float(rec.get("RQYE") or 0),
                "rzrqye": float(rec.get("RZRQYE") or 0),
            })
        except (TypeError, ValueError):
            continue

    df = pd.DataFrame(records)
    print(f"  记录: {len(df)}, 股票: {df['code'].nunique() if not df.empty else 0}")
    return df


def download_shareholder_snapshot():
    """股东户数 — 仅最新季度"""
    print("\n" + "=" * 60)
    print("3/3 股东户数快照 (最新季度)")
    print("=" * 60)

    params = {
        "sortColumns": "END_DATE",
        "sortTypes": "-1",
    }
    data, total = call_datacenter("RPT_HOLDERNUMLATEST", params, page_size=500, max_pages=20)

    if total > 500:
        # 取最新季度的数据, 限制页面数
        print(f"  total={total}, limiting to first 20 pages...")

    print(f"  fetched={len(data)}")

    records = []
    for rec in data:
        try:
            code = str(rec.get("SECURITY_CODE", "")).zfill(6)
            ratio = rec.get("HOLDER_NUM_RATIO")
            if ratio is not None:
                change_pct = float(ratio)
            else:
                cur = float(rec.get("HOLDER_NUM") or 0)
                prev = float(rec.get("PRE_HOLDER_NUM") or 0)
                change_pct = (cur - prev) / prev * 100 if prev > 0 else 0

            records.append({
                "code": code,
                "end_date": str(rec.get("END_DATE", ""))[:10],
                "holder_num": int(rec.get("HOLDER_NUM") or 0),
                "pre_holder_num": int(rec.get("PRE_HOLDER_NUM") or 0),
                "holder_change_pct": round(change_pct, 2),
                "avg_holding": float(rec.get("AVG_HOLD_NUM") or 0),
            })
        except (TypeError, ValueError):
            continue

    df = pd.DataFrame(records)
    print(f"  记录: {len(df)}, 股票: {df['code'].nunique() if not df.empty else 0}")
    return df


def main():
    print("机构面数据下载 v2 (快照模式)")
    print(f"DB: {DB_PATH}")
    start = time.time()

    conn = sqlite3.connect(str(DB_PATH))

    # 2. Margin snapshot
    df_margin = download_margin_snapshot()
    if not df_margin.empty:
        df_margin.to_sql("margin_trading", conn, if_exists="replace", index=False)
        df_margin.to_csv(DATA_DIR / "margin_trading.csv", index=False, encoding="utf-8-sig")
        print(f"  ✅ margin_trading: {len(df_margin)} rows")
    else:
        print("  ⚠️ 无 margin 数据")

    time.sleep(0.5)

    # 3. Shareholder snapshot
    df_holder = download_shareholder_snapshot()
    if not df_holder.empty:
        df_holder.to_sql("shareholder_count", conn, if_exists="replace", index=False)
        df_holder.to_csv(DATA_DIR / "shareholder_count.csv", index=False, encoding="utf-8-sig")
        print(f"  ✅ shareholder_count: {len(df_holder)} rows")
    else:
        print("  ⚠️ 无 shareholder 数据")

    conn.close()

    elapsed = time.time() - start
    print(f"\n总共耗时: {elapsed:.1f}s")
    print(f"CSV: {DATA_DIR}/margin_trading.csv, shareholder_count.csv")


if __name__ == "__main__":
    main()
