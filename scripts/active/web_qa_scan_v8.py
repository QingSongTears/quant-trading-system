"""
Web QA scan — 33 页面 GET + 截图 + console error + network 404 (Web QA v8)

任务 (Web QA v8 — 2026-07-01):
- v5 baseline: 33/33 通过 (100%) — T1-T12 全部生效
- v6 复跑: 探测回归 (任何从 200 → 4xx/5xx 或新增 console error 都算 regression)
- 每页截图到 output/web_qa_v8/<page>.png
- 记录 console error (过滤已知 CDN 沙盒失败)
- 记录 network 404 (静态资源)

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

# 33 页面 — 含子页面 + backtest_detail 兜底 (用 -1 测试 404 兜底页)
PAGES = [
    # 主导航
    ("dashboard", "/dashboard"),
    ("data", "/data"),
    ("backtest", "/backtest"),
    ("backtest_detail", "/backtest/1"),  # 数据库空 → 兜底 404
    ("strategies", "/strategies"),
    ("compare", "/compare"),
    ("workbench", "/workbench"),
    ("research", "/research"),
    # 业务页面
    ("simulate", "/simulate"),
    ("stock_detail", "/stock/600519"),
    ("ardot_specs", "/ardot-specs"),
    ("console", "/console"),
    ("fund_flow_report", "/fund-flow-report"),
    ("diagnose", "/diagnose"),
    ("sector", "/sector"),
    ("screener", "/screener"),
    # 独立模板
    ("portfolio", "/portfolio"),
    ("bull_report", "/bull-report"),
    ("signal", "/signal"),
    ("verify", "/verify"),
    ("predict", "/predict"),
    ("data_monitor", "/data-monitor"),
    ("walk_forward", "/walk-forward"),
    ("backtest_lab", "/backtest-lab"),
    ("signal_dashboard", "/signal-dashboard"),
    ("strategy_compare", "/strategy-compare"),
    ("tuning_panel", "/tuning-panel"),
    ("v5_tuning", "/v5-tuning"),
    ("v6_compare", "/v6-compare"),
    ("multi_objective", "/multi-objective"),
    ("ic_analysis", "/ic-analysis"),
    ("dim_compare", "/dim-compare"),
    ("predict_verify", "/predict-verify"),
]


def setup_context(p, api_key: str) -> BrowserContext:
    """创建浏览器 context, 注入 API key"""
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(f'window.__EXPECTED_API_KEY__ = "{api_key}";')
    return ctx


def visit_page(ctx: BrowserContext, name: str, url: str, timeout: int = 15000):
    """访问单个页面, 收集 (name, status, console_errors, network_404s, screenshot_path)"""
    page = ctx.new_page()
    console_errors: list[str] = []
    network_404s: list[str] = []
    failed_requests: list[str] = []
    status: int | None = None
    page_error: str | None = None

    def on_console(msg):
        if msg.type == "error":
            text = msg.text
            # 过滤已知沙盒可忽略项 (echarts CDN / 离线模式)
            if "cdn.jsdelivr.net" in text or "echarts" in text.lower():
                return
            if "favicon" in text.lower():
                return
            console_errors.append(text[:300])

    def on_response(resp):
        if resp.status == 404:
            network_404s.append(f"{resp.status} {resp.url[:200]}")
        if resp.status >= 500:
            failed_requests.append(f"{resp.status} {resp.url[:200]}")

    def on_pageerror(exc):
        nonlocal page_error
        page_error = str(exc)[:500]

    page.on("console", on_console)
    page.on("response", on_response)
    page.on("pageerror", on_pageerror)

    full_url = f"{BASE_URL}{url}"
    screenshot_path = OUTPUT_DIR / f"{name}.png"
    started = time.time()
    try:
        # commit 而非 domcontentloaded — 不等所有阻塞脚本
        resp = page.goto(full_url, wait_until="commit", timeout=timeout)
        status = resp.status if resp else None
        try:
            page.wait_for_selector("body", state="attached", timeout=3000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        page.screenshot(path=str(screenshot_path), full_page=False)
    except Exception as e:
        page_error = page_error or f"goto exception: {str(e)[:200]}"
        try:
            page.screenshot(path=str(screenshot_path), full_page=False)
        except Exception:
            pass
    elapsed_ms = int((time.time() - started) * 1000)
    page.close()
    return {
        "name": name,
        "url": url,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "console_errors": console_errors,
        "network_404s": network_404s,
        "server_errors": failed_requests,
        "page_error": page_error,
        "screenshot": str(screenshot_path.relative_to(PROJECT_ROOT)),
    }


def get_api_key() -> str:
    """从 dashboard 页面抓 API key"""
    import urllib.request
    import re
    req = urllib.request.Request(f"{BASE_URL}/dashboard")
    with urllib.request.urlopen(req, timeout=5) as resp:
        html = resp.read().decode("utf-8")
    m = re.search(r'api-key" content="([^"]+)"', html)
    return m.group(1) if m else ""


def main():
    api_key = get_api_key()
    print(f"[QA v8] API key: {api_key[:8]}...")
    results = []
    with sync_playwright() as p:
        ctx = setup_context(p, api_key)
        for name, url in PAGES:
            r = visit_page(ctx, name, url)
            # 兜底: backtest_detail 空数据 → 404 是预期行为
            expected_404 = name == "backtest_detail"
            is_clean = (
                r["status"] == 200
                and not r["console_errors"]
                and not r["network_404s"]
                and not r["server_errors"]
                and not r["page_error"]
            )
            is_acceptable = is_clean or (expected_404 and r["status"] == 404)
            flag = "OK" if is_acceptable else "FAIL"
            print(
                f"[{flag:4}] {name:20} {r['status']} {r['elapsed_ms']:5}ms  "
                f"err={len(r['console_errors'])} 404={len(r['network_404s'])} "
                f"500={len(r['server_errors'])} exc={r['page_error'] or '-'}"
            )
            results.append(r)
        ctx.close()

    json_path = OUTPUT_DIR / "results.json"
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    total = len(results)
    ok = sum(
        1
        for r in results
        if (
            r["status"] == 200
            and not r["console_errors"]
            and not r["network_404s"]
            and not r["server_errors"]
            and not r["page_error"]
        )
        or (r["name"] == "backtest_detail" and r["status"] == 404)
    )
    print(f"\n[QA v8] {ok}/{total} passed ({ok*100//total}%)")
    print(f"[QA v8] Results: {json_path}")
    return results


if __name__ == "__main__":
    main()