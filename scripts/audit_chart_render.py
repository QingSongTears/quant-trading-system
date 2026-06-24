"""深度验证回测详情页的 ECharts canvas 是否真的渲染"""
import asyncio
from playwright.async_api import async_playwright

PAGES = [
    ("home", "http://localhost:5054/"),
    ("backtest_detail", "http://localhost:5054/backtest/158"),
    ("compare", "http://localhost:5054/compare"),
]


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for name, url in PAGES:
            ctx = await browser.new_context(viewport={"width": 1600, "height": 900})
            page = await ctx.new_page()

            console_errs = []
            page.on("console", lambda m: console_errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: console_errs.append(f"PAGEERROR: {e}"))

            try:
                await page.goto(url, wait_until="networkidle", timeout=20000)
            except Exception as e:
                print(f"{name}: load error - {e}")
                await ctx.close()
                continue

            # 等 ECharts 渲染完成
            await page.wait_for_timeout(3000)

            stats = await page.evaluate("""() => {
                const canvases = document.querySelectorAll('canvas').length;
                const echartDivs = document.querySelectorAll('[data-zr-dom-id], div[_echarts_instance_]').length;
                const echartsLoaded = typeof window.echarts !== 'undefined';
                const inlineApiKey = (window.API_KEY || '').slice(0, 8);
                return { canvases, echartDivs, echartsLoaded, inlineApiKey };
            }""")

            print(f"{name}: {stats} | errs={len(console_errs)}")
            for e in console_errs[:5]:
                print(f"  > {e[:200]}")

            shot = f"output/smoketest_{name}.png"
            await page.screenshot(path=shot, full_page=False)
            print(f"  shot: {shot}")
            await ctx.close()
        await browser.close()


asyncio.run(main())