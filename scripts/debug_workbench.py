"""
重现 workbench 一键对比的 500 错误，捕获所有 /api/backtest/* 请求
"""
import asyncio
import json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # 捕获所有 backtest 请求
        requests_log = []
        async def on_request(req):
            if '/api/backtest' in req.url:
                requests_log.append({
                    'url': req.url,
                    'method': req.method,
                    'body': req.post_data or '(GET)',
                })
        page.on('request', on_request)

        async def on_response(res):
            if '/api/backtest/run' in res.url:
                try:
                    body = await res.text()
                    requests_log.append({
                        'response': res.url,
                        'status': res.status,
                        'body': body[:500],
                    })
                except Exception:
                    pass
        page.on('response', on_response)

        # 1. 打开 workbench
        await page.goto('http://localhost:5054/workbench')
        # 等所有模型加载完成（option 元素即使 hidden 也算 attached）
        await page.wait_for_function(
            "() => document.querySelectorAll('#model-select option').length >= 14",
            timeout=10000,
        )

        # 2. 模拟用户：选双均线交叉 + 603986
        await page.select_option('#model-select', value='双均线交叉')
        await page.fill('#stock-input', '603986')
        await page.locator('#stock-input').blur()
        await asyncio.sleep(0.3)

        # 3. 点"开始回测"
        print('=== 点击 开始回测 ===')
        requests_log.clear()
        await page.click('#btn-run')
        await asyncio.sleep(3)
        for r in requests_log:
            print(json.dumps(r, ensure_ascii=False))

        # 4. 点"一键对比全部模型"
        print('\n=== 点击 一键对比全部模型 ===')
        requests_log.clear()
        await page.click('#btn-compare-all')
        await asyncio.sleep(5)
        print(f'共捕获 {len(requests_log)} 个 backtest 请求:')
        for r in requests_log:
            print(json.dumps(r, ensure_ascii=False))

        # 截图
        shot_path = Path(__file__).resolve().parent.parent / 'screenshots' / 'dbg' / 'debug_workbench.png'
        shot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(shot_path), full_page=True)
        print(f'\n截图保存: {shot_path}')

        await browser.close()

asyncio.run(main())
