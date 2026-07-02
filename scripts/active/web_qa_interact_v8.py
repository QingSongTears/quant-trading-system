"""
Web QA v8 interaction probe — 点击按钮/触发表单提交, 探测 JS 错误 (2026-07-01)

v5 已覆盖 GET 200 + console error, 但**不覆盖交互后**的 JS 错误.
本脚本:
- 访问关键交互页 (portfolio / workbench / data / dashboard / predict)
- 点击主要按钮/触发表单
- 收集交互后 console error / page error / network 5xx
- 输出 docs/dev-notes/web-qa-v6-interact.json

约束:
- AGENTS.md §3-§6
- 不动 monitoring/ 不动 src/web/app.py
- 文件 < 500 行
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright, BrowserContext  # noqa: E402

OUTPUT_DIR = PROJECT_ROOT / "output" / "web_qa_v8"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "http://localhost:8000"

# 交互探针 — 页面 + 操作序列
PROBES = [
    {
        "name": "dashboard_refresh",
        "url": "/dashboard",
        "actions": [
            {"kind": "wait", "ms": 3000},  # 等首屏 fetch
        ],
    },
    {
        "name": "data_download_status",
        "url": "/data",
        "actions": [
            {"kind": "wait", "ms": 4000},
        ],
    },
    {
        "name": "portfolio_analyze",
        "url": "/portfolio",
        "actions": [
            {"kind": "wait", "ms": 2000},
            {"kind": "click", "selector": "button:has-text('分析持仓')"},
            {"kind": "wait", "ms": 6000},  # 等 5 只 stock 串行 fetch
        ],
    },
    {
        "name": "workbench_form",
        "url": "/workbench",
        "actions": [
            {"kind": "wait", "ms": 2000},
            {"kind": "fill", "selector": "input[type='text']:first-of-type", "value": "000001"},
        ],
    },
    {
        "name": "diagnose_run",
        "url": "/diagnose",
        "actions": [
            {"kind": "wait", "ms": 2000},
            {"kind": "click", "selector": "button"},
            {"kind": "wait", "ms": 3000},
        ],
    },
    {
        "name": "predict_dashboard",
        "url": "/predict",
        "actions": [
            {"kind": "wait", "ms": 3000},
        ],
    },
    {
        "name": "screener_filter",
        "url": "/screener",
        "actions": [
            {"kind": "wait", "ms": 3000},
        ],
    },
    {
        "name": "console_load",
        "url": "/console",
        "actions": [
            {"kind": "wait", "ms": 5000},  # 等 setInterval 触发一次
        ],
    },
]


def get_api_key() -> str:
    import urllib.request
    import re
    req = urllib.request.Request(f"{BASE_URL}/dashboard")
    with urllib.request.urlopen(req, timeout=5) as resp:
        html = resp.read().decode("utf-8")
    m = re.search(r'api-key" content="([^"]+)"', html)
    return m.group(1) if m else ""


def run_probe(ctx: BrowserContext, probe: dict, timeout: int = 20000) -> dict:
    name = probe["name"]
    url = probe["url"]
    page = ctx.new_page()
    console_errors: list[str] = []
    network_404s: list[str] = []
    server_errors: list[str] = []
    page_error: str | None = None
    status: int | None = None

    def on_console(msg):
        if msg.type == "error":
            t = msg.text
            if "cdn.jsdelivr.net" in t or "favicon" in t.lower() or "echarts" in t.lower():
                return
            console_errors.append(t[:300])

    def on_response(resp):
        if resp.status == 404:
            network_404s.append(f"{resp.status} {resp.url[:200]}")
        elif resp.status >= 500:
            server_errors.append(f"{resp.status} {resp.url[:200]}")

    def on_pageerror(exc):
        nonlocal page_error
        page_error = str(exc)[:500]

    page.on("console", on_console)
    page.on("response", on_response)
    page.on("pageerror", on_pageerror)

    started = time.time()
    action_log: list[str] = []
    try:
        resp = page.goto(f"{BASE_URL}{url}", wait_until="commit", timeout=timeout)
        status = resp.status if resp else None
        page.wait_for_timeout(2000)

        for action in probe["actions"]:
            kind = action["kind"]
            try:
                if kind == "wait":
                    page.wait_for_timeout(action["ms"])
                    action_log.append(f"wait {action['ms']}ms")
                elif kind == "click":
                    sel = action["selector"]
                    el = page.locator(sel).first
                    if el.count() == 0:
                        action_log.append(f"click {sel!r} -> NOT FOUND")
                    else:
                        el.click(timeout=3000)
                        action_log.append(f"click {sel!r} -> OK")
                elif kind == "fill":
                    sel = action["selector"]
                    val = action.get("value", "")
                    el = page.locator(sel).first
                    if el.count() == 0:
                        action_log.append(f"fill {sel!r} -> NOT FOUND")
                    else:
                        el.fill(val)
                        action_log.append(f"fill {sel!r}={val!r}")
            except Exception as e:
                action_log.append(f"{kind} ERROR: {str(e)[:100]}")
    except Exception as e:
        page_error = page_error or f"goto exception: {str(e)[:200]}"

    elapsed_ms = int((time.time() - started) * 1000)
    screenshot_path = OUTPUT_DIR / f"interact_{name}.png"
    try:
        page.screenshot(path=str(screenshot_path), full_page=False)
    except Exception:
        pass
    page.close()

    return {
        "name": name,
        "url": url,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "console_errors": console_errors,
        "network_404s": network_404s,
        "server_errors": server_errors,
        "page_error": page_error,
        "actions": action_log,
        "screenshot": str(screenshot_path.relative_to(PROJECT_ROOT)),
    }


def main():
    api_key = get_api_key()
    print(f"[QA v6 interact] API key: {api_key[:8]}...")
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(f'window.__EXPECTED_API_KEY__ = "{api_key}";')
        for probe in PROBES:
            r = run_probe(ctx, probe)
            ok = (
                (r["status"] == 200)
                and not r["console_errors"]
                and not r["network_404s"]
                and not r["server_errors"]
                and not r["page_error"]
            )
            flag = "OK" if ok else "FAIL"
            print(
                f"[{flag:4}] {r['name']:25} {r['status']} {r['elapsed_ms']:5}ms  "
                f"err={len(r['console_errors'])} 404={len(r['network_404s'])} "
                f"500={len(r['server_errors'])} exc={r['page_error'] or '-'}"
            )
            if not ok:
                for e in r["console_errors"][:3]:
                    print(f"        console: {e[:150]}")
                for n in r["network_404s"][:3]:
                    print(f"        404: {n[:150]}")
                if r["page_error"]:
                    print(f"        page_error: {r['page_error'][:200]}")
            results.append(r)
        ctx.close()
        browser.close()

    json_path = OUTPUT_DIR / "interact_results.json"
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    ok = sum(
        1
        for r in results
        if r["status"] == 200
        and not r["console_errors"]
        and not r["network_404s"]
        and not r["server_errors"]
        and not r["page_error"]
    )
    total = len(results)
    print(f"\n[QA v6 interact] {ok}/{total} passed ({ok*100//total}%)")
    print(f"[QA v6 interact] Results: {json_path}")
    return results


if __name__ == "__main__":
    main()
