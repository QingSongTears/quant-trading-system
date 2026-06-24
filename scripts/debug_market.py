"""Debug: navigate to market tab and dump DOM state."""
import asyncio, json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={'width': 1440, 'height': 900})
        page = await ctx.new_page()
        page.on('console', lambda m: print(f'[console.{m.type}] {m.text}'))
        page.on('pageerror', lambda e: print(f'[pageerror] {e}'))
        page.on('requestfailed', lambda r: print(f'[failed] {r.url}'))
        page.on('response', lambda r: print(f'[resp] {r.status} {r.url}') if 'api/' in r.url else None)

        await page.goto('http://localhost:5054/console', wait_until='load')
        await page.wait_for_function("typeof echarts !== 'undefined'", timeout=15000)
        await page.wait_for_timeout(2000)

        # Click market
        print('--- clicking market tab ---')
        await page.click('.nav-item[data-tab="market"]')
        await page.wait_for_timeout(8000)

        # Inject debug into page
        await page.evaluate("""() => {
            window._debugLog = [];
            const origApi = window.api;
            const origSelectStock = window.selectStock;
        }""")

        # Inspect DOM state
        state = await page.evaluate("""() => {
            // Force call selectStock from inside
            const out = {
                price: document.getElementById('mcPrice')?.textContent,
                change: document.getElementById('mcChange')?.textContent,
                open: document.getElementById('mcOpen')?.textContent,
                tabsCount: document.querySelectorAll('.stock-tab').length,
                activeTabCode: document.querySelector('.stock-tab.active')?.dataset.code,
                firstTabText: document.querySelector('.stock-tab')?.textContent,
            };
            return out;
        }""")
        print('DOM state:', json.dumps(state, indent=2, ensure_ascii=False))

        # Inspect selectStock state
        dbg = await page.evaluate("""() => ({
            selectCalled: window._selectStockCalled,
            lastCode: window._lastSelectCode,
            lastQuote: window._lastQuote ? {price: window._lastQuote.price} : null,
            lastQuoteResp: window._lastQuoteResp ? {success: window._lastQuoteResp.success, hasData: !!window._lastQuoteResp.data} : null,
            apiError: window._apiError,
            klineCalled: window._klineCalled,
            klineResp: window._klineResp ? {success: window._klineResp.success, candleCount: window._klineResp.data?.candles?.length} : null,
            candleCanvasW: document.querySelector('#chartCandle canvas')?.width,
        })""")
        print('debug state:', json.dumps(dbg, indent=2, ensure_ascii=False, default=str)[:1500])

        await page.screenshot(path=str(Path(__file__).resolve().parent.parent / 'screenshots' / 'dbg' / 'debug_market.png'))
        await browser.close()

asyncio.run(main())
