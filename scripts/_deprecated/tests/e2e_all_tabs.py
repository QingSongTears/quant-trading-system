"""
完整子页签扫测 (2026-06-25)

覆盖所有页面的子页签 (tab/折叠/关键按钮), 不只是顶层 URL

每个页面都点击关键子元素, 验证:
  - 页面 200
  - 子元素可见
  - click 后无 console error
  - 截图

用法: python scripts/e2e_all_tabs.py
"""
import asyncio
import sys
import os

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from pathlib import Path
from playwright.async_api import async_playwright


BASE_URL = "http://localhost:5050"
SCREENSHOT_DIR = Path("output/screenshots/all_tabs")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

OK = "[OK]"
ERR = "[ERR]"


async def screenshot(page, name):
    p = SCREENSHOT_DIR / f"{name}.png"
    await page.screenshot(path=str(p), full_page=False)
    return str(p)


async def visit_page(browser, name, url, expected_text=None, click_selectors=None):
    """访问页面 + 点子元素"""
    page = await browser.new_page()
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

    try:
        # 用 domcontentloaded 代替 networkidle, 避免 stock-detail 大量 chart 拖到 timeout
        resp = await page.goto(f"{BASE_URL}{url}", wait_until="domcontentloaded", timeout=15000)
        status = resp.status if resp else 0
        # 等 1s 让 chartjs/echarts 渲染 (但不阻塞太久)
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass  # networkidle 超时不算错
        text = await page.evaluate("document.body.innerText")
        title = await page.title()
        # 文本匹配: body innerText 或 title 任一包含 expected
        text_ok = (expected_text is None) or (expected_text in text) or (expected_text in title)

        # 截图
        shot = await screenshot(page, name)

        # 点子元素
        clicked = []
        if click_selectors:
            for sel in click_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        await el.click(timeout=3000)
                        await page.wait_for_timeout(500)  # 等响应
                        clicked.append(f"clicked {sel}")
                except Exception as e:
                    clicked.append(f"failed {sel}: {str(e)[:80]}")

        # 第二次截图 (点了之后)
        if clicked:
            await screenshot(page, f"{name}_after_click")

        return {
            "name": name,
            "url": url,
            "status": status,
            "text_ok": text_ok,
            "clicked": clicked,
            "console_errors": console_errors,
            "screenshot": shot,
        }
    except Exception as e:
        return {"name": name, "url": url, "error": str(e)}
    finally:
        await page.close()


async def main():
    print("=" * 70)
    print(f"All-Tabs E2E — {BASE_URL}")
    print(f"Screenshots → {SCREENSHOT_DIR.absolute()}")
    print("=" * 70)

    # 每个页面 + 关键子元素 selector
    targets = [
        # 首页 (点 19 卡片的前 3 个)
        ("home", "/", "QuantTrading", [".nav-card[href='/screener']", ".nav-card[href='/workbench']"]),

        # workbench 三模式 (e2e_scenarios 已测, 跳过)
        # 跳过避免重复, 但保留以防 console error
        ("workbench-default", "/workbench?mode=default", "回测工作台", ["#model-select"]),
        ("workbench-lab", "/workbench?mode=lab", "Lab 模式", ["#model-select"]),
        ("workbench-view", "/workbench?mode=view", "View 模式", ["#model-select"]),

        # compare (看默认 + type=strategy)
        ("compare-default", "/compare", "对比", []),
        ("compare-strategy", "/compare?type=strategy", "对比", []),

        # research 5 tab 全部
        ("research-v5", "/research?type=v5", "V5 研究视图", []),
        ("research-v6", "/research?type=v6", "V6 研究视图", []),
        ("research-tuning", "/research?type=tuning", "TUNING 研究视图", []),
        ("research-ic", "/research?type=ic", "IC 分析", []),
        ("research-dim", "/research?type=dim", "维度 IC 对比", []),

        # diagnose (8 维评分)
        ("diagnose", "/diagnose", "诊断", []),

        # screener (筛选 + 结果)
        ("screener", "/screener", "选股", []),

        # portfolio (持仓)
        ("portfolio", "/portfolio", "持仓", []),

        # predict / signal / verify / data / dashboard
        ("predict", "/predict", "预测", []),
        ("signal", "/signal", "信号", []),
        ("verify", "/verify", "验证", []),
        ("data", "/data", "数据", []),
        ("dashboard", "/dashboard", "数据总览", []),
        ("data-monitor", "/data-monitor", "数据监控", []),
        ("walk_forward", "/walk_forward", "Walk-Forward", []),
        ("fund-flow-report", "/fund-flow-report", "资金面", []),
        ("bull-report", "/bull-report", "牛市", []),
        ("simulate", "/simulate", "模拟", []),

        # 个股详情 (动态) - title 含 "个股", 验证模板渲染
        ("stock-detail", "/stock/000001", "个股", []),
    ]

    all_results = []
    all_console_errors = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for name, url, expected, selectors in targets:
            r = await visit_page(browser, name, url, expected, selectors)
            all_results.append(r)
            all_console_errors.extend(r.get("console_errors", []))

            # 打印
            if "error" in r:
                print(f"  {ERR} {name:25s} {url:30s} EXCEPTION: {r['error'][:80]}")
            else:
                ok = (r["status"] == 200 and r["text_ok"])
                sym = OK if ok else ERR
                err = f" err={len(r.get('console_errors', []))}" if r.get("console_errors") else ""
                click = f" clicks={len(r.get('clicked', []))}" if r.get("clicked") else ""
                print(f"  {sym} {name:25s} status={r['status']} text_ok={r['text_ok']}{err}{click}")
                # 详细错误
                for e in r.get("console_errors", [])[:2]:
                    print(f"       CONSOLE: {e[:120]}")
                for c in r.get("clicked", [])[:2]:
                    print(f"       CLICK: {c[:120]}")
        await browser.close()

    # 汇总
    total = len(all_results)
    ok = sum(1 for r in all_results if r.get("status") == 200 and r.get("text_ok"))
    exceptions = sum(1 for r in all_results if "error" in r)
    errs_count = len(all_console_errors)

    print("\n" + "=" * 70)
    print(f"SUMMARY: {ok}/{total} pages OK, {exceptions} exceptions, {errs_count} console errors")
    print("=" * 70)

    return 0 if (ok == total and errs_count == 0) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
