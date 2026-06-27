#!/usr/bin/env python3
"""
self_test.py — 浏览器自测脚本
用法:
  python scripts/dev/self_test.py              # 跑全量页面测试
  python scripts/dev/self_test.py /diagnose    # 测单个页面
  python scripts/dev/self_test.py --code 000001  # 测个股 code 在哪些页面

输出:
  - 各页面截图 → /tmp/self_test_shots/
  - 报告 → /tmp/self_test_report.md
  - 控制台: 每页 console error + 网络 404 + API 失败
"""
from __future__ import annotations
import sys, os, json, time, base64, re
from pathlib import Path
from playwright.sync_api import sync_playwright, Page

BASE_URL = "http://localhost:8000"
TOK_PATH = Path("/tmp/tok.b64")
SHOTS_DIR = Path("/tmp/self_test_shots")
SHOTS_DIR.mkdir(exist_ok=True)

# 32 个主要页面
PAGES = [
    ("/dashboard", "数据总览"),
    ("/data", "数据"),
    ("/backtest", "回测"),
    ("/strategies", "策略"),
    ("/screener", "选股"),
    ("/research", "研究"),
    ("/simulate", "模拟"),
    ("/console", "控制台"),
    ("/diagnose", "诊断"),
    ("/workbench", "工作台"),
    ("/compare", "对比"),
    ("/v5", "V5"),
    ("/v6", "V6"),
    ("/v7", "V7"),
]


def get_token() -> str:
    """从 /tmp/tok.b64 读 token"""
    if TOK_PATH.exists():
        return base64.b64decode(TOK_PATH.read_text().strip()).decode()
    # fallback: 起进程临时生成
    import subprocess
    out = subprocess.run(
        ["/home/ubuntu/.hermes/quant-venv/bin/python", "-c",
         "import sys; sys.path.insert(0, '/home/ubuntu/quant-trading-system'); "
         "from src.web.auth import get_api_key; print(get_api_key())"],
        capture_output=True, text=True
    ).stdout.strip()
    b64 = base64.b64encode(out.encode()).decode()
    TOK_PATH.write_text(b64)
    return out


def test_pages(test_path: str | None = None) -> dict:
    """跑全量页面测试, 返回 report 字典"""
    token = get_token()
    pages = [test_path] if test_path else [p[0] for p in PAGES]
    page_names = {p[0]: p[1] for p in PAGES}

    report = {"pages": [], "summary": {"ok": 0, "warn": 0, "fail": 0, "total": 0}}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu"]
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="SelfTest/1.0"
        )

        for path in pages:
            page = ctx.new_page()
            errors = []
            failed_api = []
            network_404 = []

            page.on("console", lambda m, errs=errors:
                errs.append(f"[{m.type}] {m.text[:200]}") if m.type == "error" else None)

            page.on("response", lambda r, n404=network_404, fapi=failed_api:
                n404.append(f"{r.status} {r.url}") if r.status == 404 else
                (fapi.append(f"{r.status} {r.url}") if r.status >= 500 and "/api/" in r.url else None))

            url = BASE_URL + path
            t0 = time.time()
            try:
                # /v6 /v7 是 301 redirect 到 /research, 用 follow_redirects=True
                resp = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                status = resp.status if resp else 0
                if status in (301, 302, 307, 308):
                    # 手动 follow
                    final = page.url
                    status = 200 if "localhost:8000" in final else status
                # 等关键元素渲染 (额外 2 秒)
                page.wait_for_timeout(2000)
            except Exception as e:
                status = -1
                errors.append(f"NAV ERROR: {str(e)[:200]}")
            elapsed = time.time() - t0

            # 截图
            shot = SHOTS_DIR / (path.replace("/", "_").strip("_") + ".png")
            try:
                page.screenshot(path=str(shot), full_page=False)
            except Exception:
                pass

            ok = status == 200 and len(errors) == 0 and len(failed_api) == 0
            warn = status == 200 and (len(errors) > 0 or len(failed_api) > 0)
            fail = status != 200

            if ok:
                report["summary"]["ok"] += 1
            elif warn:
                report["summary"]["warn"] += 1
            else:
                report["summary"]["fail"] += 1
            report["summary"]["total"] += 1

            report["pages"].append({
                "path": path,
                "name": page_names.get(path, "?"),
                "status": status,
                "elapsed_s": round(elapsed, 2),
                "console_errors": errors[:5],
                "failed_api": failed_api[:5],
                "network_404": network_404[:5],
                "verdict": "✅" if ok else ("⚠️" if warn else "❌"),
                "shot": str(shot.name),
            })
            print(f"  {report['pages'][-1]['verdict']} {path} ({status} {elapsed:.1f}s) errors={len(errors)} 404={len(network_404)}")
            page.close()

        browser.close()
    return report


def test_diagnose(code: str = "000001") -> dict:
    """专门测 /diagnose 页面, 模拟用户搜股票+看结果"""
    token = get_token()
    result = {"code": code, "steps": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 1200})

        # 加 Authorization header 给所有请求
        ctx.set_extra_http_headers({"Authorization": "Bearer " + token})

        page = ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(f"[{m.type}] {m.text[:300]}") if m.type == "error" else None)

        # Step 1: 打开 /diagnose
        print(f"\n=== Step 1: 打开 /diagnose ===")
        resp = page.goto(BASE_URL + "/diagnose", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        result["steps"].append({"step": "open /diagnose", "status": resp.status if resp else -1})
        page.screenshot(path=str(SHOTS_DIR / "diagnose_initial.png"))
        print(f"  status: {resp.status if resp else -1}")

        # Step 2: 找搜索框, 输 code
        print(f"\n=== Step 2: 搜 {code} ===")
        # 找 input (诊断页通常有搜索框)
        search_input = None
        for selector in ['input[placeholder*="股票"]', 'input[placeholder*="代码"]',
                        'input[type="search"]', 'input.search', 'input.form-control',
                        'input']:
            try:
                el = page.query_selector(selector)
                if el:
                    search_input = el
                    print(f"  找到 input: {selector}")
                    break
            except Exception:
                continue

        if search_input:
            # 用 code (纯数字) 直接触发诊断
            search_input.fill(code)
            page.wait_for_timeout(500)
            # 看是否有下拉建议
            page.screenshot(path=str(SHOTS_DIR / "diagnose_typed.png"))

            # Step 3: 触发搜索 (Enter 调 diagnose())
            print(f"\n=== Step 3: 触发诊断 ===")
            search_input.press("Enter")
            page.wait_for_timeout(3000)
            page.screenshot(path=str(SHOTS_DIR / "diagnose_entered.png"), full_page=True)

            # 检查诊断结果区是否显示
            diag_state = page.evaluate("""
                () => {
                    const empty = document.getElementById('emptyState');
                    const content = document.getElementById('diagContent');
                    return {
                        emptyDisplay: empty ? empty.style.display : 'no element',
                        contentDisplay: content ? content.style.display : 'no element',
                        sName: document.getElementById('sName')?.textContent,
                        sCode: document.getElementById('sCode')?.textContent,
                        sPrice: document.getElementById('sPrice')?.textContent,
                        suggestionsDisplay: document.getElementById('suggestions')?.style.display,
                        // 找所有有内容的元素
                        visibleText: Array.from(document.querySelectorAll('h1, h2, h3, h4, .stock-name, .stock-code, .stock-price, [id^="s"]'))
                            .map(e => ({tag: e.tagName, id: e.id, text: e.textContent?.slice(0, 100)}))
                            .filter(x => x.text)
                    };
                }
            """)
            result["steps"].append({"step": "diagnose result", "diag_state": diag_state})
            print(f"  结果: {json.dumps(diag_state, ensure_ascii=False)[:400]}")

            # 看具体哪些元素有内容
            print(f"\n=== 元素文本 ===")
            for item in diag_state.get('visibleText', [])[:20]:
                print(f"  {item['tag']}#{item['id']}: {item['text']}")
        else:
            result["steps"].append({"step": "find input", "error": "no search input found"})
            print(f"  ❌ 没找到搜索框")

        result["console_errors"] = errors[:10]
        browser.close()

    return result


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        arg = sys.argv[1]
        if arg.startswith("--code=") or (len(sys.argv) >= 3 and sys.argv[1] == "--code"):
            code = arg.split("=", 1)[1] if "=" in arg else sys.argv[2]
            print(f"=== 测个股诊断 code={code} ===")
            r = test_diagnose(code)
            print("\n=== 报告 ===")
            print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"=== 测单页面 {arg} ===")
            r = test_pages(arg)
            print("\n=== 报告 ===")
            print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"=== 跑全量 {len(PAGES)} 页面 ===")
        r = test_pages()
        # 输出汇总
        s = r["summary"]
        print(f"\n=== 汇总 ===")
        print(f"  ✅ OK:   {s['ok']}/{s['total']}")
        print(f"  ⚠️ WARN: {s['warn']}/{s['total']}")
        print(f"  ❌ FAIL: {s['fail']}/{s['total']}")
        # 写报告
        report_path = "/tmp/self_test_report.json"
        Path(report_path).write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        print(f"  详情: {report_path}")
        print(f"  截图: {SHOTS_DIR}/")