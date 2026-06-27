#!/usr/bin/env python3
"""
check_commit_msg.py — AI 守门 #7: 提交信息规范

挡：
  - 空标题
  - 标题不含 type（feat/fix/refactor/perf/test/docs/style/chore/revert）
  - 中文-only 标题（"优化" / "fix bug" / "update"）
  - 无 issue 关联（必须含 (#XX) 或 (#XX, #YY)）
  - 标题 > 72 字符

不挡：
  - merge commit（git 自动生成）
  - revert commit（git 自动生成）
  - chore(docs): 更新 AGENTS.md (#N) 这类守则自身变更
"""
from __future__ import annotations

import re
import subprocess
import sys
from typing import List

# Force UTF-8 output (Windows GBK compat)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
elif sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

ALLOWED_TYPES = (
    "feat", "fix", "refactor", "perf", "test", "docs",
    "style", "chore", "revert", "build", "ci",
)

# type(scope): subject
COMMIT_TITLE_RE = re.compile(
    r"^(?P<type>" + "|".join(ALLOWED_TYPES) + r")"
    r"(?:\((?P<scope>[\w\-/]+)\))?"
    r":\s+(?P<subject>.+)$"
)

# (#123) 或 (#123, #456) 或 (fixes #123)
ISSUE_REF_RE = re.compile(r"\(#\d+(?:\s*,\s*#\d+)*\)")
ISSUE_REF_RE_ALT = re.compile(r"\b(?:fixes|closes|resolves)\s+#\d+", re.IGNORECASE)

# 纯中文标题（无 ASCII 字符）
PURE_CHINESE_RE = re.compile(r"^[一-鿿\s]+$")

# 模糊标题
VAGUE_SUBJECTS = {
    "优化", "update", "fix", "fix bug", "改动", "修改", "update code",
    "wip", "tmp", "test", "init", "misc", "misc.", "各种修改",
}


def get_commit_msg() -> str:
    """Get the commit message from the right source.

    Only reads from COMMIT_MSG_FILE (the env var git passes to commit-msg hook).
    Does NOT read .git/COMMIT_EDITMSG (that's a stale draft from previous edit).
    Does NOT read git log (that's the previous commit, not the current one).
    """
    import os
    # Only the commit-msg hook context sets this env var
    msg_file = os.environ.get("COMMIT_MSG_FILE", "")
    if msg_file and os.path.exists(msg_file):
        with open(msg_file, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def get_staged_commit_msg() -> str:
    """从 git 钩子上下文获取。"""
    return get_commit_msg()


def is_merge_or_revert(title: str) -> bool:
    """检查是否是 merge/revert 自动 commit。"""
    return title.startswith("Merge ") or title.startswith("Revert ")


def main() -> int:
    """Main entry."""
    import os
    is_commit_msg_hook = "COMMIT_MSG_FILE" in os.environ
    is_in_git_hook = is_commit_msg_hook or "GIT_DIR" in os.environ or os.path.exists(ROOT / ".git" / "index.lock")

    msg = get_staged_commit_msg()

    if not msg.strip():
        if is_commit_msg_hook:
            # commit-msg hook: no message = fatal
            print("[FAIL] Empty commit message")
            return 1
        # pre-commit hook: no message yet, skip
        # Manual run: also skip (commit-msg hook will check when actually committing)
        print("[SKIP] check_commit_msg.py: no message available "
              "(commit-msg hook will validate when committing)")
        return 0

    title = msg.split("\n", 1)[0].strip()
    errors: List[str] = []

    if is_merge_or_revert(title):
        print("[SKIP] check_commit_msg.py: merge/revert commit")
        return 0

    # 1. 标题不能为空
    if not title:
        errors.append("[FAIL] Empty commit message")

    # 2. 必须匹配 Conventional Commits
    m = COMMIT_TITLE_RE.match(title)
    if not m:
        errors.append(
            f"❌ 提交标题不符合 Conventional Commits 格式\n"
            f"   当前: {title!r}\n"
            f"   期望: <type>(<scope>): <subject> (#<issue>)\n"
            f"   例:   feat(scoring): 新增 FundFlowScorer (#78)"
        )
    else:
        subject = m.group("subject").strip()

        # 3. 标题不能 > 72 字符
        if len(title) > 72:
            errors.append(f"❌ 标题过长 ({len(title)} 字符): {title}")

        # 4. 模糊标题
        if subject.lower() in VAGUE_SUBJECTS:
            errors.append(
                f"❌ 标题太模糊: {subject!r}\n"
                f"   例: '优化' → 'perf(scoring): 缓存 FundFlow 数据 (#80)'"
            )

        # 5. 纯中文标题
        if PURE_CHINESE_RE.match(subject):
            errors.append(
                f"❌ 标题不能是纯中文（无 ASCII）: {subject!r}\n"
                f"   例: '修复bug' → 'fix(scoring): 修复 FundFlow 空指针 (#80)'"
            )

    # 6. 必须有 issue 关联
    full_msg = msg
    if not (ISSUE_REF_RE.search(full_msg) or ISSUE_REF_RE_ALT.search(full_msg)):
        errors.append(
            f"❌ 提交缺少 issue 关联（必须含 (#NN)）\n"
            f"   当前标题: {title!r}\n"
            f"   期望: feat(scope): subject (#123)\n"
            f"   或在 body 写 'fixes #123' / 'closes #123'"
        )

    if errors:
        print("❌ check_commit_msg.py 发现提交信息问题:\n")
        for err in errors:
            print(err)
            print()
        print("=" * 60)
        print("规范详见: AGENTS.md §6 Conventional Commits 强制格式")
        return 1

    print(f"✅ check_commit_msg.py: 提交信息合规 — {title!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
