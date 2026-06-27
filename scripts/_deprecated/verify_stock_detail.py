"""
verify_stock_detail.py — 截图验证 stock/600519 页面真实渲染
- 打开页面等 K线 canvas 渲染完
- 截图: 整页 + 关键元素
- 校验: 价格/ECharts/无错误
"""
import asyncio
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "screenshots" / "stock_detail"
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        errors = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)

        # 1. 打开页面
        await page.goto("http://localhost:5054/stock/600519", wait_until="domcontentloaded", timeout=30000)
        # 等 K线 canvas 渲染
        await page.wait_for_selector("#kline-chart canvas", timeout=45000)
        await page.wait_for_timeout(3500)  # 等 ECharts 画完

        # 2. 整页截图
        await page.screenshot(path=str(OUT / "01_full.png"), full_page=True)
        # 3. 视口截图 (hero 区)
        await page.screenshot(path=str(OUT / "02_hero.png"), full_page=False)
        # 4. K线图截图
        kline = page.locator("#kline-chart")
        await kline.screenshot(path=str(OUT / "03_kline.png"))

        # 5. 验证渲染
        data = await page.evaluate("""() => ({
            price: document.getElementById('hd-price').textContent,
            name: document.getElementById('hd-name').textContent,
            change: document.getElementById('hd-change').textContent,
            open: document.getElementById('m-open').textContent,
            high: document.getElementById('m-high').textContent,
            low: document.getElementById('m-low').textContent,
            vol: document.getElementById('m-vol').textContent,
            amt: document.getElementById('m-amt').textContent,
            industry: document.getElementById('p-industry').textContent,
            sector: document.getElementById('p-sector').textContent,
            pressure: document.getElementById('p-pressure').textContent,
            support: document.getElementById('p-support').textContent,
            canvasCount: document.querySelectorAll('canvas').length,
            financeRows: document.querySelectorAll('#finance-table tbody tr').length,
            chartInfo: document.getElementById('chart-info').textContent,
        })""")
        print("[stock/600519] 渲染数据:")
        for k, v in data.items():
            print(f"  {k:14s} = {v}")

        # 6. 切换到周K
        await page.click('a[data-period="week"]')
        await page.wait_for_timeout(2000)
        await kline.screenshot(path=str(OUT / "04_kline_week.png"))

        # 7. 切换到分时
        await page.click('a[data-type="minute"]')
        await page.wait_for_timeout(2000)
        await kline.screenshot(path=str(OUT / "05_kline_minute.png"))

        # 8. 切到月K
        await page.click('a[data-period="month"]')
        await page.wait_for_timeout(2000)

        # 9. 财务 - 利润表
        await page.click('button[data-fintype="lrb"]')
        await page.wait_for_timeout(3000)
        fin_rows = await page.evaluate("() => document.querySelectorAll('#finance-table tbody tr').length")
        print(f"  财务 利润表 rows: {fin_rows}")

        # 10. 错误检查
        print(f"\n[errors] {len(errors)} 个")
        for e in errors[:10]:
            print(f"  {e}")

        # 10b. 等 profile 加载完
        await page.wait_for_function("document.getElementById('p-industry').textContent !== '--'", timeout=15000)
        profile_data = await page.evaluate("""() => ({
            industry: document.getElementById('p-industry').textContent,
            sector: document.getElementById('p-sector').textContent,
            listed: document.getElementById('p-listed').textContent,
            business: document.getElementById('p-business').textContent.substring(0, 60),
        })""")
        print(f"\n[profile] {profile_data}")
        await page.locator(".card:has(#profile-grid)").screenshot(path=str(OUT / "06_finance.png"))

        # 11. 首页 + 工作台审美对比
        await page.goto("http://localhost:5054/", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=str(OUT / "07_home_new.png"), full_page=False)

        await page.goto("http://localhost:5054/workbench", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(OUT / "08_workbench.png"), full_page=False)

        await page.goto("http://localhost:5054/backtest/158", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(3500)
        await page.screenshot(path=str(OUT / "09_backtest158.png"), full_page=False)

        await browser.close()

        print(f"\n[OK] 截图全部保存在: {OUT}")
        return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
