#!/usr/bin/env python3
"""
check_file_size.py — AI 守门 #6: 文件行数

挡：
  - 单文件 > 500 行（必须拆分）
  - 文档 > 1000 行（应拆章节）

不挡：
  - requirements.txt / 锁定文件
  - 自动生成文件（带 # AUTO-GENERATED 头）
  - 已存在的大文件（v2.1 治理前不追溯）
  - 守门脚本自身
  - 纯数据文件

白名单：历史已知大文件（应在 v2.2 拆）
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import List

# Force UTF-8 output (Windows GBK compat)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
elif sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]

# 代码文件最大行数
MAX_CODE_LINES = 500
# 文档最大行数
MAX_DOC_LINES = 1000

# 允许的大文件（白名单 — v2.1.2 拆分）
WHITELIST_LARGE_FILES = {
    "scripts/active/param_server.py",    # 2846 行 — 已知待拆
    "src/web/routes/api.py",             # 2227 行 — v2.1.2 拆 backtest/data/strategies 子路由
    "src/scoring/technical_scorer.py",   # 694 行 — v2.1.2 拆 indicators / scoring
    "requirements.txt",                  # 锁定文件
    "docs/CODE_WIKI.md",                 # 事实单源
    "LIVE_TRADING_ROADMAP.md",           # 主路线图
}

# 守门自身 + 生成器
HOOKS_SELF = "dev_tools/hooks/"


def get_staged_files() -> List[Path]:
    """Get all files that are STAGED, INTENT-TO-ADD, or UNTRACKED."""
    files = set()
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            status = line[:2]
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ")[-1].strip()
            if status.strip() in ("A", "AM", "M", "?", "??", "MM", "AD"):
                full_path = ROOT / path
                if full_path.exists():
                    files.add(full_path)
    except subprocess.CalledProcessError:
        pass
    return [f for f in files if f.exists()]


def is_whitelisted(filepath: Path) -> bool:
    """检查是否在白名单。"""
    rel = str(filepath.relative_to(ROOT)).replace("\\", "/")
    if rel in WHITELIST_LARGE_FILES:
        return True
    if rel.startswith(HOOKS_SELF):
        return True
    return False


def is_auto_generated(content: str) -> bool:
    """检查文件头是否标记自动生成。"""
    head = content[:500]
    return bool(re.search(r"#\s*AUTO-GENERATED", head) or
                re.search(r"#\s*DO NOT EDIT", head) or
                re.search(r"<!--\s*AUTO-GENERATED", head))


def count_lines(filepath: Path) -> int:
    """计算文件行数。"""
    try:
        return sum(1 for _ in filepath.open("r", encoding="utf-8", errors="ignore"))
    except Exception:
        return 0


def main() -> int:
    """主入口。"""
    files = get_staged_files()

    code_files = [f for f in files if f.suffix == ".py" and not is_whitelisted(f)]
    doc_files = [f for f in files if f.suffix == ".md" and not is_whitelisted(f)]

    errors: List[str] = []

    # 代码文件
    for f in code_files:
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            continue
        if is_auto_generated(content):
            continue
        lines = count_lines(f)
        if lines > MAX_CODE_LINES:
            rel = f.relative_to(ROOT)
            errors.append(
                f"  - {rel}: {lines} 行 > {MAX_CODE_LINES}（强制拆分）\n"
                f"    修复: 按职责拆成多个 .py 文件"
            )

    # 文档文件
    for f in doc_files:
        lines = count_lines(f)
        if lines > MAX_DOC_LINES:
            rel = f.relative_to(ROOT)
            errors.append(
                f"  - {rel}: {lines} 行 > {MAX_DOC_LINES}（建议拆章节）"
            )

    if errors:
        print(f"❌ check_file_size.py 发现 {len(errors)} 个文件过大:\n")
        for err in errors:
            print(err)
            print()
        print("=" * 60)
        print(f"代码文件上限: {MAX_CODE_LINES} 行（强制）")
        print(f"文档文件上限: {MAX_DOC_LINES} 行（建议）")
        print("白名单: dev_tools/hooks/README.md, requirements.txt, "
              "scripts/param_server.py（已知待拆）")
        return 1

    print(f"✅ check_file_size.py: {len(code_files) + len(doc_files)} 个文件行数合规")
    return 0


if __name__ == "__main__":
    sys.exit(main())
