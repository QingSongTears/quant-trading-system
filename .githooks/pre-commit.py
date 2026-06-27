#!/usr/bin/env python3
"""
.githooks/pre-commit.py - pre-commit hook Python implementation

Called by .githooks/pre-commit (sh wrapper) on every `git commit`.
Any guard failure -> commit rejected.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Force UTF-8 output (Windows GBK compat)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
elif sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """Main entry."""
    print("[HOOK] AI guard starting (pre-commit) ...")
    print(f"   Workdir: {ROOT}")
    print()

    env = None
    if sys.platform == "win32":
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

    result = subprocess.run(
        [sys.executable, str(ROOT / "dev_tools" / "hooks" / "run_all.py")],
        cwd=ROOT, env=env,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
