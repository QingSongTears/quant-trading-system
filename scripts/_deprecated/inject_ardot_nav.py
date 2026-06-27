"""
inject_ardot_nav.py — 把 partials/_ardot_nav.html 注入到所有独立 HTML 模板顶部
2026-06-26 Ardot 落地收尾 Phase 2

策略:
  - 优先找 <body> 标签后插入 include
  - 没有 <body> 的, 在 <header> 前插入 include (浏览器会自动补 body)
  - 已有 include 的跳过
  - 只处理 templates/ 下的 *.html (排除 base.html / dashboard.html / index.html / _backup_pre_ardot/)
"""
from pathlib import Path
import sys
import re

ROOT = Path(__file__).resolve().parent.parent
TPL_DIR = ROOT / "src" / "web" / "templates"

# 跳过这些文件 (它们自己处理 header 或有特殊结构)
SKIP = {
    "base.html",
    "dashboard.html",   # 已是首页, 不注入 topbar
    "index.html",      # 待删
    "error.html",
    "ardot_specs_index.html",
    "_ardot_nav.html",
    "stock_detail.html",  # extends base.html
}

INCLUDE_LINE = '{% include "partials/_ardot_nav.html" %}\n'


def main():
    changed = []
    skipped = []
    for html in sorted(TPL_DIR.glob("*.html")):
        if html.name in SKIP:
            skipped.append(html.name)
            continue
        # Skip _backup_pre_ardot
        if "_backup_pre_ardot" in str(html):
            continue
        text = html.read_text(encoding="utf-8")
        if INCLUDE_LINE in text:
            skipped.append(f"{html.name} (already has include)")
            continue

        new_text = None
        # 策略 1: <body> 后插入
        m = re.search(r"<body[^>]*>\s*\n?", text)
        if m:
            insert_pos = m.end()
            new_text = text[:insert_pos] + INCLUDE_LINE + text[insert_pos:]
        else:
            # 策略 2: 没有 <body>, 在第一个 <header> 前插入
            m = re.search(r"<header[^>]*>", text)
            if m:
                insert_pos = m.start()
                new_text = text[:insert_pos] + INCLUDE_LINE + text[insert_pos:]

        if new_text is None:
            skipped.append(f"{html.name} (no body or header)")
            continue

        html.write_text(new_text, encoding="utf-8")
        changed.append(html.name)

    print(f"✅ 注入 {len(changed)} 个文件:")
    for n in changed:
        print(f"   + {n}")
    print(f"\n⏭️ 跳过 {len(skipped)} 个文件:")
    for n in skipped:
        print(f"   - {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
