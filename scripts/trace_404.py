"""抓 4 个失败页面的具体 404 URL"""
import asyncio
from playwright.async_api import async_playwright

PAGES = [
    ("backtest-lab", "http://localhost:5054/backtest-lab"),
    ("data-monitor", "http://localhost:5054/data-monitor"),
    ("v5", "http://localhost:5054/v5"),
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for name, url in PAGES:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            fails = []
            page.on("response", lambda r, n=name: fails.append((r.status, r.url)) if r.status >= 400 else None)
            try:
                await page.goto(url, wait_until="networkidle", timeout=15000)
                await page.wait_for_timeout(3000)
            except Exception as e:
                print(f"{name}: load err {e}")
            print(f"\n=== {name} ({url}) ===")
            for st, u in fails:
                print(f"  {st} {u}")
            await ctx.close()
        await browser.close()

asyncio.run(main())
