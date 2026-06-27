#!/usr/bin/env python3
"""
截图验证：访问 16 个新路由，截全屏图，保存到 output/screenshots/
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

API_KEY = "w_rXe0YKrwhrwBa9pt-b_I6DvzU3ca_9ZnedwHjNLt4"
BASE = "http://127.0.0.1:5050"

ROUTES = [
    ("/", "01_home"),
    ("/dashboard", "02_dashboard"),
    ("/diagnose", "03_diagnose"),
    ("/sector", "04_sector"),
    ("/screener", "05_screener"),
    ("/portfolio", "06_portfolio"),
    ("/data-monitor", "07_data_monitor"),
    ("/fund-flow-report", "08_fund_flow_report"),
    ("/backtest-lab", "09_backtest_lab"),
    ("/signal-dashboard", "10_signal_dashboard"),
    ("/strategy-compare", "11_strategy_compare"),
    ("/tuning-panel", "12_tuning_panel"),
    ("/v5-tuning", "13_v5_tuning"),
    ("/v6-compare", "14_v6_compare"),
    ("/ic-analysis", "15_ic_analysis"),
    ("/dim-compare", "16_dim_compare"),
]

OUT_DIR = Path("E:/work/work/quant-trading-system/output/screenshots")
OUT_DIR.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        extra_http_headers={"X-API-Key": API_KEY},
    )
    page = context.new_page()

    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url} {r.failure}"))

    for path, name in ROUTES:
        url = BASE + path
        try:
            resp = page.goto(url, wait_until="networkidle", timeout=15000)
            status = resp.status if resp else "?"
            page.wait_for_timeout(800)
            png = OUT_DIR / f"{name}.png"
            page.screenshot(path=str(png), full_page=False)
            print(f"  {status}  {path:30s} → {png.name}")
        except Exception as e:
            print(f"  ERR  {path:30s} → {e}")

    if errors:
        print("\n=== Page errors ===")
        for e in errors[:20]:
            print(f"  {e}")
    browser.close()
print(f"\n截图保存到: {OUT_DIR}")
