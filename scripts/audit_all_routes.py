"""audit_all_routes.py — 全量探针 21 个 home 导航链接,检查渲染错误"""
import asyncio
import sys
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "screenshots" / "route"
OUT.mkdir(parents=True, exist_ok=True)

ROUTES = [
    "/", "/diagnose", "/sector", "/screener", "/portfolio",
    "/backtest-lab", "/backtest-view", "/predict", "/verify", "/ic",
    "/strategy-compare", "/v6-compare", "/bull-report",
    "/fund-flow-report", "/dim-compare", "/data-monitor", "/tuning",
    "/signal", "/dashboard", "/v5", "/workbench", "/strategies",
    "/backtest", "/data", "/compare",
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        bad = []
        for r in ROUTES:
            errs = []
            page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
            page.on("console", lambda m: errs.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
            try:
                resp = await page.goto(f"http://localhost:5054{r}", wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)
                status = resp.status if resp else 0
                # 探针 1: 是否有大量空 body
                body_text = (await page.locator("body").text_content()) or ""
                content_len = len(body_text.strip())
                # 探针 2: canvas 数
                canvas = await page.evaluate("() => document.querySelectorAll('canvas').length")
                # 探针 3: 主要错误元素
                err_in_page = await page.evaluate("""() => {
                    const all = document.querySelectorAll('.alert-danger, .error, [class*=error], [class*=empty]');
                    return Array.from(all).slice(0, 5).map(e => e.textContent.trim().substring(0, 80));
                }""")
                slug = r.replace("/", "_").strip("_") or "home"
                shot = OUT / f"{slug}.png"
                await page.screenshot(path=str(shot), full_page=False)
                status_str = "✓" if status == 200 and not errs and content_len > 200 else "✗"
                print(f"  {status_str} {r:25s} HTTP {status}  text={content_len:5d}  canvas={canvas}  errs={len(errs)}")
                if errs or status != 200 or content_len < 200:
                    bad.append({"route": r, "status": status, "content_len": content_len, "errors": errs[:5], "page_errors": err_in_page})
                if errs:
                    for e in errs[:3]:
                        print(f"      {e[:100]}")
            except Exception as ex:
                print(f"  ✗ {r:25s} EXC {ex}")
                bad.append({"route": r, "exception": str(ex)})

        await browser.close()
        print(f"\n[bad] {len(bad)}/{len(ROUTES)}")
        for b in bad:
            print(f"  - {b}")

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
