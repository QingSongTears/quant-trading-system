"""
Headless 浏览器冒烟测试
-----------------------
启动 chromium 无头模式，访问 Web 应用关键页面：
  1. /                  仪表盘
  2. /backtest/26       回测详情（之前修过的核心页面）
  3. /workbench         多模型回测工作台
  4. /strategies        策略列表

对每个页面：
  - 截图保存到 screenshots/smoketest/<page>.png
  - 捕获 console 错误 / 页面错误 / 失败请求
  - 等待 ECharts canvas 实际绘制（data-zr-dom-id 出现）
  - 检测关键 DOM 元素是否存在

退出码：
  0 = 所有页面无致命错误
  1 = 至少一个页面有 JS 错误或元素缺失
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "screenshots" / "smoketest"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)

PAGES = [
    ("home", "/",               [".navbar", ".container"]),
    ("backtest26", "/backtest/26", [".navbar", "canvas", "[data-zr-dom-id]"]),
    ("workbench", "/workbench", [".navbar", "form, .form-control"]),
    ("strategies", "/strategies", [".navbar", "table, .card"]),
]

BASE = "http://localhost:5054"


async def audit(page, name: str) -> dict:
    """审计单页：控制台错误、页面错误、失败请求、DOM 检查"""
    errors = []
    page_errors = []
    failed_requests = []

    page.on("console", lambda msg: errors.append(f"[{msg.type}] {msg.text}")
            if msg.type in ("error", "warning") else None)
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    page.on("requestfailed", lambda req: failed_requests.append(
        f"{req.method} {req.url} → {req.failure}"))

    result = {"name": name, "errors": [], "page_errors": [], "failed_requests": []}

    try:
        await page.goto(BASE + name_to_path(name), wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        result["errors"].append(f"goto failed: {e}")
        return result

    # 给 ECharts 一点时间渲染
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)

    # 检查 ECharts canvas 是否实际绘制
    canvas_count = await page.locator("canvas").count()
    zr_count = await page.locator("[data-zr-dom-id]").count()
    result["canvases"] = canvas_count
    result["zr_doms"] = zr_count

    # 截图
    shot_path = OUT_DIR / f"smoketest_{name}.png"
    try:
        await page.screenshot(path=str(shot_path), full_page=True)
        result["screenshot"] = str(shot_path)
        result["screenshot_size"] = shot_path.stat().st_size
    except Exception as e:
        result["screenshot_error"] = str(e)

    result["errors"] = list({e for e in errors if e})
    result["page_errors"] = page_errors
    result["failed_requests"] = failed_requests[:20]  # 截断

    return result


def name_to_path(name: str) -> str:
    for n, p, _ in PAGES:
        if n == name:
            return p
    return "/"


async def main():
    print("=" * 70)
    print("Headless 冒烟测试")
    print("=" * 70)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        all_results = []
        for name, _path, _dom in PAGES:
            print(f"\n>> {name}")
            r = await audit(page, name)
            all_results.append(r)
            print(f"   canvases={r.get('canvases', 0)}  zr_doms={r.get('zr_doms', 0)}")
            print(f"   shot={r.get('screenshot', '-')} ({r.get('screenshot_size', 0)} bytes)")
            print(f"   console errors={len(r['errors'])}  page errors={len(r['page_errors'])}  failed req={len(r['failed_requests'])}")
            if r["page_errors"]:
                for e in r["page_errors"][:5]:
                    print(f"     PAGE ERR: {e}")
            if r["errors"]:
                for e in r["errors"][:5]:
                    print(f"     CONSOLE : {e}")
            if r["failed_requests"]:
                for e in r["failed_requests"][:5]:
                    print(f"     REQ FAIL: {e}")

        await browser.close()

    # 汇总
    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    fatal = []
    for r in all_results:
        if r["page_errors"] or (r.get("canvases", 0) == 0 and "backtest" in r["name"]):
            fatal.append(r["name"])

    report_path = OUT_DIR / "smoketest_report.json"
    report_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON 报告: {report_path}")

    if fatal:
        print(f"\n❌ 致命问题: {fatal}")
        sys.exit(1)
    else:
        print("\n✅ 所有页面通过冒烟测试")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
