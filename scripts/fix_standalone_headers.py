"""
修复 19 个独立模板的 header 显示问题
- 删 position:fixed 的"← 首页"重复链接
- 修复 emoji 渲染（强制指定 font-family 回退到 Apple Color Emoji / Segoe UI Emoji）
- 统一 header 样式（高度、间距、字距）
"""
import re
from pathlib import Path

TMPL_DIR = Path("src/web/templates")

# 19 个独立模板
TARGETS = [
    "backtest-lab", "backtest-view", "bull-report", "dashboard",
    "data-monitor", "diagnose", "dim-compare", "fund-flow-report",
    "ic", "portfolio", "predict", "screener", "sector", "signal",
    "simulate", "strategy-compare", "tuning", "v5", "v6-compare", "verify",
]

# 删除重复的 fixed 首页链接（多种写法）
FIXED_HOME_PATTERNS = [
    # 完整写法
    r'<a href="/" style="position:fixed[^"]*">&larr;\s*首页</a>\s*\n',
    r'<a href="/" style="position:fixed[^"]*">←\s*首页</a>\s*\n',
    r'<a href="/" style="position:fixed[^"]*">⏎\s*首页</a>\s*\n',
    # 简化写法
    r'<a href="/"[^>]*position:\s*fixed[^>]*>\s*[←&larr;]\s*首页\s*</a>\s*\n?',
    r'<a[^>]+href="/"[^>]+position:\s*fixed[^>]+>[^<]*首页[^<]*</a>\s*\n?',
]

# header 统一 CSS（注入到 <style> 块）
HEADER_CSS = """
/* 统一 header 修复（脚本自动注入） */
header {
    display: flex !important;
    align-items: center !important;
    gap: 16px !important;
    padding: 12px 24px !important;
    min-height: 56px !important;
    background: var(--card, #1a1d27) !important;
    border-bottom: 1px solid var(--border, #2a2d3a) !important;
    position: relative !important;
    z-index: 10 !important;
}
header h1 {
    font-size: 1.15rem !important;
    font-weight: 600 !important;
    margin: 0 !important;
    white-space: nowrap !important;
    /* emoji 字体回退（修复 Windows 上 🔬 显示成乱码"❾"） */
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Segoe UI Emoji', 'Apple Color Emoji', 'Noto Color Emoji', 'Twemoji Mozilla', sans-serif !important;
}
header h1 .h1-icon {
    display: inline-block;
    margin-right: 6px;
    font-size: 1.25rem;
    vertical-align: -2px;
}
header .nav-links, header > a[href="/"] {
    margin-left: auto !important;
    color: var(--dim, #888) !important;
    text-decoration: none !important;
    font-size: 0.85rem !important;
    padding: 4px 12px !important;
    border-radius: 6px !important;
    transition: all 0.15s !important;
    white-space: nowrap !important;
}
header .nav-links a, header > a[href="/"] {
    color: var(--dim, #888) !important;
    text-decoration: none !important;
}
header .nav-links a:hover, header > a[href="/"]:hover {
    color: var(--accent, #4fc3f7) !important;
    background: rgba(79, 195, 247, 0.08) !important;
}
header > span, header > .sub {
    color: var(--dim, #888) !important;
    font-size: 0.82rem !important;
}
"""


def fix_file(name: str) -> dict:
    f = TMPL_DIR / f"{name}.html"
    text = f.read_text(encoding="utf-8")
    orig = text
    changes = []

    # 1) 删除 position:fixed 重复首页链接
    for pat in FIXED_HOME_PATTERNS:
        new_text, n = re.subn(pat, "", text)
        if n > 0:
            text = new_text
            changes.append(f"del-fixed-link({n})")

    # 2) 注入统一 header CSS（只第一次）
    if "统一 header 修复" not in text:
        # 找 </style> 前注入
        m = re.search(r"(</style>)", text)
        if m:
            text = text[: m.start()] + HEADER_CSS + "\n" + text[m.start():]
            changes.append("inject-css")
        else:
            # 没有 <style> 块，在 <head> 末尾注入
            text = re.sub(
                r"(</head>)",
                f"<style>{HEADER_CSS}</style>\n\\1",
                text,
                count=1,
            )
            changes.append("inject-style-block")

    if text != orig:
        f.write_text(text, encoding="utf-8")
    return {"file": name, "changes": changes, "modified": text != orig}


if __name__ == "__main__":
    results = [fix_file(n) for n in TARGETS]
    modified = [r for r in results if r["modified"]]
    print(f"修改 {len(modified)}/{len(results)} 个模板")
    for r in results:
        flag = "✓" if r["modified"] else "."
        print(f"  [{flag}] {r['file']}: {','.join(r['changes']) or 'unchanged'}")
