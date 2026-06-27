#!/usr/bin/env python3
"""
check_directory.py — AI 守门 #2: 目录结构

挡：
  - 在 src/ 下新建顶层目录（如 src/strategies_v2/, src/extra/）
  - 在根目录新建 .py 文件（除已允许的）
  - 在 src/ 之外建立包目录
  - 既有目录被移动/重命名（需要 ADR 记录原因）

不挡：
  - 在已有目录内新建子目录
  - scripts/active/, archive/, _deprecated/ 子目录
  - tests/unit/, integration/, regression/, e2e/ 子目录
  - docs/dev-notes/ 子目录
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
SRC_DIR = ROOT / "src"

# 允许的 src/ 顶层目录（来自 AGENTS.md §2）
ALLOWED_SRC_DIRS = {
    "strategy", "strategies", "business_strategies",  # 兼容过渡
    "scoring", "backtest", "event", "engine", "gateway",
    "data", "db", "research", "optimization", "web",
    "utils", "risk", "metrics", "indicator",
    "selection", "constants", "models",  # models 兼容（v3.0 拆分）
    "__pycache__",
}

# 允许的根目录 .py
ALLOWED_ROOT_PY = {
    "run.py",  # 主入口
    "test_weighting.py",  # 历史遗留（应迁 tests/，v3.0 删除）
}

# 允许的根目录新目录
ALLOWED_ROOT_DIRS = {
    "src", "tests", "scripts", "docs", "config", "data",
    "market_data", "database", "output", "backups", "logs",
    "screenshots", ".venv", ".venv_quick", ".workbuddy",
    ".github", ".claude", ".githooks", "dev_tools",
    ".git", ".pytest_cache", ".pytest_tmp", "__pycache__",
    "backups",  # backup dir
}


def get_staged_new_dirs() -> Set[Path]:
    """获取本次 commit 新增的目录（git status --short 中的 ??）。"""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return set()

    new_dirs = set()
    for line in result.stdout.splitlines():
        status = line[:2]
        path = line[3:].strip()
        # 只看新增（??）和重命名（R 开头）
        if status.startswith("??") or status.startswith("A "):
            full_path = ROOT / path
            if full_path.is_dir():
                new_dirs.add(full_path)
            elif full_path.is_file():
                # 文件新增也可能意味着新目录（如 __init__.py）
                parent = full_path.parent
                if parent != ROOT and parent.is_dir():
                    new_dirs.add(parent)
    return new_dirs


def get_modified_top_level_dirs() -> Set[Path]:
    """获取 src/ 下修改/新增的顶层目录。"""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", "HEAD", "--", "src/"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return set()

    top_dirs = set()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        path = parts[-1]
        full_path = ROOT / path
        # 取 src/ 下的第一级
        try:
            rel = full_path.relative_to(SRC_DIR)
            top_dir = SRC_DIR / rel.parts[0]
            top_dirs.add(top_dir)
        except ValueError:
            continue
    return top_dirs


def check_adr_exists(dir_name: str) -> bool:
    """检查 docs/adr/ 下是否有引用该目录的 ADR。"""
    adr_dir = ROOT / "docs" / "adr"
    if not adr_dir.exists():
        return False
    for adr_file in adr_dir.glob("*.md"):
        try:
            content = adr_file.read_text(encoding="utf-8")
        except Exception:
            continue
        if dir_name in content:
            return True
    return False


def main() -> int:
    """主入口。"""
    errors: List[str] = []

    # 1. 检查 src/ 下新增顶层目录
    new_top_dirs = get_modified_top_level_dirs()
    for d in new_top_dirs:
        dir_name = d.name
        if dir_name in ALLOWED_SRC_DIRS:
            continue
        if not check_adr_exists(dir_name):
            errors.append(
                f"❌ src/{dir_name}/ 是新顶层目录，需要 ADR 引用\n"
                f"   位置: {d.relative_to(ROOT)}\n"
                f"   修复: 在 docs/adr/NNNN-*.md 里说明为什么要新建 {dir_name}/"
            )

    # 2. 检查根目录新增的 .py
    for f in ROOT.glob("*.py"):
        if f.name in ALLOWED_ROOT_PY:
            continue
        # 看是否是 git 新增
        rel = f.relative_to(ROOT)
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", str(rel)],
            cwd=ROOT, capture_output=True, text=True,
        )
        if result.stdout.strip().startswith("??"):
            errors.append(
                f"❌ 根目录新增 .py 文件 {f.name}\n"
                f"   修复: 移入 src/ 或 tests/ 或 scripts/active/ 子目录"
            )

    # 3. 检查根目录新增的目录
    for d in ROOT.iterdir():
        if not d.is_dir():
            continue
        if d.name in ALLOWED_ROOT_DIRS:
            continue
        if d.name.startswith("."):
            continue
        rel = d.relative_to(ROOT)
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", str(rel)],
            cwd=ROOT, capture_output=True, text=True,
        )
        if result.stdout.strip().startswith("??"):
            errors.append(
                f"❌ 根目录新增目录 {d.name}/\n"
                f"   修复: 这是允许的根目录吗？看 AGENTS.md §2；"
                f"不是就合并到现有目录"
            )

    if errors:
        print(f"❌ check_directory.py 发现 {len(errors)} 个目录问题:\n")
        for err in errors:
            print(err)
            print()
        print("=" * 60)
        print("修复方法: 详见 AGENTS.md §2 目录地图")
        return 1

    print("✅ check_directory.py: 目录结构符合规范")
    return 0


if __name__ == "__main__":
    sys.exit(main())
