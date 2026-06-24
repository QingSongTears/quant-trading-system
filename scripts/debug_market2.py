"""Simple test: load console, manually call selectStock via evaluate, screenshot."""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={'width': 1440, 'height': 900})
        page = await ctx.new_page()
        page.on('pageerror', lambda e: print(f'[pageerror] {e}'))
        page.on('console', lambda m: print(f'[console.{m.type}] {m.text}') if '[selectStock]' in m.text or '[initMarket]' in m.text or m.type in ('error','warning') else None)

        await page.goto('http://localhost:5054/console', wait_until='load')
        await page.wait_for_function("typeof echarts !== 'undefined'", timeout=15000)
        await page.wait_for_timeout(2000)

        # Click market and wait long
        print('clicking market...')
        await page.click('.nav-item[data-tab="market"]')
        await page.wait_for_timeout(10000)

        # Check status
        s = await page.evaluate("""() => ({
            price: document.getElementById('mcPrice').textContent,
            change: document.getElementById('mcChange').textContent,
            candleDom: document.getElementById('chartCandle')?.innerHTML?.length,
            candleCanvases: document.querySelectorAll('#chartCandle canvas').length,
            klineCalled: window._klineCalled,
            klineResp: window._klineResp ? {
                success: window._klineResp.success,
                candleCount: window._klineResp.data?.candles?.length,
                error: window._klineResp.error
            } : null,
            candleDivSize: (() => { const d=document.getElementById('chartCandle'); return d? {w:d.clientWidth,h:d.clientHeight,visible:d.offsetParent!==null}:null;})(),
        })""")
        print('after 10s wait:', s)

        await browser.close()

asyncio.run(main())
