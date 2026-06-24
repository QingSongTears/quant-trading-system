"""audit_home_tabs.py — 截图验证主页 Tab 切换效果"""
import asyncio, sys
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "screenshots" / "home_tabs"
OUT.mkdir(parents=True, exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        errs = []
        page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        page.on("console", lambda m: errs.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

        await page.goto("http://localhost:5054/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector("#funcTab", timeout=10000)
        await page.wait_for_timeout(1500)

        # 0. 默认 (全部 tab)
        await page.screenshot(path=str(OUT / "00_all.png"), full_page=True)

        # 1. 切到分析诊断
        await page.click("#tab-analyze")
        await page.wait_for_timeout(600)
        await page.locator("#pane-analyze").screenshot(path=str(OUT / "01_analyze.png"))

        # 2. 切到策略与回测
        await page.click("#tab-backtest")
        await page.wait_for_timeout(600)
        await page.locator("#pane-backtest").screenshot(path=str(OUT / "02_backtest.png"))

        # 3. 切到研究报告
        await page.click("#tab-report")
        await page.wait_for_timeout(600)
        await page.locator("#pane-report").screenshot(path=str(OUT / "03_report.png"))

        # 4. 切到系统
        await page.click("#tab-system")
        await page.wait_for_timeout(600)
        await page.locator("#pane-system").screenshot(path=str(OUT / "04_system.png"))

        # 5. 验证 active tab 数量
        active_count = await page.evaluate("() => document.querySelectorAll('.tab-pane.show.active').length")
        card_count = await page.evaluate("() => document.querySelectorAll('#pane-system .nav-card').length")
        tab_count = await page.evaluate("() => document.querySelectorAll('#funcTab button').length")

        # 6. 验证 reload 后保持 tab
        await page.click("#tab-analyze")
        await page.wait_for_timeout(400)
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_selector("#funcTab", timeout=10000)
        await page.wait_for_timeout(800)
        active_after_reload = await page.evaluate("""() => {
            const a = document.querySelector('#funcTab .nav-link.active');
            return a ? a.id : null;
        }""")
        await page.screenshot(path=str(OUT / "05_reload_analyze.png"), full_page=False)

        print(f"[tab-count]  {tab_count} (期望 5)")
        print(f"[active-pane] {active_count} (期望 1)")
        print(f"[pane-system cards] {card_count} (期望 5)")
        print(f"[reload-active-tab] {active_after_reload} (期望 'tab-analyze')")
        print(f"[errors] {len(errs)} 个")
        for e in errs[:5]:
            print(f"  {e[:120]}")
        await browser.close()

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
