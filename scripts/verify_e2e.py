"""验证浏览器实际 fetch 是否带 token 通过 API"""
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1600, "height": 900})
        page = await ctx.new_page()

        # 拦截网络请求,看 Authorization header
        auth_seen = []

        def on_request(req):
            if "/api/" in req.url:
                auth = req.headers.get("authorization", "MISSING")
                auth_seen.append((req.url.split("/api/")[-1], auth[:30]))

        page.on("request", on_request)

        await page.goto("http://localhost:5054/workbench", wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(3000)

        # 看页面上是否有"加载失败"提示
        err_visible = await page.evaluate("""() => {
            const errs = Array.from(document.querySelectorAll('.alert-danger, .error, [class*="error"]'))
                .filter(el => el.offsetParent !== null)
                .map(el => el.innerText.trim().slice(0, 100));
            return errs;
        }""")

        print(f"可见错误元素: {len(err_visible)} 个")
        for e in err_visible[:3]:
            print(f"  > {e}")

        print(f"\nAPI 请求共 {len(auth_seen)} 次:")
        for path, auth in auth_seen[:8]:
            print(f"  {path} | auth: {auth}")

        # 全部带 token?
        all_with_auth = all(a != "MISSING" for _, a in auth_seen if auth_seen)
        print(f"\n所有请求都带 Authorization: {all_with_auth}")

        await ctx.close()
        await browser.close()


asyncio.run(main())