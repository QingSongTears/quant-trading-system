#!/usr/bin/env python3
"""端到端验证：访问页面，console 不报错 + 关键元素渲染"""
from playwright.sync_api import sync_playwright

PAGES_WITH_API = [
    ("/dashboard", "data-stat", "stat-card"),
    ("/diagnose", "stock-input", "code-input"),
    ("/sector", "sector-list", "stats-grid"),
    ("/screener", "screener-result", "table"),
    ("/portfolio", "portfolio-input", "input"),
    ("/data-monitor", "data-monitor", "table"),
    ("/fund-flow-report", "scenarios-table", "table"),
    ("/backtest-lab", "lab-strategy", "lab-layout"),
    ("/tuning-panel", "dim-技术面", "weight"),
]

console_errors = []

def log_err(msg):
    console_errors.append(msg)
    print(f"  [ERR] {msg[:150]}")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        extra_http_headers={"X-API-Key": "w_rXe0YKrwhrwBa9pt-b_I6DvzU3ca_9ZnedwHjNLt4"},
    )
    page = context.new_page()
    page.on("pageerror", log_err)
    page.on("console", lambda m: log_err(f"console.{m.type}: {m.text}") if m.type in ("error",) else None)

    for path, probe, css_class in PAGES_WITH_API:
        try:
            page.goto(f"http://127.0.0.1:5050{path}", wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(1500)
            bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
            has_class = page.locator(f".{css_class}").count() > 0
            print(f"  {path:30s} bg={bg:30s} .{css_class}={'✓' if has_class else '✗'}")
        except Exception as e:
            print(f"  {path:30s} ERR {e}")

    browser.close()
print(f"\nErrors: {len(console_errors)}")
