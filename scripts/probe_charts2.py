"""直接读取 base.html 注入的代码看是否被覆盖"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("http://localhost:5054/strategies", wait_until="networkidle", timeout=15000)
        await page.wait_for_timeout(2000)
        # 触发 resize
        await page.set_viewport_size({"width": 1500, "height": 900})
        await page.wait_for_timeout(1500)
        # 检查所有 script 块中的 charts 出现
        # 直接读出 base.html 内部 IIFE 的 source
        info = await page.evaluate("""() => {
            // 关键检查：WeakMap 在 chromium 里到底有没有 forEach
            const wm = new WeakMap();
            return {
                wmProto: Object.getOwnPropertyNames(Object.getPrototypeOf(wm)),
                hasOwn: Object.prototype.hasOwnProperty.call(wm, 'forEach'),
                ctor: wm.constructor.name,
                ctorForEach: typeof wm.constructor.prototype.forEach,
            };
        }""")
        print("WeakMap inspection:", info)
        await browser.close()

asyncio.run(main())
