"""
全量 HTML 模板审计脚本
- 遍历所有可访问的页面
- 捕获 console.error / pageerror / failed_request
- 检测 ECharts 渲染、关键元素是否存在
- 检测 CDN 资源是否加载
- 检测 Jinja2 未渲染的占位符泄漏
- 输出结构化报告
"""
import asyncio
import json
import sys
from pathlib import Path
from playwright.async_api import async_playwright

PAGES = [
    ("/", "home"),
    ("/backtest/26", "backtest26"),
    ("/workbench", "workbench"),
    ("/strategies", "strategies"),
    ("/backtest", "backtest_list"),
    ("/compare", "compare"),
    ("/data", "data"),
    ("/backtest_detail", "backtest_detail"),
]

BASE = "http://localhost:5054"
OUT_DIR = Path("D:/gitHub/qunat/quant-trading-system/output")
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def audit_page(browser, route, name):
    report = {
        "page": name,
        "url": f"{BASE}{route}",
        "http_status": None,
        "console_errors": [],
        "console_warnings": [],
        "page_errors": [],
        "failed_requests": [],
        "echarts_canvas_count": 0,
        "key_elements": {},
        "leaked_jinja_placeholders": [],
        "html_size_kb": 0,
        "load_ms": 0,
    }

    context = await browser.new_context(viewport={"width": 1440, "height": 900})
    page = await context.new_page()

    # Capture events
    page.on("console", lambda msg: (
        report["console_errors"].append(msg.text) if msg.type == "error"
        else report["console_warnings"].append(msg.text) if msg.type == "warning"
        else None
    ))
    page.on("pageerror", lambda exc: report["page_errors"].append(str(exc)))
    page.on("requestfailed", lambda req: report["failed_requests"].append({
        "url": req.url,
        "failure": req.failure
    }))

    try:
        import time
        t0 = time.time()
        response = await page.goto(f"{BASE}{route}", wait_until="networkidle", timeout=20000)
        report["http_status"] = response.status if response else None
        report["load_ms"] = int((time.time() - t0) * 1000)

        # Wait for ECharts / JS
        await page.wait_for_timeout(2000)

        # Get HTML size
        html = await page.content()
        report["html_size_kb"] = round(len(html.encode("utf-8")) / 1024, 1)

        # Count ECharts canvas
        report["echarts_canvas_count"] = await page.locator("canvas").count()

        # Detect leaked Jinja2 placeholders
        for pattern in ["{{ ", " }}", "{% raw %}", "{% endraw %}"]:
            if pattern in html and not pattern.replace(" ", "").startswith("{"):
                continue
        import re
        leaked = re.findall(r"\{\{[^}]*\}\}", html)
        # Filter out allowed ones (those rendered correctly as text)
        suspicious = [p for p in leaked if "}}(" not in p and "<" not in p and "function" not in p]
        report["leaked_jinja_placeholders"] = list(set(suspicious))[:5]

        # Check key elements
        checks = {
            "navbar": "nav, .navbar, [data-navbar]",
            "echarts": "canvas, [data-zr-dom-id]",
            "data_table": "table, .table, [data-table]",
            "forms": "form, input, select, button",
            "errors_visible": ".error, .alert-danger, [data-error]",
        }
        for key, sel in checks.items():
            try:
                count = await page.locator(sel).count()
                report["key_elements"][key] = count
            except Exception as e:
                report["key_elements"][key] = f"err: {e}"

        # Screenshot
        await page.screenshot(path=str(OUT_DIR / f"audit_{name}.png"), full_page=False)

    except Exception as e:
        report["page_errors"].append(f"AUDIT_ERROR: {str(e)[:200]}")

    finally:
        await context.close()

    return report


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        results = []
        for route, name in PAGES:
            print(f"Auditing {name} ({route})...", flush=True)
            r = await audit_page(browser, route, name)
            results.append(r)
            # Print quick summary
            issues = len(r["console_errors"]) + len(r["page_errors"]) + len(r["failed_requests"])
            status = f"HTTP {r['http_status']}" if r["http_status"] else "NO RESPONSE"
            print(f"  → {status}, {issues} errors, {r['echarts_canvas_count']} canvas, {r['html_size_kb']}KB")
        await browser.close()

    # Save report
    report_path = OUT_DIR / "html_audit_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    # Summary
    print("\n" + "=" * 70)
    print("AUDIT SUMMARY")
    print("=" * 70)
    print(f"{'Page':<20} {'HTTP':<6} {'Err':<4} {'Warn':<5} {'Canvas':<7} {'Size':<8}")
    print("-" * 70)
    total_errors = 0
    for r in results:
        err = len(r["console_errors"]) + len(r["page_errors"])
        total_errors += err
        print(f"{r['page']:<20} {str(r['http_status']):<6} {err:<4} "
              f"{len(r['console_warnings']):<5} {r['echarts_canvas_count']:<7} {str(r['html_size_kb'])+'KB':<8}")
    print("-" * 70)
    print(f"TOTAL ERRORS: {total_errors}")
    print(f"REPORT: {report_path}")
    print(f"SCREENSHOTS: {OUT_DIR}/audit_*.png")


if __name__ == "__main__":
    asyncio.run(main())
