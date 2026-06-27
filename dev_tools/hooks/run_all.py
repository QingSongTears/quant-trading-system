#!/usr/bin/env python3
"""
run_all.py - 一键跑全部 7 个守门脚本

Usage:
  python dev_tools/hooks/run_all.py              # 跑全部
  python dev_tools/hooks/run_all.py --only naming,legacy  # 只跑部分
  python dev_tools/hooks/run_all.py --verbose    # 详细输出
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

# Force UTF-8 output (Windows GBK compat)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
elif sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = ROOT / "dev_tools" / "hooks"

# 7 个守门（按依赖顺序）
HOOKS = [
    ("naming", "check_naming.py", "naming conventions + zh_name/en_name"),
    ("directory", "check_directory.py", "directory structure"),
    ("legacy", "check_legacy.py", "legacy/blacklist references"),
    ("test_required", "check_test_required.py", "new src/ must have tests"),
    ("import_canonical", "check_import_canonical.py", "import path canonicalization"),
    ("file_size", "check_file_size.py", "file line count limits"),
    ("commit_msg", "check_commit_msg.py", "commit message format"),
]


def run_hook(name: str, script: str, desc: str, verbose: bool = False) -> Tuple[int, float, str]:
    """Run a single hook, return (returncode, elapsed_sec, output)."""
    script_path = HOOKS_DIR / script
    start = time.time()
    try:
        # On Windows, force UTF-8 in subprocess to avoid GBK decode errors
        env = None
        if sys.platform == "win32":
            import os
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
            env=env,
        )
        elapsed = time.time() - start
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode, elapsed, output
    except subprocess.TimeoutExpired:
        return 124, 60.0, "[TIMEOUT] 60s exceeded"
    except Exception as e:
        return 1, 0.0, f"[EXCEPTION] {e}"


def main() -> int:
    """Main entry point."""
    args = sys.argv[1:]
    only = None
    verbose = "--verbose" in args
    for arg in args:
        if arg.startswith("--only="):
            only = arg.split("=", 1)[1].split(",")
        elif arg == "--verbose":
            continue
        else:
            print(f"[ERROR] Unknown arg: {arg}")
            print("Usage: run_all.py [--only=naming,legacy,...] [--verbose]")
            return 2

    print("=" * 70)
    print("[GUARD] AI Hooks - 7 auto-checks")
    print("=" * 70)
    print(f"Project root: {ROOT}")
    print(f"Rules source: AGENTS.md + docs/adr/")
    print()

    results: List[Tuple[str, int, float, str]] = []
    total_start = time.time()

    for name, script, desc in HOOKS:
        if only and name not in only:
            continue

        print(f"[WAIT] [{name}] {desc} ...", end=" ", flush=True)
        rc, elapsed, output = run_hook(name, script, desc, verbose)
        results.append((name, rc, elapsed, output))

        if rc == 0:
            print(f"[OK] ({elapsed:.2f}s)")
        else:
            print(f"[FAIL] ({elapsed:.2f}s)")

        if verbose and output:
            print("---")
            print(output.strip())
            print("---")

    total_elapsed = time.time() - total_start
    print()
    print("=" * 70)
    print(f"[SUMMARY] Total: {len(results)} checks, {total_elapsed:.2f}s")
    print("=" * 70)

    failed = [r for r in results if r[1] != 0]
    if failed:
        print(f"\n[FAIL] {len(failed)} failed:\n")
        for name, rc, elapsed, output in failed:
            print(f"=== [{name}] ===")
            print(output.strip() if output else "(no output)")
            print()
        print("=" * 70)
        print("Fix: see AGENTS.md + docs/adr/")
        print("Emergency bypass: git commit --no-verify (must write ADR in 24h)")
        return 1

    print(f"\n[OK] All {len(results)} passed - ready to commit!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
