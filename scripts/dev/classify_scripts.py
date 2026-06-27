#!/usr/bin/env python3
"""
classify_scripts.py - one-time tool to classify scripts/ into 3 buckets

Outputs a CSV report with each script's suggested bucket.
Run: python scripts/dev/classify_scripts.py > /tmp/classification.csv
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS_DIR.parent

# Force UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# Classification rules
ACTIVE_PATTERNS = [
    re.compile(r"^(build_db|backup_db|build_parquet|consolidate_db|log_rotate|health_check|param_server|update_to_)\w*"),
    re.compile(r"^(update_all_data|update_daily_data|update_quotes|update_em_news|update_to_)\w*"),
    re.compile(r"^(fill_recent|fill_circulating|fill_kline|fill_eastmoney)\w*"),
    re.compile(r"^(import_csv|import_benchmark|import_finance|import_fund|import_holder|import_institutional|import_lhb|import_margin|import_more|import_new|import_small|import_stock|import_tech|import_ths|import_backtest)"),
    re.compile(r"^(build_bull|batch_backtest|bull_backtest|multi_dim|six_dim|mini_backtest|param_grid|param_scan|walk_forward)"),
    re.compile(r"^(run_v6|predict_next|sample_weekly|seed_walk|rebuild_)"),
    re.compile(r"^(analyze_score|export_7d|export_ardot|gen_fund_flow|gen_scaler)"),
    re.compile(r"^(regenerate_combined_scores|train_xgb|daily_pred|verify_bull)"),
    re.compile(r"^(incremental_update|mark_delisted|fetch_share_structure|backfill_shares|full_all_strategies)"),
    re.compile(r"^(add_bj_stocks|akshare_finance_batch|download_fund_flow|download_institutional)"),
    re.compile(r"^(regenerate_combined_scores|fundflow_parallel)"),
]

# 已归档（ROADMAP 标了）— 一律 _deprecated
ARCHIVED_STRATEGIES = {
    "bull_8d_monthly_fixed", "v5_hybrid", "v7_bull_wave",
    "bull_wave", "v3_reversal", "v2_trend",
}

# 调试/单次脚本
DEBUG_PATTERNS = [
    re.compile(r"^debug_"),
    re.compile(r"^trace_404"),
    re.compile(r"^probe_"),
    re.compile(r"^fix_"),
    re.compile(r"^patch_"),
    re.compile(r"^quick_"),
    re.compile(r"^convert_"),
    re.compile(r"^inject_"),
    re.compile(r"^verify_"),
]

# 测试/audit/e2e
TEST_PATTERNS = [
    re.compile(r"^e2e_"),
    re.compile(r"^audit_"),
    re.compile(r"^test_"),
    re.compile(r"^verify_"),
    re.compile(r"^headless_"),
    re.compile(r"^self_test_"),
    re.compile(r"^realistic_"),
    re.compile(r"^deep_functional_"),
    re.compile(r"^static_html_"),
    re.compile(r"^full_html_"),
    re.compile(r"^graphic_"),
    re.compile(r"^screenshot_"),
    re.compile(r"^vnpy_v6_smoke"),
    re.compile(r"^check_fill_rate"),
    re.compile(r"^show_db_stats"),
    re.compile(r"^health_check"),
]

# 文件后缀名（废弃/备份）
DEPRECATED_SUFFIXES = [".bak", ".legacy", ".deprecated", ".old"]


def get_last_commit_date(filepath: Path) -> str:
    """Get last commit date for file (YYYY-MM-DD)."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=short", "--", str(filepath.relative_to(REPO_ROOT))],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip() or "never"
    except subprocess.CalledProcessError:
        return "never"


def classify(filepath: Path) -> str:
    """Classify a single script. Returns: active / archive / _deprecated / tests."""
    name = filepath.stem
    full = filepath.name

    # Deprecated suffix
    for suffix in DEPRECATED_SUFFIXES:
        if full.endswith(suffix):
            return "_deprecated"

    # Archived strategy name
    if name in ARCHIVED_STRATEGIES:
        return "_deprecated"

    # Debug/single-use
    for pat in DEBUG_PATTERNS:
        if pat.match(name):
            return "_deprecated"

    # Tests/audit (move to tests/ or dev-tools, not active scripts)
    for pat in TEST_PATTERNS:
        if pat.match(name):
            return "tests"

    # Active patterns
    for pat in ACTIVE_PATTERNS:
        if pat.match(name):
            return "active"

    # Default: not matched = uncertain
    return "?"


def main() -> int:
    scripts = sorted([p for p in SCRIPTS_DIR.glob("*.py") if p.is_file()])

    # Print CSV header
    print("script,last_commit,suggested_bucket")
    for script in scripts:
        bucket = classify(script)
        last_commit = get_last_commit_date(script)
        print(f"{script.name},{last_commit},{bucket}")

    # Summary
    print()
    print("# Summary:")
    buckets = {}
    for script in scripts:
        b = classify(script)
        buckets[b] = buckets.get(b, 0) + 1
    for bucket, count in sorted(buckets.items()):
        print(f"# {bucket}: {count}")
    print(f"# total: {len(scripts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
