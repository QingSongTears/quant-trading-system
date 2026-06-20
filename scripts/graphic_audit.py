"""
图形专项审查：抓所有有图形的页面高清截图
输出到 output/audit_graphic_<page>.png
"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

PAGES = [
    ("home", "http://localhost:5054/"),
    ("workbench", "http://localhost:5054/workbench"),
    ("backtest", "http://localhost:5054/backtest"),
    ("backtest_detail", "http://localhost:5054/backtest/26"),
    ("compare", "http://localhost:5054/compare"),
    ("strategies", "http://localhost:5054/strategies"),
    ("data", "http://localhost:5054/data"),
]

OUT = Path(__file__).parent.parent / "output"
OUT.mkdir(exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1000})
        report = []
        for name, url in PAGES:
            page = await ctx.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(f"PE: {e}"))
            page.on("console", lambda m: errs.append(f"C[{m.type}]: {m.text}") if m.type in ("error", "warning") else None)
            failed = []
            page.on("requestfailed", lambda r: failed.append(f"{r.url} - {r.failure}"))
            try:
                resp = await page.goto(url, wait_until="networkidle", timeout=15000)
                await page.wait_for_timeout(2500)  # 给 ECharts 足够时间
                # 整页截图
                shot = OUT / f"audit_graphic_{name}.png"
                await page.screenshot(path=str(shot), full_page=True)
                # 统计 canvas / ECharts 元素
                info = await page.evaluate("""() => {
                    const canvases = document.querySelectorAll('canvas').length;
                    const echartDivs = document.querySelectorAll('[_echarts_instance_]').length;
                    const svgs = document.querySelectorAll('svg').length;
                    const imgs = document.querySelectorAll('img').length;
                    return {canvases, echartDivs, svgs, imgs, height: document.body.scrollHeight, width: document.body.scrollWidth};
                }""")
                report.append({
                    "page": name, "url": url, "status": resp.status,
                    "shot": str(shot.name), "size_kb": round(shot.stat().st_size/1024, 1),
                    "errors": errs[:5], "failed_requests": failed[:3],
                    **info,
                })
                print(f"OK {name}: {info} | {shot.stat().st_size//1024}KB | errs={len(errs)} failed={len(failed)}")
            except Exception as e:
                report.append({"page": name, "url": url, "error": str(e)[:200]})
                print(f"FAIL {name}: {e}")
            await page.close()
        await browser.close()
        (OUT / "audit_graphic_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        # 汇总
        print("\n=== Summary ===")
        for r in report:
            mark = "✓" if "error" not in r and r.get("status") == 200 else "✗"
            print(f"  {mark} {r['page']:18} | {r.get('status', r.get('error', '?'))} | cvs={r.get('canvases',0)} echart={r.get('echartDivs',0)} svg={r.get('svgs',0)}")

asyncio.run(main())
