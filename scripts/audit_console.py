"""Audit /console page: switch all 7 tabs, capture errors, screenshot each tab."""
import asyncio, json, sys
from pathlib import Path
from playwright.async_api import async_playwright

# 统一截图目录 (screenshots/, 已在 .gitignore 中)
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "screenshots" / "console"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-gpu'])
        ctx = await browser.new_context(viewport={'width': 1440, 'height': 900})
        page = await ctx.new_page()

        all_errors = {}
        pageerrors = []
        all_console = []
        page.on('pageerror', lambda e: pageerrors.append(str(e)))
        page.on('console', lambda m: (all_console.append(f'[{m.type}] {m.text}'), all_errors.setdefault(m.type, []).append(m.text) if m.type == 'error' else None))
        page.on('requestfailed', lambda r: all_errors.setdefault('failed', []).append(f"{r.url} {r.failure}"))

        await page.goto('http://localhost:5054/console', wait_until='load', timeout=25000)
        # 等 ECharts 全局可用
        await page.wait_for_function("typeof echarts !== 'undefined'", timeout=15000)
        await page.wait_for_timeout(3500)  # 让 overview 异步 fetch + 图表画完

        results = {}
        for tab in ['overview', 'backtest', 'compare', 'positions', 'market', 'risk', 'logs']:
            tab_errors = []
            page.on('pageerror', lambda e, te=tab_errors: te.append(str(e)))
            try:
                # 切到该 tab
                await page.click(f'.nav-item[data-tab="{tab}"]', timeout=3000)
                # 等异步渲染 (8s 充分等 westock API 每次 ~2s + 串行 12s 加载 6 个 watchlist)
                await page.wait_for_timeout(8000)
                canvases = await page.evaluate("document.querySelectorAll('canvas').length")
                tables = await page.evaluate("document.querySelectorAll('table tbody tr').length")
                preview = await page.evaluate("""(tab)=>{
                    const h=document.getElementById('pageTitle');
                    const kpis=Array.from(document.querySelectorAll('.tab-page.active .kpi .value')).map(e=>e.textContent.trim()).slice(0,8);
                    return JSON.stringify({title:h?h.textContent:tab, kpis});
                }""", tab)
                await page.screenshot(path=str(OUT / f'tab_{tab}.png'), full_page=False)
                results[tab] = {'preview': preview, 'canvases': canvases, 'table_rows': tables, 'errors': tab_errors}
            except Exception as e:
                results[tab] = {'error': str(e)[:300], 'errors': tab_errors}

        await browser.close()
        # 只输出关键摘要
        summary = {t: {k: v for k, v in d.items() if k != 'preview'} for t, d in results.items()}
        summary['all_console_errors'] = all_errors
        summary['global_pageerrors'] = pageerrors
        summary['console_log_tail'] = all_console[-30:]
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        (OUT / 'audit_report.json').write_text(json.dumps(results, indent=2, ensure_ascii=False))


asyncio.run(main())
