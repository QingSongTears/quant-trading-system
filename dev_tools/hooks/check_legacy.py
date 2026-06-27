#!/usr/bin/env python3
"""
check_legacy.py — AI 守门 #3: 黑名单

挡：
  - import 已废弃模块（来自 AGENTS.md §4 黑名单）
  - 引用已归档策略名（v5_hybrid / v7_bull_wave 等）
  - 从 .bak / .legacy / .deprecated 文件 import
  - 引用 src/strategies/stock_screener/ 死目录
  - ADR-0010 (2026-06-27): 业务模块直接 import DataRepository / sql_utils.read_sql
    (绕过 datafeed 统一入口,业务宽表应走 data_mgr.business 门面)

白名单（这些地方引用是 OK 的）：
  - dev_tools/hooks/ 守门脚本本身
  - docs/adr/ 解释废弃原因
  - docs/CODE_WIKI.md 提到历史
  - CHANGELOG.md 历史记录
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import List, Set

# Force UTF-8 output (Windows GBK compat)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
elif sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]

# ADR-0010 (2026-06-27): 黑名单目录
# 这些目录的 .py 文件禁止直接 import DataRepository / pd.read_sql / sql_utils.read_sql
# 业务宽表应走 data_mgr.business 门面,基础数据应走 data_mgr.datafeed
DATAFEED_BLACKLIST_DIRS = (
    "src/strategies/",
    "src/scoring/",
    "src/selection/",
    "src/web/routes/",
)

# 黑名单（路径片段 / 模块名 / 策略名）
LEGACY_PATTERNS = [
    # 路径（用 . 匹配 Python import 风格；用 / 匹配字符串路径）
    re.compile(r"src\.strategies\.stock_screener"),        # Python import
    re.compile(r"src[/\\]strategies[/\\]stock_screener"),  # 文件路径
    re.compile(r"src\.data\.westock_downloader"),
    re.compile(r"src[/\\]data[/\\]westock_downloader"),
    re.compile(r"from\s+src\.models\.database\s+import\s+\*"),  # 绕过 Repository
    re.compile(r"\.legacy\.bak"),
    re.compile(r"\.deprecated\.py"),
    # 策略名（已归档）
    re.compile(r"\bv5_hybrid\b"),
    re.compile(r"\bv7_bull_wave\b"),
    re.compile(r"\bbull_8d_monthly_fixed\b"),
    re.compile(r"\bv3_reversal\b"),
    re.compile(r"\bv2_trend\b"),
    re.compile(r"\bbull_wave\b(?!_)"),  # bull_wave 但不接 _
    # vnpy 模板（已 deprecated 准备 v3.0 删除）
    re.compile(r"from\s+src\.strategy\.alpha_strategy\s+import"),
    re.compile(r"import\s+src\.strategy\.alpha_strategy"),
]

# ADR-0010 (2026-06-27): 黑名单 import 模式
# 仅在 DATAFEED_BLACKLIST_DIRS 下的 .py 文件中检查
DATAFEED_BLACKLIST_PATTERNS = [
    # 直接 import DataRepository
    (
        re.compile(r"from\s+\S*models\.repository\s+import\s+.*DataRepository"),
        "禁止业务模块直接 import DataRepository,请走 data_mgr.business 门面 (ADR-0010)",
    ),
    # 直接 pd.read_sql 调用
    (
        re.compile(r"pd\.read_sql\s*\("),
        "禁止业务模块 pd.read_sql 直读,请走 datafeed 统一入口 (ADR-0010)",
    ),
    # 直接 import read_sql from sql_utils (兼容 ..db.sql_utils / src.db.sql_utils)
    (
        re.compile(r"from\s+\S*db\.sql_utils\s+import\s+.*read_sql"),
        "禁止业务模块直接 import read_sql,请走 datafeed 统一入口 (ADR-0010)",
    ),
]

# 允许出现黑名单的文件（守门自己 + 文档）
ALLOWED_PATHS = {
    "dev_tools/hooks/",  # 整个 dev_tools 目录都需要豁免（守门自己 + README）
    "AGENTS.md",
    "docs/adr/",
    "docs/CODE_WIKI.md",
    "CHANGELOG.md",
    "docs/dev-notes/",
    "scripts/dev/",
    "LIVE_TRADING_ROADMAP.md",
    "README.md",
    "TODO.md",
    "AI_TASK_BOARD.md",
}

# 不需要检查的文件类型
SKIP_EXTENSIONS = {".md", ".txt", ".json", ".yml", ".yaml", ".csv", ".html", ".css", ".js"}


def get_staged_files() -> List[Path]:
    """Get all files that are STAGED, INTENT-TO-ADD, or UNTRACKED (about to be committed)."""
    files = set()

    # Use git status --porcelain to get everything: staged, intent-to-add, untracked, modified
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            # Format: XY filename (or XY old -> new for renames)
            # We want anything that ends in 'M' or 'A' or '?' (i.e., to be committed)
            status = line[:2]
            path = line[3:].strip()
            # Handle rename: "old -> new"
            if " -> " in path:
                path = path.split(" -> ")[-1].strip()
            if status.strip() in ("A", "AM", "M", "?", "??", "MM", "AD", "UA"):
                # Only add if it's a working-tree change (not stashed etc.)
                if status[0] in (" ", "A", "M", "?", "U") or status[1] in ("A", "M", "D"):
                    full_path = ROOT / path
                    if full_path.exists():
                        files.add(full_path)
    except subprocess.CalledProcessError:
        pass

    return [f for f in files if f.exists()]


def is_allowed_path(filepath: Path) -> bool:
    """检查路径是否在白名单。"""
    try:
        rel = str(filepath.relative_to(ROOT))
    except ValueError:
        return False
    # Normalize path separators (Windows backslash)
    rel = rel.replace("\\", "/")
    for p in ALLOWED_PATHS:
        p_norm = p.replace("\\", "/")
        if rel == p_norm or rel.startswith(p_norm):
            return True
    return False


def check_file(filepath: Path) -> List[str]:
    """检查单个文件是否引用黑名单。"""
    errors = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    rel = str(filepath.relative_to(ROOT))

    for pattern in LEGACY_PATTERNS:
        for match in pattern.finditer(content):
            # 取上下文行
            line_no = content[: match.start()].count("\n") + 1
            line_start = content.rfind("\n", 0, match.start()) + 1
            line_end = content.find("\n", match.end())
            if line_end == -1:
                line_end = len(content)
            line_content = content[line_start:line_end].strip()
            errors.append(
                f"  - {rel}:{line_no} 引用已废弃: {line_content[:100]}"
            )

    # ADR-0010: 检查黑名单目录的 datafeed 黑名单 import 模式
    rel_normalized = rel.replace("\\", "/")
    in_blacklist_dir = any(
        rel_normalized.startswith(d) for d in DATAFEED_BLACKLIST_DIRS
    )
    if in_blacklist_dir and filepath.suffix == ".py":
        for pattern, msg in DATAFEED_BLACKLIST_PATTERNS:
            for match in pattern.finditer(content):
                line_no = content[: match.start()].count("\n") + 1
                line_start = content.rfind("\n", 0, match.start()) + 1
                line_end = content.find("\n", match.end())
                if line_end == -1:
                    line_end = len(content)
                line_content = content[line_start:line_end].strip()
                errors.append(
                    f"  - {rel}:{line_no} {msg}\n    行: {line_content[:100]}"
                )

    return errors


def main() -> int:
    """主入口。"""
    files = get_staged_files()

    # 过滤：白名单 / 非代码文件
    files = [
        f for f in files
        if not is_allowed_path(f)
        and f.suffix.lower() not in SKIP_EXTENSIONS
    ]

    all_errors: List[tuple[Path, List[str]]] = []
    for filepath in files:
        errs = check_file(filepath)
        if errs:
            all_errors.append((filepath, errs))

    if all_errors:
        print(f"❌ check_legacy.py 发现 {sum(len(e) for _, e in all_errors)} 处黑名单引用:\n")
        for filepath, errs in all_errors:
            print(f"📄 {filepath.relative_to(ROOT)}")
            for err in errs:
                print(err)
            print()
        print("=" * 60)
        print("黑名单来源: AGENTS.md §4")
        print("如需使用: 必须先在 docs/adr/ 写一条 'undeprecate-XXX.md' 推翻")
        return 1

    print(f"✅ check_legacy.py: {len(files)} 个文件无黑名单引用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
