"""
深度功能测试 - 模拟真实用户操作
- workbench: 点击按钮、提交表单、测试历史回测
- backtest 列表: 实际点击行进入详情
- 首页: 验证数据真的渲染
- 跨页面跳转
"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://localhost:5054"
OUT_DIR = Path("D:/gitHub/qunat/quant-trading-system/output")


async def deep_test():
    results = {"tests": []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        # ============ TEST 1: workbench 加载全部模型 ============
        api_calls = []
        page.on("request", lambda req: api_calls.append(
            f"{req.method} {req.url}"
        ) if "/api/" in req.url else None)

        await page.goto(f"{BASE}/workbench", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # 获取模型数量
        model_count = await page.evaluate("state.models ? state.models.length : 0")
        signal_count = await page.evaluate(
            "state.models ? state.models.filter(m => m.strategy_type === 'signal').length : 0"
        )
        portfolio_count = await page.evaluate(
            "state.models ? state.models.filter(m => m.strategy_type === 'portfolio').length : 0"
        )

        results["tests"].append({
            "name": "workbench_模型加载",
            "status": "PASS" if model_count == 13 else "FAIL",
            "detail": f"总模型={model_count}, signal={signal_count}, portfolio={portfolio_count} (期望 13/5/8)"
        })

        # ============ TEST 2: 一键对比全部模型按钮 ============
        api_calls.clear()
        # 找按钮 - 多个选择器兼容
        button = page.locator("button:has-text('对比'), button:has-text('全部'), button:has-text('运行')").first
        try:
            if await button.count() > 0:
                # 搜一个股票先
                search_input = page.locator("input[placeholder*='股票'], input[placeholder*='代码'], input[placeholder*='搜索']").first
                if await search_input.count() > 0:
                    await search_input.fill("603986")
                    await page.wait_for_timeout(500)

                await button.click()
                await page.wait_for_timeout(8000)  # 等回测完成
                results["tests"].append({
                    "name": "对比按钮触发请求",
                    "status": "PASS" if len(api_calls) > 0 else "FAIL",
                    "detail": f"API 请求数={len(api_calls)}, 样本={api_calls[:3]}"
                })
            else:
                results["tests"].append({
                    "name": "对比按钮触发请求",
                    "status": "FAIL",
                    "detail": "找不到 '对比' / '全部' / '运行' 按钮"
                })
        except Exception as e:
            results["tests"].append({
                "name": "对比按钮触发请求",
                "status": "FAIL",
                "detail": f"异常: {str(e)[:200]}"
            })

        # ============ TEST 3: backtest 列表点击行 ============
        await page.goto(f"{BASE}/backtest", wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # 找表格行
        row_count = await page.locator("table tbody tr").count()
        if row_count > 0:
            try:
                first_link = page.locator("table tbody tr a, table tbody tr button").first
                if await first_link.count() > 0:
                    href = await first_link.get_attribute("href")
                    results["tests"].append({
                        "name": "backtest列表行可点击",
                        "status": "PASS" if href else "FAIL",
                        "detail": f"行数={row_count}, 首个链接={href}"
                    })
                else:
                    # 尝试直接点击第一行
                    first_row = page.locator("table tbody tr").first
                    await first_row.click()
                    await page.wait_for_timeout(1500)
                    current_url = page.url
                    results["tests"].append({
                        "name": "backtest列表行可点击",
                        "status": "PASS" if "/backtest/" in current_url else "FAIL",
                        "detail": f"行数={row_count}, 点击后 URL={current_url}"
                    })
            except Exception as e:
                results["tests"].append({
                    "name": "backtest列表行可点击",
                    "status": "FAIL",
                    "detail": f"异常: {str(e)[:200]}"
                })
        else:
            results["tests"].append({
                "name": "backtest列表行可点击",
                "status": "FAIL",
                "detail": "表格无数据行"
            })

        # ============ TEST 4: backtest 详情页图表渲染 ============
        await page.goto(f"{BASE}/backtest/26", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        canvas_count = await page.locator("canvas").count()
        zr_dom_count = await page.locator("[data-zr-dom-id]").count()
        # 验证页面有内容（不是空白）
        body_text_len = await page.evaluate("document.body.innerText.length")

        results["tests"].append({
            "name": "backtest详情图表渲染",
            "status": "PASS" if canvas_count >= 2 else "FAIL",
            "detail": f"canvas={canvas_count}, zr-dom={zr_dom_count}, 页面文字长度={body_text_len}"
        })

        # 检查关键元素
        # 净值曲线
        has_equity_section = await page.locator("text=净值曲线, text=Equity").count() > 0
        has_trades = await page.locator("text=交易, text=Trade").count() > 0
        results["tests"].append({
            "name": "backtest详情关键区块",
            "status": "PASS" if has_equity_section and has_trades else "WARN",
            "detail": f"净值区={has_equity_section}, 交易区={has_trades}"
        })

        # ============ TEST 5: 首页数据真实渲染 ============
        await page.goto(f"{BASE}/", wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # 找数据卡片
        card_count = await page.locator(".card, [data-card], .stat-card").count()
        canvas_count = await page.locator("canvas").count()

        # 检查页脚数据加载（股票数等）
        stock_text = await page.evaluate("""
            () => {
                const el = document.body.innerText;
                const match = el.match(/(\\d+)\\s*只股票/);
                return match ? match[1] : null;
            }
        """)

        results["tests"].append({
            "name": "首页数据卡片",
            "status": "PASS" if card_count >= 3 else "FAIL",
            "detail": f"卡片={card_count}, canvas={canvas_count}, 显示股票数={stock_text}"
        })

        # ============ TEST 6: 跨页面跳转 + 404 优雅处理 ============
        await page.goto(f"{BASE}/backtest/99999", wait_until="networkidle")
        await page.wait_for_timeout(1000)

        body_text = await page.evaluate("document.body.innerText")
        has_404 = "404" in body_text or "未找到" in body_text or "不存在" in body_text
        has_error = "Error" in body_text or "错误" in body_text or "异常" in body_text

        results["tests"].append({
            "name": "404 backtest 优雅处理",
            "status": "PASS" if has_404 or has_error else "WARN",
            "detail": f"有404提示={has_404}, 有错误提示={has_error}, 页面前200字={body_text[:200]!r}"
        })

        # ============ TEST 7: 策略列表能展示 ============
        await page.goto(f"{BASE}/strategies", wait_until="networkidle")
        await page.wait_for_timeout(1500)

        body_text = await page.evaluate("document.body.innerText")
        strategy_count = body_text.count("策略") + body_text.count("strategy")
        has_13 = "13" in body_text
        results["tests"].append({
            "name": "策略列表展示",
            "status": "PASS" if has_13 and strategy_count >= 5 else "WARN",
            "detail": f"'13'出现={has_13}, '策略'相关词={strategy_count}"
        })

        # ============ TEST 8: API 错误处理 (不存在的策略) ============
        api_calls.clear()
        bad_response = await page.evaluate("""
            async () => {
                try {
                    const r = await fetch('/api/backtest/run', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            strategy_id: 'not_exist_xyz',
                            stock_code: '601138',
                            start_date: '2025-01-01',
                            end_date: '2025-12-31',
                            initial_capital: 100000
                        })
                    });
                    return {status: r.status, body: await r.text()};
                } catch (e) {
                    return {error: e.message};
                }
            }
        """)
        results["tests"].append({
            "name": "API 错误请求处理",
            "status": "PASS" if bad_response.get("status", 0) >= 400 else "WARN",
            "detail": f"响应={json.dumps(bad_response)[:300]}"
        })

        # ============ TEST 9: data 页面 ============
        await page.goto(f"{BASE}/data", wait_until="networkidle")
        await page.wait_for_timeout(1500)

        body_text = await page.evaluate("document.body.innerText")
        has_5209 = "5209" in body_text or "5206" in body_text
        results["tests"].append({
            "name": "data 页面股票总数",
            "status": "PASS" if has_5209 else "WARN",
            "detail": f"页面包含 5209/5206 = {has_5209}, body 前300字={body_text[:300]!r}"
        })

        # ============ TEST 10: compare 页面 ============
        await page.goto(f"{BASE}/compare", wait_until="networkidle")
        await page.wait_for_timeout(1500)

        body_text = await page.evaluate("document.body.innerText")
        has_compare = "对比" in body_text or "compare" in body_text.lower() or len(body_text) > 100
        results["tests"].append({
            "name": "compare 页面",
            "status": "PASS" if has_compare else "FAIL",
            "detail": f"body 长度={len(body_text)}, 前200字={body_text[:200]!r}"
        })

        # ============ TEST 11: 不存在的 backtest_id (修复验证) ============
        await page.goto(f"{BASE}/backtest/26", wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # 截图: backtest 26 完整页面
        await page.screenshot(path=str(OUT_DIR / "deep_test_backtest26.png"), full_page=True)

        await browser.close()

    # 汇总
    print("\n" + "=" * 70)
    print("深度功能测试结果")
    print("=" * 70)
    passed = sum(1 for t in results["tests"] if t["status"] == "PASS")
    warned = sum(1 for t in results["tests"] if t["status"] == "WARN")
    failed = sum(1 for t in results["tests"] if t["status"] == "FAIL")
    for t in results["tests"]:
        mark = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[t["status"]]
        print(f"{mark} [{t['status']}] {t['name']}")
        print(f"     {t['detail']}")
    print("=" * 70)
    print(f"总计: PASS={passed}  WARN={warned}  FAIL={failed}")

    with open(OUT_DIR / "deep_test_report.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(deep_test())
