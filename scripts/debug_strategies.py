"""Debug: 在 strategies 页面抓 pageerror 完整堆栈"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1000})
        page = await ctx.new_page()
        page.on("pageerror", lambda e: print(f"PAGEERROR: {e}\n---"))
        page.on("console", lambda m: print(f"CONSOLE[{m.type}]: {m.text}") if m.type in ("error", "warning") else None)
        await page.goto("http://localhost:5054/strategies", wait_until="networkidle", timeout=15000)
        await page.wait_for_timeout(3000)
        # 触发 resize 模拟用户操作
        await page.set_viewport_size({"width": 1500, "height": 900})
        await page.wait_for_timeout(1000)
        await page.set_viewport_size({"width": 1600, "height": 1000})
        await page.wait_for_timeout(2000)
        await browser.close()

asyncio.run(main())
