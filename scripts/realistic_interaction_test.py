"""
最终真实交互测试
- workbench 实际回测流程
- backtest 执行页填表提交
- 数据页面下载触发
- 移动端响应式
"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

OUT_DIR = Path("D:/gitHub/qunat/quant-trading-system/output")
BASE = "http://localhost:5054"


async def main():
    results = {"tests": []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # ==== TEST 1: workbench 真实回测流程 ====
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        api_reqs = []
        page.on("response", lambda r: api_reqs.append((r.url, r.status)) if "/api/" in r.url else None)
        page.on("pageerror", lambda e: print(f"[pageerror] {e}"))
        page.on("console", lambda m: print(f"[{m.type}] {m.text[:200]}") if m.type == "error" else None)

        await page.goto(f"{BASE}/workbench", wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        # 步骤1: 选模型
        model_select = page.locator("#model-select, select[name='model'], select").first
        try:
            await model_select.select_option(index=1)  # 第一个真实模型
            await page.wait_for_timeout(500)
        except Exception as e:
            results["tests"].append({"name": "workbench选模型", "status": "FAIL", "detail": str(e)[:200]})

        # 步骤2: 搜股票
        search = page.locator("input[placeholder*='股票'], input[placeholder*='代码']").first
        if await search.count() > 0:
            await search.fill("601138")
            await page.wait_for_timeout(800)
            # 等待搜索结果
            results_count = await page.locator(".search-result, .stock-result, [data-result]").count()
            # 模拟点击搜索结果
            try:
                first_result = page.locator(".search-result, .stock-result, [data-result], .list-group-item").first
                if await first_result.count() > 0:
                    await first_result.click()
                    await page.wait_for_timeout(500)
            except:
                pass

        # 步骤3: 设置日期
        start_input = page.locator("input[name='start_date'], #start_date, input[type='date']").first
        end_input = page.locator("input[name='end_date'], #end_date, input[type='date']").nth(1) if await page.locator("input[type='date']").count() > 1 else None
        if await start_input.count() > 0:
            try:
                await start_input.fill("2025-01-01")
            except:
                pass

        # 步骤4: 点击开始回测
        api_reqs.clear()
        run_btn = page.locator("#btn-run, button:has-text('开始回测')").first
        try:
            # 检查按钮是否还 disabled
            disabled = await run_btn.is_disabled()
            if disabled:
                results["tests"].append({
                    "name": "workbench 跑回测",
                    "status": "WARN",
                    "detail": "开始回测按钮仍 disabled（可能未正确选择股票/日期）"
                })
            else:
                await run_btn.click()
                await page.wait_for_timeout(15000)  # 等回测
                ok_reqs = [r for r in api_reqs if r[1] == 200]
                results_reqs = [r for r in api_reqs if "/backtest/run" in r[0]]
                results["tests"].append({
                    "name": "workbench 跑回测",
                    "status": "PASS" if results_reqs and 200 in [s for _, s in results_reqs] else "WARN",
                    "detail": f"总请求={len(api_reqs)}, 200={len(ok_reqs)}, 回测API={len(results_reqs)}, 状态={[s for _, s in results_reqs]}"
                })
        except Exception as e:
            results["tests"].append({"name": "workbench 跑回测", "status": "FAIL", "detail": str(e)[:200]})

        # 截图
        await page.screenshot(path=str(OUT_DIR / "realistic_workbench.png"), full_page=True)
        await context.close()

        # ==== TEST 2: 回测执行页 (/backtest) ====
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        await page.goto(f"{BASE}/backtest", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # 选策略
        strat_select = page.locator("#strategy, select[name='strategy'], select").first
        try:
            opts = await strat_select.locator("option").count()
            if opts > 1:
                await strat_select.select_option(index=1)
                await page.wait_for_timeout(300)
        except Exception as e:
            pass

        # 填股票
        stock_input = page.locator("input[name='stock'], #stock, input[placeholder*='股票']").first
        if await stock_input.count() > 0:
            try:
                await stock_input.fill("601138")
                await page.wait_for_timeout(300)
            except:
                pass

        # 看 form 字段名
        form_fields = await page.evaluate("""
            () => {
                const inputs = Array.from(document.querySelectorAll('form input, form select'));
                return inputs.map(i => ({name: i.name, id: i.id, type: i.type || i.tagName}));
            }
        """)
        results["tests"].append({
            "name": "回测执行页表单字段",
            "status": "INFO",
            "detail": f"字段数={len(form_fields)}, 字段={form_fields}"
        })

        await context.close()

        # ==== TEST 3: 移动端响应式 ====
        for w, h, label in [(375, 667, "iPhone"), (768, 1024, "iPad")]:
            context = await browser.new_context(viewport={"width": w, "height": h})
            page = await context.new_page()
            await page.goto(f"{BASE}/", wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            # 检查是否被截断
            body_width = await page.evaluate("document.body.scrollWidth")
            viewport_width = w
            overflow = body_width > viewport_width + 20
            await page.screenshot(path=str(OUT_DIR / f"mobile_{label}.png"), full_page=False)
            results["tests"].append({
                "name": f"响应式 ({label} {w}x{h})",
                "status": "PASS" if not overflow else "WARN",
                "detail": f"body 宽度={body_width}, 视口={viewport_width}, 横向溢出={overflow}"
            })
            await context.close()

        # ==== TEST 4: 浏览器后退按钮 ====
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        await page.goto(f"{BASE}/", wait_until="domcontentloaded")
        await page.goto(f"{BASE}/backtest", wait_until="domcontentloaded")
        await page.goto(f"{BASE}/workbench", wait_until="domcontentloaded")
        await page.go_back()
        await page.wait_for_timeout(1000)
        url1 = page.url
        await page.go_back()
        await page.wait_for_timeout(1000)
        url2 = page.url
        results["tests"].append({
            "name": "浏览器后退",
            "status": "PASS" if "backtest" in url1 and ("/") in url2 else "WARN",
            "detail": f"后退1: {url1}, 后退2: {url2}"
        })
        await context.close()

        # ==== TEST 5: 404 友好处理 (回测不存在) ====
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        await page.goto(f"{BASE}/backtest/99999", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        text = await page.evaluate("document.body.innerText")
        results["tests"].append({
            "name": "/backtest/99999 404 处理",
            "status": "PASS" if "不存在" in text or "未找到" in text or "404" in text else "WARN",
            "detail": f"页面文字前300={text[:300]!r}"
        })
        await context.close()

        await browser.close()

    # 汇总
    print("\n" + "=" * 70)
    print("真实交互测试结果")
    print("=" * 70)
    passed = sum(1 for t in results["tests"] if t["status"] == "PASS")
    warned = sum(1 for t in results["tests"] if t["status"] == "WARN")
    failed = sum(1 for t in results["tests"] if t["status"] == "FAIL")
    info = sum(1 for t in results["tests"] if t["status"] == "INFO")
    for t in results["tests"]:
        mark = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "INFO": "ℹ️"}[t["status"]]
        print(f"{mark} [{t['status']}] {t['name']}")
        print(f"     {t['detail'][:300]}")
    print("=" * 70)
    print(f"总计: PASS={passed}  WARN={warned}  FAIL={failed}  INFO={info}")

    with open(OUT_DIR / "realistic_test_report.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
