#!/usr/bin/env python3
"""
check_naming.py - AI Guard #1: Naming conventions

Guards (only for NEW / STAGED files - does not check existing committed code):
  - New business classes must declare zh_name / en_name
  - Class suffix must be one of allowed (Strategy/Scorer/Engine/...)
  - Filename must match snake_case
  - Filename must NOT have date-stamp suffix (_0625, _v2, _v3)
  - Filename stem must match main class name (e.g. v6_reversal.py for V6ReversalStrategy)

Skips:
  - __init__.py
  - Private classes (starting with _)
  - Utility/data classes (Report, Bridge, Adapter, ...) - these have no business meaning
  - Existing committed files (v2.1 cleanup work does not fail this check)
  - dev_tools/hooks/ itself
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Set, Tuple

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
HOOKS_SELF = ROOT / "dev_tools" / "hooks"

# Business base classes (must declare zh_name / en_name)
BUSINESS_BASES = {
    "BaseStrategy", "BaseSelectionStrategy", "EquityStrategy",
    "BaseScorer", "Scorer",
    "BaseEngine", "BacktestEngine", "PortfolioEngine",
    "BaseProvider", "Provider",
    "BaseModel", "AlphaModel",
    "BaseRepository", "Repository",
}

# Allowed class suffixes
ALLOWED_SUFFIXES = (
    "Strategy", "Scorer", "Engine", "Provider", "Model",
    "Repository", "Router", "Manager", "Handler", "Builder",
    "Factory", "Adapter", "Wrapper", "Registry", "Service",
    "Config", "Context",
)

# Allowed class names (no suffix required - utility/data classes)
ALLOWED_CLASS_NAMES = {
    # Stdlib
    "Protocol", "ABC", "BaseModel", "Enum", "IntEnum", "TestCase",
    "Exception", "Error",
    # Common dataclass / DTO
    "Data", "Record", "Result", "Response", "Request", "Event",
    # Common utility
    "Logger", "Metrics", "Client",
    # Test fixtures
    "Mock", "Fake", "Stub",
    # Project-specific singletons
    "Main", "App", "Simulator",
}

# VNPY 借鉴类名豁免 (跳过类后缀检查 + 文件名 stem 匹配)
# 理由: 借鉴 vnpy 原类名时, 去掉 Manager/App/Engine 后缀是本项目简写惯例
#       (见 ADR-0007 修复 1); 事件载荷 dataclass 借鉴 vnpy.Event 风格
# 新增条目时: 必须是真"vnpy 借鉴", 不是绕开规范
VNPY_SHORTHAND_EXEMPTIONS = {
    "RiskEngine": "vnpy-shorthand",      # vnpy RiskManager → RiskEngine (ADR-0007)
    "RiskAlert": "vnpy-event-data",      # 事件载荷 dataclass (借鉴 vnpy.Event 风格)
    "PnlSnapshot": "vnpy-event-data",      # ADR-0012 #83 — PnL 事件载荷
    "PositionSnapshot": "vnpy-event-data", # ADR-0012 #83 — 持仓事件载荷
    "AnomalyEvent": "vnpy-event-data",     # ADR-0012 #83 — 异常事件载荷
    "MetricStore": "vnpy-store-base",      # ADR-0012 #83 — 监控指标抽象 store
    "InMemoryBuffer": "vnpy-store-impl",   # ADR-0012 #83 — 内存 ring buffer 实现
    "SqliteStore": "vnpy-store-impl",      # ADR-0012 #83 — SQLite store 实现
    "AnomalyDetector": "vnpy-detector",    # ADR-0012 #83 — 异常检测器
    "PnlCollector": "vnpy-collector",       # ADR-0012 #83 — PnL 事件订阅器
    "PositionCollector": "vnpy-collector", # ADR-0012 #83 — 持仓事件订阅器
    "RiskAlertCollector": "vnpy-collector", # ADR-0012 #83 — 风控告警事件订阅器
    "AlertRule": "vnpy-alert-rule",        # ADR-0012 #83 — 报警规则 dataclass
    "AlertDispatcher": "vnpy-dispatcher",  # ADR-0012 #83 — 报警分发器
    "MonitoringHub": "vnpy-hub",            # ADR-0012 #83 — 监控统一 facade
}

# Forbidden filename patterns
FORBIDDEN_FILENAME_PATTERNS = [
    re.compile(r"_\d{4}$"),          # _0625, _2025
    re.compile(r"_v\d+$"),           # _v2, _v3
    re.compile(r"_\d{6}$"),          # _250626
    re.compile(r"_old$|_new$|_backup$|_bak$"),
]

# snake_case pattern
SNAKE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.py$")


def get_staged_new_files() -> List[Path]:
    """Get files that are NEWLY ADDED or UNTRACKED (not just modified).

    Naming/test checks only apply to NEW files. Modified files inherit
    their original compliance status. file_size / import_canonical apply
    to all changes.
    """
    files = set()
    # Staged - ONLY added (A), not modified (M)
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=A"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        for line in result.stdout.splitlines():
            if line.strip():
                files.add(ROOT / line.strip())
    except subprocess.CalledProcessError:
        pass
    # Untracked
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        for line in result.stdout.splitlines():
            if line.strip():
                files.add(ROOT / line.strip())
    except subprocess.CalledProcessError:
        pass
    return [f for f in files if f.exists()]


def find_classes(tree: ast.Module) -> List[Tuple[str, ast.ClassDef]]:
    """Find all top-level class definitions."""
    return [(node.name, node) for node in tree.body
            if isinstance(node, ast.ClassDef)]


def inherits_from_business_base(cls_node: ast.ClassDef) -> bool:
    """Check if class inherits from a business base."""
    for base in cls_node.bases:
        if isinstance(base, ast.Name) and base.id in BUSINESS_BASES:
            return True
        if isinstance(base, ast.Attribute) and base.attr in BUSINESS_BASES:
            return True
    return False


def has_zh_en_alias(cls_node: ast.ClassDef) -> Tuple[bool, bool]:
    """Check if class declares zh_name and en_name as class attributes."""
    has_zh = has_en = False
    for stmt in cls_node.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    if target.id == "zh_name" and isinstance(stmt.value, ast.Constant):
                        if isinstance(stmt.value.value, str) and stmt.value.value.strip():
                            has_zh = True
                    if target.id == "en_name" and isinstance(stmt.value, ast.Constant):
                        if isinstance(stmt.value.value, str) and stmt.value.value.strip():
                            has_en = True
    return has_zh, has_en


def is_skippable_class(cls_name: str) -> bool:
    """Skip private classes, allowed utility classes, and very short names."""
    if cls_name.startswith("_"):  # _InternalOrder, _Foo
        return True
    if cls_name in ALLOWED_CLASS_NAMES:
        return True
    if cls_name in VNPY_SHORTHAND_EXEMPTIONS:
        return True
    return False


def has_valid_suffix(cls_name: str) -> bool:
    """Check if class name has a valid suffix or is in allowed list."""
    if cls_name in ALLOWED_CLASS_NAMES:
        return True
    return any(cls_name.endswith(suffix) for suffix in ALLOWED_SUFFIXES)


def compute_expected_stem(cls_name: str) -> str:
    """Compute expected snake_case filename stem from class name."""
    # CamelCase to snake_case: insert _ before uppercase, lowercase all
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", cls_name).lower()
    return s


def check_filename(filepath: Path, main_class_name: str | None) -> List[str]:
    """Check filename conventions."""
    errors = []
    fname = filepath.name
    stem = filepath.stem

    # __init__.py: skip
    if stem == "__init__":
        return []

    # snake_case
    if not SNAKE_CASE_PATTERN.match(fname):
        errors.append(
            f"  - {fname}: filename not snake_case (should be lowercase_underscore)"
        )

    # forbidden patterns
    for pattern in FORBIDDEN_FILENAME_PATTERNS:
        if pattern.search(stem):
            errors.append(
                f"  - {fname}: forbidden suffix (date-stamp/version/backup)"
            )
            break

    # filename <-> main class match
    if main_class_name and main_class_name not in VNPY_SHORTHAND_EXEMPTIONS:
        expected = compute_expected_stem(main_class_name)
        if stem != expected:
            errors.append(
                f"  - {fname}: filename stem '{stem}' does not match "
                f"main class '{main_class_name}' (expected '{expected}.py')"
            )

    return errors


def check_file(filepath: Path) -> List[str]:
    """Check a single Python file."""
    errors = []
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        return [f"  - {filepath.name}: syntax error: {e}"]

    classes = find_classes(tree)

    # Find first business-base subclass, or first class
    main_class = None
    for name, node in classes:
        if not is_skippable_class(name) and inherits_from_business_base(node):
            main_class = (name, node)
            break
    if not main_class and classes:
        # First non-skippable class
        for name, node in classes:
            if not is_skippable_class(name):
                main_class = (name, node)
                break

    # Class suffix check (only for the main class)
    if main_class:
        cls_name, cls_node = main_class
        if not has_valid_suffix(cls_name):
            sample = "/".join(ALLOWED_SUFFIXES[:5])
            errors.append(
                f"  - {filepath.name}:{cls_node.lineno} "
                f"class {cls_name} missing valid suffix "
                f"(should end in {sample}/...)"
            )

        # zh_name / en_name check (only for business base subclasses)
        if inherits_from_business_base(cls_node):
            has_zh, has_en = has_zh_en_alias(cls_node)
            if not has_zh:
                errors.append(
                    f"  - {filepath.name}:{cls_node.lineno} "
                    f"class {cls_name} missing zh_name (see AGENTS.md §3.2)"
                )
            if not has_en:
                errors.append(
                    f"  - {filepath.name}:{cls_node.lineno} "
                    f"class {cls_name} missing en_name (see AGENTS.md §3.2)"
                )

    # filename check
    if main_class:
        errors.extend(check_filename(filepath, main_class[0]))
    else:
        # No class at all - just filename check
        if filepath.stem != "__init__":
            errors.extend(check_filename(filepath, None))

    return errors


def main() -> int:
    """Main entry."""
    if len(sys.argv) > 1 and sys.argv[1] != "--all":
        # Manual override: check specific files
        targets = [Path(p) for p in sys.argv[1:] if Path(p).exists()]
    else:
        # Default: only NEW / STAGED files
        targets = get_staged_new_files()

    # Filter: src/ python files, skip dev_tools/
    targets = [
        t for t in targets
        if t.suffix == ".py"
        and SRC_DIR in t.parents
        and t not in {HOOKS_SELF / f for f in [
            "check_naming.py", "check_directory.py", "check_legacy.py",
            "check_test_required.py", "check_import_canonical.py",
            "check_file_size.py", "check_commit_msg.py", "run_all.py"
        ]}
    ]

    if not targets:
        print("[OK] check_naming.py: no new/staged files to check")
        return 0

    all_errors: List[Tuple[Path, List[str]]] = []
    for filepath in targets:
        errs = check_file(filepath)
        if errs:
            all_errors.append((filepath, errs))

    if all_errors:
        print(f"[FAIL] check_naming.py: {len(all_errors)} files have naming issues:\n")
        for filepath, errs in all_errors:
            print(f"File: {filepath.relative_to(ROOT)}")
            for err in errs:
                print(err)
            print()
        print("=" * 60)
        print("Fix: see AGENTS.md §3 Naming conventions")
        return 1

    print(f"[OK] check_naming.py: {len(targets)} new files all pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
