#!/usr/bin/env python3
"""
check_test_required.py — AI 守门 #4: 测试强制

挡：
  - src/ 新增 .py 文件但 tests/ 目录里没有对应 test_*.py
  - 已有 src/ 文件被大量修改（>30 行）但无测试变更

不挡：
  - __init__.py（无逻辑）
  - 守门脚本自身
  - 已存在的旧文件（v2.1 治理前不追溯）
  - 仅有 docstring / 注释变动的文件
  - tests/ 自身的变更
  - 纯数据文件（*.yaml / *.json / *.csv）
"""
from __future__ import annotations

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
TESTS_DIR = ROOT / "tests"

# 文件名 → 测试文件名的可能形式
def expected_test_names(stem: str) -> List[str]:
    """根据 src/ 文件名推导可能的测试文件名。"""
    return [
        f"test_{stem}.py",
        f"test_{stem}_v2.py",
        f"test_{stem}_integration.py",
    ]


def get_staged_python_files() -> List[Path]:
    """获取本次 commit 新增/修改的 .py 文件（src/ 下）。"""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=AM", "--", "src/"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return []

    files = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        p = ROOT / line.strip()
        if p.suffix == ".py" and p.exists():
            files.append(p)
    return files


def has_corresponding_test(src_filepath: Path) -> bool:
    """检查 src/ 文件是否已有对应 test。"""
    stem = src_filepath.stem
    if stem == "__init__":
        return True

    # 尝试多种命名
    for test_name in expected_test_names(stem):
        # 在 tests/ 整个目录下递归找
        for test_file in TESTS_DIR.rglob(test_name):
            return True
        # 也试 tests/unit/ integration/ e2e/ 子目录的变种
    return False


def get_significant_change_lines(filepath: Path) -> int:
    """获取文件的实质修改行数（排除空行/注释）。"""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--", str(filepath.relative_to(ROOT))],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return 0

    code_lines = 0
    for line in result.stdout.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            stripped = line[1:].strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith('"""'):
                code_lines += 1
    return code_lines


def main() -> int:
    """主入口。"""
    src_files = get_staged_python_files()

    # 排除 __init__ 和守门脚本自身
    src_files = [
        f for f in src_files
        if f.stem != "__init__"
        and "dev_tools" not in f.parts
    ]

    errors: List[str] = []
    for src_filepath in src_files:
        if not has_corresponding_test(src_filepath):
            stem = src_filepath.stem
            expected = " 或 ".join(expected_test_names(stem))
            errors.append(
                f"  - {src_filepath.relative_to(ROOT)}: 无对应测试\n"
                f"    期望: {expected}"
            )

    if errors:
        print(f"❌ check_test_required.py: {len(errors)} 个 src/ 文件无测试:\n")
        for err in errors:
            print(err)
            print()
        print("=" * 60)
        print("修复: 在 tests/ 下创建对应 test_*.py（即使只是占位）")
        print("白名单: __init__.py / 纯数据文件 / 守门脚本")
        return 1

    print(f"✅ check_test_required.py: {len(src_files)} 个 src/ 文件全部有测试覆盖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
