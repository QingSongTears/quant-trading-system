"""
深度调试 4 个潜在问题
"""
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width':1440,'height':900})

        # ==== DEBUG 1: backtest 列表 ====
        await page.goto('http://localhost:5054/backtest', wait_until='domcontentloaded')
        await page.wait_for_timeout(3000)
        rows = page.locator('table tbody tr')
        n = await rows.count()
        print(f'==== DEBUG 1: /backtest 列表 ====')
        print(f'行数={n}')
        if n > 0:
            row_text = await rows.first.inner_text()
            print(f'首行 text: {row_text[:300]}')
            onclick = await rows.first.get_attribute('onclick')
            print(f'首行 onclick={onclick}')
            # 找链接
            links = await rows.first.locator('a').count()
            print(f'首行内 <a> 数量={links}')

        # ==== DEBUG 2: workbench 对比按钮 ====
        await page.goto('http://localhost:5054/workbench', wait_until='domcontentloaded')
        await page.wait_for_timeout(3000)
        btns = await page.evaluate('''() => {
            return Array.from(document.querySelectorAll("button")).map(b => ({
                id: b.id || "",
                text: b.innerText.trim().slice(0, 40),
                disabled: b.disabled,
                classes: b.className.slice(0, 50)
            }));
        }''')
        print(f'\\n==== DEBUG 2: workbench 按钮 ====')
        for b in btns:
            print(b)

        # ==== DEBUG 3: backtest/26 标题与可见文字 ====
        await page.goto('http://localhost:5054/backtest/26', wait_until='domcontentloaded')
        await page.wait_for_timeout(3000)
        # 看页面主要文字
        main_text = await page.evaluate('document.querySelector("main, .main, .container")?.innerText || document.body.innerText')
        print(f'\\n==== DEBUG 3: /backtest/26 主体文字 (前 1500 字) ====')
        print(main_text[:1500])

        # ==== DEBUG 4: strategies 页面 ====
        await page.goto('http://localhost:5054/strategies', wait_until='domcontentloaded')
        await page.wait_for_timeout(3000)
        body_text = await page.evaluate('document.body.innerText')
        print(f'\\n==== DEBUG 4: /strategies 主体 (前 1500 字) ====')
        print(body_text[:1500])

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
