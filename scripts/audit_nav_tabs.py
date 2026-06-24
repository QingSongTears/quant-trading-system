"""扫描主页 index.html 所有导航卡片,逐个点击验证是否 200 且不报错"""
import asyncio
import re
from pathlib import Path
from playwright.async_api import async_playwright

TEMPLATE = Path(r"D:\gitHub\qunat\quant-trading-system\src\web\templates\index.html")
BASE_URL = "http://localhost:5054"


def extract_links():
    text = TEMPLATE.read_text(encoding="utf-8")
    # 抓 nav-card / recent-card / form action 里的 href
    hrefs = set(re.findall(r'href="(/[^"]+)"', text))
    hrefs = {h for h in hrefs if h.startswith("/")}
    return sorted(hrefs)


async def main():
    links = extract_links()
    print(f"=== 找到 {len(links)} 个导航链接 ===\n")
    for h in links:
        print(f"  {h}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1600, "height": 900})

        results = []
        for h in links:
            page = await ctx.new_page()
            errs = []
            page.on("console", lambda m, e=errs: e.append(f"CONSOLE: {m.text[:150]}") if m.type == "error" else None)
            page.on("pageerror", lambda exc, e=errs: e.append(f"PAGEERR: {str(exc)[:150]}"))
            page.on("requestfailed", lambda r, e=errs: e.append(f"FAILED: {r.url[:80]} {r.failure[:60] if r.failure else ''}"))

            url = f"{BASE_URL}{h}"
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                status = resp.status if resp else 0
                await page.wait_for_timeout(800)
                title = await page.title()
            except Exception as e:
                status = 0
                title = f"EXC: {e}"

            ok = status == 200 and len(errs) == 0
            tag = "✅" if ok else "❌"
            print(f"  {tag} {h:30s} HTTP={status:3d}  errs={len(errs)}  title={title[:40]}")
            for e in errs[:2]:
                print(f"      {e}")
            results.append((h, status, len(errs), title, ok))
            await page.close()

        await browser.close()

        # 汇总
        print(f"\n=== 汇总: {sum(1 for r in results if r[4])}/{len(results)} 通过 ===")
        bad = [r for r in results if not r[4]]
        if bad:
            print("失败链接:")
            for h, st, n, t, _ in bad:
                print(f"  ❌ {h}  HTTP={st}  errs={n}  title={t}")


asyncio.run(main())
