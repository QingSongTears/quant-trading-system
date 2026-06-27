#!/usr/bin/env python3
"""
apply_classification.py - one-time tool to mv scripts/ into 3 buckets.

Reads classify_scripts.py output, uses git mv to move each file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS_DIR.parent

# Force UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 19 uncertain ones are all update_*/import_*/parallel_* - mark as active
UNCERTAIN_AS_ACTIVE = True

# Bucket directory mapping
BUCKET_DIRS = {
    "active": "scripts/active",
    "_deprecated": "scripts/_deprecated",
    "tests": "scripts/_deprecated/tests",  # tests go to _deprecated, not tests/ (which is pytest)
}

# Manual classification for the 19 uncertain
MANUAL_ACTIVE = {
    "bull_8d_analysis.py",
    "db_optimize.py",
    "full_bull_backtest.py",
    "import_from_westock_baostock_akshare.py",
    "increment_research.py",
    "migrate_xgb_scaler.py",
    "parallel_margin_block.py",
    "parallel_tech_indicators.py",
    "refresh_stock_profile.py",
    "refresh_tencent_quotes.py",
    "sync_csv_to_db.py",
    "update_announcements.py",
    "update_dragon_tiger.py",
    "update_fund_flow_correct.py",
    "update_holder_num.py",
    "update_hot_reason.py",
    "update_shares.py",
    "update_snapshots.py",
    "update_tech_indicators_csv.py",
}


def classify_file(name: str) -> str:
    """Get bucket for a script file name."""
    from classify_scripts import classify, ARCHIVED_STRATEGIES, DEBUG_PATTERNS, TEST_PATTERNS, ACTIVE_PATTERNS, DEPRECATED_SUFFIXES

    if name in MANUAL_ACTIVE:
        return "active"

    stem = Path(name).stem
    if stem in ARCHIVED_STRATEGIES:
        return "_deprecated"
    for s in DEPRECATED_SUFFIXES:
        if name.endswith(s):
            return "_deprecated"
    for p in DEBUG_PATTERNS:
        if p.match(stem):
            return "_deprecated"
    for p in TEST_PATTERNS:
        if p.match(stem):
            return "tests"
    for p in ACTIVE_PATTERNS:
        if p.match(stem):
            return "active"
    return "?"


def main() -> int:
    """Move all scripts to their buckets."""
    scripts = sorted([p for p in SCRIPTS_DIR.glob("*.py") if p.is_file()])
    # Skip dev/ and other subdirs
    scripts = [p for p in scripts if p.parent == SCRIPTS_DIR]

    moves = []
    for script in scripts:
        bucket = classify_file(script.name)
        target_dir = BUCKET_DIRS.get(bucket, "scripts/active")
        target = REPO_ROOT / target_dir / script.name
        moves.append((script, target, bucket))

    # Print plan
    print("=" * 70)
    print("Plan: move scripts into buckets")
    print("=" * 70)
    by_bucket = {}
    for src, dst, bucket in moves:
        by_bucket.setdefault(bucket, []).append((src, dst))

    for bucket, items in sorted(by_bucket.items()):
        print(f"\n[{bucket}] {len(items)} files -> {BUCKET_DIRS.get(bucket, '?')}")
        for src, dst in items[:5]:
            print(f"  {src.name} -> {dst.relative_to(REPO_ROOT)}")
        if len(items) > 5:
            print(f"  ... and {len(items) - 5} more")

    # Confirm and execute
    print()
    response = input("Execute? (yes/no): ").strip().lower()
    if response != "yes":
        print("Aborted.")
        return 0

    # Create dirs and move
    for bucket, items in by_bucket.items():
        target_dir = REPO_ROOT / BUCKET_DIRS[bucket]
        target_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    for src, dst, bucket in moves:
        if not dst.parent.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
        # Use git mv to preserve history
        result = subprocess.run(
            ["git", "mv", str(src.relative_to(REPO_ROOT)), str(dst.relative_to(REPO_ROOT))],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        if result.returncode != 0:
            # Fall back to plain mv
            print(f"[WARN] git mv failed for {src.name}: {result.stderr}")
            src.rename(dst)
        else:
            moved += 1
            print(f"  [OK] {src.name} -> {dst.relative_to(REPO_ROOT)}")

    print(f"\nMoved {moved} files.")
    print()
    print("Next steps:")
    print("  1. Run dev_tools/hooks/run_all.py to verify nothing broke")
    print("  2. Update any code that imports from old scripts/ location")
    print("  3. Commit: chore(scripts): 三桶分类 (active/archive/_deprecated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
