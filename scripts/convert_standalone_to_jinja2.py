#!/usr/bin/env python3
"""
convert_standalone_to_jinja2.py
将 output/*.html (Ardot 设计的 dark theme standalone) 批量改造成 Jinja2 模板，
挂载到现有 FastAPI 系统的 src/web/templates/ 下。

策略:
1. 提取 <title>
2. 检测 chart.js / echarts 用量 → 设 {% set need_chartjs = true %}
3. 替换 <head> 之前所有内容 → 替换为 {% include "partials/_standalone_head.html" %}
4. 保留 <body>...</body> 全部内容（含 <style>、<script>）
5. 删除 </body></html> 收尾

输出: src/web/templates/<name>.html
"""
import re
from pathlib import Path

OUTPUT_DIR = Path("E:/work/work/quant-trading-system/output")
TEMPLATES_DIR = Path("E:/work/work/quant-trading-system/src/web/templates")

# 15 个新页面，按 src/web/templates/ 已有文件名（避免和模板系统冲突）
# 1=1 覆盖替换
PAGES = [
    "dashboard",
    "backtest_lab",
    "signal_dashboard",
    "portfolio",
    "screener",
    "sector",
    "strategy_compare",
    "data_monitor",
    "diagnose",
    "tuning_panel",
    "v5_tuning",
    "v6_compare",
    "ic_analysis",
    "dim_compare",
    "fund_flow_report",
    "index",
]

JINJA_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
{% set page_title = "__TITLE__" %}
{% set need_chartjs = __NEED_CHARTJS__ %}
{% set need_echarts = __NEED_ECHARTS__ %}
{% include "partials/_standalone_head.html" %}
{# 页面特定 style (从 output/ 原样保留) #}
<style>
__STYLE__
</style>
</head>
"""


def convert(html_text: str) -> str:
    """核心转换逻辑"""
    # 1. 提取 <title>
    title_match = re.search(r"<title[^>]*>([^<]+)</title>", html_text)
    title = title_match.group(1).strip() if title_match else "Quant Trading"

    # 2. 检测 chart.js / echarts
    #    输出 HTML 来自 Ardot 设计，inline CDN 已带（chart.js 4.4.1）
    has_chartjs = bool(re.search(r"new Chart\(|chart\.js", html_text, re.I))
    has_echarts = bool(re.search(r"echarts\.", html_text, re.I))

    # 3. 提取 <style> 块内容
    style_match = re.search(r"<style[^>]*>(.*?)</style>", html_text, re.S)
    style_content = style_match.group(1).strip() if style_match else ""

    # 4. 提取 <body>...</body> 内容
    body_match = re.search(r"<body[^>]*>(.*?)</body>\s*</html>\s*$", html_text, re.S)
    if not body_match:
        # 部分文件可能没有 <body> 包裹
        body_match = re.search(r"</head>\s*(.*?)\s*</html>\s*$", html_text, re.S)
    body_content = body_match.group(1).strip() if body_match else ""

    # 5. 组装新 head
    new_head = (
        JINJA_HEAD
        .replace("__TITLE__", title)
        .replace("__NEED_CHARTJS__", str(has_chartjs).lower())
        .replace("__NEED_ECHARTS__", str(has_echarts).lower())
        .replace("__STYLE__", style_content)
    )

    # 6. 组装最终 Jinja2 模板
    return new_head + body_content + "\n"


def main():
    converted = []
    for name in PAGES:
        src = OUTPUT_DIR / f"{name}.html"
        dst = TEMPLATES_DIR / f"{name}.html"
        if not src.exists():
            print(f"  SKIP: {src.name} 不存在")
            continue
        text = src.read_text(encoding="utf-8")
        new_text = convert(text)
        dst.write_text(new_text, encoding="utf-8")
        converted.append((name, src.stat().st_size, dst.stat().st_size, len(new_text)))
        print(f"  OK:   {name}.html  ({src.stat().st_size} → {len(new_text)} bytes)")

    print(f"\n转换完成: {len(converted)} 个文件")
    print("=" * 60)


if __name__ == "__main__":
    main()
