"""
全量导航页审计: 用 playwright headless 访问首页所有 href, 逐个验证
- HTTP 状态码
- console.error / pageerror
- 关键 DOM 元素存在
- canvas 数量
- 截图保存

输出: screenshots/nav/audit_nav_<page>.png + screenshots/nav/audit_nav_report.json
"""
import json
import time
import re
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = "http://localhost:5054"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "screenshots" / "nav"
OUT.mkdir(parents=True, exist_ok=True)


def grab_hrefs_from_index():
    """从首页 HTML 抓所有 href"""
    import urllib.request
    with urllib.request.urlopen(f"{BASE}/", timeout=10) as r:
        html = r.read().decode("utf-8", errors="ignore")
    hrefs = re.findall(r'href="(/[a-z0-9\-_/]+?)"', html)
    # 去重 + 排序 + 过滤静态资源
    hrefs = sorted(set(h for h in hrefs if not h.startswith("/static") and not h.startswith("/cdn")))
    return hrefs


def audit_one(page, url):
    """访问一个 URL, 返回诊断结果"""
    result = {
        "url": url,
        "http_status": None,
        "title": None,
        "console_errors": [],
        "page_errors": [],
        "failed_requests": [],
        "canvas_count": 0,
        "key_elements": {},
        "ok": False,
        "error": None,
    }
    try:
        resp = page.goto(f"{BASE}{url}", wait_until="domcontentloaded", timeout=15000)
        result["http_status"] = resp.status if resp else None
        # 等 ECharts 渲染
        page.wait_for_timeout(1500)
        result["title"] = page.title()
        # canvas 数量
        result["canvas_count"] = page.locator("canvas").count()
        # 关键元素 (按常见 nav 卡片分类探测)
        for sel, name in [
            ("table", "table"),
            ("form", "form"),
            (".echarts, [data-echart]", "echart_box"),
            ("h1, h2", "heading"),
            ("nav, .navbar", "navbar"),
        ]:
            try:
                result["key_elements"][name] = page.locator(sel).count()
            except Exception:
                result["key_elements"][name] = 0
        # 成功判据: 200 + 无 pageerror
        result["ok"] = (result["http_status"] == 200
                        and len(result["page_errors"]) == 0)
    except PWTimeout:
        result["error"] = "timeout"
    except Exception as e:
        result["error"] = str(e)[:200]
    return result


def main():
    print("=" * 70)
    print("首页全量导航页审计")
    print("=" * 70)

    hrefs = grab_hrefs_from_index()
    print(f"首页发现 {len(hrefs)} 个 href: {hrefs}\n")

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # 收集 console.error / pageerror / failed_request
        page.on("console", lambda msg: results[-1].__setitem__("console_errors",
            results[-1]["console_errors"] + [msg.text])
            if msg.type == "error" and results else None)
        page.on("pageerror", lambda exc: results[-1].__setitem__("page_errors",
            results[-1]["page_errors"] + [str(exc)])
            if results else None)
        page.on("requestfailed", lambda req: results[-1].__setitem__("failed_requests",
            results[-1]["failed_requests"] + [f"{req.method} {req.url}: {req.failure}"])
            if results else None)

        for href in hrefs:
            r = audit_one(page, href)
            results.append(r)
            status_emoji = "✅" if r["ok"] else "❌" if r["http_status"] else "⏱"
            err = f" err={r['error']}" if r["error"] else ""
            ce = f" ce={len(r['console_errors'])}" if r["console_errors"] else ""
            pe = f" pe={len(r['page_errors'])}" if r["page_errors"] else ""
            fr = f" fr={len(r['failed_requests'])}" if r["failed_requests"] else ""
            print(f"  {status_emoji} {r['http_status']} {href:30s} canvas={r['canvas_count']}{ce}{pe}{fr}{err}")
            # 截图 (除 /api/*)
            if not href.startswith("/api"):
                try:
                    slug = href.replace("/", "_").strip("_") or "root"
                    page.screenshot(path=str(OUT / f"audit_nav_{slug}.png"), full_page=False)
                except Exception as e:
                    print(f"      截图失败: {e}")

        browser.close()

    # 汇总
    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    ok = sum(1 for r in results if r["ok"])
    bad = [r for r in results if not r["ok"]]
    print(f"通过: {ok}/{len(results)}")
    if bad:
        print(f"失败 ({len(bad)}):")
        for r in bad:
            print(f"  - {r['url']}: status={r['http_status']} err={r['error']}")
            for e in r["page_errors"][:3]:
                print(f"      pageerror: {e[:120]}")
            for e in r["console_errors"][:3]:
                print(f"      console: {e[:120]}")

    # 写报告
    with open(OUT / "audit_nav_report.json", "w", encoding="utf-8") as f:
        json.dump({"total": len(results), "ok": ok, "results": results},
                  f, ensure_ascii=False, indent=2)
    print(f"\n报告: {OUT}/audit_nav_report.json")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
