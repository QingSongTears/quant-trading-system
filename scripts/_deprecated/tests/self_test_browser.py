"""
浏览器自测脚本 (2026-06-25) — 用 playwright 跑端到端验证

用法:
    python scripts/self_test_browser.py

输出:
    - 控制台打印每个页面的状态码 + 关键元素
    - 截图存到 output/screenshots/<page>.png

不依赖 MCP server, 直接调 playwright Python (已装).
"""
import asyncio
import sys
import os

# 修 Windows GBK 编码问题
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from pathlib import Path

from playwright.async_api import async_playwright


BASE_URL = "http://localhost:5050"
SCREENSHOT_DIR = Path("output/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# 用 ASCII 避免 Windows console GBK 问题
OK = "[OK]"
ERR = "[ERR]"
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


BASE_URL = "http://localhost:5050"
SCREENSHOT_DIR = Path("output/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


PAGES_TO_TEST = [
    ("home", "/", ["QuantTrading", "数据来源"]),
    ("workbench", "/workbench", ["回测工作台"]),
    ("workbench-lab", "/workbench?mode=lab", ["Lab 模式"]),
    ("workbench-view", "/workbench?mode=view", ["View 模式"]),
    ("compare", "/compare", ["多模型对比"]),
    ("research-v5", "/research?type=v5", ["V5 研究视图"]),
    ("research-ic", "/research?type=ic", ["IC 分析"]),
    ("research-dim", "/research?type=dim", ["维度 IC 对比"]),
    ("screener", "/screener", ["选股工作台"]),
    ("dashboard", "/dashboard", ["数据总览"]),
    ("strategies", "/strategies", ["策略"]),
    ("data", "/data", ["数据"]),
    ("backtest", "/backtest", ["回测记录"]),
]


async def test_page(browser, name, path, expected_texts):
    """测试单个页面: 状态码 + 关键文本 + 截图"""
    page = await browser.new_page(viewport={"width": 1440, "height": 900})

    console_errors = []
    page.on("console", lambda msg: console_errors.append(f"{msg.type}: {msg.text}") if msg.type == "error" else None)

    page_errors = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    try:
        resp = await page.goto(f"{BASE_URL}{path}", wait_until="networkidle", timeout=10000)
        status = resp.status if resp else 0
        content = await page.content()

        # 关键文本检查
        text_ok = all(t in content for t in expected_texts)

        # 截图
        shot_path = SCREENSHOT_DIR / f"{name}.png"
        await page.screenshot(path=str(shot_path), full_page=False)

        # Footer API key 检查 (在 home 测一次)
        has_api_key = False
        if name == "home":
            api_key_elem = await page.query_selector("#footer-api-key")
            if api_key_elem:
                api_key_text = await api_key_elem.inner_text()
                has_api_key = len(api_key_text) > 10  # 临时 key 至少 32 字符

        result = {
            "name": name,
            "path": path,
            "status": status,
            "text_ok": text_ok,
            "screenshot": str(shot_path),
            "console_errors": console_errors,
            "page_errors": page_errors,
            "has_api_key": has_api_key,
        }
        return result
    except Exception as e:
        return {
            "name": name,
            "path": path,
            "status": 0,
            "error": str(e),
            "screenshot": None,
        }
    finally:
        await page.close()


async def main():
    print("=" * 70)
    print(f"Browser Self-Test — {BASE_URL}")
    print(f"Screenshots → {SCREENSHOT_DIR.absolute()}")
    print("=" * 70)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        results = []
        for name, path, texts in PAGES_TO_TEST:
            r = await test_page(browser, name, path, texts)
            results.append(r)
            # 打印
            ok = OK if (r.get("status") == 200 and r.get("text_ok")) else ERR
            err = ""
            if r.get("console_errors"):
                err = f" console_err={len(r['console_errors'])}"
            if r.get("page_errors"):
                err += f" page_err={len(r['page_errors'])}"
            print(f"  {ok} {name:18s} {path:30s} status={r.get('status')} text_ok={r.get('text_ok')}{err}")

            # 错误详情
            if r.get("error"):
                print(f"     ERROR: {r['error']}")
            for e in r.get("console_errors", [])[:3]:
                print(f"     CONSOLE: {e[:100]}")
            for e in r.get("page_errors", [])[:3]:
                print(f"     PAGE: {e[:100]}")

        await browser.close()

    # 汇总
    total = len(results)
    ok = sum(1 for r in results if r.get("status") == 200 and r.get("text_ok"))
    console_errs = sum(len(r.get("console_errors", [])) for r in results)
    page_errs = sum(len(r.get("page_errors", [])) for r in results)

    print("\n" + "=" * 70)
    print(f"SUMMARY: {ok}/{total} pages OK, {console_errs} console errors, {page_errs} page errors")

    # Footer API key
    home = next((r for r in results if r["name"] == "home"), None)
    if home:
        if home.get("has_api_key"):
            print(f"{OK} Footer API key visible")
        else:
            print(f"{ERR} Footer API key MISSING")

    print("=" * 70)

    # 退出码
    return 0 if (ok == total and console_errs == 0 and page_errs == 0) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))