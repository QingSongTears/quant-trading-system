#!/usr/bin/env python3
"""
.githooks/commit-msg.py - commit-msg hook Python implementation

Called by .githooks/commit-msg (sh wrapper). Git passes message file as $1.
Validates commit message format (other guards ran in pre-commit).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Force UTF-8 output
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
    msg_file = ""
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        msg_file = sys.argv[1]
    elif "COMMIT_MSG_FILE" in os.environ:
        candidate = os.environ["COMMIT_MSG_FILE"]
        if Path(candidate).exists():
            msg_file = candidate

    if not msg_file:
        # No message file - skip (pre-commit hook validated everything else)
        return 0

    print("[HOOK] AI guard starting (commit-msg) ...")
    print(f"   Message file: {msg_file}")
    print()

    env = {**os.environ, "COMMIT_MSG_FILE": msg_file, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    result = subprocess.run(
        [sys.executable, str(ROOT / "dev_tools" / "hooks" / "check_commit_msg.py")],
        cwd=ROOT, env=env,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
