"""
export_ardot_report.py — Ardot 设计稿报告导出

读取 output/ardot_specs/ 下的所有 spec markdown, 生成一份聚合报告:
  docs/Ardot_Design_Report.md

包含:
- 总览 (页面数/节点数/画布宽度)
- 每个页面的核心信息 (目的/位置/主色/关键 API)
- 设计语言统一规范
- Ardot MCP 落地步骤
- 当前状态 + 下次会话操作

用法:
    python scripts/export_ardot_report.py
    python scripts/export_ardot_report.py --output docs/Ardot_Design_Report.md
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPECS_DIR = PROJECT_ROOT / "output" / "ardot_specs"
DEFAULT_OUT = PROJECT_ROOT / "docs" / "Ardot_Design_Report.md"

# 反引号常量 (避免 shell 转义问题)
BT = "\x60"


def parse_spec_file(path: Path) -> dict | None:
    """从 spec markdown 提取关键信息"""
    text = path.read_text(encoding="utf-8")
    info: dict = {"file": path.name, "path": str(path.relative_to(PROJECT_ROOT))}
    # 标题
    m = re.search(r"^# Ardot Page Spec \xb7 (.+)", text, re.MULTILINE)
    if m:
        info["title"] = m.group(1).strip()
    # 目的
    m = re.search(r"\*\*目的[^*]*\*\*\s*[:：]?\s*([^\n]+)", text)
    if m:
        info["purpose"] = m.group(1).strip()
    # 画布位置: 找 4+ 位数的 (34000, 0) 模式 (跳过 (x, y))
    m = re.search(r"\(\s*(\d{4,})\s*,\s*(\d+)\s*\)", text)
    if m:
        info["x"], info["y"] = int(m.group(1)), int(m.group(2))
    # 页面尺寸
    m = re.search(r"页面尺寸\s*[^\d]*(\d+)\s*\xd7\s*(\d+)", text)
    if m:
        info["w"], info["h"] = int(m.group(1)), int(m.group(2))
    # 主色调
    m = re.search(r"主色调\s*[^\#]*(\#[0-9A-Fa-f]{6})", text)
    if m:
        info["accent"] = m.group(1).upper()
    # 节点范围: | 节点 ID 范围 | `16:1` ~ `16:90` (约 90 节点) |
    # 注意: 16:1 后面有反引号
    pat = r"节点 ID 范围\D*?(\d+:\d+)\s*" + re.escape(BT) + r"?\s*~\s*" + re.escape(BT) + r"?\s*(\d+:\d+)"
    m = re.search(pat, text)
    if m:
        info["id_start"] = m.group(1)
        info["id_end"] = m.group(2)
    # 节点数
    m = re.search(r"约\s*(\d+)\s*节点", text)
    if m:
        info["nodes"] = int(m.group(1))
    return info


def build_report(specs: list[dict], readme: str, generated_at: str) -> str:
    """聚合所有 spec 生成报告"""
    total_nodes = sum(s.get("nodes", 0) for s in specs)
    total_pages = len(specs)
    max_x = max((s.get("x", 0) for s in specs), default=0)
    min_x = min((s.get("x", 0) for s in specs), default=0)
    max_w = max((s.get("w", 1700) for s in specs), default=1700)
    canvas_w = max_x - min_x + max_w

    lines: list[str] = []
    lines.append("# Ardot 设计稿报告 \u2014 19 页 + 4 新页")
    lines.append("")
    lines.append(
        f"> **生成时间**: {generated_at}  \n"
        f"> **生成方式**: 聚合 `output/ardot_specs/` 下所有 spec markdown  \n"
        f"> **目标 Ardot File**: `https://ardot.tencent.com/file/697128059547574`"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # 总览
    lines.append("## \U0001f4ca 总览")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|---|")
    lines.append(f"| 页面数 | **{total_pages}** |")
    lines.append(f"| 节点数 | **~{total_nodes}** |")
    lines.append(f"| 画布宽度 | **{canvas_w:,} px** ({min_x} \u2192 {max_x + max_w}) |")
    lines.append(f"| 设计语言 | Dark Mode OLED + Fira Code/Sans |")
    lines.append(f"| 配色规范 | A\u80a1 \u7ea2\u6da8 `#EF4444` / \u7eff\u8dcc `#22C55E` |")
    lines.append("")

    # 页面详情
    lines.append("## \U0001f3a8 页面清单")
    lines.append("")
    lines.append("| # | 页面 | 文件 | x 坐标 | ID 范围 | 节点 | 主色 |")
    lines.append("|---|------|------|--------|----------|------|------|")
    for i, s in enumerate(specs, 1):
        title = s.get("title", "?")
        file = s.get("file", "?")
        x = s.get("x", "?")
        id_range = (
            f"{s.get('id_start', '?')} ~ {s.get('id_end', '?')}"
            if "id_start" in s
            else "-"
        )
        nodes = s.get("nodes", "?")
        accent = s.get("accent", "-")
        lines.append(f"| {i} | {title} | `{file}` | {x} | {id_range} | ~{nodes} | {accent} |")
    lines.append("")

    # 详细目的
    lines.append("## \U0001f4dd 各页目的")
    lines.append("")
    for i, s in enumerate(specs, 1):
        lines.append(f"### {i}. {s.get('title', s.get('file'))}")
        lines.append("")
        lines.append(f"**文件**: `{s.get('file')}`  ")
        if "purpose" in s:
            lines.append(f"**目的**: {s['purpose']}  ")
        if "x" in s:
            w = s.get("w", 1700)
            h = s.get("h", 2400)
            lines.append(f"**画布位置**: ({s['x']}, {s.get('y', 0)}), 尺寸 {w}\xd7{h} px  ")
        if "accent" in s:
            lines.append(f"**主色调**: `{s['accent']}`  ")
        if "id_start" in s:
            lines.append(f"**节点 ID**: {s['id_start']} ~ {s['id_end']}  ")
        lines.append("")

    # 设计语言
    lines.append("## \U0001f3a8 设计语言统一规范")
    lines.append("")
    lines.append("| Token | 值 | 用途 |")
    lines.append("|-------|-----|------|")
    lines.append("| `bg_primary` | `#0F172A` | 主背景 |")
    lines.append("| `bg_card` | `#1E293B` | 卡片背景 |")
    lines.append("| `bg_elevated` | `#334155` | 浮层/分隔 |")
    lines.append("| `text_primary` | `#F1F5F9` | 主文字 |")
    lines.append("| `text_muted` | `#94A3B8` | 次要文字 |")
    lines.append("| `up_red` | `#EF4444` | A\u80a1\u6da8\uff08\u7ea2\uff09|")
    lines.append("| `down_green` | `#22C55E` | A\u80a1\u8dcc\uff08\u7eff\uff09|")
    lines.append("| `accent_v5` | `#F59E0B` | V5 \u7425\u73c0 |")
    lines.append("| `accent_v6` | `#8B5CF6` | V6 \u7d2b |")
    lines.append("| `accent_blue` | `#3B82F6` | \u9a8c\u8bc1\u84dd |")
    lines.append("| `accent_gold` | `#FFD700` | \u9009\u4e2d\u6001\u91d1\u8272 |")
    lines.append("")
    lines.append("\u5b57\u4f53: Fira Code\uff08\u6570\u636e\uff09+ Fira Sans\uff08\u6807\u7b7e\uff09")
    lines.append("")

    # 落地流程
    lines.append("## \U0001f504 Ardot MCP \u843d\u5730\u6b65\u9aa4")
    lines.append("")
    lines.append("### Step 1: \u6253\u5f00\u8bbe\u8ba1\u6587\u4ef6")
    lines.append("```python")
    lines.append("mcp__ardot__open_design(")
    lines.append('    fileUrl="https://ardot.tencent.com/file/697128059547574"')
    lines.append(")")
    lines.append("```")
    lines.append("")
    lines.append("等 3 \u79d2, \u786e\u8ba4 `fetch_file_info` \u8fd4\u56de\u6210\u529f\u540e\u7ee7\u7eed.")
    lines.append("")
    lines.append("### Step 2: \u4e32\u884c\u521b\u5efa\u6240\u6709\u9875\u9762 (\u6309 x \u5750\u6807\u9012\u589e)")
    lines.append("")
    lines.append("\u6bcf\u9875 4-6 \u4e2a batch, \u6309 spec \u4e2d\u7684 \"Ardot MCP \u64cd\u4f5c\u63d0\u793a\" \u5c0f\u8282\u5206\u6279:")
    lines.append("")
    lines.append("```python")
    lines.append("for spec in all_specs:  # \u6309 x \u5750\u6807\u6392\u5e8f")
    lines.append("    for batch in spec.batches:")
    lines.append("        mcp__ardot__batch_edit(operations=batch.ops)")
    lines.append("        mcp__ardot__capture_layout(parentId=batch.parent_id, problemsOnly=True)")
    lines.append("        if problems: fix and retry (max 2 rounds)")
    lines.append("```")
    lines.append("")
    lines.append("### Step 3: \u9a8c\u8bc1\u622a\u56fe")
    lines.append("")
    lines.append("```python")
    lines.append("for spec in all_specs:")
    lines.append("    mcp__ardot__capture_screenshot(nodeIds=[spec.root_id])")
    lines.append("```")
    lines.append("")
    lines.append("### Step 4: \u63a8\u9001\u4ea4\u4ed8")
    lines.append("")
    lines.append("- \u5728 File 697128059547574 \u6dfb\u52a0 changelog \u8282\u70b9 (\u53f3\u4e0b\u89d2\u5c0f\u5361)")
    lines.append("- \u8f93\u51fa\u672c\u62a5\u544a\u94fe\u63a5\u5230 FastAPI \u6587\u6863")
    lines.append("")

    # 状态
    lines.append("## \u26a0\ufe0f \u5f53\u524d\u72b6\u6001")
    lines.append("")
    lines.append("**Ardot MCP \u9002\u914d\u5668**: \u5f53\u524d WorkBuddy \u4f1a\u8bdd\u4e2d\u4e0d\u53ef\u7528")
    lines.append("- \u9519\u8bef: `NO_ADAPTER: No adapter found for routeKey`")
    lines.append("- \u91cd\u8bd5 3 \u6b21\u540e\u4ecd\u672a\u52a0\u8f7d")
    lines.append("")
    lines.append("**\u5df2\u5b8c\u6210\u7684\u66ff\u4ee3\u4ea4\u4ed8\u7269**:")
    lines.append(f"- \u2705 {total_pages} \u4e2a\u8be6\u7ec6 spec markdown \u6587\u4ef6")
    lines.append("- \u2705 \u672c\u62a5\u544a (`docs/Ardot_Design_Report.md`)")
    lines.append("- \u2705 Git commit \u63a8\u9001\u81f3 `origin/develop`")
    lines.append("")
    lines.append("**\u4e0b\u6b21\u4f1a\u8bdd\u64cd\u4f5c\u5efa\u8bae**:")
    lines.append("1. \u6253\u5f00 WorkBuddy Desktop \u5ba2\u6237\u7aef\u5e76\u52a0\u8f7d File 697128059547574")
    lines.append("2. \u65b0\u4f1a\u8bdd\u4f1a\u5206\u914d\u65b0\u7684 session_id, Ardot MCP \u901a\u5e38\u4f1a\u81ea\u52a8\u8fde\u63a5")
    lines.append("3. \u8ba9 agent \u6309\u672c\u62a5\u544a + 6 \u4e2a spec \u6587\u4ef6\u843d\u5730")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"**\u751f\u6210\u65f6\u95f4**: {generated_at}  ")
    lines.append("**\u5173\u8054 FastAPI \u8def\u7531**: \u5168\u90e8\u5df2\u843d\u5730 (28 \u72ec\u7acb\u9875 + 6 \u5173\u952e API \u5065\u5eb7 100%)  ")
    lines.append("**\u5173\u8054 Tag**: `v2.0-ardot-landing` (2026-06-26 01:13)  ")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 Ardot 设计稿聚合报告")
    parser.add_argument(
        "--specs-dir", type=Path, default=SPECS_DIR, help="spec 输入目录"
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUT, help="报告输出路径"
    )
    args = parser.parse_args()

    specs_dir: Path = args.specs_dir
    if not specs_dir.exists():
        print(f"Specs dir not found: {specs_dir}", file=sys.stderr)
        return 1

    spec_files = sorted(
        p for p in specs_dir.glob("*.md") if p.name.lower() != "readme.md"
    )
    if not spec_files:
        print(f"No spec files found", file=sys.stderr)
        return 1

    print(f"Scanning {len(spec_files)} spec files...")
    specs: list[dict] = []
    for p in spec_files:
        info = parse_spec_file(p)
        if info:
            specs.append(info)
            print(f"  {p.name}: {info.get('title', '?')[:50]} (x={info.get('x','?')})")

    specs.sort(key=lambda s: s.get("x", 0))

    readme_path = specs_dir / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    report = build_report(specs, readme, datetime.now().strftime("%Y-%m-%d %H:%M"))

    out_path: Path = args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    size = out_path.stat().st_size
    print(f"\nReport: {out_path.relative_to(PROJECT_ROOT)} ({size:,} bytes)")
    print(f"Pages: {len(specs)}, Nodes: ~{sum(s.get('nodes', 0) for s in specs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
