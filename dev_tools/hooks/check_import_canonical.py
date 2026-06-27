#!/usr/bin/env python3
"""
check_import_canonical.py — AI 守门 #5: 强制规范导入路径

挡：
  - 绕过统一门面直接 import 内部模块
  - 用 import * 模糊导入
  - import 路径违反分层（高层 import 低层 OK，反之不行）

允许：
  - 显式 named import
  - 同层之间互相 import
  - 守门脚本自身
  - tests/ 内的 import（测试需要深入）
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
SRC_DIR = ROOT / "src"

# 分层定义（数字越大越高层）
LAYER_MAP = {
    "constants": 0,
    "utils": 1,
    "db": 1,
    "indicator": 2,
    "data": 2,
    "event": 2,
    "engine": 2,
    "gateway": 2,
    "scoring": 3,
    "selection": 3,
    "research": 3,
    "optimization": 3,
    "backtest": 4,
    "strategies": 4,
    "business_strategies": 4,
    "strategy": 4,
    "risk": 4,
    "metrics": 4,
    "web": 5,
}

# 禁止 import 模式（绕过统一门面）
FORBIDDEN_IMPORTS = [
    # 绕过 ORM 走 Repository
    (re.compile(r"from\s+src\.models\.database\s+import\s+\*\s*$"),
     "禁止 import * ORM 实体，请显式 named import 或走 DataRepository"),
    (re.compile(r"from\s+src\.db\..*\.orm\s+import\s+\*"),
     "禁止 import * ORM 实体，请走 DataRepository"),
    # 绕过 EventEngine 直接调内部 handler
    (re.compile(r"from\s+src\.event\._handlers\s+import"),
     "禁止 import EventEngine 内部 handler，请用 EventEngine.register()"),
    # 绕过 data manager
    (re.compile(r"from\s+src\.data\.[a-z_]+\s+import\s+pd\.read_csv"),
     "禁止在 src/ 直接读 CSV，请走 DataManager"),
]


def get_staged_python_files() -> List[Path]:
    """获取本次 commit 的 src/ 下 .py 文件。"""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=AM", "--", "src/"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return []
    return [ROOT / line.strip() for line in result.stdout.splitlines()
            if line.strip() and (ROOT / line.strip()).exists()
            and (ROOT / line.strip()).suffix == ".py"]


def get_layer(filepath: Path) -> int | None:
    """获取文件所在的分层。"""
    try:
        rel = filepath.relative_to(SRC_DIR)
        package = rel.parts[0]
        return LAYER_MAP.get(package)
    except (ValueError, IndexError):
        return None


def extract_imports(content: str) -> List[tuple[str, int]]:
    """提取所有 src.* 形式的 import。"""
    imports = []
    for i, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        # from src.X import Y
        m = re.match(r"^from\s+(src\.[\w.]+)\s+import\s+(.+)$", line)
        if m:
            imports.append((m.group(1), i))
            continue
        # import src.X.Y
        m = re.match(r"^import\s+(src\.[\w.]+)", line)
        if m:
            imports.append((m.group(1), i))
    return imports


def check_layer_violation(src_filepath: Path, content: str) -> List[str]:
    """检查分层违规（低层 import 高层 = 错误）。"""
    errors = []
    src_layer = get_layer(src_filepath)
    if src_layer is None:
        return []

    imports = extract_imports(content)
    for import_path, line_no in imports:
        # 解析 import 的包名
        m = re.match(r"src\.(\w+)", import_path)
        if not m:
            continue
        target_package = m.group(1)
        target_layer = LAYER_MAP.get(target_package)
        if target_layer is None:
            continue

        # 低层不能 import 高层
        if target_layer > src_layer:
            errors.append(
                f"  - {src_filepath.relative_to(ROOT)}:{line_no} "
                f"分层违规: 层 {src_layer} ({src_filepath.relative_to(SRC_DIR).parts[0]}) "
                f"不能 import 层 {target_layer} ({target_package})"
            )
    return errors


def check_forbidden_patterns(src_filepath: Path, content: str) -> List[str]:
    """检查禁止的 import 模式。"""
    errors = []
    for pattern, msg in FORBIDDEN_IMPORTS:
        for i, line in enumerate(content.splitlines(), 1):
            if pattern.search(line):
                rel = src_filepath.relative_to(ROOT)
                errors.append(
                    f"  - {rel}:{i} {msg}\n"
                    f"    行: {line.strip()}"
                )
    return errors


def main() -> int:
    """主入口。"""
    files = get_staged_python_files()

    # 排除守门自身
    files = [f for f in files if "dev_tools" not in f.parts]

    all_errors: List[str] = []
    for filepath in files:
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception:
            continue
        all_errors.extend(check_layer_violation(filepath, content))
        all_errors.extend(check_forbidden_patterns(filepath, content))

    if all_errors:
        print(f"❌ check_import_canonical.py 发现 {len(all_errors)} 个 import 问题:\n")
        for err in all_errors:
            print(err)
            print()
        print("=" * 60)
        print("修复: 详见 AGENTS.md §2 分层规则")
        return 1

    print(f"✅ check_import_canonical.py: {len(files)} 个文件 import 合规")
    return 0


if __name__ == "__main__":
    sys.exit(main())
