"""用 domcontentloaded + 短等待 抓 4 个页面的 404 URL"""
import asyncio
from playwright.async_api import async_playwright

PAGES = [
    ("backtest-lab", "http://localhost:5054/backtest-lab"),
    ("data-monitor", "http://localhost:5054/data-monitor"),
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for name, url in PAGES:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            fails = []
            page.on("response", lambda r: fails.append((r.status, r.url)) if r.status >= 400 else None)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                await page.wait_for_timeout(5000)  # 给轮询 API 5s 跑出 404
            except Exception as e:
                print(f"{name}: {e}")
            print(f"\n=== {name} ===")
            for st, u in fails[:20]:
                print(f"  {st} {u}")
            await ctx.close()
        await browser.close()

asyncio.run(main())
