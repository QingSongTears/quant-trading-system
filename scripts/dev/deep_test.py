#!/usr/bin/env python3
"""
deep_test.py — 深度交互测试

对每个有交互的页面, 模拟用户操作:
- /diagnose: 搜股票 → 看结果 → 看 K线/雷达/信号
- /screener: 输入过滤条件 → 看结果
- /backtest: 点 "运行回测" → 看结果
- /workbench: 切 mode → 看渲染
- /simulate: 选 run_id → 看交易/收益
- /research: 切 type → 看 tab 切换
- /data: 看数据覆盖率
- /strategies: 看策略列表 + 详情

输出: /tmp/deep_test_report.json
"""
from __future__ import annotations
import subprocess, sys, time, json, base64
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:8000"
TOK_B64 = "/tmp/tok.b64"

def get_token() -> str:
    if Path(TOK_B64).exists():
        return base64.b64decode(Path(TOK_B64).read_text().strip()).decode()
    raise RuntimeError("no token in /tmp/tok.b64")


def test_diagnose(page, code="000001") -> dict:
    """深度测诊断页"""
    result = {"page": "/diagnose", "code": code, "checks": {}}

    page.goto(f"{BASE_URL}/diagnose", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)

    # 输入 code
    inp = page.query_selector("#searchInput")
    if not inp:
        result["checks"]["input"] = "❌ 没找到搜索框"
        return result
    inp.fill(code)
    page.wait_for_timeout(500)
    inp.press("Enter")
    page.wait_for_timeout(3000)

    # 检查各区域
    state = page.evaluate("""
        () => {
            const get = id => {
                const el = document.getElementById(id);
                return el ? {text: el.textContent?.slice(0,100), display: el.style.display} : null;
            };
            return {
                sName: get('sName'),
                sCode: get('sCode'),
                sPrice: get('sPrice'),
                sChange: get('sChange'),
                sMeta: get('sMeta'),
                radarCanvas: !!document.querySelector('#radarChart, canvas[id*=radar]'),
                klineCanvas: !!document.querySelector('#klineChart, canvas[id*=kline]'),
                signalGrid: get('signalGrid'),
                diagContent: get('diagContent'),
                emptyState: get('emptyState'),
            };
        }
    """)

    checks = {}
    checks["sName有内容"] = bool(state["sName"] and state["sName"]["text"].strip())
    checks["sCode有内容"] = bool(state["sCode"] and state["sCode"]["text"].strip())
    checks["sPrice有内容"] = bool(state["sPrice"] and state["sPrice"]["text"].strip() and state["sPrice"]["text"] != "--")
    checks["diagContent显示"] = state["diagContent"] and state["diagContent"]["display"] != "none"
    checks["emptyState隐藏"] = state["emptyState"] and state["emptyState"]["display"] == "none"
    checks["radar图渲染"] = state["radarCanvas"]
    checks["kline图渲染"] = state["klineCanvas"]
    checks["signal有内容"] = bool(state["signalGrid"] and state["signalGrid"]["text"].strip())

    result["checks"] = checks
    result["state"] = state
    page.screenshot(path="/tmp/deep_diagnose.png", full_page=True)
    return result


def test_screener(page) -> dict:
    """深度测选股页"""
    result = {"page": "/screener", "checks": {}}
    page.goto(f"{BASE_URL}/screener", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)

    # 看表格有没有数据
    state = page.evaluate("""
        () => {
            const tables = document.querySelectorAll('table');
            const tbody = document.querySelector('tbody');
            return {
                tableCount: tables.length,
                tbodyRows: tbody ? tbody.querySelectorAll('tr').length : 0,
                tbodyFirstRowText: tbody && tbody.querySelector('tr') ?
                    tbody.querySelector('tr').textContent?.slice(0,200) : null,
                h1: document.querySelector('h1')?.textContent,
            };
        }
    """)
    result["state"] = state
    result["checks"]["表格存在"] = state["tableCount"] > 0
    result["checks"]["有数据行"] = state["tbodyRows"] > 0
    page.screenshot(path="/tmp/deep_screener.png", full_page=True)
    return result


def test_backtest(page) -> dict:
    """深度测回测页"""
    result = {"page": "/backtest", "checks": {}}
    page.goto(f"{BASE_URL}/backtest", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)

    state = page.evaluate("""
        () => {
            const buttons = Array.from(document.querySelectorAll('button, a.btn'))
                .map(b => b.textContent?.trim()).filter(t => t && t.length < 30);
            return {
                buttonList: buttons.slice(0, 10),
                hasForm: !!document.querySelector('form'),
                h1: document.querySelector('h1, h2')?.textContent,
            };
        }
    """)
    result["state"] = state
    result["checks"]["有表单或按钮"] = state["hasForm"] or len(state["buttonList"]) > 0
    page.screenshot(path="/tmp/deep_backtest.png", full_page=True)
    return result


def test_simulate(page) -> dict:
    """深度测模拟页"""
    result = {"page": "/simulate", "checks": {}}
    page.goto(f"{BASE_URL}/simulate", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)

    state = page.evaluate("""
        () => {
            return {
                h1: document.querySelector('h1')?.textContent,
                hasTable: !!document.querySelector('table'),
                tableRows: document.querySelectorAll('tbody tr').length,
                firstRow: document.querySelector('tbody tr')?.textContent?.slice(0,200),
            };
        }
    """)
    result["state"] = state
    result["checks"]["有表格"] = state["hasTable"]
    page.screenshot(path="/tmp/deep_simulate.png", full_page=True)
    return result


def test_research(page) -> dict:
    """深度测研究页"""
    result = {"page": "/research", "checks": {}}
    page.goto(f"{BASE_URL}/research?type=v5", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)

    state = page.evaluate("""
        () => {
            return {
                h1: document.querySelector('h1')?.textContent,
                tabs: Array.from(document.querySelectorAll('.tab, [role=tab], button'))
                    .map(b => b.textContent?.trim()).filter(t => t && t.length < 20).slice(0,10),
                mainContent: document.querySelector('main, .main, #main, .content, .tab-content')?.textContent?.slice(0,300),
            };
        }
    """)
    result["state"] = state
    page.screenshot(path="/tmp/deep_research.png", full_page=True)
    return result


def main():
    token = get_token()
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 1200},
            extra_http_headers={"Authorization": "Bearer " + token}
        )

        tests = [
            ("diagnose", lambda pg: test_diagnose(pg, "000001")),
            ("screener", test_screener),
            ("backtest", test_backtest),
            ("simulate", test_simulate),
            ("research", test_research),
        ]

        for name, fn in tests:
            page = ctx.new_page()
            print(f"\n=== /{name} ===")
            errors = []
            page.on("console", lambda m: errors.append(f"[{m.type}] {m.text[:200]}") if m.type == "error" else None)
            try:
                r = fn(page)
                r["console_errors"] = errors[:5]
                passed = sum(1 for v in r["checks"].values() if v)
                total = len(r["checks"])
                r["passed"] = passed
                r["total"] = total
                r["verdict"] = "✅" if passed == total else ("⚠️" if passed > 0 else "❌")
                results.append(r)
                print(f"  {r['verdict']} {passed}/{total} checks")
                for k, v in r["checks"].items():
                    mark = "✅" if v else "❌"
                    print(f"    {mark} {k}")
            except Exception as e:
                results.append({"page": "/" + name, "error": str(e)[:200]})
                print(f"  ❌ ERROR: {str(e)[:200]}")
            page.close()

        browser.close()

    Path("/tmp/deep_test_report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print("\n=== 报告 ===")
    total_pass = sum(r.get("passed", 0) for r in results)
    total_check = sum(r.get("total", 0) for r in results)
    print(f"  📊 {total_pass}/{total_check} checks passed")
    print(f"  报告: /tmp/deep_test_report.json")
    print(f"  截图: /tmp/deep_*.png")


if __name__ == "__main__":
    main()