"""获取 pageerror 完整堆栈"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        # 包装器捕获完整 stack
        page.on("pageerror", lambda e: print(f"PAGEERROR type={type(e).__name__} str={e!s}"))
        await page.goto("http://localhost:5054/strategies", wait_until="networkidle", timeout=15000)
        await page.wait_for_timeout(2000)
        # 触发 resize
        await page.set_viewport_size({"width": 1500, "height": 900})
        await page.wait_for_timeout(1000)
        # 在页面内查找问题源
        info = await page.evaluate("""() => {
            // 找所有 charts 变量
            const results = {};
            results.hasCharts = typeof window.charts;
            results.hasWeakMap = 'WeakMap' in window;
            try {
                const wm = new WeakMap();
                results.weakMapForEach = typeof wm.forEach;
            } catch(e) { results.weakMapForEach = 'ERR: ' + e.message; }
            // 看 echart 加载后
            results.hasEcharts = typeof window.echarts;
            return results;
        }""")
        print("EVAL:", info)
        await browser.close()

asyncio.run(main())
