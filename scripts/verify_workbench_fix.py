"""
完整验证 workbench 修复后状态
1. 一键对比应该跑 5 个 signal 模型
2. 开始回测 + 603986 应该 200
3. 点 portfolio 类型的回测记录，loadHistoryResult 不应触发重跑 500
"""
import asyncio
import json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # 收集所有 backtest 请求和响应
        backtest_reqs = []
        page.on('request', lambda req: backtest_reqs.append({
            'kind': 'req', 'url': req.url, 'method': req.method, 'body': req.post_data
        }) if '/api/backtest' in req.url else None)
        page.on('response', lambda res: backtest_reqs.append({
            'kind': 'res', 'url': res.url, 'status': res.status
        }) if '/api/backtest' in res.url else None)

        # 收集 console 错误
        errors = []
        page.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))
        page.on('console', lambda m: errors.append(f'console.{m.type}: {m.text}') if m.type == 'error' else None)

        # 1. 打开 workbench
        print('=== 1. 打开 workbench ===')
        await page.goto('http://localhost:5054/workbench')
        await page.wait_for_function(
            "() => document.querySelectorAll('#model-select option').length >= 14",
            timeout=10000,
        )
        print(f'模型数: {await page.eval_on_selector_all("#model-select option", "els => els.length")}')

        # 2. 验证 strategy_type 字段已生效
        signal_count = await page.evaluate("""
            () => Array.from(document.querySelectorAll('#model-select option'))
                .filter(o => o.dataset.type === 'signal').length
        """)
        portfolio_count = await page.evaluate("""
            () => Array.from(document.querySelectorAll('#model-select option'))
                .filter(o => o.dataset.type === 'portfolio').length
        """)
        voting_count = await page.evaluate("""
            () => Array.from(document.querySelectorAll('#model-select option'))
                .filter(o => o.dataset.type === 'voting').length
        """)
        print(f'signal={signal_count}, portfolio={portfolio_count}, voting={voting_count}')

        # 3. 选双均线交叉 + 603986，点开始回测
        print('\n=== 2. 双均线交叉 + 603986, 点开始回测 ===')
        backtest_reqs.clear()
        await page.select_option('#model-select', value='双均线交叉')
        await page.fill('#stock-input', '603986')
        await page.locator('#stock-input').blur()
        await asyncio.sleep(0.5)
        await page.click('#btn-run')
        await asyncio.sleep(3)
        for r in backtest_reqs:
            print(f"  {r['kind']} {r['url'].split('/')[-1]} {r.get('status', r.get('method', ''))}")
        if any(r.get('status') == 500 for r in backtest_reqs):
            print('  ❌ 发现 500 错误')
        else:
            print('  ✅ 无 500')

        # 4. 一键对比全部模型
        print('\n=== 3. 一键对比全部模型 ===')
        backtest_reqs.clear()
        await page.click('#btn-compare-all')
        await asyncio.sleep(15)
        reqs = [r for r in backtest_reqs if r['kind'] == 'req']
        ress = [r for r in backtest_reqs if r['kind'] == 'res']
        print(f'共 {len(reqs)} 个 backtest 请求, {len(ress)} 个响应')
        success = sum(1 for r in ress if r.get('status') == 200)
        failed = sum(1 for r in ress if r.get('status') != 200)
        print(f'  200 OK: {success}')
        print(f'  失败: {failed}')
        if failed > 0:
            for r in ress:
                if r.get('status') != 200:
                    print(f"    ❌ {r['url']} -> {r['status']}")
        # 检查每个请求的 strategy_name
        for r in reqs:
            if r.get('body'):
                try:
                    body = json.loads(r['body'])
                    print(f"    body: strategy_name={body.get('strategy_name','?')}, stock_code={body.get('stock_code','-')}")
                except Exception:
                    pass

        # 5. 验证防御: 在 stock-code 注入 PORTFOLIO 触发拦截
        print('\n=== 4. PORTFOLIO 防御测试 ===')
        backtest_reqs.clear()
        # 通过 JS 模拟: 设置 stock-code 隐藏字段为 PORTFOLIO
        await page.evaluate("""
            () => {
                document.getElementById('stock-code').value = 'PORTFOLIO';
                document.getElementById('stock-name').value = '组合回测';
                document.getElementById('stock-input').value = 'PORTFOLIO 组合回测';
            }
        """)
        # 注册 alert 监听
        alert_text = []
        page.on('dialog', lambda d: (alert_text.append(d.message), asyncio.create_task(d.dismiss())))
        await page.click('#btn-run')
        await asyncio.sleep(2)
        if alert_text:
            print(f'  ✅ 触发防御 alert: {alert_text[0][:100]}')
        else:
            print(f'  ❌ 未触发 alert, 实际请求: {len(backtest_reqs)} 个')
        # 没有 fetch 发出
        fetch_500 = [r for r in backtest_reqs if r.get('status') == 500]
        if not fetch_500:
            print('  ✅ 无 500 错误')
        else:
            print(f'  ❌ 仍有 {len(fetch_500)} 个 500')

        # 6. 截图
        await page.screenshot(path='output/workbench_after_fix.png', full_page=True)
        print('\n截图: output/workbench_after_fix.png')

        if errors:
            print('\n=== Console 错误 ===')
            for e in errors[:5]:
                print(f'  {e[:150]}')

        await browser.close()

asyncio.run(main())
