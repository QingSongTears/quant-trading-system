"""
验证 19 个独立模板 header 修复结果
- 每个页面截图顶部 120px
- 报告 console error / pageerror
- 检查 header 高度 ≥ 50px, h1 不被遮挡
"""
from playwright.sync_api import sync_playwright
import json
from pathlib import Path

PAGES = [
    'diagnose', 'dashboard', 'ic', 'strategy-compare', 'portfolio',
    'bull-report', 'predict', 'screener', 'sector', 'signal',
    'tuning', 'verify', 'v5', 'v6-compare', 'dim-compare',
    'fund-flow-report', 'backtest-lab', 'backtest-view', 'data-monitor',
]

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "screenshots" / "header"
OUT_DIR.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    results = {}

    for name in PAGES:
        page = context.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(("pageerror", str(e)[:120])))
        page.on("console", lambda m: m.type == "error" and errs.append(("console", m.text[:120])))
        try:
            page.goto(f"http://localhost:5054/{name}", wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(1000)
            # 截图顶部 200px
            page.screenshot(path=str(OUT_DIR / f"{name}.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 120})
            # 检查 header 高度
            info = page.evaluate("""() => {
                const h = document.querySelector('header');
                if (!h) return {has_header: false};
                const r = h.getBoundingClientRect();
                const h1 = h.querySelector('h1');
                const h1r = h1 ? h1.getBoundingClientRect() : null;
                const fixedHome = document.querySelector('a[href=\"/\"][style*=\"fixed\"]');
                return {
                    has_header: true,
                    header_h: Math.round(r.height),
                    header_y: Math.round(r.y),
                    h1_text: h1 ? h1.textContent.trim().slice(0, 30) : '',
                    h1_w: h1r ? Math.round(h1r.width) : 0,
                    h1_h: h1r ? Math.round(h1r.height) : 0,
                    has_fixed_home_dup: !!fixedHome,
                };
            }""")
            results[name] = {"info": info, "errors": errs}
        except Exception as e:
            results[name] = {"info": {}, "errors": [("load_error", str(e)[:120])]}
        page.close()

    browser.close()

# 输出报告
print("=" * 80)
print(f"{'PAGE':<25} {'H_H':<5} {'H1_TEXT':<20} {'FIX_DUP':<8} {'ERR'}")
print("=" * 80)
ok = bad = 0
for name, r in results.items():
    info = r["info"]
    if not info.get("has_header"):
        print(f"{name:<25} NO HEADER (独立页面没 header 块)")
        continue
    h_h = info.get("header_h", 0)
    h1 = info.get("h1_text", "")[:20]
    dup = "❌有" if info.get("has_fixed_home_dup") else "✓无"
    n_err = len(r["errors"])
    err_short = "; ".join(f"{e[0]}:{e[1][:30]}" for e in r["errors"][:2]) or "✓"
    flag = "✓" if h_h >= 50 and n_err == 0 else "✗"
    print(f"{name:<25} {h_h:<5} {h1:<20} {dup:<8} {err_short}")
    if h_h >= 50 and n_err == 0 and not info.get("has_fixed_home_dup"):
        ok += 1
    else:
        bad += 1
print("=" * 80)
print(f"OK: {ok}  BAD: {bad}  TOTAL: {len(results)}")
